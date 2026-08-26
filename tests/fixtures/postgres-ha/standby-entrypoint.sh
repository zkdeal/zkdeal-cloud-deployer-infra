#!/bin/sh
set -eu

: "${PRIMARY_HOST:?PRIMARY_HOST is required}"
: "${REPLICATION_SLOT:?REPLICATION_SLOT is required}"
: "${PGDATA:=/var/lib/postgresql/data}"

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  find "$PGDATA" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  until pg_isready -h "$PRIMARY_HOST" -U replicator -d postgres >/dev/null 2>&1; do sleep 1; done
  gosu postgres pg_basebackup \
    -h "$PRIMARY_HOST" -U replicator -D "$PGDATA" \
    -Fp -Xs -P -R -C -S "$REPLICATION_SLOT"
  chown -R postgres:postgres "$PGDATA"
  chmod 0700 "$PGDATA"
fi

exec /usr/local/bin/docker-entrypoint.sh postgres \
  -c hot_standby=on \
  -c wal_level=replica \
  -c max_wal_senders=10 \
  -c max_replication_slots=10 \
  -c wal_keep_size=64MB
