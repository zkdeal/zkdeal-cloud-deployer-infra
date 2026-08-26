#!/bin/sh
set -eu

: "${PRIMARY_HOST:?PRIMARY_HOST is required}"
: "${REPLICATION_SLOT:?REPLICATION_SLOT is required}"
: "${REPLICATION_PASSWORD_FILE:?REPLICATION_PASSWORD_FILE is required}"
: "${PGDATA:=/var/lib/postgresql/data}"
[ -f "$REPLICATION_PASSWORD_FILE" ] || {
  echo 'replication password must resolve to a regular file' >&2
  exit 1
}
export PGPASSWORD
PGPASSWORD=$(cat "$REPLICATION_PASSWORD_FILE")
[ "${#PGPASSWORD}" -ge 16 ] || { echo 'replication password is too short' >&2; exit 1; }

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  # Never erase or reuse a partial replica automatically. Recovery requires an
  # explicit fresh target directory so a former primary cannot rejoin.
  if find "$PGDATA" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    echo 'standby data directory is nonempty but has no PG_VERSION; reseed a fresh target' >&2
    exit 1
  fi
  until pg_isready -h "$PRIMARY_HOST" -U replicator -d postgres >/dev/null 2>&1; do sleep 1; done
  if [ "$(id -u)" -eq "$(id -u postgres)" ]; then
    pg_basebackup \
      -h "$PRIMARY_HOST" -U replicator -D "$PGDATA" \
      -Fp -Xs -P -R -C -S "$REPLICATION_SLOT"
  else
    gosu postgres pg_basebackup \
      -h "$PRIMARY_HOST" -U replicator -D "$PGDATA" \
      -Fp -Xs -P -R -C -S "$REPLICATION_SLOT"
    chown -R postgres:postgres "$PGDATA"
  fi
  chmod 0700 "$PGDATA"
fi

exec /usr/local/bin/docker-entrypoint.sh postgres \
  -c hot_standby=on \
  -c wal_level=replica \
  -c max_wal_senders=10 \
  -c max_replication_slots=10 \
  -c wal_keep_size=1024MB
