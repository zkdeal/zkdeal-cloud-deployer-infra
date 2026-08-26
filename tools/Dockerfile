FROM docker:27-cli@sha256:851f91d241214e7c6db86513b270d58776379aacc5eb9c4a87e5b47115e3065c AS docker-cli
FROM minio/mc@sha256:aead63c77f9db9107f1696fb08ecb0faeda23729cde94b0f663edf4fe09728e3 AS minio-client
FROM alpine/helm@sha256:e7ecbf4a200dea73d64bfb8cb0936829164945f2b4d02a0274093073ee8d264f AS helm-client

FROM python:3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d

# The socket is mounted only by the separately profiled orchestrator service.
# Keeping the pinned CLI in the image lets that service inspect the exact image
# IDs used for evidence and launch isolated render/acceptance containers. The
# default deployment-tools service still has no daemon access.
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins /usr/local/libexec/docker/cli-plugins
COPY --from=minio-client /usr/bin/mc /usr/local/bin/mc
COPY --from=helm-client /usr/bin/helm /usr/local/bin/helm
COPY tools/requirements.lock /tmp/requirements.lock
COPY zkdeal-BUSL-1.1-LICENSE /zkdeal-BUSL-1.1-LICENSE

RUN wget -q -O /usr/local/bin/kind https://github.com/kubernetes-sigs/kind/releases/download/v0.27.0/kind-linux-amd64 \
 && echo "a6875aaea358acf0ac07786b1a6755d08fd640f4c79b7a2e46681cc13f49a04b  /usr/local/bin/kind" | sha256sum -c - \
 && wget -q -O /usr/local/bin/kubectl https://dl.k8s.io/release/v1.32.2/bin/linux/amd64/kubectl \
 && echo "4f6a959dcc5b702135f8354cc7109b542a2933c46b808b248a214c1f69f817ea  /usr/local/bin/kubectl" | sha256sum -c - \
 && wget -q -O /tmp/kurtosis.tgz https://github.com/kurtosis-tech/kurtosis-cli-release-artifacts/releases/download/1.20.0/kurtosis-cli_1.20.0_linux_amd64.tar.gz \
 && echo "3866e805a83a63d3d0b177f54c3fb556066b8c0e12f6442a0579bb7deaa1d5ae  /tmp/kurtosis.tgz" | sha256sum -c - \
 && tar -C /usr/local/bin -xzf /tmp/kurtosis.tgz kurtosis \
 && rm -f /tmp/kurtosis.tgz \
 && chmod 0555 /usr/local/bin/kind /usr/local/bin/kubectl /usr/local/bin/helm /usr/local/bin/mc /usr/local/bin/kurtosis \
 && pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock \
 && rm -f /tmp/requirements.lock

LABEL org.opencontainers.image.licenses="BUSL-1.1"

WORKDIR /workspace/cloud-deployer-infra
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python"]
