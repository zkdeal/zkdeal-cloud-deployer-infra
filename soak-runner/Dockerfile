# OWNER_SOAK_DRIVER_IMAGE must be a global meta-arg (declared before the first
# FROM) so the candidate stage's FROM interpolation can see its default; the
# default `scratch` keeps the unused owner-driver stage parseable for the
# non-candidate acceptance build while the candidate target still fails closed
# without a real owner image plus matching source hash.
ARG OWNER_SOAK_DRIVER_IMAGE=scratch

FROM python:3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d AS base

COPY soak-runner/zkdeal_soak.py /opt/zkdeal/zkdeal_soak.py
COPY scripts/common.py /opt/zkdeal/common.py
COPY scripts/soak.py /opt/zkdeal/soak.py
COPY config/schemas/release-soak-manifest.schema.json /opt/zkdeal/release-soak-manifest.schema.json
COPY LICENSE /LICENSE

ARG SOAK_RUNNER_SOURCE_SHA256
RUN test -n "$SOAK_RUNNER_SOURCE_SHA256" \
 && actual="$(cat /opt/zkdeal/zkdeal_soak.py /opt/zkdeal/common.py /opt/zkdeal/soak.py /opt/zkdeal/release-soak-manifest.schema.json | sha256sum | awk '{print $1}')" \
 && test "$actual" = "$SOAK_RUNNER_SOURCE_SHA256" \
 && addgroup -S -g 65532 zkdeal \
 && adduser -S -D -H -u 65532 -G zkdeal zkdeal \
 && ln -s /opt/zkdeal/zkdeal_soak.py /opt/zkdeal-soak \
 && chmod 0555 /opt/zkdeal/zkdeal_soak.py /opt/zkdeal/soak.py \
 && chmod 0444 /opt/zkdeal/common.py /opt/zkdeal/release-soak-manifest.schema.json \
 && mkdir -p /evidence \
 && chown 65532:65532 /evidence

LABEL org.opencontainers.image.title="zkdeal-soak-runner" \
      org.opencontainers.image.description="Restart-safe owner lifecycle soak orchestrator and closure verifier" \
      org.zkdeal.soak-runner.source.sha256="$SOAK_RUNNER_SOURCE_SHA256" \
      org.opencontainers.image.licenses="MIT"

USER 65532:65532
WORKDIR /work
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SOAK_EVIDENCE_DIR=/evidence
ENTRYPOINT ["/opt/zkdeal-soak"]

# --- candidate target ---------------------------------------------------------
# The release soak requires the broad-sealed owner driver at
# /opt/zkdeal-owner-soak. This repository never synthesizes that driver: the
# `candidate` target copies it from the exact digest-pinned owner image named
# by OWNER_SOAK_DRIVER_IMAGE and fails closed unless the copied bytes match
# the declared owner source SHA-256. Build it explicitly with
# `--target candidate`; without the owner image and hash the build fails.
FROM ${OWNER_SOAK_DRIVER_IMAGE} AS owner-driver

FROM base AS candidate
USER 0:0
COPY --from=owner-driver /opt/zkdeal-owner-soak /opt/zkdeal-owner-soak
ARG OWNER_SOAK_DRIVER_SOURCE_SHA256
RUN test -n "$OWNER_SOAK_DRIVER_SOURCE_SHA256" \
 && test ! -L /opt/zkdeal-owner-soak \
 && actual="$(sha256sum /opt/zkdeal-owner-soak | awk '{print $1}')" \
 && test "$actual" = "$OWNER_SOAK_DRIVER_SOURCE_SHA256" \
 && chown 0:0 /opt/zkdeal-owner-soak \
 && chmod 0555 /opt/zkdeal-owner-soak
LABEL org.zkdeal.owner-soak-driver.source.sha256="$OWNER_SOAK_DRIVER_SOURCE_SHA256"
USER 65532:65532
WORKDIR /work
ENTRYPOINT ["/opt/zkdeal-soak"]

# Default target: the driver-free orchestrator. It is runnable only far enough
# to fail closed on the absent owner driver and is never release evidence.
FROM base AS runner
