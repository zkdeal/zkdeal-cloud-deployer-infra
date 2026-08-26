FROM python:3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d
COPY tests/fixtures/failover_platform_fixture.py /opt/zkdeal/fixture.py
USER 65532:65532
ENTRYPOINT ["python", "/opt/zkdeal/fixture.py"]
