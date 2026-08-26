#!/bin/sh
set -eu

cat >>"$PGDATA/pg_hba.conf" <<'EOF'
host replication replicator 0.0.0.0/0 scram-sha-256
host all all 0.0.0.0/0 scram-sha-256
EOF

psql --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replication-acceptance-password';
CREATE TABLE app_writer_fence (
  lease_name text PRIMARY KEY,
  epoch bigint NOT NULL,
  holder text NOT NULL
);
INSERT INTO app_writer_fence VALUES ('coordinator-writer', 1, 'primary-active');
CREATE TABLE fenced_writes (
  id bigserial PRIMARY KEY,
  writer_epoch bigint NOT NULL,
  payload text NOT NULL
);
CREATE TABLE hosted_idempotency_records (
  scope text NOT NULL,
  operation text NOT NULL,
  idempotency_key text NOT NULL,
  request_hash text NOT NULL,
  response_status integer,
  response_body jsonb,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  expires_at timestamptz NOT NULL,
  PRIMARY KEY(scope, operation, idempotency_key)
);
CREATE FUNCTION enforce_writer_epoch() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.writer_epoch <> (SELECT epoch FROM app_writer_fence WHERE lease_name = 'coordinator-writer') THEN
    RAISE EXCEPTION 'stale writer epoch %', NEW.writer_epoch;
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER fenced_writes_epoch BEFORE INSERT ON fenced_writes
FOR EACH ROW EXECUTE FUNCTION enforce_writer_epoch();
INSERT INTO fenced_writes(writer_epoch, payload) VALUES (1, 'replicate-before-failover');
SQL
