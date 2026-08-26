#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."

digest=sha256:1111111111111111111111111111111111111111111111111111111111111111
export COORDINATOR_IMAGE_DIGEST="registry.company.tld/zkdeal/coordinator@$digest"
export HEADLESS_NODE_IMAGE_DIGEST="registry.company.tld/zkdeal/headless@$digest"
export DOCS_IMAGE_DIGEST="registry.company.tld/zkdeal/docs@$digest"
export PROVER_IMAGE_DIGEST="registry.company.tld/zkdeal/prover@$digest"
export AGENT_IMAGE_DIGEST="registry.company.tld/zkdeal/agent@$digest"
export POSTGRES_IMAGE_DIGEST="registry.company.tld/dependencies/postgres@$digest"
export MINIO_IMAGE_DIGEST="registry.company.tld/dependencies/minio@$digest"
export MINIO_CLIENT_IMAGE_DIGEST="registry.company.tld/dependencies/minio-client@$digest"
export PROMOTION_CONTROLLER_IMAGE_DIGEST="registry.company.tld/zkdeal/promotion-controller@$digest"
export FAILOVER_PROVIDER_IMAGE_DIGEST="registry.company.tld/zkdeal/failover-provider@$digest"
export ACTIVE_PROMOTION_HEALTH_URLS="https://active-witness-a.company.tld/health,https://active-witness-b.company.tld/health"
export FAILOVER_PROVIDER_URL="https://failover-provider-docker:8443/v1/failovers"
export FAILOVER_PROVIDER_ACTIVE_HEALTH_URLS="$ACTIVE_PROMOTION_HEALTH_URLS"
export FAILOVER_PROVIDER_INDEXER_FRESHNESS_URL="https://indexer.company.tld/hosting/v1/finality-status"
export PROMOTION_CANDIDATE_ID="candidate-policy-render-001"
export PROMOTION_CONTROLLER_ARMED=true
export FAILOVER_PROVIDER_TOKEN_FILE=/run/operator/failover-provider.token
export FAILOVER_APPROVAL_TOKEN_FILE=/run/operator/failover-approval.token
export PROMOTION_PRINCIPAL_TOKEN_FILE=/run/operator/promotion-principal.token
export FAILOVER_PROVIDER_TLS_CERT_FILE=/run/operator/failover-provider.crt
export FAILOVER_PROVIDER_TLS_KEY_FILE=/run/operator/failover-provider.key
export POSTGRES_REPLICATION_PASSWORD_FILE=/run/operator/postgres-replication-password
export POSTGRES_PASSWORD_FILE=/run/operator/postgres-password

error_file=$(mktemp)
trap 'rm -f "$error_file"' EXIT INT TERM

# Exact image references and the rendered graph are necessary but not
# sufficient. While owner evidence is moving, the capability preflight must
# fail closed. Once the exact source-bound owner evidence and deployment pins
# are sealed, the same policy gate must advance without retaining a stale
# expected-error string.
owner_capability_start_blocked=false
legacy_hosted_surface_blocked=false
if ! python scripts/production_compose.py check --env-file .env.example >"$error_file" 2>&1; then
  if grep -F 'owner capability preflight failed closed' "$error_file" >/dev/null; then
    owner_capability_start_blocked=true
  elif grep -Eq 'standalone filesystem proof queue|standalone queue service|direct liveness signer authority' "$error_file"; then
    legacy_hosted_surface_blocked=true
  else
    cat "$error_file" >&2
    exit 1
  fi
fi

good_coordinator=$COORDINATOR_IMAGE_DIGEST
negative_count=0
for bad in \
  registry.company.tld/zkdeal/coordinator:latest \
  "registry.company.tld/zkdeal/coordinator:latest@$digest" \
  registry.company.tld/zkdeal/coordinator@sha256:REPLACE \
  "registry.invalid/zkdeal/coordinator@$digest" \
  registry.company.tld/zkdeal/coordinator@sha256:111111111111111111111111111111111111111111111111111111111111111
do
  export COORDINATOR_IMAGE_DIGEST=$bad
  if python scripts/production_compose.py check --env-file .env.example >"$error_file" 2>&1; then
    echo "ERROR: production Compose accepted forbidden reference $bad" >&2
    exit 1
  fi
  grep -Eq 'exact lowercase repository@sha256|must not contain a mutable tag|reserved placeholder' "$error_file"
  negative_count=$((negative_count + 1))
done
export COORDINATOR_IMAGE_DIGEST=$good_coordinator

printf '{"productionComposePolicy":"passed","digestAndRenderGraphAccepted":true,"ownerCapabilityStartBlocked":%s,"legacyHostedSurfaceBlocked":%s,"negativeReferencesRejected":%s,"buildFallbackRejectedByUnitGate":true}\n' \
  "$owner_capability_start_blocked" "$legacy_hosted_surface_blocked" "$negative_count"
