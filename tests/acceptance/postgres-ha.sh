#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."
project=zkdeal-postgres-ha-acceptance
file=compose/compose.postgres-ha.test.yaml
network=${project}_database

cleanup() {
  docker compose --project-name "$project" -f "$file" --profile reseed --profile promotion down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
cleanup
if ! docker compose --project-name "$project" -f "$file" up -d --wait pg-primary pg-standby; then
  docker compose --project-name "$project" -f "$file" logs --no-color >&2 || true
  exit 1
fi

primary=$(docker compose --project-name "$project" -f "$file" ps -q pg-primary)
standby=$(docker compose --project-name "$project" -f "$file" ps -q pg-standby)
docker exec "$primary" psql -U zkdeal -d zkdeal --set ON_ERROR_STOP=1 -c \
  "alter system set synchronous_standby_names='FIRST 1 (zkdeal_standby)'" >/dev/null
docker exec "$primary" psql -U zkdeal -d zkdeal --set ON_ERROR_STOP=1 -c "select pg_reload_conf()" >/dev/null
attempt=0
until [ "$(docker exec "$primary" psql -U zkdeal -d zkdeal -tAc "select count(*) from pg_stat_replication where application_name='zkdeal_standby' and state='streaming' and sync_state in ('sync','quorum')")" = 1 ]; do
  attempt=$((attempt + 1)); [ "$attempt" -lt 30 ] || { echo "standby did not enter synchronous streaming" >&2; exit 1; }
  sleep 1
done
streaming=$(docker exec "$primary" psql -U zkdeal -d zkdeal -tAc "select count(*) from pg_stat_replication where application_name='zkdeal_standby' and state='streaming' and sync_state in ('sync','quorum')")
[ "$streaming" = 1 ] || { echo "standby was not synchronously streaming" >&2; exit 1; }
replicated=$(docker exec "$standby" psql -U zkdeal -d zkdeal -tAc "select count(*) from fenced_writes where payload='replicate-before-failover'")
[ "$replicated" = 1 ] || { echo "pre-failover committed row was missing on standby" >&2; exit 1; }
required_lsn=$(docker exec "$primary" psql -U zkdeal -d zkdeal -tAc "select pg_current_wal_flush_lsn()")
[ -n "$required_lsn" ] || { echo "failed to capture the fenced primary flush LSN" >&2; exit 1; }

started=$(date +%s)
docker kill "$primary" >/dev/null
docker network disconnect "$network" "$primary" >/dev/null 2>&1 || true
docker exec --user postgres "$standby" pg_ctl -D /var/lib/postgresql/data promote -w >/dev/null
docker network disconnect "$network" "$standby" >/dev/null
docker network connect --alias postgres-writer "$network" "$standby"
until [ "$(docker exec "$standby" psql -U zkdeal -d zkdeal -tAc 'select pg_is_in_recovery()')" = f ]; do sleep 1; done

docker compose --project-name "$project" -f "$file" --profile promotion up -d --wait promotion-owner
curl_image=curlimages/curl@sha256:4026b29997dc7c823b51c164b71e2b51e0fd95cce4601f78202c513d97da2922
if docker run --rm --network "$network" "$curl_image" --fail --silent --show-error \
  --header 'Authorization: Bearer promotion-acceptance-admin-token' --data '{}' \
  http://promotion-owner:3000/hosting/v1/admin/promote >/dev/null 2>&1; then
  echo "promotion endpoint accepted a missing Idempotency-Key" >&2
  exit 1
fi
if PROMOTION_REQUIRED_REPLAY_LSN=FFFFFFFF/FFFFFFFF \
  docker compose --project-name "$project" -f "$file" --profile promotion run --rm promotion-gate >/dev/null 2>&1; then
  echo "promotion gate accepted an unreplayed required LSN" >&2
  exit 1
fi
if PROMOTION_REQUIRED_REPLAY_LSN="$required_lsn" \
  docker compose --project-name "$project" -f "$file" --profile promotion run --rm \
  -e PROMOTION_IDEMPOTENCY_KEY= promotion-gate >/dev/null 2>&1; then
  echo "promotion gate accepted a missing operation key" >&2
  exit 1
fi
PROMOTION_REQUIRED_REPLAY_LSN="$required_lsn" \
  docker compose --project-name "$project" -f "$file" --profile promotion run --rm promotion-gate >/dev/null
docker run --rm --network "$network" "$curl_image" --fail --silent --show-error \
  --header 'X-Acceptance-Control: reset' --data '{}' \
  http://promotion-owner:3000/test/reset-standby >/dev/null
if PROMOTION_REQUIRED_REPLAY_LSN="$required_lsn" \
  docker compose --project-name "$project" -f "$file" --profile promotion run --rm promotion-gate >/dev/null 2>&1; then
  echo "promotion gate accepted a duplicate/replayed operation key" >&2
  exit 1
fi
promotion_stats=$(docker run --rm --network "$network" "$curl_image" --fail --silent --show-error \
  http://promotion-owner:3000/test/stats)
printf '%s' "$promotion_stats" | grep -Fq '"postCount":1' || {
  echo "promotion wrapper did not make exactly one owner call" >&2
  exit 1
}
promotion_records=$(docker exec "$standby" psql -U zkdeal -d zkdeal -tAc \
  "select count(*) from hosted_idempotency_records where scope='deployment-promotion' and response_status=200")
[ "$promotion_records" = 1 ] || { echo "promotion operation journal was not closed exactly once" >&2; exit 1; }

docker exec "$standby" psql -U zkdeal -d zkdeal --set ON_ERROR_STOP=1 -c \
  "update app_writer_fence set epoch=2, holder='promoted-standby' where lease_name='coordinator-writer'" >/dev/null
if docker exec "$standby" psql -U zkdeal -d zkdeal --set ON_ERROR_STOP=1 -c \
  "insert into fenced_writes(writer_epoch,payload) values(1,'stale-writer-must-fail')" >/dev/null 2>&1; then
  echo "promoted database accepted a stale application writer epoch" >&2
  exit 1
fi
docker exec "$standby" psql -U zkdeal -d zkdeal --set ON_ERROR_STOP=1 -c \
  "insert into fenced_writes(writer_epoch,payload) values(2,'post-promotion-write')" >/dev/null
rto=$(( $(date +%s) - started ))
[ "$rto" -lt 300 ] || { echo "database promotion exceeded the five-minute RTO: ${rto}s" >&2; exit 1; }

docker compose --project-name "$project" -f "$file" --profile reseed up -d --wait pg-reseeded-replica
reseeded=$(docker compose --project-name "$project" -f "$file" --profile reseed ps -q pg-reseeded-replica)
streaming=$(docker exec "$standby" psql -U zkdeal -d zkdeal -tAc "select count(*) from pg_stat_replication where application_name='zkdeal_reseeded' and state='streaming'")
[ "$streaming" = 1 ] || { echo "fresh replica did not stream from promoted primary" >&2; exit 1; }
restored=$(docker exec "$reseeded" psql -U zkdeal -d zkdeal -tAc "select count(*) from fenced_writes where payload='post-promotion-write'")
[ "$restored" = 1 ] || { echo "fresh replica missed post-promotion data" >&2; exit 1; }

printf '{"databaseHa":"passed","streaming":"synchronous-before-failure","forcedPrimaryLoss":true,"requiredReplayLsn":"%s","staleReplayBlocked":true,"missingIdempotencyKeyBlocked":true,"duplicateReplayBlocked":true,"ownerPromotionCalls":1,"promotionRtoSeconds":%s,"staleWriterEpochRejected":true,"oldPrimaryRejoin":"forbidden-reseed-required","freshReplica":"streaming"}\n' "$required_lsn" "$rto"
