#!/bin/sh
set -eu

work="$(mktemp -d)"
fixture_pid=""
cleanup() {
  [ -z "$fixture_pid" ] || kill "$fixture_pid" >/dev/null 2>&1 || true
  rm -rf -- "$work"
}
trap cleanup EXIT INT TERM

python tests/fixtures/promotion_controller_fixture.py \
  --endpoints-file "$work/endpoints.json" >"$work/fixture.log" 2>&1 &
fixture_pid=$!
attempt=0
while [ ! -s "$work/endpoints.json" ]; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 50 ] || { echo "promotion fixture did not start" >&2; exit 1; }
  sleep 0.1
done

endpoint() {
  python -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' \
    "$work/endpoints.json" "$1"
}
control=$(endpoint control)
export ACTIVE_HEALTH_URLS="$(endpoint witness-a)/health,$(endpoint witness-b)/health"
export STANDBY_HEALTH_URL="$(endpoint standby)/hosting/v1/health"
export FAILOVER_PROVIDER_URL="$(endpoint provider)/v1/failovers"
export PROMOTION_ENDPOINT="$(endpoint standby)/hosting/v1/admin/promote"
export ACTIVE_COORDINATOR_ID=primary-region-a
export STANDBY_COORDINATOR_ID=standby-region-b
export FAILOVER_PROVIDER_TOKEN=provider-token-acceptance
export FAILOVER_APPROVAL_TOKEN=approval-token-acceptance
export PROMOTION_PRINCIPAL_TOKEN=principal-token-acceptance
export PROMOTION_CONTROLLER_ARMED=true
export PROMOTION_ALLOW_INSECURE_HTTP=acceptance-only
export FAILURE_THRESHOLD=3
export POLL_SECONDS=1
export MAX_MONITOR_CYCLES=3
export MAX_RTO_SECONDS=30
export REQUEST_TIMEOUT_SECONDS=2

reset() {
  wget -qO- --header='Content-Type: application/json' --post-data="$1" \
    "$control/control/reset" >/dev/null
}
stats() { wget -qO- "$control/stats"; }

# One independent healthy witness vetoes automatic failover.
reset '{"witnessAHealthy":true,"witnessBHealthy":false}'
export PROMOTION_CANDIDATE_ID=candidate-healthy-veto-001
export PROMOTION_CONTROLLER_STATE_PATH="$work/veto.json"
set +e
python scripts/promotion_controller.py >"$work/veto.out" 2>"$work/veto.err"
rc=$?
set -e
[ "$rc" -eq 3 ] || { echo "healthy witness did not veto promotion" >&2; exit 1; }
stats | grep -Fq '"providerCalls":0'

# A provider that cannot prove the former writer fence is rejected before the
# owner endpoint and before signer/routing commit.
reset '{"witnessAHealthy":false,"witnessBHealthy":false,"unsafeProvider":true}'
export PROMOTION_CANDIDATE_ID=candidate-unsafe-fence-001
export PROMOTION_CONTROLLER_STATE_PATH="$work/unsafe.json"
if python scripts/promotion_controller.py >"$work/unsafe.out" 2>"$work/unsafe.err"; then
  echo "unsafe failover provider was accepted" >&2
  exit 1
fi
grep -Fq 'did not prove activeFenced' "$work/unsafe.err"
unsafe_stats=$(stats)
printf '%s' "$unsafe_stats" | grep -Fq '"providerCalls":1'
printf '%s' "$unsafe_stats" | grep -Fq '"ownerCalls":0'
printf '%s' "$unsafe_stats" | grep -Fq '"commitCalls":0'

# Wrong scoped provider credentials fail before owner promotion.
reset '{"witnessAHealthy":false,"witnessBHealthy":false}'
export PROMOTION_CANDIDATE_ID=candidate-wrong-token-001
export PROMOTION_CONTROLLER_STATE_PATH="$work/wrong-token.json"
FAILOVER_PROVIDER_TOKEN=wrong-provider-token-0001 \
  python scripts/promotion_controller.py >"$work/wrong-token.out" 2>"$work/wrong-token.err" && {
    echo "wrong provider token was accepted" >&2
    exit 1
  }
wrong_stats=$(stats)
printf '%s' "$wrong_stats" | grep -Fq '"unauthorizedCalls":1'
printf '%s' "$wrong_stats" | grep -Fq '"ownerCalls":0'

# The safe path proves fence -> DB replay/promotion -> owner promotion -> stable
# route and signer activation in that order, within the bounded RTO.
reset '{"witnessAHealthy":false,"witnessBHealthy":false}'
export PROMOTION_CANDIDATE_ID=candidate-safe-promotion-001
export PROMOTION_CONTROLLER_STATE_PATH="$work/safe.json"
python scripts/promotion_controller.py >"$work/safe.out" 2>"$work/safe.err"
grep -Fq '"status":"promotion-complete"' "$work/safe.out"
safe_stats=$(stats)
printf '%s' "$safe_stats" | grep -Fq '"providerCalls":1'
printf '%s' "$safe_stats" | grep -Fq '"ownerCalls":1'
printf '%s' "$safe_stats" | grep -Fq '"commitCalls":1'
printf '%s' "$safe_stats" | grep -Fq '"events":["provider-prepare","owner-promote","provider-commit"]'

printf '%s\n' '{"promotionControllerFixture":"passed","classification":"fixture-live-non-production","healthyWitnessVeto":true,"unsafeFenceBlocked":true,"wrongProviderTokenBlocked":true,"safeOrder":["provider-prepare","owner-promote","provider-commit"],"rtoBoundSeconds":30,"realProviderEvidence":false}'
