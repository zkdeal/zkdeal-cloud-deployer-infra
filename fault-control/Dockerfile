FROM python:3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d

COPY fault-control/fault_control.py /opt/zkdeal/fault_control.py
COPY LICENSE /LICENSE

ARG FAULT_CONTROL_SOURCE_SHA256
RUN test -n "$FAULT_CONTROL_SOURCE_SHA256" \
 && echo "$FAULT_CONTROL_SOURCE_SHA256  /opt/zkdeal/fault_control.py" | sha256sum -c - \
 && addgroup -S -g 65532 zkdeal \
 && adduser -S -D -H -u 65532 -G zkdeal zkdeal \
 && chmod 0555 /opt/zkdeal/fault_control.py

LABEL org.opencontainers.image.title="zkdeal-fault-control" \
      org.opencontainers.image.description="Candidate-bound allowlisted fault and load controller" \
      org.zkdeal.fault-control.source.sha256="$FAULT_CONTROL_SOURCE_SHA256" \
      org.opencontainers.image.licenses="MIT"

USER 65532:65532
WORKDIR /work
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    FAULT_CONTROL_HOST=0.0.0.0 FAULT_CONTROL_PORT=8080 \
    FAULT_CONTROL_JOURNAL_DIR=/journal
EXPOSE 8080
ENTRYPOINT ["/opt/zkdeal/fault_control.py"]
