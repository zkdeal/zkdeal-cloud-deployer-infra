FROM minio/mc@sha256:aead63c77f9db9107f1696fb08ecb0faeda23729cde94b0f663edf4fe09728e3 AS minio-client
FROM alpine/openssl@sha256:42c7389ef077aed0eb4e96d0abbd094083d701bbaff1313073b061c0c9cd8278 AS openssl

FROM postgres:17.6-alpine@sha256:ef257d85f76e48da1c64832459b59fcaba1a4dac97bf5d7450c77753542eee94
COPY --from=minio-client /usr/bin/mc /usr/local/bin/mc
COPY --from=openssl /usr/bin/openssl /usr/local/bin/openssl
COPY scripts/live_backup.sh scripts/live_restore.sh scripts/archive_guard.sh /usr/local/bin/
COPY zkdeal-BUSL-1.1-LICENSE /zkdeal-BUSL-1.1-LICENSE
RUN chmod 0555 /usr/local/bin/live_backup.sh /usr/local/bin/live_restore.sh /usr/local/bin/archive_guard.sh \
 && chmod 0444 /zkdeal-BUSL-1.1-LICENSE
LABEL org.opencontainers.image.licenses="BUSL-1.1"
USER 70:70
ENTRYPOINT []
