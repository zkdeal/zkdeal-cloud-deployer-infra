#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."

digest='sha256:1111111111111111111111111111111111111111111111111111111111111111'
export COORDINATOR_IMAGE_DIGEST="registry.invalid/zkdeal-coordinator@$digest"
export HEADLESS_NODE_IMAGE_DIGEST="registry.invalid/zkdeal-headless-room-node@$digest"
export DOCS_IMAGE_DIGEST="registry.invalid/zkdeal-docs@$digest"
export PROVER_IMAGE_DIGEST="registry.invalid/zkdeal-prover@$digest"
export AGENT_IMAGE_DIGEST="registry.invalid/zkdeal-agent@$digest"
export POSTGRES_IMAGE_DIGEST="postgres@$digest"
export MINIO_IMAGE_DIGEST="minio/minio@$digest"
export MINIO_CLIENT_IMAGE_DIGEST="minio/mc@$digest"
export PROMOTION_CONTROLLER_IMAGE_DIGEST="registry.invalid/zkdeal-promotion-controller@$digest"
export FAILOVER_PROVIDER_IMAGE_DIGEST="registry.invalid/zkdeal-failover-provider@$digest"
export ACTIVE_PROMOTION_HEALTH_URLS="https://active-witness-a.invalid/health,https://active-witness-b.invalid/health"
export FAILOVER_PROVIDER_URL="https://failover-provider-docker:8443/v1/failovers"
export FAILOVER_PROVIDER_ACTIVE_HEALTH_URLS="$ACTIVE_PROMOTION_HEALTH_URLS"
export FAILOVER_PROVIDER_INDEXER_FRESHNESS_URL="https://indexer.invalid/hosting/v1/finality-status"
export PROMOTION_CANDIDATE_ID="compose-config-candidate-001"
export PROMOTION_CONTROLLER_ARMED=true
export FAILOVER_PROVIDER_TOKEN_FILE=/run/operator/failover-provider.token
export FAILOVER_APPROVAL_TOKEN_FILE=/run/operator/failover-approval.token
export PROMOTION_PRINCIPAL_TOKEN_FILE=/run/operator/promotion-principal.token
export FAILOVER_PROVIDER_TLS_CERT_FILE=/run/operator/failover-provider.crt
export FAILOVER_PROVIDER_TLS_KEY_FILE=/run/operator/failover-provider.key
export POSTGRES_REPLICATION_PASSWORD_FILE=/run/operator/postgres-replication-password
export POSTGRES_PASSWORD_FILE=/run/operator/postgres-password
export WEB3SIGNER_LIVENESS_CONFIG_DIR=../.state/test/signer-liveness
export WEB3SIGNER_OPERATIONS_CONFIG_DIR=../.state/test/signer-operations
export WEB3SIGNER_PAYOUT_CONFIG_DIR=../.state/test/signer-payout
export WEB3SIGNER_FINALITY_CONFIG_DIR=../.state/test/signer-finality
export WEB3SIGNER_SPONSOR_CONFIG_DIR=../.state/test/signer-sponsor
export WEB3SIGNER_WITHDRAWAL_CONFIG_DIR=../.state/test/signer-withdrawal
export WEB3SIGNER_BLOB_CONFIG_DIR=../.state/test/signer-blob
export TLS_CERT_FILE=../.state/test/tls/fullchain.pem
export TLS_KEY_FILE=../.state/test/tls/key.pem

base='-f compose/compose.yaml'
dependencies='-f compose/compose.dependencies.yaml'
hosted='-f compose/compose.hosted.yaml'
provider='-f compose/compose.failover-provider.yaml'

docker compose --env-file .env.example $base config --quiet
docker compose --env-file .env.example $base $dependencies $hosted --profile hosted config --quiet
docker compose --env-file .env.example $base $dependencies $hosted $provider -f compose/compose.production.yaml --profile hosted --profile promotion-controller --profile promotion-provider config --quiet
docker compose --env-file .env.example $base $dependencies $hosted -f compose/compose.signer-production.example.yaml --profile hosted --profile signer-production config --quiet
docker compose --env-file .env.example $base $dependencies -f compose/compose.conformance.yaml --profile conformance config --quiet
docker compose --env-file .env.example $base -f compose/compose.tls.yaml config --quiet

printf '%s\n' '{"composeConfigGates":6,"hostedOwnerServices":["coordinator","coordinator-standby","indexer","reconciler","publisher","auto-claimer","headless-node"],"automatedPromotionController":"profile-gated","firstPartyDockerFailoverProvider":"profile-gated","withdrawalApiOwner":"coordinator","duplicateWithdrawalWorker":false}'
