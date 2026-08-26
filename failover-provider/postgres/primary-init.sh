#!/bin/sh
set -eu

: "${REPLICATION_PASSWORD_FILE:?REPLICATION_PASSWORD_FILE is required}"
[ -f "$REPLICATION_PASSWORD_FILE" ] || {
  echo 'replication password must resolve to a regular file' >&2
  exit 1
}
replication_password=$(cat "$REPLICATION_PASSWORD_FILE")
[ "${#replication_password}" -ge 16 ] || { echo 'replication password is too short' >&2; exit 1; }

grep -F 'zkdeal-managed-replication' "$PGDATA/pg_hba.conf" >/dev/null 2>&1 || cat >>"$PGDATA/pg_hba.conf" <<'EOF'
# zkdeal-managed-replication
host replication replicator 0.0.0.0/0 scram-sha-256
EOF

psql --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set "replication_password=$replication_password" <<'SQL'
SELECT 'CREATE ROLE replicator WITH REPLICATION LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'replicator')\gexec
ALTER ROLE replicator WITH REPLICATION LOGIN PASSWORD :'replication_password';
SQL
