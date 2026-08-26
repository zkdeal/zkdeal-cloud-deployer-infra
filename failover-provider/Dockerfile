FROM docker:27-cli@sha256:851f91d241214e7c6db86513b270d58776379aacc5eb9c4a87e5b47115e3065c AS docker-cli

FROM python:3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
RUN wget -q -O /usr/local/bin/kubectl https://dl.k8s.io/release/v1.32.2/bin/linux/amd64/kubectl \
 && echo "4f6a959dcc5b702135f8354cc7109b542a2933c46b808b248a214c1f69f817ea  /usr/local/bin/kubectl" | sha256sum -c - \
 && chmod 0555 /usr/local/bin/kubectl

COPY failover-provider/provider.py /opt/zkdeal/provider.py
COPY promotion-controller/failover-provider-v1.openapi.json /opt/zkdeal/failover-provider-v1.openapi.json
COPY zkdeal-BUSL-1.1-LICENSE /zkdeal-BUSL-1.1-LICENSE

RUN addgroup -S -g 65532 zkdeal \
 && adduser -S -D -H -u 65532 -G zkdeal zkdeal \
 && mkdir -p /var/lib/zkdeal-failover-provider \
    /run/zkdeal-provider/secrets /run/zkdeal-provider/tls \
    /var/run/secrets/kubernetes.io/serviceaccount \
 && chown -R 65532:65532 /var/lib/zkdeal-failover-provider /run/zkdeal-provider \
 && chmod 0555 /opt/zkdeal/provider.py \
 && chmod 0444 /opt/zkdeal/failover-provider-v1.openapi.json

LABEL org.opencontainers.image.licenses="BUSL-1.1"

USER 65532:65532
EXPOSE 8443
ENTRYPOINT ["python", "/opt/zkdeal/provider.py"]
