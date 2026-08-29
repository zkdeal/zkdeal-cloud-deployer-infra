FROM python:3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d

COPY acceptance-runner/zkdeal_acceptance.py /opt/zkdeal/zkdeal_acceptance.py
COPY acceptance-runner/scenario-plan.schema.json /opt/zkdeal/scenario-plan.schema.json
COPY acceptance-runner/capability.json /opt/zkdeal/capability.json
COPY LICENSE /LICENSE

ARG ACCEPTANCE_RUNNER_SOURCE_SHA256
RUN test -n "$ACCEPTANCE_RUNNER_SOURCE_SHA256" \
 && actual="$(cat /opt/zkdeal/zkdeal_acceptance.py /opt/zkdeal/scenario-plan.schema.json /opt/zkdeal/capability.json | sha256sum | awk '{print $1}')" \
 && test "$actual" = "$ACCEPTANCE_RUNNER_SOURCE_SHA256" \
 && addgroup -S -g 65532 zkdeal \
 && adduser -S -D -H -u 65532 -G zkdeal zkdeal \
 && ln -s /opt/zkdeal/zkdeal_acceptance.py /opt/zkdeal-acceptance \
 && chmod 0555 /opt/zkdeal/zkdeal_acceptance.py \
 && chmod 0444 /opt/zkdeal/scenario-plan.schema.json \
 && chmod 0444 /opt/zkdeal/capability.json \
 && mkdir -p /evidence \
 && chown 65532:65532 /evidence

LABEL org.opencontainers.image.title="zkdeal-owner-acceptance-runner" \
      org.opencontainers.image.description="Independent source-bound hosted acceptance verifier" \
      org.zkdeal.acceptance.source.sha256="$ACCEPTANCE_RUNNER_SOURCE_SHA256" \
      org.opencontainers.image.licenses="MIT"

USER 65532:65532
WORKDIR /work
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ACCEPTANCE_EVIDENCE_DIR=/evidence
ENTRYPOINT ["/opt/zkdeal-acceptance"]
