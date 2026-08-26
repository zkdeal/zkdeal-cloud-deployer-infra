#!/bin/sh
set -eu

chart="helm/zkdeal"
fixture="tests/fixtures/helm-production-render.yaml"
output="$(mktemp)"
trap 'rm -f "$output"' EXIT

render() {
  helm template zkdeal "$chart" -f "$fixture" "$@" >"$output" 2>&1
}

render
grep -F 'name: NODE_LIVENESS_COORDINATOR_URL' "$output" >/dev/null
grep -F 'name: NODE_LIVENESS_ACCOUNT' "$output" >/dev/null
grep -F 'name: NODE_LIVENESS_COORDINATOR_AUTH_TOKEN' "$output" >/dev/null
grep -F 'name: L1_CHAIN_ID' "$output" >/dev/null
grep -F 'name: ROOM_POOL' "$output" >/dev/null

python - "$output" <<'PY'
import sys, yaml
documents = list(yaml.safe_load_all(open(sys.argv[1], encoding="utf-8")))
deployment = next(value for value in documents if value and value.get("kind") == "Deployment" and value["metadata"]["name"].endswith("-prover-agent"))
container = deployment["spec"]["template"]["spec"]["containers"][0]
names = {row["name"] for row in container["env"]}
required = {"QUEUE_URL", "ZKDEAL_QUEUE_NODE_TOKEN", "NODE_ID", "PROVER_URL", "ZKDEAL_PROVER_TOKEN", "ROOM_POOL", "NODE_LIVENESS_COORDINATOR_URL", "NODE_LIVENESS_COORDINATOR_AUTH_TOKEN", "NODE_LIVENESS_ACCOUNT", "L1_CHAIN_ID"}
forbidden = {"NODE_SERVICE_KEY", "NODE_LIVENESS_SIGNER_URL", "NODE_LIVENESS_SIGNER_AUTH_TOKEN", "NODE_LIVENESS_DEV_MODE", "NODE_LIVENESS_DEV_PRIVATE_KEY", "L1_RPC_URL"}
assert required <= names, sorted(required - names)
assert not (forbidden & names), sorted(forbidden & names)
assert container["command"] == ["node", "/app/agent/agent.js"]
assert container["workingDir"] == "/app"
PY

negative_count=0
assert_rejected() {
  expected="$1"
  shift
  if render "$@"; then
    printf 'ERROR: unsafe prover-agent signer configuration rendered: %s\n' "$*" >&2
    exit 1
  fi
  if ! grep -F "$expected" "$output" >/dev/null; then
    printf 'ERROR: render failed for the wrong reason; expected %s\n' "$expected" >&2
    sed -n '1,80p' "$output" >&2
    exit 1
  fi
  negative_count=$((negative_count + 1))
}

assert_rejected 'must not receive any direct signer role' \
  --set-string components.proverAgent.signerRole=operationsSettlement
assert_rejected 'forbids direct sender environment NODE_SERVICE_KEY' \
  --set-string components.proverAgent.secretEnv.NODE_SERVICE_KEY=node-service-key
assert_rejected 'requires scoped secret NODE_LIVENESS_COORDINATOR_AUTH_TOKEN' \
  --set-string components.proverAgent.secretEnv.NODE_LIVENESS_COORDINATOR_AUTH_TOKEN=
assert_rejected 'liveness URL must be the hosted coordinator' \
  --set-string components.proverAgent.env.NODE_LIVENESS_COORDINATOR_URL=https://signer.invalid
assert_rejected 'must not receive direct L1 RPC configuration' \
  --set components.proverAgent.injectChain=true
assert_rejected 'forbids the standalone filesystem proof queue' \
  --set components.queue.enabled=true
assert_rejected 'requires the server-bound liveness account' \
  --set-string components.proverAgent.env.NODE_LIVENESS_ACCOUNT=
assert_rejected 'requires its independently packaged owner-source image' \
  --set-string components.proverAgent.image.repository=zkdeal-coordinator

printf '{"helmProverLivenessPolicy":"passed","positiveRender":true,"negativeConfigurationsRejected":%s}\n' "$negative_count"
