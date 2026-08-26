#!/bin/sh
set -eu
export BUILDX_GIT_INFO=false

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."

image=${HEADLESS_IMAGE_TAG:-zkdeal-headless-room-node:acceptance}
# Current owner artifact is 590,064,130 bytes; 680 MB is bounded headroom.
max_bytes=${HEADLESS_IMAGE_MAX_BYTES:-680000000}

case "$max_bytes" in
  ''|*[!0-9]*) echo "ERROR: HEADLESS_IMAGE_MAX_BYTES must be an integer" >&2; exit 2 ;;
esac

docker build --pull=false -f ../app-node/packages/room-node/Dockerfile -t "$image" ..
image_bytes=$(docker image inspect "$image" --format '{{.Size}}')
image_id=$(docker image inspect "$image" --format '{{.Id}}')
case "$image_bytes" in
  ''|*[!0-9]*) echo "ERROR: Docker returned an invalid headless image size" >&2; exit 2 ;;
esac
if [ "$image_bytes" -gt "$max_bytes" ]; then
  echo "ERROR: headless runtime image is ${image_bytes} bytes; explicit ceiling is ${max_bytes} bytes" >&2
  exit 1
fi

capability_sha=$(sha256sum ../app-node/packages/room-node/capabilities/room-node.json | awk '{print $1}')
printf '{"headlessImage":"%s","imageId":"%s","imageBytes":%s,"maxBytes":%s,"capabilitySha256":"%s"}\n' \
  "$image" "$image_id" "$image_bytes" "$max_bytes" "$capability_sha"
