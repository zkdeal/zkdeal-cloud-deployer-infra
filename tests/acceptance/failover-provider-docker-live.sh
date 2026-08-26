#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$root"
project=zkdeal-failover-provider-acceptance
base=compose/compose.postgres-ha.test.yaml
overlay=compose/compose.failover-provider.test.yaml
network=${project}_database
curl_image=curlimages/curl@sha256:4026b29997dc7c823b51c164b71e2b51e0fd95cce4601f78202c513d97da2922
provider=http://failover-provider-docker:8443
candidate_provider=${FAILOVER_PROVIDER_CANDIDATE_IMAGE:-}
candidate_mode=false

require_digest_image() {
  image_label=$1
  image_reference=$2
  PYTHONPATH="$root/scripts" IMAGE_LABEL="$image_label" IMAGE_REFERENCE="$image_reference" python -c \
    'import os; from production_compose import validate_reference; validate_reference(os.environ["IMAGE_REFERENCE"], os.environ["IMAGE_LABEL"])'
}

if [ -n "$candidate_provider" ]; then
  require_digest_image FAILOVER_PROVIDER_CANDIDATE_IMAGE "$candidate_provider"
  export FAILOVER_PROVIDER_IMAGE="$candidate_provider"
  docker pull "$candidate_provider" >/dev/null
  candidate_mode=true
fi

dc() {
  docker compose --project-name "$project" -f "$base" -f "$overlay" --profile provider --profile signer "$@"
}
cleanup() {
  dc down --volumes --remove-orphans >/dev/null 2>&1 || true
}
on_exit() {
  status=$?
  if [ "$status" -ne 0 ]; then
    dc ps -a >&2 || true
    dc logs --no-color failover-provider-docker >&2 || true
  fi
  cleanup
  exit "$status"
}
trap on_exit EXIT
trap 'exit 130' INT TERM
cleanup

dc up -d --wait pg-primary pg-standby coordinator-active coordinator-standby witness-a witness-b indexer-freshness
dc create signer-authority >/dev/null
primary=$(dc ps -q pg-primary)
standby=$(dc ps -q pg-standby)
active=$(dc ps -q coordinator-active)
signer=$(dc ps -q -a signer-authority)

docker exec "$primary" psql -U zkdeal -d zkdeal --set ON_ERROR_STOP=1 -c \
  "alter system set synchronous_standby_names='FIRST 1 (zkdeal_standby)'" >/dev/null
docker exec "$primary" psql -U zkdeal -d zkdeal --set ON_ERROR_STOP=1 -c "select pg_reload_conf()" >/dev/null
attempt=0
until [ "$(docker exec "$primary" psql -U zkdeal -d zkdeal -tAc "select count(*) from pg_stat_replication where application_name='zkdeal_standby' and state='streaming' and sync_state in ('sync','quorum')")" = 1 ]; do
  attempt=$((attempt + 1)); [ "$attempt" -lt 30 ] || { echo 'standby never became synchronous' >&2; exit 1; }
  sleep 1
done

if [ "$candidate_mode" = true ]; then
  dc up -d --wait --no-build failover-provider-docker
else
  dc up -d --wait --build failover-provider-docker
fi
provider_container=$(dc ps -q failover-provider-docker)
provider_id=$(docker inspect "$provider_container" --format '{{.Image}}')
configured_provider=$(docker inspect "$provider_container" --format '{{.Config.Image}}')
if [ "$candidate_mode" = true ]; then
  candidate_provider_id=$(docker image inspect "$candidate_provider" --format '{{.Id}}')
  [ "$configured_provider" = "$candidate_provider" ] || {
    echo "provider container did not retain the exact candidate reference" >&2
    exit 1
  }
  [ "$provider_id" = "$candidate_provider_id" ] || {
    echo "provider container ID differs from the pulled candidate digest" >&2
    exit 1
  }
fi
call() {
  docker run --rm --network "$network" "$curl_image" --silent --show-error "$@"
}
prepare='{"candidateId":"candidate-20260821-001","activeCoordinatorId":"active-region-a","standbyCoordinatorId":"standby-region-b","failedWitnessCount":2}'

status=$(call -o /tmp/response -w '%{http_code}' -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer wrong-provider-token' -H 'X-Zkdeal-Failover-Approval: approval-acceptance-token-0001' \
  -H 'Idempotency-Key: candidate-prepare-key-0001' --data "$prepare" "$provider/v1/failovers")
[ "$status" = 401 ] || { echo "wrong provider token was not denied: $status" >&2; exit 1; }

status=$(call -o /tmp/response -w '%{http_code}' -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer provider-acceptance-token-0001' -H 'X-Zkdeal-Failover-Approval: wrong-approval-token' \
  -H 'Idempotency-Key: candidate-prepare-key-0001' --data "$prepare" "$provider/v1/failovers")
[ "$status" = 403 ] || { echo "wrong approval token was not denied: $status" >&2; exit 1; }

# One healthy independent witness vetoes before the adapter can stop anything.
status=$(call -o /tmp/response -w '%{http_code}' -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer provider-acceptance-token-0001' -H 'X-Zkdeal-Failover-Approval: approval-acceptance-token-0001' \
  -H 'Idempotency-Key: candidate-prepare-key-0001' --data "$prepare" "$provider/v1/failovers")
[ "$status" = 409 ] || { echo "healthy witness did not veto: $status" >&2; exit 1; }
[ "$(docker inspect --format '{{.State.Running}}' "$active")" = true ] || { echo 'healthy-witness veto happened after the writer fence' >&2; exit 1; }

call --fail -H 'X-Acceptance-Control: set-unhealthy' --data '{}' http://witness-a:8080/test/unhealthy >/dev/null
prepare_out=$(call -w '\n%{http_code}' -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer provider-acceptance-token-0001' -H 'X-Zkdeal-Failover-Approval: approval-acceptance-token-0001' \
  -H 'Idempotency-Key: candidate-prepare-key-0001' --data "$prepare" "$provider/v1/failovers")
prepare_status=$(printf '%s' "$prepare_out" | tail -n1)
response=$(printf '%s' "$prepare_out" | sed '$d')
if [ "$prepare_status" != 200 ]; then
  echo "prepare failed with HTTP $prepare_status: $response" >&2
  exit 1
fi
printf '%s' "$response" | grep -F '"status":"READY_FOR_APPLICATION_PROMOTION"' >/dev/null
printf '%s' "$response" | grep -F '"standbySignerAuthorityActive":false' >/dev/null
[ "$(docker inspect --format '{{.State.Running}}' "$active")" = false ] || { echo 'old app writer was not stopped' >&2; exit 1; }
[ "$(docker inspect --format '{{.State.Running}}' "$primary")" = false ] || { echo 'old primary database was not stopped' >&2; exit 1; }
[ "$(docker inspect --format '{{.State.Running}}' "$signer")" = false ] || { echo 'signer activated before owner promotion' >&2; exit 1; }
[ "$(docker exec "$standby" psql -U zkdeal -d zkdeal -tAc 'select pg_is_in_recovery()')" = f ] || { echo 'standby database was not promoted' >&2; exit 1; }

replay=$(call --fail -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer provider-acceptance-token-0001' -H 'X-Zkdeal-Failover-Approval: approval-acceptance-token-0001' \
  -H 'Idempotency-Key: candidate-prepare-key-0001' --data "$prepare" "$provider/v1/failovers")
[ "$replay" = "$response" ] || { echo 'exact prepare replay changed response bytes' >&2; exit 1; }

commit='{"ownerResponseSha256":"1111111111111111111111111111111111111111111111111111111111111111"}'
committed=$(call --fail -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer provider-acceptance-token-0001' -H 'X-Zkdeal-Failover-Approval: approval-acceptance-token-0001' \
  -H 'Idempotency-Key: candidate-commit-key-0001' --data "$commit" "$provider/v1/failovers/candidate-20260821-001/commit")
printf '%s' "$committed" | grep -F '"signerAuthorityActivatedAfterFence":true' >/dev/null
[ "$(docker inspect --format '{{.State.Running}}' "$signer")" = true ] || { echo 'signer was not activated after commit' >&2; exit 1; }
identity=$(call --fail http://coordinator-writer:8080/identity)
printf '%s' "$identity" | grep -F '"coordinatorId":"standby-region-b"' >/dev/null || { echo 'stable app route did not switch to standby' >&2; exit 1; }

# Durable state must survive provider restart and return the exact responses.
dc restart failover-provider-docker >/dev/null
attempt=0
until call --fail "$provider/ready" >/dev/null 2>&1; do
  attempt=$((attempt + 1)); [ "$attempt" -lt 30 ] || { echo 'provider did not recover durable state' >&2; exit 1; }
  sleep 1
done
replayed_commit=$(call --fail -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer provider-acceptance-token-0001' -H 'X-Zkdeal-Failover-Approval: approval-acceptance-token-0001' \
  -H 'Idempotency-Key: candidate-commit-key-0001' --data "$commit" "$provider/v1/failovers/candidate-20260821-001/commit")
[ "$replayed_commit" = "$committed" ] || { echo 'commit replay changed after provider restart' >&2; exit 1; }

printf '{"dockerFailoverProvider":"passed","realPostgresqlReplication":true,"healthyWitnessVetoBeforeFence":true,"oldWriterStopped":true,"oldPrimaryStopped":true,"replayAtOrAfterTarget":true,"stableDbRoute":true,"signerBeforeFence":false,"stableAppRouteCommitted":true,"durableReplayAfterRestart":true,"candidateDigestMode":%s,"providerReference":"%s","providerImageId":"%s","classification":"%s"}\n' \
  "$candidate_mode" "$configured_provider" "$provider_id" "$(if [ "$candidate_mode" = true ]; then printf candidate-digest-docker-live; else printf local-docker-live; fi)"
