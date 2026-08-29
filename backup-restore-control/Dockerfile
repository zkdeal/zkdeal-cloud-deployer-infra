FROM minio/mc@sha256:aead63c77f9db9107f1696fb08ecb0faeda23729cde94b0f663edf4fe09728e3 AS minio-client
FROM postgres@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94

# This postgres digest is the alpine variant, so packages come from apk:
# python3 plus py3-cryptography carries a working AES-256-GCM AEAD.
RUN apk add --no-cache python3 py3-cryptography

COPY --from=minio-client /usr/bin/mc /usr/bin/mc
COPY backup-restore-control/backup_restore_control.py /opt/zkdeal/backup_restore_control.py
COPY LICENSE /LICENSE

ARG BACKUP_RESTORE_CONTROL_SOURCE_SHA256
RUN test -n "$BACKUP_RESTORE_CONTROL_SOURCE_SHA256" \
 && echo "$BACKUP_RESTORE_CONTROL_SOURCE_SHA256  /opt/zkdeal/backup_restore_control.py" | sha256sum -c - \
 && python3 -c "from cryptography.hazmat.primitives.ciphers.aead import AESGCM; AESGCM(bytes(32))" \
 && addgroup -S -g 65532 zkdeal \
 && adduser -S -D -H -u 65532 -G zkdeal zkdeal \
 && chmod 0555 /opt/zkdeal/backup_restore_control.py /usr/bin/mc

LABEL org.opencontainers.image.title="zkdeal-backup-restore-control" \
      org.opencontainers.image.description="Candidate-bound PostgreSQL and MinIO backup/restore controller" \
      org.zkdeal.backup-restore-control.source.sha256="$BACKUP_RESTORE_CONTROL_SOURCE_SHA256" \
      org.opencontainers.image.licenses="MIT"

USER 65532:65532
WORKDIR /work
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    BACKUP_RESTORE_HOST=0.0.0.0 BACKUP_RESTORE_PORT=8080 \
    BACKUP_RESTORE_JOURNAL_DIR=/journal
EXPOSE 8080
ENTRYPOINT ["python3", "/opt/zkdeal/backup_restore_control.py"]
