#!/bin/sh
set -eu

root=/workspace/cloud-deployer-infra
state=$root/.state/oci-registry-acceptance
project=zkdeal-oci-registry-acceptance
source_image=python@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d
registry_image=registry@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373
curl_image=curlimages/curl@sha256:4026b29997dc7c823b51c164b71e2b51e0fd95cce4601f78202c513d97da2922
source_volume=zkdeal-oci-registry-acceptance
restore_volume=zkdeal-oci-registry-restore-acceptance
restore_container=zkdeal-oci-registry-restore-acceptance
key_dir=

cleanup() {
  docker rm -f "$restore_container" >/dev/null 2>&1 || true
  OCI_REGISTRY_VOLUME=$source_volume OCI_REGISTRY_PORT=5000 \
    docker compose --project-name "$project" -f compose/compose.registry.yaml down --remove-orphans >/dev/null 2>&1 || true
  docker volume rm "$source_volume" "$restore_volume" >/dev/null 2>&1 || true
  case "$state" in "$root"/.state/oci-registry-acceptance) rm -rf "$state" ;; *) exit 2 ;; esac
  case "${key_dir:-}" in "") : ;; /tmp/*) rm -rf "$key_dir" ;; *) exit 2 ;; esac
}
trap cleanup EXIT INT TERM
cleanup
mkdir -p "$state"
key_dir=$(mktemp -d)

docker image pull "$source_image" >/dev/null
OCI_REGISTRY_VOLUME=$source_volume OCI_REGISTRY_PORT=5000 \
  docker compose --project-name "$project" -f compose/compose.registry.yaml up -d --wait registry

python scripts/oci_registry.py publish \
  --source "$source_image" --candidate acceptance-20260821 --artifact tools-runtime \
  --registry localhost:5000 --output "$state/publication.json" >/dev/null
python scripts/oci_registry.py verify-image --manifest "$state/publication.json" >/dev/null

printf '%s' 'zkdeal-acceptance-promotion-mac-key-00000000000000000001' > "$key_dir/promotion.key"
chmod 0600 "$key_dir/promotion.key"
python -c 'import hashlib,json,pathlib; state=pathlib.Path("/workspace/cloud-deployer-infra/.state/oci-registry-acceptance"); publication=json.loads((state/"publication.json").read_text()); token="sha256:"+"a"*64; generated="b"*64; candidate={"schemaVersion":1,"candidateId":publication["candidate"],"ownerBroadSeal":{"hostedIntegrationAcceptanceToken":token},"zkvmGeneratedTrustRoot":{"generatedClosure":{"sha256":generated},"artifactLockSha256":"c"*64,"programId":"0x"+"d"*64},"images":{"prover":publication["immutableReference"]}}; (state/"candidate.json").write_text(json.dumps(candidate,sort_keys=True)+"\n"); composite={"schema":"zkdeal/4090-evidence-closure/v2","algorithm":"sha256","source":{"generatedTrustRootClosureSha256":generated},"physicalAcceptance":{"ownerAcceptanceToken":token},"artifactLockSha256":"c"*64,"runtimeImage":publication["immutableReference"],"programId":"0x"+"d"*64}; (state/"composite.json").write_text(json.dumps(composite,sort_keys=True)+"\n")'
python scripts/oci_registry.py promote \
  --manifest "$state/publication.json" --candidate-file "$state/candidate.json" \
  --composite-seal "$state/composite.json" --image-key prover \
  --release release-acceptance-20260821 --registry localhost:5000 \
  --key-file "$key_dir/promotion.key" --output "$state/promotion.json" >/dev/null
python scripts/oci_registry.py verify-promotion \
  --receipt "$state/promotion.json" --manifest "$state/publication.json" \
  --candidate-file "$state/candidate.json" --composite-seal "$state/composite.json" \
  --image-key prover --key-file "$key_dir/promotion.key" \
  --output "$state/promotion-verification.json" >/dev/null
if grep -Eq ':promotion-|zkdeal-acceptance-promotion-mac-key' "$state/promotion.json"; then
  echo "promotion receipt leaked a mutable tag or MAC key" >&2
  exit 1
fi
python -c 'import json,pathlib; source=pathlib.Path("/workspace/cloud-deployer-infra/.state/oci-registry-acceptance/promotion.json"); value=json.loads(source.read_text()); value["payload"]["sameDaemonImageId"]=False; pathlib.Path(str(source)+".tampered").write_text(json.dumps(value)+"\n")'
if python scripts/oci_registry.py verify-promotion \
  --receipt "$state/promotion.json.tampered" --manifest "$state/publication.json" \
  --candidate-file "$state/candidate.json" --composite-seal "$state/composite.json" \
  --image-key prover --key-file "$key_dir/promotion.key" >/dev/null 2>&1; then
  echo "promotion verifier accepted a tampered receipt" >&2
  exit 1
fi

if python scripts/oci_registry.py publish \
  --source "$source_image" --candidate acceptance-20260821 --artifact tools-runtime \
  --registry localhost:5000 --output "$state/replayed-publication.json" >/dev/null 2>&1; then
  echo "registry publisher reused a candidate transport reference" >&2
  exit 1
fi
if grep -Fq ':transfer-' "$state/publication.json"; then
  echo "publication evidence contains a mutable transport tag" >&2
  exit 1
fi

repository=$(python -c 'import json; print(json.load(open("/workspace/cloud-deployer-infra/.state/oci-registry-acceptance/publication.json"))["repositoryPath"])')
digest=$(python -c 'import json; print(json.load(open("/workspace/cloud-deployer-infra/.state/oci-registry-acceptance/publication.json"))["digest"])')
delete_status=$(docker run --rm --network "${project}_registry" "$curl_image" --silent --output /dev/null \
  --write-out '%{http_code}' --request DELETE "http://registry:5000/v2/$repository/manifests/$digest")
[ "$delete_status" = 405 ] || { echo "local registry did not refuse manifest deletion: $delete_status" >&2; exit 1; }

OCI_REGISTRY_VOLUME=$source_volume OCI_REGISTRY_PORT=5000 \
  docker compose --project-name "$project" -f compose/compose.registry.yaml stop registry >/dev/null
python scripts/oci_registry.py backup --volume "$source_volume" \
  --output "$state/registry.tar.gz" --manifest "$state/registry.tar.gz.manifest.json" \
  --publication-manifest "$state/publication.json" >/dev/null
python scripts/oci_registry.py verify-backup \
  --archive "$state/registry.tar.gz" --manifest "$state/registry.tar.gz.manifest.json" >/dev/null
python scripts/oci_registry.py restore \
  --archive "$state/registry.tar.gz" --manifest "$state/registry.tar.gz.manifest.json" \
  --volume "$restore_volume" --output "$state/restore.json" >/dev/null

docker run -d --name "$restore_container" --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true -p 127.0.0.1:5001:5000 \
  -e REGISTRY_STORAGE_DELETE_ENABLED=false -v "$restore_volume:/var/lib/registry" \
  "$registry_image" >/dev/null
attempt=0
until python scripts/oci_registry.py verify-image \
  --manifest "$state/publication.json" --registry localhost:5001 >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 30 ] || { docker logs "$restore_container" >&2 || true; exit 1; }
  sleep 1
done

archive_sha=$(python -c 'import json; print(json.load(open("/workspace/cloud-deployer-infra/.state/oci-registry-acceptance/registry.tar.gz.manifest.json"))["archive"]["sha256"])')
content_sha=$(python -c 'import json; print(json.load(open("/workspace/cloud-deployer-infra/.state/oci-registry-acceptance/registry.tar.gz.manifest.json"))["content"]["manifestSha256"])')
publication_sha=$(sha256sum "$state/publication.json" | awk '{print $1}')
promotion_sha=$(sha256sum "$state/promotion.json" | awk '{print $1}')
promotion_verification_sha=$(sha256sum "$state/promotion-verification.json" | awk '{print $1}')
python -c 'import json,pathlib; value=json.loads(pathlib.Path("/workspace/cloud-deployer-infra/.state/oci-registry-acceptance/promotion-verification.json").read_text()); assert value["verified"] is True; assert value["exactDigestPreserved"] is True; assert value["sameDaemonImageId"] is True'
printf '{"localOciRegistry":"passed","candidateScoped":true,"digest":"%s","publicationManifestSha256":"%s","promotionReceiptSha256":"%s","promotionVerificationSha256":"%s","registryArchiveSha256":"%s","registryContentManifestSha256":"%s","deleteRefused":true,"mutableTagRecorded":false,"signedExactDigestPromotion":true,"tamperDenied":true,"freshVolumeRestore":true,"daemonDigestPullAfterRestore":true}\n' \
  "$digest" "$publication_sha" "$promotion_sha" "$promotion_verification_sha" "$archive_sha" "$content_sha"
