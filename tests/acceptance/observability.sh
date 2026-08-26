#!/bin/sh
set -eu
export BUILDX_GIT_INFO=false

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."
files="-f compose/compose.observability.test.yaml"
project="zkdeal-observability-${HOSTNAME:-container}"

cleanup() {
  docker compose --project-name "$project" $files down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
cleanup

prometheus_base=${PROMETHEUS_IMAGE:-prom/prometheus@sha256:63805ebb8d2b3920190daf1cb14a60871b16fd38bed42b857a3182bc621f4996}
alertmanager_base=${ALERTMANAGER_IMAGE:-prom/alertmanager@sha256:27c475db5fb156cab31d5c18a4251ac7ed567746a2483ff264516437a39b15ba}
python_base=${PYTHON_IMAGE:-python@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d}
build_args="--build-arg PROMETHEUS_BASE=$prometheus_base --build-arg ALERTMANAGER_BASE=$alertmanager_base --build-arg PYTHON_BASE=$python_base"
docker build --pull=false $build_args --target metrics-fixture -f tests/fixtures/Observability.Dockerfile -t zkdeal-observability-metrics:acceptance .
docker build --pull=false $build_args --target webhook -f tests/fixtures/Observability.Dockerfile -t zkdeal-observability-webhook:acceptance .
docker build --pull=false $build_args --target alertmanager -f tests/fixtures/Observability.Dockerfile -t zkdeal-observability-alertmanager:acceptance .
docker build --pull=false $build_args --target prometheus -f tests/fixtures/Observability.Dockerfile -t zkdeal-observability-prometheus:acceptance .

docker compose --project-name "$project" $files config --quiet
docker compose --project-name "$project" $files up -d

docker compose --project-name "$project" $files exec -T prometheus-test \
  promtool check config /etc/prometheus/prometheus.yml
docker compose --project-name "$project" $files exec -T prometheus-test \
  promtool check rules /etc/prometheus/owner-alerts.yml /etc/prometheus/local-alerts.yml /etc/prometheus/acceptance-alert.yml
docker compose --project-name "$project" $files exec -T alertmanager-test \
  amtool check-config /etc/alertmanager/alertmanager.yml

events() {
  docker compose --project-name "$project" $files exec -T metrics-fixture python -c \
    'import urllib.request; print(urllib.request.urlopen("http://webhook-test:9080/events", timeout=2).read().decode())'
}

wait_for_status() {
  wanted=$1
  attempt=0
  while [ "$attempt" -lt 45 ]; do
    if events 2>/dev/null | grep -q "ZkdealAcceptanceSignal" \
      && events 2>/dev/null | grep -q "\"status\": \"$wanted\""; then
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 1
  done
  echo "timed out waiting for $wanted Alertmanager webhook" >&2
  events >&2 || true
  return 1
}

wait_for_status firing
docker compose --project-name "$project" $files exec -T metrics-fixture python -c \
  'import urllib.request; urllib.request.urlopen("http://127.0.0.1:9100/set?value=0", timeout=2).read()'
wait_for_status resolved

printf '%s\n' '{"observability":"passed","promtool":true,"alertmanagerConfig":true,"webhookFired":true,"webhookRecovered":true,"payloadStorage":"sanitized-bounded-summary"}'
