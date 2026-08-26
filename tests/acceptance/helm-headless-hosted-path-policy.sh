#!/bin/sh
set -eu

chart="helm/zkdeal"
fixture="tests/fixtures/helm-production-render.yaml"
output="$(mktemp)"
trap 'rm -f "$output"' EXIT INT TERM

render() {
  helm template zkdeal "$chart" -f "$fixture" "$@" >"$output" 2>&1
}

# This is a structural future-state render only. It is not a substitute for
# the owner capability verifier or the live end-to-end acceptance record.
render

if helm template zkdeal "$chart" \
  -f "$chart/values-production.example.yaml" >"$output" 2>&1
then
  echo "ERROR: current production example accepted an unpublished headless hosted path" >&2
  exit 1
fi
grep -F "requires an owner-published admission lease" "$output" >/dev/null

negative_count=0
assert_rejected() {
  expected="$1"
  shift
  if render "$@"; then
    printf 'ERROR: incomplete headless hosted path rendered successfully: %s\n' "$*" >&2
    exit 1
  fi
  if ! grep -F "$expected" "$output" >/dev/null; then
    printf 'ERROR: render failed for the wrong reason; expected %s\n' "$expected" >&2
    sed -n '1,80p' "$output" >&2
    exit 1
  fi
  negative_count=$((negative_count + 1))
}

assert_rejected "requires an owner-published admission lease" \
  --set ownerCapabilities.hostedAdmissionLease=false
assert_rejected "requires the owner managed room-batch publication path" \
  --set ownerCapabilities.hostedRoomBatchEnabled=false
assert_rejected "requires the live app engine to current zkVM BatchInputV5 witness bridge" \
  --set ownerCapabilities.hostedEngineToBatchInputV5=false
assert_rejected "requires durable hosted PostgreSQL queue submission" \
  --set ownerCapabilities.hostedDurablePostgresQueue=false
assert_rejected "requires the external authenticated prover path" \
  --set ownerCapabilities.hostedExternalProver=false
assert_rejected "requires live restart/resume acceptance across the hosted proof path" \
  --set ownerCapabilities.hostedRestartResume=false
assert_rejected "forbids fixture-prepared hosted proof witnesses" \
  --set ownerCapabilities.hostedFixturePrepare=true
assert_rejected "forbids the legacy Groth16 checkpoint path" \
  --set ownerCapabilities.hostedLegacyGroth16=true
assert_rejected "Does not match pattern" \
  --set-string ownerCapabilities.hostedIntegrationAcceptanceToken=unpublished

printf '{"helmHeadlessHostedPathPolicy":"passed","structuralFutureRender":true,"ownerEvidence":false,"currentProductionExampleBlocked":true,"negativeCapabilitiesRejected":%s}\n' "$negative_count"
