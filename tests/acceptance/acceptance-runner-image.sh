#!/bin/sh
set -eu

image=${ACCEPTANCE_RUNNER_IMAGE_TAG:-zkdeal-acceptance-runner:acceptance}
source_sha=$(cat \
  acceptance-runner/zkdeal_acceptance.py \
  acceptance-runner/scenario-plan.schema.json \
  acceptance-runner/capability.json | sha256sum | awk '{print $1}')

docker build --pull=false --file acceptance-runner/Dockerfile \
  --build-arg "ACCEPTANCE_RUNNER_SOURCE_SHA256=$source_sha" \
  --tag "$image" . >/dev/null

label=$(docker image inspect "$image" --format '{{index .Config.Labels "org.zkdeal.acceptance.source.sha256"}}')
[ "$label" = "$source_sha" ] || { echo "acceptance runner source label differs" >&2; exit 1; }
user=$(docker image inspect "$image" --format '{{.Config.User}}')
[ "$user" = "65532:65532" ] || { echo "acceptance runner is not numeric non-root" >&2; exit 1; }
entrypoint=$(docker image inspect "$image" --format '{{json .Config.Entrypoint}}')
[ "$entrypoint" = '["/opt/zkdeal-acceptance"]' ] || { echo "acceptance runner entrypoint differs" >&2; exit 1; }
image_bytes=$(docker image inspect "$image" --format '{{.Size}}')
max_bytes=${ACCEPTANCE_RUNNER_MAX_IMAGE_BYTES:-100000000}
[ "$image_bytes" -le "$max_bytes" ] || { echo "acceptance runner image exceeds budget" >&2; exit 1; }

docker run --rm --read-only --network none --entrypoint python "$image" -c '
import json
value=json.load(open("/opt/zkdeal/capability.json", encoding="utf-8"))
assert value["entrypoint"] == "/opt/zkdeal-acceptance"
assert len(value["scenarios"]) == 18
assert value["candidatePreflight"]["requiresExactHostedIntegrationToken"] is True
assert value["credentials"]["planHashBound"] is True
assert value["evidence"]["fixtureRunsAreReleaseEvidence"] is False
' >/dev/null

if docker run --rm --read-only --network none "$image" \
  reorg --assert-rollback-and-retraction >/dev/null 2>&1; then
  echo "acceptance runner accepted a missing candidate plan" >&2
  exit 1
fi

for command in curl docker kubectl psql; do
  if docker run --rm --entrypoint /bin/sh "$image" -c "command -v $command" >/dev/null 2>&1; then
    echo "acceptance runner unexpectedly contains external authority tool: $command" >&2
    exit 1
  fi
done

printf '{"acceptanceRunnerImage":"%s","imageId":"%s","imageBytes":%s,"sourceSha256":"%s","numericUser":true,"capabilityScenarios":18,"missingCandidateRejected":true,"externalAuthorityToolsAbsent":true,"releaseEvidence":false}\n' \
  "$image" "$(docker image inspect "$image" --format '{{.Id}}')" "$image_bytes" "$source_sha"
