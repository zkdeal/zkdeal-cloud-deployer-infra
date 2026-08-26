#!/bin/sh
set -eu

test -f /.dockerenv || { echo 'ERROR: Docker-only acceptance' >&2; exit 2; }
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."

prefix="zkdeal-agent-live-$$"
network="$prefix-net"
fixture_image="$prefix-fixture"
agent_image=${PROVER_AGENT_CANDIDATE_IMAGE:-zkdeal-prover-agent:acceptance}
curl_image='curlimages/curl@sha256:4026b29997dc7c823b51c164b71e2b51e0fd95cce4601f78202c513d97da2922'
candidate_mode=false

cleanup() {
  docker rm -f "$prefix-agent" "$prefix-owner" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  docker image rm "$fixture_image" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if [ -n "${PROVER_AGENT_CANDIDATE_IMAGE:-}" ]; then
  candidate_mode=true
  case "$agent_image" in
    *@sha256:????????????????????????????????????????????????????????????????) ;;
    *) echo 'ERROR: PROVER_AGENT_CANDIDATE_IMAGE must be exact repository@sha256:<64 hex>' >&2; exit 2 ;;
  esac
  repository=${agent_image%@sha256:*}
  case "${repository##*/}" in *:*) echo 'ERROR: candidate agent reference must not contain a tag' >&2; exit 2 ;; esac
  case "$agent_image" in *[A-F]*|*REPLACE*|registry.invalid/*|registry.example/*) echo 'ERROR: candidate agent reference is mutable or a placeholder' >&2; exit 2 ;; esac
  docker pull "$agent_image" >/dev/null
fi

agent_id=$(docker image inspect "$agent_image" --format '{{.Id}}')
source_label=$(docker image inspect "$agent_image" --format '{{index .Config.Labels "org.zkdeal.owner.source.sha256"}}')
capability_label=$(docker image inspect "$agent_image" --format '{{index .Config.Labels "org.zkdeal.owner.liveness-capability.sha256"}}')
trace_capability_label=$(docker image inspect "$agent_image" --format '{{index .Config.Labels "org.zkdeal.owner.trace-capability.sha256"}}')
expected_source=$(cd ../prover-node && find agent/src -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}')
expected_capability=$(sha256sum ../prover-node/agent/liveness-capability.json | awk '{print $1}')
expected_trace_capability=$(sha256sum ../prover-node/agent/trace-capability.json | awk '{print $1}')
test "$source_label" = "$expected_source" || { echo 'ERROR: agent image source label differs from current owner bytes' >&2; exit 1; }
test "$capability_label" = "$expected_capability" || { echo 'ERROR: agent image capability label differs from current owner bytes' >&2; exit 1; }
test "$trace_capability_label" = "$expected_trace_capability" || { echo 'ERROR: agent image trace capability label differs from current owner bytes' >&2; exit 1; }

docker build --pull=false -f tests/fixtures/ProbeBoundary.Dockerfile -t "$fixture_image" . >/dev/null
docker network create --internal "$network" >/dev/null
docker run -d --rm --name "$prefix-owner" --network "$network" --network-alias owner \
  "$fixture_image" --mode agent-owner --port 8005 --token liveness-token \
  --queue-token queue-token --prover-token prover-token >/dev/null

docker run -d --rm --name "$prefix-agent" --network "$network" --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=8m \
  -e QUEUE_URL=http://owner:8005 \
  -e ZKDEAL_QUEUE_NODE_TOKEN=queue-token \
  -e NODE_ID=provider-node-live-a \
  -e PROVER_URL=http://owner:8005 \
  -e ZKDEAL_PROVER_TOKEN=prover-token \
  -e ZKDEAL_AGENT_GPU=1 \
  -e POLL_INTERVAL_MS=100 \
  -e HEARTBEAT_INTERVAL_MS=200 \
  -e ROOM_POOL=0x1111111111111111111111111111111111111111 \
  -e NODE_LIVENESS_COORDINATOR_URL=http://owner:8005 \
  -e NODE_LIVENESS_COORDINATOR_AUTH_TOKEN=liveness-token \
  -e NODE_LIVENESS_ACCOUNT=0x2222222222222222222222222222222222222222 \
  -e L1_CHAIN_ID=1 \
  -e NODE_LIVENESS_INTERVAL_MS=250 \
  -e NODE_LIVENESS_CONFIRMATIONS=2 \
  -e NODE_LIVENESS_COORDINATOR_POLL_MS=25 \
  "$agent_image" >/dev/null

state=''
attempt=0
until state=$(docker run --rm --network "$network" "$curl_image" -fsS http://owner:8005/acceptance/state 2>/dev/null) \
  && printf '%s' "$state" | python -c 'import json,sys; s=json.load(sys.stdin); assert s["completions"] == 1 and s["proves"] == 1 and s["heartbeats"] >= 1' 2>/dev/null; do
  attempt=$((attempt + 1))
  test "$attempt" -lt 80 || { docker logs "$prefix-agent" >&2; echo 'ERROR: packaged agent never completed owner-boundary flow' >&2; exit 1; }
  sleep 0.25
done

printf '%s' "$state" | python -c '
import json,sys
s=json.load(sys.stdin)
assert s["failures"] == 0
assert s["queueAuth"] and s["proverAuth"] and s["heartbeatAuth"]
assert s["schemaNegotiated"] and s["idempotencyBound"] and s["correlationBound"]
assert s["completedJobId"] == "job-live-0001"
'

agent_logs=$(docker logs "$prefix-agent" 2>&1)
printf '%s\n' "$agent_logs" | python -c '
import json,sys
records=[]
for line in sys.stdin:
    try: value=json.loads(line)
    except json.JSONDecodeError: continue
    if value.get("component")=="prover-agent": records.append(value)
required={"schemaVersion","timestamp","correlationId","tenantId","roomId","jobId","operationId","component","event","outcome"}
for event,outcome in (("job.start","started"),("job.complete","succeeded")):
    matching=[r for r in records if r.get("event")==event and r.get("outcome")==outcome]
    assert len(matching)==1,(event,records)
    record=matching[0]
    assert set(record)==required,record
    assert record["schemaVersion"]==1 and record["operationId"] is None
    assert record["correlationId"]=="corr-job-live-0001"
    assert record["tenantId"]=="tenant-live-a" and record["roomId"]=="7"
    assert record["jobId"]=="job-live-0001"
    assert len(json.dumps(record,separators=(",",":")))<=2048
assert not ({"authorization","token","request","result","proof","seal","errorReason","environment"} & set().union(*(set(r) for r in records)))
'

docker stop "$prefix-agent" >/dev/null
error_file=$(mktemp)
trap 'rm -f "$error_file"; cleanup' EXIT INT TERM
if docker run --rm --network "$network" --read-only --tmpfs /tmp:rw,noexec,nosuid,size=8m \
  -e QUEUE_URL=http://owner:8005 -e ZKDEAL_QUEUE_NODE_TOKEN=queue-token \
  -e NODE_ID=forbidden-direct-signer -e PROVER_URL=http://owner:8005 \
  -e ZKDEAL_PROVER_TOKEN=prover-token -e ROOM_POOL=0x1111111111111111111111111111111111111111 \
  -e NODE_LIVENESS_SIGNER_URL=https://signer.invalid \
  "$agent_image" 2>"$error_file"; then
  echo 'ERROR: packaged agent accepted direct Web3Signer authority' >&2
  exit 1
fi
grep -F 'direct Web3Signer heartbeat publication is forbidden' "$error_file" >/dev/null

printf '{"proverAgentOwnerBoundary":"passed","classification":"owner-protocol-fixture-live-not-release-owner-stack","candidateMode":%s,"agentImageId":"%s","ownerSourceSha256":"%s","livenessCapabilitySha256":"%s","traceCapabilitySha256":"%s","durableQueueLeaseComplete":true,"authenticatedProverForward":true,"durableHeartbeatFinalized":true,"schemaNegotiated":true,"idempotencyAndCorrelationBound":true,"structuredTraceJoin":true,"directSignerRejected":true}\n' \
  "$candidate_mode" "$agent_id" "$source_label" "$capability_label" "$trace_capability_label"
