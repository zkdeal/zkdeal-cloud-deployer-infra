FROM python:3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d
COPY tests/fixtures/openapi_replay_server.py /opt/zkdeal/openapi_replay_server.py
COPY tests/fixtures/hosting-v1.openapi.fixture.json /opt/zkdeal/hosting-v1.openapi.json
USER 65532:65532
ENTRYPOINT ["python", "/opt/zkdeal/openapi_replay_server.py", "--openapi", "/opt/zkdeal/hosting-v1.openapi.json"]
