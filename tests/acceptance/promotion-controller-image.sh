#!/bin/sh
set -eu

image=${PROMOTION_CONTROLLER_IMAGE_TAG:-zkdeal-promotion-controller:acceptance}
max_bytes=${PROMOTION_CONTROLLER_IMAGE_MAX_BYTES:-80000000}

docker build --pull=false --tag "$image" \
  --file promotion-controller/Dockerfile . >/dev/null

image_id=$(docker image inspect "$image" --format '{{.Id}}')
image_bytes=$(docker image inspect "$image" --format '{{.Size}}')
user=$(docker image inspect "$image" --format '{{.Config.User}}')
entrypoint=$(docker image inspect "$image" --format '{{json .Config.Entrypoint}}')
[ "$user" = "65532:65532" ] || { echo "promotion controller image has wrong user: $user" >&2; exit 1; }
[ "$entrypoint" = '["python","/opt/zkdeal/promotion_controller.py"]' ] || {
  echo "promotion controller image has wrong entrypoint: $entrypoint" >&2
  exit 1
}
case "$image_bytes" in ''|*[!0-9]*) echo "invalid image size" >&2; exit 1;; esac
[ "$image_bytes" -le "$max_bytes" ] || {
  echo "promotion controller image exceeds ${max_bytes} bytes: ${image_bytes}" >&2
  exit 1
}

docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --entrypoint /bin/sh "$image" -ec \
  '! command -v docker && ! command -v kubectl && ! command -v psql' >/dev/null

if docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  "$image" >/dev/null 2>&1
then
  echo "promotion controller started without its required configuration" >&2
  exit 1
fi

printf '{"promotionControllerImage":"%s","imageId":"%s","imageBytes":%s,"maxBytes":%s,"numericUser":"%s","noDockerKubectlPsql":true,"emptyConfigRejected":true}\n' \
  "$image" "$image_id" "$image_bytes" "$max_bytes" "$user"
