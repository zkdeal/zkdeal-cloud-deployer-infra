FROM python:3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d
WORKDIR /app
COPY tests/fixtures/conformance_service.py /app/conformance_service.py
USER 65532:65532
ENTRYPOINT ["python", "/app/conformance_service.py"]
