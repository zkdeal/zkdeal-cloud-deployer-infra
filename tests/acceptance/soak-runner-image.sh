#!/bin/sh
set -eu

image=${SOAK_RUNNER_IMAGE_TAG:-zkdeal-soak-runner:acceptance}
source_sha=$(cat \
  soak-runner/zkdeal_soak.py \
  scripts/common.py \
  scripts/soak.py \
  config/schemas/release-soak-manifest.schema.json | sha256sum | awk '{print $1}')

docker build --pull=false --file soak-runner/Dockerfile \
  --build-arg "SOAK_RUNNER_SOURCE_SHA256=$source_sha" \
  --tag "$image" . >/dev/null

label=$(docker image inspect "$image" --format '{{index .Config.Labels "org.zkdeal.soak-runner.source.sha256"}}')
[ "$label" = "$source_sha" ] || { echo "soak runner source label differs" >&2; exit 1; }
user=$(docker image inspect "$image" --format '{{.Config.User}}')
[ "$user" = "65532:65532" ] || { echo "soak runner is not numeric non-root" >&2; exit 1; }
entrypoint=$(docker image inspect "$image" --format '{{json .Config.Entrypoint}}')
[ "$entrypoint" = '["/opt/zkdeal-soak"]' ] || { echo "soak runner entrypoint differs" >&2; exit 1; }
image_bytes=$(docker image inspect "$image" --format '{{.Size}}')
max_bytes=${SOAK_RUNNER_MAX_IMAGE_BYTES:-70000000}
[ "$image_bytes" -le "$max_bytes" ] || { echo "soak runner image exceeds budget" >&2; exit 1; }

if docker run --rm "$image" \
  --submit-real-proof-jobs --restart \
  --assert-durable-results,cursors,nonces,charges,sealed-output,safety,claims,fairness,deadlines \
  --bounded-backoff --emit-evidence-closure >/dev/null 2>&1; then
  echo "soak runner accepted missing manifest/real owner driver/evidence configuration" >&2
  exit 1
fi

printf '{"soakRunnerImage":"%s","imageId":"%s","imageBytes":%s,"sourceSha256":"%s","numericUser":true,"missingOwnerDriverRejected":true,"releaseEvidence":false}\n' \
  "$image" "$(docker image inspect "$image" --format '{{.Id}}')" "$image_bytes" "$source_sha"
