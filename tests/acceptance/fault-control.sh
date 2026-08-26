#!/bin/sh
set -eu

# Docker-requiring acceptance flow for the fault control adapter.  Runs on the
# remote node where Docker is allowed.  It proves the fault actuations the
# runner depends on are real Docker/RPC operations behind the scoped token:
#   * unauthenticated control is refused (fail closed);
#   * two independent RPC providers can be driven into an observable
#     disagreement and back into agreement;
#   * a reorg replaces the canonical chain head;
#   * one of the two L1 RPC providers can be stopped and started as a real
#     container operation;
#   * a service can be paused and unpaused.

root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$root"
project=zkdeal-fault-control-acceptance
network=${project}_net
prefix=${project}-
anvil_image=${L1_RPC_IMAGE:-ghcr.io/foundry-rs/foundry:latest}
alpine_image=${ALPINE_IMAGE:-alpine@sha256:a8560b36e8b8210634f77d9f7f9efd7ffa463e380b75e2e74aff4511df3ef88c}
curl_image=${CURL_IMAGE:-curlimages/curl@sha256:4026b29997dc7c823b51c164b71e2b51e0fd95cce4601f78202c513d97da2922}
candidate=fixture-candidate-20260821
controller=zkdeal-fault-control:acceptance
work=$(mktemp -d)

cleanup() {
  for name in controller rpc-a rpc-b coordinator-active indexer; do
    docker rm -f "${prefix}${name}" >/dev/null 2>&1 || true
  done
  docker network rm "$network" >/dev/null 2>&1 || true
  rm -rf "$work"
}
on_exit() {
  status=$?
  [ "$status" -eq 0 ] || docker logs "${prefix}controller" >&2 2>&1 || true
  cleanup
  exit "$status"
}
trap on_exit EXIT
trap 'exit 130' INT TERM
cleanup
# The eager cleanup above also removes $work; recreate it for this run.
mkdir -p "$work"

sha256() { sha256sum "$1" | cut -d' ' -f1; }
running() { docker inspect --format '{{.State.Running}}' "$1"; }
paused() { docker inspect --format '{{.State.Paused}}' "$1"; }
opid() { sed -n 's/.*"operationId":"\(fc-[0-9a-f]\{40\}\)".*/\1/p'; }

docker network create "$network" >/dev/null

source_sha=$(sha256 fault-control/fault_control.py)
docker build -f fault-control/Dockerfile \
  --build-arg FAULT_CONTROL_SOURCE_SHA256="$source_sha" -t "$controller" . >/dev/null

# Both providers must agree before any fault is injected; a pinned genesis
# timestamp keeps two independently started anvils byte-identical (wall-clock
# genesis made agreement depend on both starting in the same second).
docker run -d --name "${prefix}rpc-a" --network "$network" --network-alias rpc-a \
  --entrypoint anvil "$anvil_image" --host 0.0.0.0 --port 8545 --timestamp 1755820800 >/dev/null
docker run -d --name "${prefix}rpc-b" --network "$network" --network-alias rpc-b \
  --entrypoint anvil "$anvil_image" --host 0.0.0.0 --port 8545 --timestamp 1755820800 >/dev/null
docker run -d --name "${prefix}coordinator-active" --network "$network" --network-alias coordinator-active \
  "$alpine_image" sleep 100000 >/dev/null
docker run -d --name "${prefix}indexer" --network "$network" --network-alias indexer \
  "$alpine_image" sleep 100000 >/dev/null

rpc_ready() {
  attempt=0
  until docker run --rm --network "$network" "$curl_image" --silent --fail \
    -H 'Content-Type: application/json' \
    --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' "http://$1:8545/" >/dev/null 2>&1; do
    attempt=$((attempt + 1)); [ "$attempt" -lt 60 ] || { echo "$1 never became ready" >&2; exit 1; }
    sleep 1
  done
}
rpc_ready rpc-a
rpc_ready rpc-b

umask 077
token="eph_fault_control_acceptance_token_000001"
# The controller runs as uid 65532 and enforces 0600-or-stricter on its token
# file, so the token must be owned by that uid; the non-secret topology gets
# world-read instead.
printf '%s' "$token" >"$work/fault-token"
chmod 0600 "$work/fault-token"
chown 65532:65532 "$work/fault-token"

cat >"$work/topology.json" <<JSON
{
  "schemaVersion": 1,
  "classification": "non-release-fixture",
  "candidateId": "${candidate}",
  "planSha256": "$(printf '%064d' 1 | tr 0-9 a)",
  "hostedIntegrationToken": "sha256:$(printf '%064d' 2 | tr 0-9 b)",
  "faultControlTokenSha256": "$(sha256 "$work/fault-token")",
  "allowedHosts": ["rpc-a", "rpc-b"],
  "rpcProviders": {"rpc-a": "http://rpc-a:8545", "rpc-b": "http://rpc-b:8545"},
  "docker": {
    "socket": "/var/run/docker.sock",
    "containerPrefix": "${prefix}",
    "restartTargets": {
      "rpc-a": "${prefix}rpc-a",
      "rpc-b": "${prefix}rpc-b",
      "coordinator-active": "${prefix}coordinator-active",
      "indexer": "${prefix}indexer"
    },
    "partitionNetwork": null,
    "partitionTargets": []
  },
  "loadProfiles": {},
  "requestTimeoutSeconds": 10
}
JSON
chmod 0644 "$work/topology.json"
topology_sha=$(sha256 "$work/topology.json")
plan_sha=$(printf '%064d' 1 | tr 0-9 a)
hosted_token="sha256:$(printf '%064d' 2 | tr 0-9 b)"
docker_gid=$(stat -c %g /var/run/docker.sock)

docker run -d --name "${prefix}controller" --network "$network" --network-alias fault-control \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --group-add "$docker_gid" \
  --tmpfs /journal:rw,uid=65532,gid=65532,mode=0700 \
  -v "$work/topology.json:/topology.json:ro" -v "$work/fault-token:/fault-token:ro" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e FAULT_CONTROL_TOPOLOGY_FILE=/topology.json -e FAULT_CONTROL_TOPOLOGY_SHA256="$topology_sha" \
  -e FAULT_CONTROL_CANDIDATE_ID="$candidate" -e FAULT_CONTROL_PLAN_SHA256="$plan_sha" \
  -e FAULT_CONTROL_HOSTED_INTEGRATION_TOKEN="$hosted_token" \
  -e FAULT_CONTROL_TOKEN_FILE=/fault-token \
  -e FAULT_CONTROL_CANDIDATE_DESCRIPTOR_SHA256="$(printf '%064d' 3 | tr 0-9 c)" \
  -e FAULT_CONTROL_ADAPTER_SOURCE_SHA256="$source_sha" \
  -e FAULT_CONTROL_ADAPTER_IMAGE="registry.local/zkdeal-fault-control@sha256:${source_sha}" \
  -e FAULT_CONTROL_PLATFORM=docker \
  "$controller" >/dev/null

call() { docker run --rm --network "$network" "$curl_image" --silent --show-error "$@"; }
base=http://fault-control:8080
attempt=0
until call --fail "$base/ready" >/dev/null 2>&1; do
  attempt=$((attempt + 1)); [ "$attempt" -lt 60 ] || { echo 'controller did not become ready' >&2; exit 1; }
  sleep 1
done

binding="\"binding\":{\"candidateId\":\"${candidate}\",\"planSha256\":\"${plan_sha}\",\"hostedIntegrationToken\":\"${hosted_token}\"}"
auth="Authorization: Bearer ${token}"
fault() {
  action=$1; parameters=$2; key=$3; corr=$4
  call --fail -H 'Content-Type: application/json' -H "$auth" \
    -H "Idempotency-Key: ${key}" -H "X-Correlation-Id: ${corr}" \
    --data "{\"schemaVersion\":1,${binding},\"action\":\"${action}\",\"parameters\":${parameters}}" "$base/v1/faults"
}

# Fail closed without the scoped token.
status=$(call -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: unauth-key-0001' -H 'X-Correlation-Id: unauth-correlation-0001' \
  --data "{\"schemaVersion\":1,${binding},\"action\":\"rpc-disagreement\",\"parameters\":{\"phase\":\"start\",\"preparedOperationId\":null}}" "$base/v1/faults")
[ "$status" = 401 ] || { echo "unauthenticated fault was not denied: $status" >&2; exit 1; }

# Two-provider disagreement, then restored agreement.
disagree=$(fault rpc-disagreement '{"phase":"start","preparedOperationId":null}' disagree-start-0001 disagree-correlation-0001)
printf '%s' "$disagree" | grep -F '"phase":"DISAGREEING"' >/dev/null || { echo 'RPC providers did not disagree' >&2; exit 1; }
disagree_op=$(printf '%s' "$disagree" | opid)
restored=$(fault rpc-disagreement "{\"phase\":\"restore\",\"preparedOperationId\":\"${disagree_op}\"}" disagree-restore-0001 disagree-correlation-0002)
printf '%s' "$restored" | grep -F '"phase":"RESTORED"' >/dev/null || { echo 'RPC providers did not converge' >&2; exit 1; }

# Reorg replaces the canonical head.
prepared=$(fault l1-reorg '{"phase":"prepare","depth":3,"preparedOperationId":null}' reorg-prepare-0001 reorg-correlation-0001)
printf '%s' "$prepared" | grep -F '"phase":"PREPARED"' >/dev/null || { echo 'reorg preparation failed' >&2; exit 1; }
prepared_op=$(printf '%s' "$prepared" | opid)
replaced=$(fault l1-reorg "{\"phase\":\"replace\",\"depth\":3,\"preparedOperationId\":\"${prepared_op}\"}" reorg-replace-0001 reorg-correlation-0002)
printf '%s' "$replaced" | grep -F '"phase":"REPLACED"' >/dev/null || { echo 'reorg replacement failed' >&2; exit 1; }

# Stop one of the two L1 RPC providers, then start it - a real container op.
[ "$(running "${prefix}rpc-a")" = true ] || { echo 'rpc-a not running before stop' >&2; exit 1; }
fault rpc-provider-control '{"provider":"rpc-a","phase":"stop"}' rpc-stop-0001 rpc-correlation-0001 >/dev/null
[ "$(running "${prefix}rpc-a")" = false ] || { echo 'rpc-provider-control stop did not stop the container' >&2; exit 1; }
fault rpc-provider-control '{"provider":"rpc-a","phase":"start"}' rpc-start-0001 rpc-correlation-0002 >/dev/null
[ "$(running "${prefix}rpc-a")" = true ] || { echo 'rpc-provider-control start did not start the container' >&2; exit 1; }

# Pause and unpause a service.
fault service-pause '{"target":"indexer","phase":"pause"}' pause-0001 pause-correlation-0001 >/dev/null
[ "$(paused "${prefix}indexer")" = true ] || { echo 'service-pause did not pause the container' >&2; exit 1; }
fault service-pause '{"target":"indexer","phase":"unpause"}' unpause-0001 pause-correlation-0002 >/dev/null
[ "$(paused "${prefix}indexer")" = false ] || { echo 'service-pause did not unpause the container' >&2; exit 1; }

echo 'fault control acceptance passed: fail-closed auth, two-provider disagreement and restore, canonical reorg, real rpc provider stop/start, and service pause/unpause'
