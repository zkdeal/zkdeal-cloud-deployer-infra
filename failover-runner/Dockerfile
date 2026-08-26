FROM python:3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d

COPY failover-runner/zkdeal_failover.py /opt/zkdeal/zkdeal_failover.py
COPY scripts/promotion_controller.py /opt/zkdeal/promotion_controller.py
COPY acceptance-runner/zkdeal_acceptance.py /opt/zkdeal-acceptance
COPY promotion-controller/failover-provider-v1.openapi.json /opt/zkdeal/failover-provider-v1.openapi.json
COPY zkdeal-BUSL-1.1-LICENSE /zkdeal-BUSL-1.1-LICENSE

ARG FAILOVER_RUNNER_SOURCE_SHA256
RUN test -n "$FAILOVER_RUNNER_SOURCE_SHA256" \
 && actual="$(cat /opt/zkdeal/zkdeal_failover.py /opt/zkdeal/promotion_controller.py /opt/zkdeal-acceptance /opt/zkdeal/failover-provider-v1.openapi.json | sha256sum | awk '{print $1}')" \
 && test "$actual" = "$FAILOVER_RUNNER_SOURCE_SHA256" \
 && addgroup -S -g 65532 zkdeal \
 && adduser -S -D -H -u 65532 -G zkdeal zkdeal \
 && ln -s /opt/zkdeal/zkdeal_failover.py /opt/zkdeal-failover \
 && chmod 0555 /opt/zkdeal/zkdeal_failover.py /opt/zkdeal/promotion_controller.py /opt/zkdeal-acceptance \
 && chmod 0444 /opt/zkdeal/failover-provider-v1.openapi.json \
 && mkdir -p /evidence \
 && chown 65532:65532 /evidence

LABEL org.opencontainers.image.title="zkdeal-failover-runner" \
      org.opencontainers.image.description="Provider-backed promotion and stale-writer assertion runner" \
      org.zkdeal.failover-runner.source.sha256="$FAILOVER_RUNNER_SOURCE_SHA256" \
      org.opencontainers.image.licenses="BUSL-1.1"

USER 65532:65532
WORKDIR /work
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    FAILOVER_EVIDENCE_DIR=/evidence ACCEPTANCE_EVIDENCE_DIR=/evidence
ENTRYPOINT ["/opt/zkdeal-failover"]
