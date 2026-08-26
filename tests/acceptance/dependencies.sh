#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."
project=zkdeal-dependency-acceptance
files="-f compose/compose.yaml -f compose/compose.dependencies.yaml -f compose/compose.dependencies.test.yaml"

cleanup() {
  docker compose --project-name "$project" --env-file .env.example $files --profile hosted down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
cleanup

docker compose --project-name "$project" --env-file .env.example $files --profile hosted up -d --wait postgres minio

docker compose --project-name "$project" --env-file .env.example $files exec -T postgres \
  psql -U zkdeal -d zkdeal -v ON_ERROR_STOP=1 -c 'create table if not exists infra_acceptance(id int primary key, value text not null);' \
  -c "insert into infra_acceptance values (1, 'durable') on conflict (id) do update set value=excluded.value;"

docker compose --project-name "$project" --env-file .env.example $files run --rm minio-init
printf 'durable-object\n' | docker run --rm -i --network "${project}_internal" \
  -e MC_HOST_local=http://local-minio-admin:local-minio-password-only@minio:9000 \
  minio/mc:RELEASE.2025-04-16T18-13-26Z \
  pipe local/zkdeal-evidence/dependency-acceptance.txt

docker compose --project-name "$project" --env-file .env.example $files stop postgres minio
docker compose --project-name "$project" --env-file .env.example $files start postgres minio

for service in postgres minio; do
  attempts=0
  until [ "$(docker inspect --format '{{.State.Health.Status}}' "${project}-${service}-1")" = healthy ]; do
    attempts=$((attempts + 1))
    [ "$attempts" -lt 60 ] || { echo "$service did not recover" >&2; exit 1; }
    sleep 1
  done
done

value=$(docker compose --project-name "$project" --env-file .env.example $files exec -T postgres \
  psql -U zkdeal -d zkdeal -Atc 'select value from infra_acceptance where id=1;')
[ "$value" = durable ] || { echo "PostgreSQL data did not survive restart" >&2; exit 1; }

object=$(docker run --rm --network "${project}_internal" \
  -e MC_HOST_local=http://local-minio-admin:local-minio-password-only@minio:9000 \
  minio/mc:RELEASE.2025-04-16T18-13-26Z \
  cat local/zkdeal-evidence/dependency-acceptance.txt)
[ "$object" = durable-object ] || { echo "MinIO object did not survive restart" >&2; exit 1; }

echo "dependency acceptance passed: health gates and restart persistence"
