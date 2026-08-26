ARG PROMETHEUS_BASE=prom/prometheus@sha256:63805ebb8d2b3920190daf1cb14a60871b16fd38bed42b857a3182bc621f4996
ARG ALERTMANAGER_BASE=prom/alertmanager@sha256:27c475db5fb156cab31d5c18a4251ac7ed567746a2483ff264516437a39b15ba
ARG PYTHON_BASE=python@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d

FROM ${PROMETHEUS_BASE} AS prometheus
COPY tests/fixtures/prometheus-acceptance.yml /etc/prometheus/prometheus.yml
COPY tests/fixtures/prometheus-acceptance-alert.yml /etc/prometheus/acceptance-alert.yml
COPY observability/prometheus/alerts.yml /etc/prometheus/owner-alerts.yml
COPY observability/prometheus/alerts.local.yml /etc/prometheus/local-alerts.yml

FROM ${ALERTMANAGER_BASE} AS alertmanager
COPY tests/fixtures/alertmanager-acceptance.yml /etc/alertmanager/alertmanager.yml

FROM ${PYTHON_BASE} AS metrics-fixture
COPY tests/fixtures/metrics_fixture.py /app/metrics_fixture.py
CMD ["python", "/app/metrics_fixture.py"]

FROM ${PYTHON_BASE} AS webhook
COPY observability/webhook-receiver.py /app/webhook-receiver.py
CMD ["python", "/app/webhook-receiver.py"]
