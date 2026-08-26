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

negative_count=0
assert_rejected() {
  expected="$1"
  shift
  if render "$@"; then
    printf 'ERROR: unsafe owner filesystem capability rendered successfully: %s\n' "$*" >&2
    exit 1
  fi
  if ! grep -F "$expected" "$output" >/dev/null; then
    printf 'ERROR: render failed for the wrong reason; expected %s\n' "$expected" >&2
    sed -n '1,80p' "$output" >&2
    exit 1
  fi
  negative_count=$((negative_count + 1))
}

assert_rejected "forbids filesystem application-state authority" \
  --set ownerCapabilities.filesystemApplicationStateAuthority=true
assert_rejected "must not require DATA_DIR state" \
  --set ownerCapabilities.dataDirRequired=true
assert_rejected "requires owner read-only-root support" \
  --set ownerCapabilities.readOnlyRootSupported=false
assert_rejected "legacy observer mutations to be retired with HTTP 410" \
  --set-string ownerCapabilities.legacyObserverRoutes=active-file-store
assert_rejected "PostgreSQL canonical projection" \
  --set-string ownerCapabilities.observationAuthority=replica-local-json

printf '{"helmFilesystemCapabilityPolicy":"passed","positiveRender":true,"negativeCapabilitiesRejected":%s}\n' "$negative_count"
