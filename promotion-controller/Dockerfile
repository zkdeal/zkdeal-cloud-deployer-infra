FROM python:3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d

COPY --chown=65532:65532 scripts/promotion_controller.py /opt/zkdeal/promotion_controller.py
COPY zkdeal-BUSL-1.1-LICENSE /zkdeal-BUSL-1.1-LICENSE

LABEL org.opencontainers.image.licenses="BUSL-1.1"

USER 65532:65532
WORKDIR /tmp
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "/opt/zkdeal/promotion_controller.py"]
