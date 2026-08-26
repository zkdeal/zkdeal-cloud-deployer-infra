#!/bin/sh
set -eu

: "${POSTGRES_HOST:?POSTGRES_HOST is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_PASSWORD_FILE:?POSTGRES_PASSWORD_FILE is required}"
[ -f "$POSTGRES_PASSWORD_FILE" ] && [ ! -L "$POSTGRES_PASSWORD_FILE" ] || {
  echo 'database password must be a regular file' >&2
  exit 1
}
export PGPASSWORD
PGPASSWORD=$(cat "$POSTGRES_PASSWORD_FILE")
until pg_isready -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do sleep 1; done
psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" --set ON_ERROR_STOP=1 \
  -c "alter system set synchronous_standby_names='FIRST 1 (zkdeal_standby)'" >/dev/null
psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" --set ON_ERROR_STOP=1 \
  -c 'select pg_reload_conf()' >/dev/null
attempt=0
until [ "$(psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select count(*) from pg_stat_replication where application_name='zkdeal_standby' and state='streaming' and sync_state in ('sync','quorum')")" = 1 ]; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 120 ] || { echo 'standby never became synchronous' >&2; exit 1; }
  sleep 1
done
