#!/bin/sh
set -eu
export BUILDX_GIT_INFO=false

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."

image=${OWNER_IMAGE_TAG:-zkdeal-coordinator:acceptance}
# Current pruned owner artifact is 1,019,212,667 bytes. The 1.18 GB ceiling
# leaves bounded rebuild variance without accepting the historical 3.37 GB image.
max_bytes=${OWNER_IMAGE_MAX_BYTES:-1180000000}
reuse_id=${OWNER_IMAGE_REUSE_ID:-}

case "$max_bytes" in
  ''|*[!0-9]*) echo "ERROR: OWNER_IMAGE_MAX_BYTES must be an integer" >&2; exit 2 ;;
esac

source_rebuilt=true
if [ -n "$reuse_id" ]; then
  source_rebuilt=false
  image_id=$(docker image inspect "$image" --format '{{.Id}}' 2>/dev/null || true)
  if [ -z "$image_id" ] || [ "$image_id" != "$reuse_id" ]; then
    echo "ERROR: requested owner-image reuse ID $reuse_id does not equal local $image ID ${image_id:-missing}" >&2
    exit 1
  fi
else
  docker build --pull=false -f ../web2-api/server/Dockerfile -t "$image" ..
fi
image_bytes=$(docker image inspect "$image" --format '{{.Size}}')
image_id=$(docker image inspect "$image" --format '{{.Id}}')
case "$image_bytes" in
  ''|*[!0-9]*) echo "ERROR: Docker returned an invalid owner image size" >&2; exit 2 ;;
esac
if [ "$image_bytes" -gt "$max_bytes" ]; then
  echo "ERROR: owner runtime image is ${image_bytes} bytes; explicit ceiling is ${max_bytes} bytes. Reduce runtime layers before kind transfer." >&2
  exit 1
fi

policy_sha=$(sha256sum ../.dockerignore | awk '{print $1}')
printf '{"ownerImage":"%s","imageId":"%s","imageBytes":%s,"maxBytes":%s,"umbrellaDockerignoreSha256":"%s","sourceRebuilt":%s}\n' \
  "$image" "$image_id" "$image_bytes" "$max_bytes" "$policy_sha" "$source_rebuilt"
