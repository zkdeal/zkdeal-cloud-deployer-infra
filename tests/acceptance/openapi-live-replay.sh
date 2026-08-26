#!/bin/sh
set -eu

test -f /.dockerenv || { echo 'ERROR: Docker-only acceptance' >&2; exit 2; }

prefix="zkdeal-openapi-replay-$$"
network="$prefix-net"
image="$prefix-fixture"
server="$prefix-server"
runner="$(hostname)"
connected=0

cleanup() {
  if [ "$connected" = 1 ]; then docker network disconnect -f "$network" "$runner" >/dev/null 2>&1 || true; fi
  docker rm -f "$server" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  docker image rm "$image" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker build --pull=false -f tests/fixtures/OpenApiReplay.Dockerfile -t "$image" . >/dev/null
docker network create --internal "$network" >/dev/null
docker run -d --rm --name "$server" --network "$network" --network-alias owner-api \
  "$image" --port 8090 --token acceptance-token >/dev/null
docker network connect "$network" "$runner"
connected=1

attempt=0
until python -c 'import urllib.request; urllib.request.urlopen("http://owner-api:8090/hosting/v1/health", timeout=2).read()' 2>/dev/null; do
  attempt=$((attempt + 1))
  test "$attempt" -lt 20 || { echo 'ERROR: OpenAPI fixture never became healthy' >&2; exit 1; }
  sleep 1
done

if ZKDEAL_OPENAPI_REPLAY_TOKEN=wrong-token python scripts/openapi_live_replay.py \
  --static tests/fixtures/hosting-v1.openapi.fixture.json \
  --examples tests/fixtures/openapi-replay-examples.fixture.json \
  --base-url http://owner-api:8090 >/tmp/zkdeal-openapi-wrong-token.out 2>/tmp/zkdeal-openapi-wrong-token.err; then
  echo 'ERROR: live replay accepted the wrong bearer token' >&2
  exit 1
fi
grep -q 'returned HTTP 401' /tmp/zkdeal-openapi-wrong-token.err

ZKDEAL_OPENAPI_REPLAY_TOKEN=acceptance-token python scripts/openapi_live_replay.py \
  --static tests/fixtures/hosting-v1.openapi.fixture.json \
  --examples tests/fixtures/openapi-replay-examples.fixture.json \
  --base-url http://owner-api:8090

printf '{"openapiReplayHarness":"passed","fixtureOnly":true,"staticLiveEqual":true,"schemaValidated":true,"anonymousDenied":true,"wrongBearerRejected":true,"idempotentMutationReplayed":true,"ownerReleaseReplayPending":true}\n'
