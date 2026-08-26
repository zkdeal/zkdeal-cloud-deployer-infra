#!/bin/sh
set -eu
export BUILDX_GIT_INFO=false

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."

image=${PROVER_AGENT_IMAGE_TAG:-zkdeal-prover-agent:acceptance}
max_bytes=${PROVER_AGENT_IMAGE_MAX_BYTES:-420000000}
case "$max_bytes" in
  ''|*[!0-9]*) echo "ERROR: PROVER_AGENT_IMAGE_MAX_BYTES must be an integer" >&2; exit 2 ;;
esac

source_sha=$(cd ../prover-node && find agent/src -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}')
capability_sha=$(sha256sum ../prover-node/agent/liveness-capability.json | awk '{print $1}')
trace_capability_sha=$(sha256sum ../prover-node/agent/trace-capability.json | awk '{print $1}')
docker build --pull=false \
  --build-arg "AGENT_SOURCE_SHA256=$source_sha" \
  --build-arg "AGENT_CAPABILITY_SHA256=$capability_sha" \
  --build-arg "AGENT_TRACE_CAPABILITY_SHA256=$trace_capability_sha" \
  -f agent/Dockerfile -t "$image" ..

image_id=$(docker image inspect "$image" --format '{{.Id}}')
image_bytes=$(docker image inspect "$image" --format '{{.Size}}')
image_user=$(docker image inspect "$image" --format '{{.Config.User}}')
label_source=$(docker image inspect "$image" --format '{{index .Config.Labels "org.zkdeal.owner.source.sha256"}}')
label_capability=$(docker image inspect "$image" --format '{{index .Config.Labels "org.zkdeal.owner.liveness-capability.sha256"}}')
label_trace_capability=$(docker image inspect "$image" --format '{{index .Config.Labels "org.zkdeal.owner.trace-capability.sha256"}}')
test "$image_user" = "10001:10001"
test "$label_source" = "$source_sha"
test "$label_capability" = "$capability_sha"
test "$label_trace_capability" = "$trace_capability_sha"
test "$image_bytes" -le "$max_bytes" || {
  echo "ERROR: prover-agent runtime is ${image_bytes} bytes; ceiling is ${max_bytes}" >&2
  exit 1
}

# The packaged owner entrypoint must execute and fail closed before networking
# when its required hosted queue configuration is absent.
error_file=$(mktemp)
trap 'rm -f "$error_file"' EXIT INT TERM
if docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=8m "$image" 2>"$error_file"; then
  echo "ERROR: prover-agent started without QUEUE_URL" >&2
  exit 1
fi
grep -F 'QUEUE_URL is required' "$error_file" >/dev/null

printf '{"proverAgentImage":"%s","imageId":"%s","imageBytes":%s,"maxBytes":%s,"runtimeUser":"%s","ownerSourceSha256":"%s","livenessCapabilitySha256":"%s","traceCapabilitySha256":"%s","missingQueueFailClosed":true}\n' \
  "$image" "$image_id" "$image_bytes" "$max_bytes" "$image_user" "$source_sha" "$capability_sha" "$trace_capability_sha"
