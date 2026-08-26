#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."
project=zkdeal-backup-acceptance
files="-f compose/compose.yaml -f compose/compose.dependencies.yaml -f compose/compose.dependencies.test.yaml"
backup_image=${BACKUP_TOOLS_IMAGE:-zkdeal-backup-tools:local}
tools_image=${DEPLOYMENT_TOOLS_IMAGE:-zkdeal-deployment-tools:local}
minio_client_image=${MINIO_CLIENT_IMAGE:-minio/mc@sha256:aead63c77f9db9107f1696fb08ecb0faeda23729cde94b0f663edf4fe09728e3}
network=${project}_internal
guard_volume=${project}-archive-guard
key=8f4d2b7a39b31e8b42be8e8bb9651fd83d71efb7f26eef1cf633e2f66f93216f
wrong_key=9f4d2b7a39b31e8b42be8e8bb9651fd83d71efb7f26eef1cf633e2f66f93216f

cleanup() {
  docker compose --project-name "$project" --env-file .env.example $files --profile hosted down --volumes --remove-orphans >/dev/null 2>&1 || true
  docker volume rm -f "$guard_volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
cleanup

docker compose --project-name "$project" --env-file .env.example $files --profile hosted up -d --wait postgres minio
docker compose --project-name "$project" --env-file .env.example $files exec -T postgres \
  psql -U zkdeal -d zkdeal -v ON_ERROR_STOP=1 \
  -c 'create table backup_acceptance(id int primary key, value text not null);' \
  -c "insert into backup_acceptance values (1, 'database-durable-marker');" \
  -c 'create database zkdeal_restore;'

mc_run() {
  docker run --rm --network "$network" \
    -e MC_HOST_local=http://local-minio-admin:local-minio-password-only@minio:9000 \
    "$minio_client_image" "$@"
}
mc_run mb --ignore-existing local/zkdeal-source >/dev/null
printf 'object-durable-marker\n' | docker run --rm -i --network "$network" \
  -e MC_HOST_local=http://local-minio-admin:local-minio-password-only@minio:9000 \
  "$minio_client_image" pipe local/zkdeal-source/live/sample.txt >/dev/null

common_run="--rm --read-only --cap-drop ALL --security-opt no-new-privileges:true --network $network --tmpfs /work:rw,size=256m,uid=70,gid=70,mode=0700 --tmpfs /tmp:rw,size=64m,uid=70,gid=70,mode=0700"
docker run $common_run \
  -e DATABASE_URL=postgresql://zkdeal:local-postgres-password-only@postgres:5432/zkdeal \
  -e SOURCE_OBJECT_STORE_ENDPOINT=http://minio:9000 \
  -e SOURCE_OBJECT_STORE_ACCESS_KEY=local-minio-admin \
  -e SOURCE_OBJECT_STORE_SECRET_KEY=local-minio-password-only \
  -e BACKUP_OBJECT_STORE_ENDPOINT=http://minio:9000 \
  -e BACKUP_OBJECT_STORE_ACCESS_KEY=local-minio-admin \
  -e BACKUP_OBJECT_STORE_SECRET_KEY=local-minio-password-only \
  -e SOURCE_BUCKET=zkdeal-source -e SOURCE_PREFIX=live \
  -e BACKUP_BUCKET=zkdeal-backups -e BACKUP_PREFIX=hosted \
  -e BACKUP_ENCRYPTION_KEY="$key" -e BACKUP_ID=acceptance-001 \
  -e BACKUP_RETENTION_DAYS=365 "$backup_image" /usr/local/bin/live_backup.sh

if mc_run cat local/zkdeal-backups/hosted/acceptance-001/database.dump.enc | grep -a -q database-durable-marker; then
  echo "encrypted database backup exposed plaintext" >&2; exit 1
fi
if mc_run cat local/zkdeal-backups/hosted/acceptance-001/manifest.json.enc | grep -a -q databaseSha256; then
  echo "encrypted backup manifest exposed plaintext" >&2; exit 1
fi

if docker run $common_run \
  -e TARGET_DATABASE_URL=postgresql://zkdeal:local-postgres-password-only@postgres:5432/zkdeal_restore \
  -e BACKUP_OBJECT_STORE_ENDPOINT=http://minio:9000 \
  -e BACKUP_OBJECT_STORE_ACCESS_KEY=local-minio-admin \
  -e BACKUP_OBJECT_STORE_SECRET_KEY=local-minio-password-only \
  -e TARGET_OBJECT_STORE_ENDPOINT=http://minio:9000 \
  -e TARGET_OBJECT_STORE_ACCESS_KEY=local-minio-admin \
  -e TARGET_OBJECT_STORE_SECRET_KEY=local-minio-password-only \
  -e BACKUP_BUCKET=zkdeal-backups -e BACKUP_PREFIX=hosted \
  -e TARGET_BUCKET=zkdeal-restored -e TARGET_PREFIX=live \
  -e BACKUP_ENCRYPTION_KEY="$wrong_key" -e RESTORE_ID=acceptance-001 \
  "$backup_image" /usr/local/bin/live_restore.sh >/dev/null 2>&1; then
  echo "restore accepted the wrong envelope key" >&2; exit 1
fi

docker run $common_run \
  -e TARGET_DATABASE_URL=postgresql://zkdeal:local-postgres-password-only@postgres:5432/zkdeal_restore \
  -e BACKUP_OBJECT_STORE_ENDPOINT=http://minio:9000 \
  -e BACKUP_OBJECT_STORE_ACCESS_KEY=local-minio-admin \
  -e BACKUP_OBJECT_STORE_SECRET_KEY=local-minio-password-only \
  -e TARGET_OBJECT_STORE_ENDPOINT=http://minio:9000 \
  -e TARGET_OBJECT_STORE_ACCESS_KEY=local-minio-admin \
  -e TARGET_OBJECT_STORE_SECRET_KEY=local-minio-password-only \
  -e BACKUP_BUCKET=zkdeal-backups -e BACKUP_PREFIX=hosted \
  -e TARGET_BUCKET=zkdeal-restored -e TARGET_PREFIX=live \
  -e BACKUP_ENCRYPTION_KEY="$key" -e RESTORE_ID=acceptance-001 \
  "$backup_image" /usr/local/bin/live_restore.sh

database_value=$(docker compose --project-name "$project" --env-file .env.example $files exec -T postgres \
  psql -U zkdeal -d zkdeal_restore -Atc 'select value from backup_acceptance where id=1;')
[ "$database_value" = database-durable-marker ] || { echo "fresh database restore mismatch" >&2; exit 1; }
object_value=$(mc_run cat local/zkdeal-restored/live/sample.txt)
[ "$object_value" = object-durable-marker ] || { echo "object restore mismatch" >&2; exit 1; }

# The same validator used by restore must reject both traversal and non-regular
# archive entries before extraction.  A separate tools container creates the
# hostile fixtures in an isolated Docker volume.
docker volume create "$guard_volume" >/dev/null
docker run --rm --entrypoint python -v "$guard_volume:/guard" "$tools_image" -c \
  'import io,tarfile
with tarfile.open("/guard/traversal.tar", "w") as tf:
    info=tarfile.TarInfo("../escape")
    payload=b"must-not-extract"
    info.size=len(payload)
    tf.addfile(info, io.BytesIO(payload))
with tarfile.open("/guard/symlink.tar", "w") as tf:
    info=tarfile.TarInfo("link")
    info.type=tarfile.SYMTYPE
    info.linkname="/etc/passwd"
    tf.addfile(info)'
if docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges:true --tmpfs /tmp:rw,size=8m,mode=1777 \
  -v "$guard_volume:/guard:ro" "$backup_image" /usr/local/bin/archive_guard.sh /guard/traversal.tar >/dev/null 2>&1; then
  echo "archive guard accepted a traversal member" >&2; exit 1
fi
if docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges:true --tmpfs /tmp:rw,size=8m,mode=1777 \
  -v "$guard_volume:/guard:ro" "$backup_image" /usr/local/bin/archive_guard.sh /guard/symlink.tar >/dev/null 2>&1; then
  echo "archive guard accepted a symlink member" >&2; exit 1
fi

# Ciphertext authentication is checked before decryption.  Clone the valid
# envelope, corrupt its MAC, and prove restore fails closed.
mc_run cp --recursive local/zkdeal-backups/hosted/acceptance-001/ local/zkdeal-backups/hosted/acceptance-tamper/ >/dev/null
printf '%064d\n' 0 | docker run --rm -i --network "$network" \
  -e MC_HOST_local=http://local-minio-admin:local-minio-password-only@minio:9000 \
  "$minio_client_image" pipe \
  local/zkdeal-backups/hosted/acceptance-tamper/manifest.json.enc.hmac >/dev/null
if docker run $common_run \
  -e TARGET_DATABASE_URL=postgresql://zkdeal:local-postgres-password-only@postgres:5432/zkdeal_restore \
  -e BACKUP_OBJECT_STORE_ENDPOINT=http://minio:9000 \
  -e BACKUP_OBJECT_STORE_ACCESS_KEY=local-minio-admin \
  -e BACKUP_OBJECT_STORE_SECRET_KEY=local-minio-password-only \
  -e TARGET_OBJECT_STORE_ENDPOINT=http://minio:9000 \
  -e TARGET_OBJECT_STORE_ACCESS_KEY=local-minio-admin \
  -e TARGET_OBJECT_STORE_SECRET_KEY=local-minio-password-only \
  -e BACKUP_BUCKET=zkdeal-backups -e BACKUP_PREFIX=hosted \
  -e TARGET_BUCKET=zkdeal-restored -e TARGET_PREFIX=live \
  -e BACKUP_ENCRYPTION_KEY="$key" -e RESTORE_ID=acceptance-tamper \
  "$backup_image" /usr/local/bin/live_restore.sh >/dev/null 2>&1; then
  echo "restore accepted a tampered envelope" >&2; exit 1
fi

echo "live backup acceptance passed: HKDF-separated encrypted/HMAC dump and object snapshot restored; tamper, traversal and link attacks rejected"
