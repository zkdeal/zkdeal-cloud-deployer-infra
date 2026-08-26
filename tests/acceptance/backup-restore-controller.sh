#!/bin/sh
set -eu

# Docker-requiring acceptance flow for the backup/restore control adapter.
# Runs on the remote node where Docker is allowed.  It proves the four release
# guarantees the runner depends on:
#   * INDEPENDENT backup store  - artifacts land in a separately credentialed
#     MinIO, never in the primary data MinIO;
#   * AUTHENTICATED encryption  - every artifact is AES-256-GCM ciphertext;
#   * TAMPER gate               - a flipped byte makes restore refuse distinctly
#     and leaves the fresh targets untouched;
#   * round-trip                - a clean restore reproduces the source state.

root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$root"
project=zkdeal-backup-restore-controller-acceptance
network=${project}_net
postgres_image=${POSTGRES_IMAGE:-postgres@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94}
minio_image=${MINIO_IMAGE:-minio/minio:RELEASE.2025-04-22T22-12-26Z}
mc_image=${MINIO_CLIENT_IMAGE:-minio/mc@sha256:aead63c77f9db9107f1696fb08ecb0faeda23729cde94b0f663edf4fe09728e3}
curl_image=${CURL_IMAGE:-curlimages/curl@sha256:4026b29997dc7c823b51c164b71e2b51e0fd95cce4601f78202c513d97da2922}
candidate=fixture-candidate-20260821
controller=zkdeal-backup-restore-control:acceptance
work=$(mktemp -d)

cleanup() {
  for name in controller postgres-source minio-source minio-backup postgres-fresh minio-fresh; do
    docker rm -f "${project}-${name}" >/dev/null 2>&1 || true
  done
  docker network rm "$network" >/dev/null 2>&1 || true
  rm -rf "$work"
}
on_exit() {
  status=$?
  [ "$status" -eq 0 ] || docker logs "${project}-controller" >&2 2>&1 || true
  cleanup
  exit "$status"
}
trap on_exit EXIT
trap 'exit 130' INT TERM
cleanup

sha256() { sha256sum "$1" | cut -d' ' -f1; }
# Leading -e/-v pairs are docker flags; the rest is the mc command line, so the
# image reference must sit between them (values never contain spaces here).
run_mc() {
  docker_flags=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -e|-v) docker_flags="$docker_flags $1 $2"; shift 2;;
      *) break;;
    esac
  done
  docker run --rm --network "$network" $docker_flags "$mc_image" "$@"
}

docker network create "$network" >/dev/null

# Build the controller image and bind its exact source hash.
source_sha=$(sha256 backup-restore-control/backup_restore_control.py)
docker build -f backup-restore-control/Dockerfile \
  --build-arg BACKUP_RESTORE_CONTROL_SOURCE_SHA256="$source_sha" -t "$controller" . >/dev/null

start_pg() {
  name=$1
  docker run -d --name "${project}-${name}" --network "$network" --network-alias "$name" \
    -e POSTGRES_USER=zkdeal -e POSTGRES_PASSWORD="$2" -e POSTGRES_DB=zkdeal \
    "$postgres_image" >/dev/null
}
start_minio() {
  name=$1
  docker run -d --name "${project}-${name}" --network "$network" --network-alias "$name" \
    -e MINIO_ROOT_USER="$2" -e MINIO_ROOT_PASSWORD="$3" \
    "$minio_image" server /data >/dev/null
}

source_pg_password=source-postgres-password-value
fresh_pg_password=fresh-postgres-password-value
source_minio_access=source-minio-access; source_minio_secret=source-minio-secret-value
backup_minio_access=backup-minio-access; backup_minio_secret=backup-minio-secret-value
fresh_minio_access=fresh-minio-access; fresh_minio_secret=fresh-minio-secret-value
encryption_key=8f4d2b7a39b31e8b42be8e8bb9651fd83d71efb7f26eef1cf633e2f66f93216f

start_pg postgres-source "$source_pg_password"
start_pg postgres-fresh "$fresh_pg_password"
start_minio minio-source "$source_minio_access" "$source_minio_secret"
start_minio minio-backup "$backup_minio_access" "$backup_minio_secret"
start_minio minio-fresh "$fresh_minio_access" "$fresh_minio_secret"

# Wait for dependencies to answer.
for target in postgres-source postgres-fresh; do
  attempt=0
  until docker exec "${project}-${target}" pg_isready -U zkdeal >/dev/null 2>&1; do
    attempt=$((attempt + 1)); [ "$attempt" -lt 60 ] || { echo "$target never became ready" >&2; exit 1; }
    sleep 1
  done
done
minio_ready() {
  alias_host=$1; access=$2; secret=$3
  attempt=0
  until run_mc -e "MC_HOST_probe=http://${access}:${secret}@${alias_host}:9000" ready probe >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
      echo "${alias_host} never became ready" >&2
      docker logs "${project}-${alias_host}" >&2 2>&1 || true
      run_mc -e "MC_HOST_probe=http://${access}:${secret}@${alias_host}:9000" ready probe >&2 2>&1 || true
      exit 1
    fi
    sleep 1
  done
}
minio_ready minio-source "$source_minio_access" "$source_minio_secret"
minio_ready minio-backup "$backup_minio_access" "$backup_minio_secret"
minio_ready minio-fresh "$fresh_minio_access" "$fresh_minio_secret"

# Seed the source database and object store with durable markers.
docker exec "${project}-postgres-source" psql -U zkdeal -d zkdeal -v ON_ERROR_STOP=1 \
  -c 'create table backup_marker(id int primary key, value text not null);' \
  -c "insert into backup_marker values (1, 'database-durable-marker');" >/dev/null
run_mc -e "MC_HOST_src=http://${source_minio_access}:${source_minio_secret}@minio-source:9000" \
  mb --ignore-existing src/zkdeal-source >/dev/null
printf 'object-durable-marker' | docker run --rm -i --network "$network" \
  -e "MC_HOST_src=http://${source_minio_access}:${source_minio_secret}@minio-source:9000" \
  "$mc_image" pipe src/zkdeal-source/live/sample.txt >/dev/null

# Write the reviewed secret files and the SHA-256-bound topology.
secrets="$work/secrets"; mkdir -p "$secrets"; umask 077
write_secret() { printf '%s' "$2" >"$secrets/$1"; chmod 0600 "$secrets/$1"; }
write_secret source-postgres-password "$source_pg_password"
write_secret fresh-postgres-password "$fresh_pg_password"
write_secret source-minio-access "$source_minio_access"
write_secret source-minio-secret "$source_minio_secret"
write_secret backup-minio-access "$backup_minio_access"
write_secret backup-minio-secret "$backup_minio_secret"
write_secret fresh-minio-access "$fresh_minio_access"
write_secret fresh-minio-secret "$fresh_minio_secret"
write_secret backup-encryption-key "$encryption_key"
token="eph_backup_restore_acceptance_token_00001"
write_secret backup-restore-token "$token"

cat >"$work/topology.json" <<JSON
{
  "schemaVersion": 1,
  "classification": "non-release-fixture",
  "candidateId": "${candidate}",
  "planSha256": "$(printf '%064d' 1 | tr 0-9 a)",
  "hostedIntegrationToken": "sha256:$(printf '%064d' 2 | tr 0-9 b)",
  "backupRestoreTokenSha256": "$(sha256 "$secrets/backup-restore-token")",
  "allowedHosts": ["postgres-source", "postgres-fresh", "minio-source", "minio-backup", "minio-fresh"],
  "source": {
    "postgres": {"host": "postgres-source", "port": 5432, "database": "zkdeal", "user": "zkdeal", "passwordFile": "/secrets/source-postgres-password", "passwordSha256": "$(sha256 "$secrets/source-postgres-password")"},
    "minio": {"endpoint": "http://minio-source:9000", "accessKeyFile": "/secrets/source-minio-access", "accessKeySha256": "$(sha256 "$secrets/source-minio-access")", "secretKeyFile": "/secrets/source-minio-secret", "secretKeySha256": "$(sha256 "$secrets/source-minio-secret")", "bucket": "zkdeal-source"}
  },
  "backupStore": {"endpoint": "http://minio-backup:9000", "accessKeyFile": "/secrets/backup-minio-access", "accessKeySha256": "$(sha256 "$secrets/backup-minio-access")", "secretKeyFile": "/secrets/backup-minio-secret", "secretKeySha256": "$(sha256 "$secrets/backup-minio-secret")", "bucket": "zkdeal-backups"},
  "encryption": {"algorithm": "AES-256-GCM", "keyFile": "/secrets/backup-encryption-key", "keySha256": "$(sha256 "$secrets/backup-encryption-key")"},
  "freshTargets": {
    "fresh-primary": {
      "postgres": {"host": "postgres-fresh", "port": 5432, "database": "zkdeal", "user": "zkdeal", "passwordFile": "/secrets/fresh-postgres-password", "passwordSha256": "$(sha256 "$secrets/fresh-postgres-password")"},
      "minio": {"endpoint": "http://minio-fresh:9000", "accessKeyFile": "/secrets/fresh-minio-access", "accessKeySha256": "$(sha256 "$secrets/fresh-minio-access")", "secretKeyFile": "/secrets/fresh-minio-secret", "secretKeySha256": "$(sha256 "$secrets/fresh-minio-secret")", "bucket": "zkdeal-restored"}
    }
  },
  "processTimeoutSeconds": 900
}
JSON
# The controller runs as uid 65532: secret files stay 0600 but must belong to
# that uid (it enforces 0600-or-stricter), and the non-secret topology gets
# world-read.
chown -R 65532:65532 "$secrets"
chmod 0755 "$secrets"
chmod 0644 "$work/topology.json"
topology_sha=$(sha256 "$work/topology.json")

plan_sha=$(printf '%064d' 1 | tr 0-9 a)
hosted_token="sha256:$(printf '%064d' 2 | tr 0-9 b)"
docker run -d --name "${project}-controller" --network "$network" --network-alias backup-restore-control \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /journal:rw,uid=65532,gid=65532,mode=0700 \
  --tmpfs /tmp:rw,uid=65532,gid=65532,mode=0700 \
  --tmpfs /work:rw,uid=65532,gid=65532,mode=0700 \
  -v "$work/topology.json:/topology.json:ro" -v "$secrets:/secrets:ro" \
  -e BACKUP_RESTORE_TOPOLOGY_FILE=/topology.json -e BACKUP_RESTORE_TOPOLOGY_SHA256="$topology_sha" \
  -e BACKUP_RESTORE_CANDIDATE_ID="$candidate" -e BACKUP_RESTORE_PLAN_SHA256="$plan_sha" \
  -e BACKUP_RESTORE_HOSTED_INTEGRATION_TOKEN="$hosted_token" \
  -e BACKUP_RESTORE_TOKEN_FILE=/secrets/backup-restore-token \
  -e BACKUP_RESTORE_CANDIDATE_DESCRIPTOR_SHA256="$(printf '%064d' 3 | tr 0-9 c)" \
  -e BACKUP_RESTORE_ADAPTER_SOURCE_SHA256="$source_sha" \
  -e BACKUP_RESTORE_ADAPTER_IMAGE="registry.local/zkdeal-backup-restore-control@sha256:${source_sha}" \
  -e BACKUP_RESTORE_PLATFORM=docker \
  "$controller" >/dev/null

call() { docker run --rm --network "$network" "$curl_image" --silent --show-error "$@"; }
base=http://backup-restore-control:8080
attempt=0
until call --fail "$base/ready" >/dev/null 2>&1; do
  attempt=$((attempt + 1)); [ "$attempt" -lt 60 ] || { echo 'controller did not become ready' >&2; exit 1; }
  sleep 1
done

binding="\"binding\":{\"candidateId\":\"${candidate}\",\"planSha256\":\"${plan_sha}\",\"hostedIntegrationToken\":\"${hosted_token}\"}"
auth="Authorization: Bearer ${token}"

# Missing token fails closed.
status=$(call -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: backup-key-0001' -H 'X-Correlation-Id: backup-correlation-0001' \
  --data "{\"schemaVersion\":1,${binding}}" "$base/v1/backups")
[ "$status" = 401 ] || { echo "unauthenticated backup was not denied: $status" >&2; exit 1; }

backup=$(call --fail -H 'Content-Type: application/json' -H "$auth" \
  -H 'Idempotency-Key: backup-key-0001' -H 'X-Correlation-Id: backup-correlation-0001' \
  --data "{\"schemaVersion\":1,${binding}}" "$base/v1/backups")
printf '%s' "$backup" | grep -F '"independentBackupStore":true' >/dev/null || { echo 'backup did not attest an independent store' >&2; exit 1; }
printf '%s' "$backup" | grep -F '"algorithm":"AES-256-GCM"' >/dev/null || { echo 'backup did not attest AEAD' >&2; exit 1; }
backup_id=$(printf '%s' "$backup" | sed -n 's/.*"backupId":"\(bk-[0-9a-f]\{40\}\)".*/\1/p')
[ -n "$backup_id" ] || { echo 'no backupId returned' >&2; exit 1; }

prefix="${candidate}/${backup_id}"
# INDEPENDENT store: artifacts are in the backup MinIO and NOT the source MinIO.
run_mc -e "MC_HOST_bk=http://${backup_minio_access}:${backup_minio_secret}@minio-backup:9000" \
  stat "bk/zkdeal-backups/${prefix}/database.dump.enc" >/dev/null || { echo 'backup ciphertext missing from independent store' >&2; exit 1; }
if run_mc -e "MC_HOST_src=http://${source_minio_access}:${source_minio_secret}@minio-source:9000" \
  stat "src/zkdeal-source/${prefix}/database.dump.enc" >/dev/null 2>&1; then
  echo 'a backup artifact leaked into the primary data MinIO' >&2; exit 1
fi
# AEAD: the stored ciphertext must not expose the plaintext marker.
if run_mc -e "MC_HOST_bk=http://${backup_minio_access}:${backup_minio_secret}@minio-backup:9000" \
  cat "bk/zkdeal-backups/${prefix}/database.dump.enc" | grep -a -q database-durable-marker; then
  echo 'encrypted database backup exposed plaintext' >&2; exit 1
fi

# TAMPER gate: keep the original bytes, flip one byte of the stored ciphertext,
# and prove restore refuses distinctly and leaves the still-empty fresh targets
# untouched.  Authenticated decryption runs before any target is mutated.
run_mc -v "$work:/mnt" -e "MC_HOST_bk=http://${backup_minio_access}:${backup_minio_secret}@minio-backup:9000" \
  cp "bk/zkdeal-backups/${prefix}/objects.bundle.tar.enc" /mnt/original.enc >/dev/null
cp "$work/original.enc" "$work/tampered.enc"
printf '\001' | dd of="$work/tampered.enc" bs=1 seek=8 count=1 conv=notrunc >/dev/null 2>&1
run_mc -v "$work:/mnt" -e "MC_HOST_bk=http://${backup_minio_access}:${backup_minio_secret}@minio-backup:9000" \
  cp /mnt/tampered.enc "bk/zkdeal-backups/${prefix}/objects.bundle.tar.enc" >/dev/null
# The curl container cannot write host paths, so capture body and status on
# stdout and split them.
tamper_out=$(call -w '\n%{http_code}' -H 'Content-Type: application/json' -H "$auth" \
  -H 'Idempotency-Key: restore-tamper-key-0001' -H 'X-Correlation-Id: restore-tamper-correlation-0001' \
  --data "{\"schemaVersion\":1,${binding},\"backupId\":\"${backup_id}\",\"freshTarget\":\"fresh-primary\"}" "$base/v1/restores")
tamper_status=$(printf '%s' "$tamper_out" | tail -n1)
tamper_body=$(printf '%s' "$tamper_out" | sed '$d')
[ "$tamper_status" = 409 ] || { echo "tampered restore was not refused: $tamper_status" >&2; exit 1; }
printf '%s' "$tamper_body" | grep -F '"code":"BACKUP_ARTIFACT_TAMPERED"' >/dev/null || { echo 'tampered restore lacked the distinct error' >&2; exit 1; }
fresh_objects=$(docker exec "${project}-postgres-fresh" psql -U zkdeal -d zkdeal -tAc \
  "select count(*) from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid=c.relnamespace where c.relkind='r' and n.nspname not in ('pg_catalog','information_schema');")
[ "$fresh_objects" = 0 ] || { echo 'tampered restore left partial database side effects' >&2; exit 1; }

# Repair the artifact and run a clean restore into the still-fresh target.
run_mc -v "$work:/mnt" -e "MC_HOST_bk=http://${backup_minio_access}:${backup_minio_secret}@minio-backup:9000" \
  cp /mnt/original.enc "bk/zkdeal-backups/${prefix}/objects.bundle.tar.enc" >/dev/null
restore=$(call --fail -H 'Content-Type: application/json' -H "$auth" \
  -H 'Idempotency-Key: restore-key-0001' -H 'X-Correlation-Id: restore-correlation-0001' \
  --data "{\"schemaVersion\":1,${binding},\"backupId\":\"${backup_id}\",\"freshTarget\":\"fresh-primary\"}" "$base/v1/restores")
printf '%s' "$restore" | grep -F '"integrityVerifiedBeforeRestore":true' >/dev/null || { echo 'restore did not attest pre-restore integrity' >&2; exit 1; }
printf '%s' "$restore" | grep -F '"hashesVerified":true' >/dev/null || { echo 'restore hashes not verified' >&2; exit 1; }
value=$(docker exec "${project}-postgres-fresh" psql -U zkdeal -d zkdeal -Atc 'select value from backup_marker where id=1;')
[ "$value" = database-durable-marker ] || { echo 'fresh database restore mismatch' >&2; exit 1; }
object=$(run_mc -e "MC_HOST_fresh=http://${fresh_minio_access}:${fresh_minio_secret}@minio-fresh:9000" cat fresh/zkdeal-restored/live/sample.txt)
[ "$object" = object-durable-marker ] || { echo 'fresh object restore mismatch' >&2; exit 1; }

echo 'backup-restore controller acceptance passed: independent store, AES-256-GCM ciphertext, tamper refusal with no partial side effect, and clean round-trip'
