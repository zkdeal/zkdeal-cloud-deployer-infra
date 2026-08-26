#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."
project=zkdeal-edge-acceptance
file=compose/compose.edge.test.yaml
network=${project}_edge
curl_image=curlimages/curl@sha256:4026b29997dc7c823b51c164b71e2b51e0fd95cce4601f78202c513d97da2922

cleanup() {
  docker compose --project-name "$project" -f "$file" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
cleanup
if ! docker compose --project-name "$project" -f "$file" up -d --wait; then
  docker compose --project-name "$project" -f "$file" logs --no-color front-door >&2 || true
  exit 1
fi

curl_edge() {
  docker run --rm --network "$network" "$curl_image" "$@"
}

response=$(curl_edge -sS -i -H 'X-Request-ID: attacker-controlled' -H 'X-Forwarded-For: 203.0.113.66' http://front-door:8088/echo)
request_id=$(printf '%s\n' "$response" | sed -n 's/^[Xx]-[Rr]equest-[Ii][Dd]: *\([^\r]*\).*/\1/p' | tail -n 1)
[ -n "$request_id" ] && [ "$request_id" != attacker-controlled ] || { echo "edge reused a spoofed correlation ID" >&2; exit 1; }
body=$(printf '%s\n' "$response" | sed -n '/^{/,$p')
printf '%s' "$body" | python -c 'import json,sys; v=json.load(sys.stdin); assert v["writer"]=="active"; assert v["requestId"] not in (None,"attacker-controlled"); assert "203.0.113.66" not in (v["forwardedFor"] or "")'
printf '%s' "$body" | grep -Fq "$request_id" || { echo "response and upstream correlation IDs differ" >&2; exit 1; }

for route in /metrics /hosting/v1/admin/promote /hosting/v1/indexer/blocks /hosting/v1/admissions/1/lease; do
  status=$(curl_edge -sS -o /dev/null -w '%{http_code}' "http://front-door:8088$route")
  [ "$status" = 404 ] || { echo "private edge route was exposed: $route -> $status" >&2; exit 1; }
done
status=$(curl_edge -sS -o /dev/null -w '%{http_code}' 'http://front-door:8088/?q=%3Cscript%3E')
[ "$status" = 403 ] || { echo "hostile query was not blocked" >&2; exit 1; }
status=$(head -c 2100000 /dev/zero | docker run --rm -i --network "$network" "$curl_image" -sS -o /dev/null -w '%{http_code}' -X POST --data-binary @- http://front-door:8088/hosting/v1/json-rpc)
[ "$status" = 413 ] || { echo "oversized body was not rejected: $status" >&2; exit 1; }

first_cache=$(curl_edge -sS -D - -o /dev/null http://front-door:8088/docs/ | sed -n 's/^[Xx]-[Cc]ache-[Ss]tatus: *\([^\r]*\).*/\1/p')
second_cache=$(curl_edge -sS -D - -o /dev/null http://front-door:8088/docs/ | sed -n 's/^[Xx]-[Cc]ache-[Ss]tatus: *\([^\r]*\).*/\1/p')
[ "$first_cache" = MISS ] && [ "$second_cache" = HIT ] || { echo "docs cache did not transition MISS->HIT: $first_cache/$second_cache" >&2; exit 1; }

sse=$(curl_edge -sS -i --no-buffer -H 'Last-Event-ID: 41' http://front-door:8088/hosting/v1/events)
printf '%s' "$sse" | grep -qi '^X-Accel-Buffering: no' || { echo "SSE buffering was not disabled" >&2; exit 1; }
printf '%s' "$sse" | grep -qi '^Cache-Control: no-store' || { echo "SSE cache was not disabled" >&2; exit 1; }
printf '%s' "$sse" | grep -q 'id: 42' || { echo "SSE event was not flushed" >&2; exit 1; }
printf '%s' "$sse" | grep -q 'resumed=41' || { echo "Last-Event-ID was not forwarded" >&2; exit 1; }

statuses=$(docker run --rm --network "$network" --entrypoint /bin/sh "$curl_image" -ec 'i=0; while [ "$i" -lt 100 ]; do curl -sS -o /dev/null -w "%{http_code}\n" http://front-door:8088/health; i=$((i+1)); done')
printf '%s\n' "$statuses" | grep -q '^429$' || { echo "edge rate limit did not activate" >&2; exit 1; }

active=$(docker compose --project-name "$project" -f "$file" ps -q writer-active)
standby=$(docker compose --project-name "$project" -f "$file" ps -q writer-standby)
docker stop "$active" >/dev/null
docker network disconnect "$network" "$active" >/dev/null 2>&1 || true
failure_status=$(curl_edge -sS --max-time 5 -o /dev/null -w '%{http_code}' http://front-door:8088/health || true)
case "$failure_status" in 502|503|504|000) ;; *) echo "writer failure did not fail closed: $failure_status" >&2; exit 1 ;; esac

docker network disconnect "$network" "$standby" >/dev/null
docker network connect --alias coordinator-writer "$network" "$standby"
attempt=0
while :; do
  promoted=$(curl_edge -sS --max-time 3 http://front-door:8088/health 2>/dev/null || true)
  printf '%s' "$promoted" | grep -q '"writer": "standby"' && break
  attempt=$((attempt + 1)); [ "$attempt" -lt 20 ] || { echo "edge did not route to the promoted writer alias" >&2; exit 1; }
  sleep 1
done

echo "front-door acceptance passed: CIDR/XFF and correlation isolation, private routes, WAF/body/rate/cache controls, SSE resume/flush, bounded failure and promoted-writer reroute"
