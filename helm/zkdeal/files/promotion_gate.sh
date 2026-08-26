#!/bin/sh
# Fail-closed application promotion wrapper. The database must already have
# been physically promoted after the old writer was fenced. The required LSN
# is captured from the fenced primary and proves that the new primary replayed
# every acknowledged WAL record before this wrapper calls the owner endpoint.
set -eu

required() {
  name=$1
  eval "value=\${$name:-}"
  [ -n "$value" ] || { echo "missing required $name" >&2; exit 2; }
}

required PROMOTION_ENDPOINT
required PROMOTION_DATABASE_URL
required PROMOTION_REQUIRED_REPLAY_LSN
required PROMOTION_IDEMPOTENCY_KEY
required PROMOTION_PRINCIPAL_TOKEN
required PROMOTION_STANDBY_COORDINATOR_ID

case "$PROMOTION_REQUIRED_REPLAY_LSN" in
  *[!0-9A-Fa-f/]*|*/*/*|/*|*/) echo "invalid required replay LSN" >&2; exit 2 ;;
esac
case "$PROMOTION_IDEMPOTENCY_KEY" in
  *[!A-Za-z0-9._:-]*) echo "invalid promotion idempotency key" >&2; exit 2 ;;
esac
key_length=${#PROMOTION_IDEMPOTENCY_KEY}
[ "$key_length" -ge 8 ] && [ "$key_length" -le 200 ] || {
  echo "promotion idempotency key must contain 8 through 200 safe characters" >&2
  exit 2
}
case "$PROMOTION_STANDBY_COORDINATOR_ID" in
  *[!a-z0-9._-]*|'') echo "invalid standby coordinator identity" >&2; exit 2 ;;
esac

export PGCONNECT_TIMEOUT=${PGCONNECT_TIMEOUT:-10}
database_gate=$(psql "$PROMOTION_DATABASE_URL" --no-psqlrc --set ON_ERROR_STOP=1 \
  --set required_lsn="$PROMOTION_REQUIRED_REPLAY_LSN" --tuples-only --no-align <<'SQL'
SELECT concat_ws('|',
  CASE WHEN pg_is_in_recovery() THEN 'still-in-recovery' ELSE 'promoted-primary' END,
  COALESCE(pg_last_wal_replay_lsn()::text, 'missing-replay-lsn'),
  CASE
    WHEN pg_last_wal_replay_lsn() IS NOT NULL
      AND pg_wal_lsn_diff(pg_last_wal_replay_lsn(), :'required_lsn'::pg_lsn) >= 0
    THEN 'caught-up'
    ELSE 'stale'
  END
);
SQL
)
[ "$database_gate" != "" ] || { echo "empty database replay response" >&2; exit 1; }
case "$database_gate" in
  promoted-primary*'|'caught-up) : ;;
  *) echo "database replay gate rejected promotion: $database_gate" >&2; exit 1 ;;
esac

endpoint=$PROMOTION_ENDPOINT
base=${endpoint%/hosting/v1/admin/promote}
health=$(wget -qO- --timeout=10 "$base/hosting/v1/health")
printf '%s' "$health" | grep -Fq '"configuredRole":"standby"'
printf '%s' "$health" | grep -Fq '"effectiveRole":"standby"'

capabilities=$(wget -qO- --timeout=10 "$base/hosting/v1/capabilities")
printf '%s' "$capabilities" | grep -Fq '"fencing":"monotonic-transactional"'
printf '%s' "$capabilities" | grep -Fq '"freshnessGateBlocks":8'
printf '%s' "$capabilities" | grep -Fq '"primaryTarget":"durable-fenced-wal-checkpoint"'
printf '%s' "$capabilities" | grep -Fq '"standbyReplay":"pg_last_wal_replay_lsn"'
printf '%s' "$capabilities" | grep -Fq '"atomicWithFenceTransfer":true'

# Claim the supplied operation key before making the one-way request. The
# owner's durable idempotency journal gives concurrent/replayed Jobs one
# winner; a failed in-flight attempt remains a manual-recovery event rather
# than silently repeating a promotion.
request_hash=$(printf '%s\0%s\0%s' \
  "$PROMOTION_STANDBY_COORDINATOR_ID" "$PROMOTION_REQUIRED_REPLAY_LSN" "$endpoint" \
  | sha256sum | awk '{print $1}')
claimed=$(psql "$PROMOTION_DATABASE_URL" --no-psqlrc --set ON_ERROR_STOP=1 \
  --set promotion_key="$PROMOTION_IDEMPOTENCY_KEY" \
  --set request_hash="$request_hash" \
  --set promotion_operation="$PROMOTION_STANDBY_COORDINATOR_ID:$PROMOTION_REQUIRED_REPLAY_LSN" \
  --tuples-only --no-align <<'SQL'
WITH claim AS (
  INSERT INTO hosted_idempotency_records(
    scope, operation, idempotency_key, request_hash,
    response_status, response_body, expires_at
  ) VALUES (
    'deployment-promotion', :'promotion_operation', :'promotion_key',
    :'request_hash', NULL, NULL, clock_timestamp() + interval '365 days'
  ) ON CONFLICT DO NOTHING RETURNING 1
)
SELECT count(*) FROM claim;
SQL
)
[ "$claimed" = "1" ] || {
  echo "duplicate or replayed promotion operation rejected before owner call" >&2
  exit 1
}

mark_failed() {
  psql "$PROMOTION_DATABASE_URL" --no-psqlrc --set ON_ERROR_STOP=1 \
    --set promotion_key="$PROMOTION_IDEMPOTENCY_KEY" \
    --set promotion_operation="$PROMOTION_STANDBY_COORDINATOR_ID:$PROMOTION_REQUIRED_REPLAY_LSN" \
    >/dev/null <<'SQL' || true
UPDATE hosted_idempotency_records
SET response_status = 599,
    response_body = '{"status":"RECOVERY_REQUIRED","reason":"promotion-owner-call-failed"}'::jsonb
WHERE scope='deployment-promotion' AND operation=:'promotion_operation'
  AND idempotency_key=:'promotion_key' AND response_status IS NULL;
SQL
}
trap mark_failed EXIT INT TERM

result=$(wget -qO- --timeout=30 \
  --header="Authorization: Bearer $PROMOTION_PRINCIPAL_TOKEN" \
  --header="Idempotency-Key: $PROMOTION_IDEMPOTENCY_KEY" \
  --header='Content-Type: application/json' \
  --post-data='{}' "$endpoint")
printf '%s' "$result" | grep -Fq '"promoted":true'
printf '%s' "$result" | grep -Fq '"effectiveRole":"active"'
printf '%s' "$result" | grep -Fq '"indexerHeadMatchesL1":true'
printf '%s' "$result" | grep -Fq '"promotionReplication":{'

psql "$PROMOTION_DATABASE_URL" --no-psqlrc --set ON_ERROR_STOP=1 \
  --set promotion_key="$PROMOTION_IDEMPOTENCY_KEY" \
  --set promotion_operation="$PROMOTION_STANDBY_COORDINATOR_ID:$PROMOTION_REQUIRED_REPLAY_LSN" \
  >/dev/null <<'SQL'
UPDATE hosted_idempotency_records
SET response_status = 200,
    response_body = '{"status":"PROMOTED","ownerResponseVerified":true}'::jsonb
WHERE scope='deployment-promotion' AND operation=:'promotion_operation'
  AND idempotency_key=:'promotion_key' AND response_status IS NULL;
SQL
trap - EXIT INT TERM
printf '%s\n' "$result"
