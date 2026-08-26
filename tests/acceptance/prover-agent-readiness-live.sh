#!/bin/sh
set -eu

test -f /.dockerenv || { echo 'ERROR: Docker-only acceptance' >&2; exit 2; }

prefix="zkdeal-probe-$$"
network="$prefix-net"
image="$prefix-boundary"
node_image='node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436'

cleanup() {
  docker rm -f "$prefix-coordinator" "$prefix-coordinator-disabled" "$prefix-prover" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  docker image rm "$image" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker build --pull=false -f tests/fixtures/ProbeBoundary.Dockerfile -t "$image" . >/dev/null
docker network create --internal "$network" >/dev/null
docker run -d --rm --name "$prefix-coordinator" --network "$network" --network-alias coordinator \
  "$image" --mode coordinator --port 8001 --token acceptance-token --heartbeat-enabled true >/dev/null
docker run -d --rm --name "$prefix-coordinator-disabled" --network "$network" --network-alias coordinator-disabled \
  "$image" --mode coordinator --port 8004 --token acceptance-token --heartbeat-enabled false >/dev/null
docker run -d --rm --name "$prefix-prover" --network "$network" --network-alias prover \
  "$image" --mode health --port 8002 --path /healthz >/dev/null

probe="$(helm template zkdeal helm/zkdeal -f tests/fixtures/helm-production-render.yaml | python scripts/extract_prover_agent_probe.py)"

run_probe() {
  coordinator="$1"
  token="${2:-acceptance-token}"
  docker run --rm --network "$network" \
    -e QUEUE_URL="http://$coordinator" \
    -e PROVER_URL=http://prover:8002 \
    -e NODE_LIVENESS_COORDINATOR_URL="http://$coordinator" \
    -e NODE_LIVENESS_COORDINATOR_AUTH_TOKEN="$token" \
    "$node_image" node -e "$probe"
}

attempt=0
until run_probe coordinator:8001; do
  attempt=$((attempt + 1))
  test "$attempt" -lt 20 || { echo 'ERROR: positive readiness never passed' >&2; exit 1; }
  sleep 1
done

if run_probe coordinator-disabled:8004; then
  echo 'ERROR: readiness accepted a disabled heartbeat capability' >&2
  exit 1
fi

if run_probe coordinator:8001 wrong-token; then
  echo 'ERROR: readiness accepted an invalid coordinator credential' >&2
  exit 1
fi

docker stop "$prefix-prover" >/dev/null
if run_probe coordinator:8001; then
  echo 'ERROR: readiness accepted an unavailable prover' >&2
  exit 1
fi

printf '{"proverAgentReadiness":"passed","positive":true,"disabledHeartbeatCapabilityRejected":true,"invalidCoordinatorCredentialRejected":true,"missingProverRejected":true,"directSignerUsed":false,"nodeImage":"%s"}\n' "$node_image"
