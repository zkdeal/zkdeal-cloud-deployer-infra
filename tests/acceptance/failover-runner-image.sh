#!/bin/sh
set -eu

image=${FAILOVER_RUNNER_IMAGE_TAG:-zkdeal-failover-runner:acceptance}
source_sha=$(cat \
  failover-runner/zkdeal_failover.py \
  scripts/promotion_controller.py \
  acceptance-runner/zkdeal_acceptance.py \
  promotion-controller/failover-provider-v1.openapi.json | sha256sum | awk '{print $1}')

docker build --pull=false --file failover-runner/Dockerfile \
  --build-arg "FAILOVER_RUNNER_SOURCE_SHA256=$source_sha" \
  --tag "$image" . >/dev/null

label=$(docker image inspect "$image" --format '{{index .Config.Labels "org.zkdeal.failover-runner.source.sha256"}}')
[ "$label" = "$source_sha" ] || { echo "failover runner source label differs" >&2; exit 1; }
user=$(docker image inspect "$image" --format '{{.Config.User}}')
[ "$user" = "65532:65532" ] || { echo "failover runner is not numeric non-root" >&2; exit 1; }
entrypoint=$(docker image inspect "$image" --format '{{json .Config.Entrypoint}}')
[ "$entrypoint" = '["/opt/zkdeal-failover"]' ] || { echo "failover runner entrypoint differs" >&2; exit 1; }
image_bytes=$(docker image inspect "$image" --format '{{.Size}}')
max_bytes=${FAILOVER_RUNNER_MAX_IMAGE_BYTES:-100000000}
[ "$image_bytes" -le "$max_bytes" ] || { echo "failover runner image exceeds budget" >&2; exit 1; }

if docker run --rm "$image" \
  --terminate-active --persist-primary-target-lsn --assert-standby-replay \
  --promote --assert-stale-writer-denied --assert-rto-seconds 300 >/dev/null 2>&1; then
  echo "failover runner accepted missing provider/owner/candidate configuration" >&2
  exit 1
fi

for command in docker kubectl psql; do
  if docker run --rm --entrypoint /bin/sh "$image" -c "command -v $command" >/dev/null 2>&1; then
    echo "failover runner unexpectedly contains platform authority: $command" >&2
    exit 1
  fi
done

printf '{"failoverRunnerImage":"%s","imageId":"%s","imageBytes":%s,"sourceSha256":"%s","numericUser":true,"missingConfigRejected":true,"platformCredentialFree":true}\n' \
  "$image" "$(docker image inspect "$image" --format '{{.Id}}')" "$image_bytes" "$source_sha"
