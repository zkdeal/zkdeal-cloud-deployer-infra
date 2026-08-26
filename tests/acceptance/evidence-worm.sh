#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."
project=zkdeal-evidence-worm-acceptance
compose_file=compose/compose.evidence-worm.test.yaml
tools_image=zkdeal-deployment-tools:local
mc_image=minio/mc@sha256:aead63c77f9db9107f1696fb08ecb0faeda23729cde94b0f663edf4fe09728e3
network=${project}_worm
bucket=zkdeal-evidence-worm-acceptance
endpoint=http://evidence-minio:9000
root=$(pwd)
seal_key=$(python -c 'import secrets; print(secrets.token_hex(32))')

cleanup() {
  docker compose --project-name "$project" -f "$compose_file" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
cleanup
docker compose --project-name "$project" -f "$compose_file" up -d --wait evidence-minio

run_tools() {
  docker run --rm --network "$network" \
    -v "$root:/workspace/cloud-deployer-infra:rw" \
    -w /workspace/cloud-deployer-infra \
    -e EVIDENCE_SEALING_KEY_HEX="$seal_key" \
    -e EVIDENCE_WORM_ENDPOINT="$endpoint" \
    -e EVIDENCE_WORM_BUCKET="$bucket" \
    -e EVIDENCE_WORM_ACCESS_KEY=evidence-worm-admin \
    -e EVIDENCE_WORM_SECRET_KEY=evidence-worm-acceptance-password \
    -e EVIDENCE_WORM_ADMIN_ACCESS_KEY=evidence-worm-admin \
    -e EVIDENCE_WORM_ADMIN_SECRET_KEY=evidence-worm-acceptance-password \
    --read-only --cap-drop ALL --security-opt no-new-privileges:true \
    --tmpfs /tmp:rw,size=32m,mode=1777 \
    "$tools_image" scripts/evidence_closure.py "$@"
}

run_tools provision --allow-http-local --retention-mode COMPLIANCE --retention-duration 1d
seal_json=$(run_tools seal)
manifest=$(printf '%s' "$seal_json" | python -c 'import json,sys; print(json.load(sys.stdin)["manifest"])')
mac=$(printf '%s' "$seal_json" | python -c 'import json,sys; print(json.load(sys.stdin)["hmac"])')
run_tools verify --manifest "$manifest" --hmac "$mac" --evidence-root evidence >/dev/null
receipt=$(run_tools publish --allow-http-local --retention-mode COMPLIANCE --retention-duration 1d --manifest "$manifest" --hmac "$mac")

manifest_key=$(printf '%s' "$receipt" | python -c 'import json,sys; print(json.load(sys.stdin)["objects"]["manifest"]["key"])')
manifest_version=$(printf '%s' "$receipt" | python -c 'import json,sys; print(json.load(sys.stdin)["objects"]["manifest"]["versionId"])')
manifest_hash=$(printf '%s' "$receipt" | python -c 'import json,sys; print(json.load(sys.stdin)["objects"]["manifest"]["sha256"])')
observed=$(printf '%s' "$receipt" | python -c 'import json,sys; print(str(json.load(sys.stdin)["objectLockMetadataObserved"]["manifest"]).lower())')
[ "$observed" = true ] || { echo "publication did not observe object-lock metadata" >&2; exit 1; }

mc_run() {
  docker run --rm --network "$network" \
    -e MC_HOST_worm=http://evidence-worm-admin:evidence-worm-acceptance-password@evidence-minio:9000 \
    "$mc_image" "$@"
}

retrieved_hash=$(mc_run cat --version-id "$manifest_version" "worm/$bucket/$manifest_key" | sha256sum | awk '{print $1}')
[ "$retrieved_hash" = "$manifest_hash" ] || { echo "retrieved retained manifest hash changed" >&2; exit 1; }

# COMPLIANCE mode must reject deletion of the exact published version, even by
# the bucket administrator.
if mc_run rm --version-id "$manifest_version" "worm/$bucket/$manifest_key" >/dev/null 2>&1; then
  echo "COMPLIANCE object lock allowed retained-version deletion" >&2
  exit 1
fi

# S3 versioning permits a new version at an existing key.  The deployment
# publisher adds the stronger logical rule: content-addressed keys must retain
# identical bytes, so it refuses to publish over this conflicting latest
# version.  The original locked version remains independently retrievable.
printf 'conflicting-bytes-must-be-refused\n' | mc_run pipe "worm/$bucket/$manifest_key" >/dev/null
if run_tools publish --allow-http-local --retention-mode COMPLIANCE --retention-duration 1d --manifest "$manifest" --hmac "$mac" >/dev/null 2>&1; then
  echo "publisher accepted conflicting bytes at a content-addressed key" >&2
  exit 1
fi
retrieved_hash=$(mc_run cat --version-id "$manifest_version" "worm/$bucket/$manifest_key" | sha256sum | awk '{print $1}')
[ "$retrieved_hash" = "$manifest_hash" ] || { echo "original retained version was not recoverable" >&2; exit 1; }

if grep -R -F "$seal_key" evidence/closures >/dev/null 2>&1; then
  echo "evidence sealing key was written beside evidence" >&2
  exit 1
fi

echo "WORM evidence acceptance passed: content address/HMAC verified, COMPLIANCE delete rejected, logical overwrite rejected, retained version hash retrieved"
