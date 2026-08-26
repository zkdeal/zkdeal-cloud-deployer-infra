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
    printf 'ERROR: unsafe failover provider rendered: %s\n' "$*" >&2
    exit 1
  fi
  grep -F "$expected" "$output" >/dev/null || {
    printf 'ERROR: wrong Helm failure, expected: %s\n' "$expected" >&2
    sed -n '1,100p' "$output" >&2
    exit 1
  }
  negative_count=$((negative_count + 1))
}

reject "requires the first-party failover provider" \
  --set operations.promotion.provider.enabled=false
reject "requires a concrete sha256 image digest" \
  --set-string operations.promotion.provider.image.digest=sha256:REPLACE
reject "must use the same independent witness set" \
  --set-json 'operations.promotion.provider.activeHealthUrls=["https://other-a.render.invalid/health","https://other-b.render.invalid/health"]'
reject "active-health witnesses require HTTPS" \
  --set-json 'operations.promotion.provider.activeHealthUrls=["http://active-witness-a.render.invalid/hosting/v1/health","https://active-witness-b.render.invalid/hosting/v1/health"]' \
  --set-json 'operations.promotion.controller.activeHealthUrls=["http://active-witness-a.render.invalid/hosting/v1/health","https://active-witness-b.render.invalid/hosting/v1/health"]'
reject "must use the same standby health gate" \
  --set-string operations.promotion.provider.standbyHealthUrl=http://other-standby:3000/hosting/v1/health
reject "requires the owner-advertised canonical indexer freshness URL" \
  --set-string operations.promotion.provider.indexerFreshnessUrl=REPLACE_WITH_OWNER_ROUTE
reject "requires durable state and TLS secret refs" \
  --set-string operations.promotion.provider.stateClaimName=
reject "credentials require distinct secret keys" \
  --set-string operations.promotion.provider.approvalTokenSecretKey=failover-provider-token
reject "credential refs must match" \
  --set-string operations.promotion.provider.providerTokenSecretKey=other-provider-token
reject "chart-owned stable writer Service" \
  --set-string operations.promotion.provider.applicationService=unscoped-writer
reject "chart-owned post-fence Deployment" \
  --set-string operations.promotion.provider.signerAuthorityDeployment=unscoped-signer
reject "route scope must use the Helm instance label" \
  --set-string operations.promotion.provider.routeScopeLabelKey=app
reject "route scope must equal the Helm release name" \
  --set-string operations.promotion.provider.routeScopeLabelValue=another-release
reject "selector key differs from the chart-owned failover label" \
  --set-string operations.promotion.provider.routeLabelKey=role
reject "primary and standby database pods must differ" \
  --set-string operations.promotion.provider.standbyDatabasePod=postgresql-primary-0
reject "PostgreSQL data path must be absolute" \
  --set-string operations.promotion.provider.pgData=relative/data
reject "chart-owned post-fence signer authority" \
  --set operations.promotion.provider.signerAuthority.enabled=false
reject "signer authority requires a concrete sha256 image digest" \
  --set-string operations.promotion.provider.signerAuthority.image.digest=sha256:REPLACE
reject "must call the chart-owned failover provider Service" \
  --set-string operations.promotion.controller.failoverProviderUrl=https://external-provider.example/v1/failovers
reject "requires explicit CIDRs for two witnesses and the Kubernetes API" \
  --set-json 'networkPolicy.operationalFlows.failoverProvider.cidrs=["192.0.2.44/32","192.0.2.45/32"]'
reject "external egress must be restricted to port 443" \
  --set-json 'networkPolicy.operationalFlows.failoverProvider.ports=[443,6443]'
reject "signer authority egress is missing scoped role CIDR" \
  --set-json 'networkPolicy.operationalFlows.signerAuthority.cidrs=["192.0.2.32/32","192.0.2.33/32","192.0.2.34/32","192.0.2.35/32","192.0.2.36/32"]'
reject "is not assigned to a scoped signer role" \
  --set-json 'networkPolicy.operationalFlows.signerAuthority.cidrs=["192.0.2.32/32","192.0.2.33/32","192.0.2.34/32","192.0.2.35/32","192.0.2.36/32","192.0.2.37/32","192.0.2.99/32"]'
reject "signer authority egress must be restricted to port 443" \
  --set-json 'networkPolicy.operationalFlows.signerAuthority.ports=[9000]'

printf '{"helmFailoverProviderPolicy":"passed","structuralRender":true,"negativeConfigurationsRejected":%s,"kubernetesLiveEvidence":false}\n' "$negative_count"
