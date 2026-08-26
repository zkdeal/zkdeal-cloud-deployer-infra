ARG POSTGRES_BASE=postgres@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94
FROM ${POSTGRES_BASE}
COPY tests/fixtures/postgres-ha/primary-init.sh /docker-entrypoint-initdb.d/10-ha.sh
COPY tests/fixtures/postgres-ha/standby-entrypoint.sh /opt/zkdeal/standby-entrypoint.sh
COPY helm/zkdeal/files/promotion_gate.sh /opt/zkdeal/promotion_gate.sh
RUN chmod 0555 /docker-entrypoint-initdb.d/10-ha.sh /opt/zkdeal/standby-entrypoint.sh /opt/zkdeal/promotion_gate.sh
