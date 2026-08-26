#!/bin/sh
set -eu

chart=helm/zkdeal
fixture=tests/fixtures/helm-production-render.yaml
automatic="$(mktemp)"
manual="$(mktemp)"
trap 'rm -f "$automatic" "$manual"' EXIT INT TERM

# The structural fixture explicitly opts into the manual recovery artifact so
# its replay/idempotency semantics remain render-tested.
helm template zkdeal "$chart" -f "$fixture" >"$manual"
for suffix in promote-standby promotion-gate promotion-egress promotion-to-coordinator standby-from-promotion; do
  grep -E "^[[:space:]]*name: .*-${suffix}$" "$manual" >/dev/null || {
    printf 'ERROR: explicitly enabled manual recovery artifact is missing: %s\n' "$suffix" >&2
    exit 1
  }
done

# A routine automated release must be safe even if stale manual-only values
# remain in the values file: no Job, gate ConfigMap, or Job network policy may
# be installed. The automated controller and its network boundary must remain.
helm template zkdeal "$chart" -f "$fixture" \
  --set operations.promotion.manualJobEnabled=false \
  --set-string operations.promotion.requiredReplayLsn=REPLACE \
  --set-json 'networkPolicy.operationalFlows.promotion.cidrs=[]' \
  >"$automatic"

if grep -E "^[[:space:]]*name: .*-(promote-standby|promotion-gate|promotion-egress|promotion-to-coordinator|standby-from-promotion)$" "$automatic" >/dev/null; then
  echo 'ERROR: routine chart render contains a one-shot manual promotion artifact' >&2
  exit 1
fi

for suffix in promotion-controller promotion-controller-egress promotion-controller-to-standby standby-from-promotion-controller; do
  grep -E "^[[:space:]]*name: .*-${suffix}$" "$automatic" >/dev/null || {
    printf 'ERROR: automated promotion boundary disappeared: %s\n' "$suffix" >&2
    exit 1
  }
done

printf '{"helmManualPromotionPolicy":"passed","routineInstallStartsManualJob":false,"explicitManualRenderContainsJob":true,"automatedControllerPreserved":true}\n'
