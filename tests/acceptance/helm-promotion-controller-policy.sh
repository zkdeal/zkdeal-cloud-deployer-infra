#!/bin/sh
set -eu

chart=helm/zkdeal
fixture=tests/fixtures/helm-production-render.yaml
output="$(mktemp)"
trap 'rm -f "$output"' EXIT INT TERM

render() { helm template zkdeal "$chart" -f "$fixture" "$@" >"$output" 2>&1; }
render

negative_count=0
reject() {
  expected=$1
  shift
  if render "$@"; then
    printf 'ERROR: unsafe promotion controller rendered: %s\n' "$*" >&2
    exit 1
  fi
  grep -F "$expected" "$output" >/dev/null || {
    printf 'ERROR: wrong Helm failure, expected: %s\n' "$expected" >&2
    sed -n '1,80p' "$output" >&2
    exit 1
  }
  negative_count=$((negative_count + 1))
}

reject "requires the automated promotion controller" \
  --set operations.promotion.controller.enabled=false
reject "requires an explicit incident approval arm" \
  --set operations.promotion.controller.armed=false
reject "requires a concrete sha256 image digest" \
  --set-string operations.promotion.controller.image.digest=sha256:REPLACE
reject "requires two independent active-health witnesses" \
  --set-json 'operations.promotion.controller.activeHealthUrls=["https://one.example/health"]'
reject "active-health witnesses require HTTPS" \
  --set-json 'operations.promotion.controller.activeHealthUrls=["http://one.example/health","https://two.example/health"]'
reject "failover provider requires HTTPS" \
  --set-string operations.promotion.controller.failoverProviderUrl=http://provider.example/v1/failovers
reject "requires a unique lowercase incident candidate ID" \
  --set-string operations.promotion.controller.candidateId=REPLACE
reject "greater than or equal to 3" \
  --set operations.promotion.controller.failureThreshold=2
reject "maximum RTO exceeds the availability objective" \
  --set operations.promotion.controller.maxRtoSeconds=300
reject "credentials require distinct secret keys" \
  --set-string operations.promotion.controller.approvalTokenSecretKey=failover-provider-token
reject "requires explicit witness/provider CIDRs" \
  --set-json 'networkPolicy.operationalFlows.promotionController.cidrs=[]'

printf '{"helmPromotionControllerPolicy":"passed","structuralRender":true,"negativeConfigurationsRejected":%s,"realProviderEvidence":false}\n' "$negative_count"
