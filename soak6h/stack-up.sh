#!/usr/bin/env bash
# stack-up.sh -- bring up the live candidate stack that satisfies the owner soak
# driver's endpoint contract, then write ~/soak6h/endpoints.env.
#
# THIS IS A TEST SOAK RIG, NOT THE 12-HOUR RELEASE GATE.
#   * the fault and backup topologies are classified "non-release-fixture";
#   * L1 is two throwaway anvil devnets on chain 31337;
#   * an auth edge translates the soak package's eph_ tokens into the
#     coordinator's own zkd.<principal>.<secret> credentials (PHASE 5/8b),
#     because the two credential formats are genuinely different.
# Everything it starts is first-party code: the real coordinator, indexer,
# reconciler, publisher, withdrawal worker, the real headless room node, the
# real CUDA prover plus prover-agent, and the three reviewed control adapters.
#
# Run it on the GPU node (Docker is allowed there). It is idempotent: every run
# removes its own previous containers, network and volumes first.
#
#   ./stack-up.sh
#
# Companion teardown: ./stack-down.sh

set -euo pipefail

# ---------------------------------------------------------------------------
# 0. Fixed identity of this rig
# ---------------------------------------------------------------------------

PROJECT="${SOAK6H_PROJECT:-zksoak6h}"
PREFIX="${PROJECT}-"
NETWORK="${PROJECT}_soaknet"
WORK="${SOAK6H_WORK:-$HOME/soak6h}"
REPO="${SOAK6H_REPO:-$HOME/zkdeal-rc}"
DIGESTS_ENV="${SOAK6H_DIGESTS_ENV:-$HOME/sm120-digests.env}"

CHAIN_ID="${SOAK6H_CHAIN_ID:-31337}"
# Both anvils MUST use the same pinned genesis timestamp, or their genesis
# hashes differ and every two-provider agreement check fails. This is the known
# gotcha documented in tests/acceptance/fault-control.sh.
ANVIL_TIMESTAMP="${SOAK6H_ANVIL_TIMESTAMP:-1755820800}"
ANVIL_BLOCK_TIME="${SOAK6H_ANVIL_BLOCK_TIME:-2}"

CANDIDATE_ID="${SOAK6H_CANDIDATE_ID:-soak6h-test-candidate-20260823}"
ACTIVE_COORDINATOR_ID="${SOAK6H_ACTIVE_COORDINATOR_ID:-soak6h-active-region}"
STANDBY_COORDINATOR_ID="${SOAK6H_STANDBY_COORDINATOR_ID:-soak6h-standby-region}"
# The hosted room id and the CHAIN room id are one namespace: the room node
# calls attachRoom(BigInt(config.roomId)) and the coordinator resolves policy by
# the same number. A label like 9001 therefore only works while no room has to
# exist on chain - roomState(9001) is all zeros, because RoomManager assigns ids
# from 1 upward. When SOAK6H_CREATE_REAL_ROOM is set, create-room.sh overwrites
# this with the id createRoom actually returned.
HEADLESS_ROOM_ID="${SOAK6H_HEADLESS_ROOM_ID:-9001}"
CREATE_REAL_ROOM="${SOAK6H_CREATE_REAL_ROOM:-0}"
PROOF_CLASS="${SOAK6H_PROOF_CLASS:-groth16-production}"
BILLING_UNIT="${SOAK6H_BILLING_UNIT:-gpu-second}"
# The hosted store constrains currency to ^[A-Z]{3}$ and requires a job's
# maximumChargeCurrency to equal the quoted price's currency exactly, so the
# whole rig has to agree on one uppercase code.
BILLING_CURRENCY="${SOAK6H_BILLING_CURRENCY:-WEI}"

IMG_COORDINATOR="${SOAK6H_COORDINATOR_IMAGE:-zkdeal-coordinator:acceptance}"
IMG_HEADLESS="${SOAK6H_HEADLESS_IMAGE:-zkdeal-headless-room-node:acceptance}"
IMG_AGENT="${SOAK6H_AGENT_IMAGE:-zkdeal-prover-agent:acceptance}"
IMG_FAULT="${SOAK6H_FAULT_IMAGE:-zkdeal-fault-control:acceptance}"
IMG_BACKUP="${SOAK6H_BACKUP_IMAGE:-zkdeal-backup-restore-control:acceptance}"
IMG_FAILOVER="${SOAK6H_FAILOVER_IMAGE:-zkdeal-failover-provider:local}"
IMG_FOUNDRY="${SOAK6H_FOUNDRY_IMAGE:-ghcr.io/foundry-rs/foundry:v1.7.1@sha256:8347b728d5d393dac1c018691b36f506d23b9dcd78341d40ea0fcb11c3a19cdd}"
IMG_POSTGRES_HA="${SOAK6H_POSTGRES_HA_IMAGE:-zkdeal-postgres-ha-fixture:acceptance}"
IMG_POSTGRES="${SOAK6H_POSTGRES_IMAGE:-postgres:17.6-alpine}"
IMG_MINIO="${SOAK6H_MINIO_IMAGE:-minio/minio:RELEASE.2025-04-22T22-12-26Z}"
IMG_MC="${SOAK6H_MC_IMAGE:-minio/mc:RELEASE.2025-04-16T18-13-26Z}"
IMG_NGINX="${SOAK6H_NGINX_IMAGE:-nginx:1.27-alpine}"
IMG_PYTHON="${SOAK6H_PYTHON_IMAGE:-python:3.13-alpine}"
IMG_WEB3SIGNER="${SOAK6H_WEB3SIGNER_IMAGE:-consensys/web3signer:26.4.2-distroless}"
IMG_ALPINE="${SOAK6H_ALPINE_IMAGE:-alpine:3.21}"
IMG_CURL="${SOAK6H_CURL_IMAGE:-curlimages/curl:8.15.0}"

# The soak package's thirteen scoped roles, in the order main.star requires.
AUTH_ALIASES=(tenant_a tenant_b node_a node_b l1_liveness l1_room l1_aggregate
  l1_sponsor withdrawal fault_control backup_restore failover_control
  failover_approval)

# The eight independent L1 signing identities the coordinator wants.
# The ninth role, `admission`, exists so the coordinator can sign admission
# receipts. Without it AdmissionService is never constructed and every
# POST /rooms/:id/transactions answers 503 ADMISSION_UNAVAILABLE - which is why
# the rig has never exercised admissions. The identity must be generated before
# any room is created: `admissionSigner` is written once at intake with no
# setter, and the coordinator refuses to sign for a room whose signer is not its
# own address.
SIGNER_ROLES=(room-operations aggregate pool-sponsor pool-finality
  pool-beneficiary withdrawal blob-publisher node-liveness admission)

START_EPOCH="$(date -u +%s)"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log()  { printf '[soak6h %s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
step() { printf '\n[soak6h %s] ===== %s =====\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die()  { printf '\n[soak6h FATAL] %s\n' "$*" >&2; exit 1; }

# die_service <service> <why> -- name the service that did not come up and dump
# its recent log so the failure is diagnosable without a second six-hour run.
die_service() {
  printf '\n[soak6h FATAL] service %s did not come up: %s\n' "$1" "$2" >&2
  printf '[soak6h FATAL] last 80 log lines from %s%s:\n' "$PREFIX" "$1" >&2
  docker logs --tail 80 "${PREFIX}$1" >&2 2>&1 \
    || printf '  (no container named %s%s)\n' "$PREFIX" "$1" >&2
  exit 1
}

on_error() {
  local code=$?
  if [ "$code" -ne 0 ]; then
    printf '\n[soak6h] container inventory at failure:\n' >&2
    docker ps -a --filter "name=^${PREFIX}" \
      --format '  {{.Names}}\t{{.Status}}' >&2 2>&1 || true
    printf '[soak6h] the stack is left running for diagnosis; ./stack-down.sh removes it.\n' >&2
  fi
  return 0
}
trap on_error EXIT

need() { command -v "$1" >/dev/null 2>&1 || die "required command '$1' is not on PATH"; }
have_image() { docker image inspect "$1" >/dev/null 2>&1; }
require_image() { have_image "$1" || die "image '$1' is absent on this node (override with $2)"; }
sha256() { sha256sum "$1" | cut -d' ' -f1; }
sha256_of() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }
hex_random() { head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
lower() { printf '%s' "$1" | tr 'A-F' 'a-f'; }

in_net() { local image="$1"; shift; docker run --rm --network "$NETWORK" "$image" "$@"; }
curl_net() { in_net "$IMG_CURL" --silent --show-error "$@"; }

running() { [ "$(docker inspect -f '{{.State.Running}}' "${PREFIX}$1" 2>/dev/null)" = "true" ]; }
exited_ok() {
  [ "$(docker inspect -f '{{.State.Running}}' "${PREFIX}$1" 2>/dev/null)" = "false" ] &&
  [ "$(docker inspect -f '{{.State.ExitCode}}' "${PREFIX}$1" 2>/dev/null)" = "0" ]
}

# wait_http <service> <url> <seconds> [curl args...] -- bounded, real-clock.
wait_http() {
  local svc="$1" url="$2" budget="$3"; shift 3
  local deadline=$(( $(date -u +%s) + budget ))
  until curl_net --fail --max-time 5 "$@" "$url" >/dev/null 2>&1; do
    [ "$(date -u +%s)" -lt "$deadline" ] \
      || die_service "$svc" "no HTTP 2xx from $url within ${budget}s"
    sleep 2
  done
  log "ok: $svc answered $url"
}

# wait_for <service> <seconds> <predicate-function> [args...]
wait_for() {
  local svc="$1" budget="$2"; shift 2
  local deadline=$(( $(date -u +%s) + budget ))
  until "$@" >/dev/null 2>&1; do
    [ "$(date -u +%s)" -lt "$deadline" ] \
      || die_service "$svc" "readiness predicate '$*' did not pass within ${budget}s"
    sleep 2
  done
  log "ok: $svc passed '$*'"
}

pg_ready() { docker exec "${PREFIX}$1" pg_isready -U "$PG_USER" -d "$PG_DB"; }

psql_primary() {
  docker exec -i "${PREFIX}postgres-primary" \
    psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" "$@"
}

# Chown/chmod host paths without host root: a throwaway root container does it.
as_root_in() {
  local path="$1"; shift
  docker run --rm -u 0:0 -v "$path:/target" "$IMG_ALPINE" sh -c "$*" >/dev/null
}

dc() {
  docker compose --project-name "$PROJECT" --project-directory "$WORK" \
    -f "$WORK/compose.yaml" "$@"
}

rpc_ready() {
  local host="$1"
  local deadline=$(( $(date -u +%s) + 150 ))
  until curl_net --fail --max-time 4 -H 'content-type: application/json' \
      --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' \
      "http://${host}:8545/" >/dev/null 2>&1; do
    [ "$(date -u +%s)" -lt "$deadline" ] || die_service "$host" "eth_blockNumber never answered"
    sleep 2
  done
  log "ok: $host is answering JSON-RPC"
}

genesis_hash() {
  curl_net --fail -H 'content-type: application/json' \
    --data '{"jsonrpc":"2.0","id":1,"method":"eth_getBlockByNumber","params":["0x0",false]}' \
    "http://$1:8545/" | tr ',' '\n' | grep '"hash"' | head -1
}

# ---------------------------------------------------------------------------
# 1. Preflight
# ---------------------------------------------------------------------------

step "PHASE 1  preflight"

need docker; need sha256sum; need od; need sed; need grep
docker compose version >/dev/null 2>&1 || die "'docker compose' v2 is required"
docker info >/dev/null 2>&1 || die "cannot talk to the Docker daemon"

[ -d "$REPO/web3-protocol/contracts" ] || die "'$REPO/web3-protocol/contracts' not found (set SOAK6H_REPO)"
[ -d "$REPO/cloud-deployer-infra" ]    || die "'$REPO/cloud-deployer-infra' not found (set SOAK6H_REPO)"
[ -d "$REPO/web3-protocol/contracts/out" ] \
  || die "'$REPO/web3-protocol/contracts/out' is missing; build the contracts before running the rig"
[ -S /var/run/docker.sock ] || die "/var/run/docker.sock is required by the three control adapters"

[ -f "$DIGESTS_ENV" ] || die "'$DIGESTS_ENV' not found; the minted prover RUNTIME digest lives there"
RUNTIME=""
# shellcheck disable=SC1090
. "$DIGESTS_ENV"
[ -n "${RUNTIME:-}" ] || die "RUNTIME= is not set in $DIGESTS_ENV"
IMG_PROVER="${SOAK6H_PROVER_IMAGE:-$RUNTIME}"
log "prover runtime: $IMG_PROVER"

require_image "$IMG_COORDINATOR" SOAK6H_COORDINATOR_IMAGE
require_image "$IMG_HEADLESS"    SOAK6H_HEADLESS_IMAGE
require_image "$IMG_AGENT"       SOAK6H_AGENT_IMAGE
require_image "$IMG_FAULT"       SOAK6H_FAULT_IMAGE
require_image "$IMG_BACKUP"      SOAK6H_BACKUP_IMAGE
require_image "$IMG_FAILOVER"    SOAK6H_FAILOVER_IMAGE
have_image "$IMG_PROVER" || die "prover runtime '$IMG_PROVER' is absent; pull it from localhost:5000 first"

docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia \
  || die "the nvidia container runtime is not registered with this Docker daemon"

for image in "$IMG_FOUNDRY" "$IMG_POSTGRES" "$IMG_MINIO" "$IMG_MC" "$IMG_NGINX" \
             "$IMG_PYTHON" "$IMG_WEB3SIGNER" "$IMG_ALPINE" "$IMG_CURL"; do
  have_image "$image" || { log "pulling $image"; docker pull "$image" >/dev/null || die "cannot pull $image"; }
done

if ! have_image "$IMG_POSTGRES_HA"; then
  log "building the streaming-replication postgres fixture ($IMG_POSTGRES_HA)"
  docker build -f "$REPO/cloud-deployer-infra/tests/fixtures/PostgresHa.Dockerfile" \
    -t "$IMG_POSTGRES_HA" "$REPO/cloud-deployer-infra" >/dev/null \
    || die "could not build $IMG_POSTGRES_HA from tests/fixtures/PostgresHa.Dockerfile"
fi

# ---------------------------------------------------------------------------
# 2. Idempotent teardown of the previous run
# ---------------------------------------------------------------------------

step "PHASE 2  removing any previous ${PREFIX} stack"

if [ -f "$WORK/compose.yaml" ]; then
  dc down --remove-orphans --volumes --timeout 20 >/dev/null 2>&1 || true
fi
# Belt and braces: the failover provider can detach containers from the network
# mid-incident, which leaves compose unable to see them.
leftovers="$(docker ps -aq --filter "name=^${PREFIX}" || true)"
if [ -n "$leftovers" ]; then
  # shellcheck disable=SC2086
  docker rm -f $leftovers >/dev/null 2>&1 || true
fi
docker network rm "$NETWORK" >/dev/null 2>&1 || true
for volume in pg-primary pg-standby pg-fresh minio-data minio-backup-data \
              minio-fresh-data room-node-secrets room-node-state failover-provider-state; do
  docker volume rm -f "${PROJECT}_${volume}" >/dev/null 2>&1 || true
done

rm -rf "$WORK"
mkdir -p "$WORK" "$WORK/auth" "$WORK/ctl-secrets" "$WORK/nginx" "$WORK/shim" \
         "$WORK/web3signer/keys" "$WORK/deploy-out" "$WORK/room-node/secrets" "$WORK/logs"

# ---------------------------------------------------------------------------
# 3. Credentials, tokens and signer keys
# ---------------------------------------------------------------------------

step "PHASE 3  generating credentials"

umask 077

PG_USER=zkdeal
PG_DB=zkdeal
PG_PASSWORD="soak6h$(hex_random 12)"
PG_REPLICATION_PASSWORD="replication-acceptance-password"  # must match the baked role in tests/fixtures/postgres-ha/primary-init.sh
MINIO_USER="soak6h-minio-admin"
MINIO_PASSWORD="soak6h$(hex_random 16)"
BACKUP_MINIO_USER="soak6h-backup-access"
BACKUP_MINIO_PASSWORD="soak6h$(hex_random 16)"
FRESH_MINIO_USER="soak6h-fresh-access"
FRESH_MINIO_PASSWORD="soak6h$(hex_random 16)"
FRESH_PG_PASSWORD="soak6hfresh$(hex_random 12)"
BACKUP_ENCRYPTION_KEY="$(hex_random 32)"
API_KEY_PEPPER="soak6h-api-key-pepper-$(hex_random 16)"
SIGNER_AUTH_TOKEN="soak6h-signer-auth-token-$(hex_random 12)"
PROVER_TOKEN="soak6h-prover-token-$(hex_random 12)"
ROOM_NODE_CONTROL_TOKEN="soak6h-room-node-control-$(hex_random 12)"

# The soak package's eph_ tokens: eph_ plus 28..120 chars of [A-Za-z0-9_-], and
# every role independently revocable, so each alias gets its own random value.
declare -A EPH
for alias in "${AUTH_ALIASES[@]}"; do
  EPH[$alias]="eph_$(hex_random 20)"
  printf '%s' "${EPH[$alias]}" >"$WORK/auth/${alias}.token"
  chmod 0600 "$WORK/auth/${alias}.token"
done
log "wrote 13 scoped eph_ tokens to $WORK/auth (mode 0600)"

cp "$WORK/auth/fault_control.token"  "$WORK/ctl-secrets/fault-token"
cp "$WORK/auth/backup_restore.token" "$WORK/ctl-secrets/backup-restore-token"

write_ctl_secret() { printf '%s' "$2" >"$WORK/ctl-secrets/$1"; }
write_ctl_secret source-postgres-password "$PG_PASSWORD"
write_ctl_secret fresh-postgres-password  "$FRESH_PG_PASSWORD"
write_ctl_secret source-minio-access      "$MINIO_USER"
write_ctl_secret source-minio-secret      "$MINIO_PASSWORD"
write_ctl_secret backup-minio-access      "$BACKUP_MINIO_USER"
write_ctl_secret backup-minio-secret      "$BACKUP_MINIO_PASSWORD"
write_ctl_secret fresh-minio-access       "$FRESH_MINIO_USER"
write_ctl_secret fresh-minio-secret       "$FRESH_MINIO_PASSWORD"
write_ctl_secret backup-encryption-key    "$BACKUP_ENCRYPTION_KEY"

# The anvil dev account the task names; it is pre-funded at genesis and only
# used to deploy. Every scoped signing identity below is freshly generated and
# funded explicitly, so no key here is transcribed from memory.
DEPLOYER_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80

cast_address() {
  docker run --rm --entrypoint cast "$IMG_FOUNDRY" wallet address --private-key "$1" | tr -d '\r\n'
}

DEPLOYER_ADDRESS="$(cast_address "$DEPLOYER_KEY")"
[ -n "$DEPLOYER_ADDRESS" ] || die "could not derive the deployer address with 'cast wallet address'"

declare -A SIGNER_ADDRESS
# The raw keys are retained alongside the addresses because one operation cannot
# go through web3signer at all: fundServiceBond must be sent BY the room's
# admission signer (msg.sender is checked), and no managed L1 operation covers
# it. These are ephemeral rig identities, freshly generated here and already
# written to the signer's key directory on this host.
declare -A SIGNER_KEY
for role in "${SIGNER_ROLES[@]}"; do
  key="0x$(hex_random 32)"
  addr="$(cast_address "$key")"
  [ -n "$addr" ] || die "could not derive an address for the $role signing identity"
  SIGNER_ADDRESS[$role]="$(lower "$addr")"
  SIGNER_KEY[$role]="$key"
  cat >"$WORK/web3signer/keys/${role}.yaml" <<YAML
type: "file-raw"
keyType: "SECP256K1"
privateKey: "${key}"
YAML
  chmod 0644 "$WORK/web3signer/keys/${role}.yaml"
done
chmod 0755 "$WORK/web3signer" "$WORK/web3signer/keys"
log "deployer $DEPLOYER_ADDRESS; ${#SIGNER_ROLES[@]} independent scoped signer keys staged"

OPERATIONS_ACCOUNT="${SIGNER_ADDRESS[room-operations]}"
SPONSOR_ACCOUNT="${SIGNER_ADDRESS[pool-sponsor]}"
# The address a soak room is created with, and the only account allowed to fund
# that room's service bond.
ADMISSION_ACCOUNT="${SIGNER_ADDRESS[admission]}"

# Fixture-grade candidate binding. The driver and all three adapters compare
# the same three values, so they only have to agree with each other.
PLAN_SHA256="$(sha256_of "soak6h-plan|$CANDIDATE_ID")"
HOSTED_INTEGRATION_TOKEN="sha256:$(sha256_of "soak6h-hosted|$CANDIDATE_ID")"
CANDIDATE_DESCRIPTOR_SHA256="$(sha256_of "soak6h-descriptor|$CANDIDATE_ID")"
DEPLOYMENT_DOMAIN="0x$(sha256_of "soak6h-domain|$CANDIDATE_ID")"

# ---------------------------------------------------------------------------
# 4. Coordinator principals (deterministic; seeded straight into PostgreSQL)
# ---------------------------------------------------------------------------
#
# The coordinator mints credentials as zkd.<principalId>.<secret> and stores
# HMAC-SHA256(API_KEY_PEPPER, token). The admin REST route that would mint them
# is gated behind HOSTING_DEV_STATIC_ADMIN, which config.ts refuses unless the
# coordinator binds loopback -- impossible for a container other services must
# reach. So the rig derives the same tokens, computes the same HMAC, and
# inserts the rows once the coordinator has applied its schema (PHASE 8b).

principal_token() {  # <kind-prefix> <slug>
  local id_hex secret
  id_hex="$(sha256_of "soak6h-principal|$CANDIDATE_ID|$1|$2" | cut -c1-20)"
  secret="$(sha256_of "soak6h-secret|$CANDIDATE_ID|$1|$2|$API_KEY_PEPPER" | cut -c1-52)"
  printf 'zkd.%s_%s.%s' "$1" "$id_hex" "$secret"
}

# name|tenant|kind-prefix|kind|roles
# Role sets obey PRINCIPAL_ROLE_MATRIX and the "exactly one role" rule the L1
# operation routes enforce on their service credentials.
PRINCIPAL_SPECS=(
  "admin|tenant-a|svc|service|hosting-admin"
  "tenant_a|tenant-a|key|api-key|tenant-admin"
  "tenant_b|tenant-b|key|api-key|tenant-admin"
  "node_a_key|tenant-a|key|api-key|job-submit,job-read"
  "node_b_key|tenant-b|key|api-key|job-submit,job-read"
  "node_a_node|tenant-a|node|node|prove-node,room-operator"
  "node_b_node|tenant-b|node|node|prove-node,room-operator"
  "withdrawal|tenant-a|key|api-key|withdrawal-read,withdrawal-claim"
  "l1_liveness|tenant-a|svc|service|l1-liveness"
  "l1_room|tenant-a|svc|service|l1-room-submit"
  "l1_aggregate|tenant-a|svc|service|l1-aggregate-submit"
  "l1_sponsor|tenant-a|svc|service|l1-pool-sponsor"
  "l1_publish|tenant-a|svc|service|l1-publish"
  "indexer_write|tenant-a|svc|service|indexer-write"
  "agent_node|tenant-a|node|node|prove-node"
)

declare -A PRINCIPAL_TOKEN
for spec in "${PRINCIPAL_SPECS[@]}"; do
  IFS='|' read -r name _tenant prefix _kind _roles <<<"$spec"
  PRINCIPAL_TOKEN[$name]="$(principal_token "$prefix" "$name")"
done
log "derived ${#PRINCIPAL_SPECS[@]} deterministic coordinator principal tokens"

# ---------------------------------------------------------------------------
# 5. Rendered configuration
# ---------------------------------------------------------------------------

step "PHASE 5  rendering the shim, the auth edge and compose"

DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
FAULT_SOURCE_SHA256="$(sha256 "$REPO/cloud-deployer-infra/fault-control/fault_control.py")"
BACKUP_SOURCE_SHA256="$(sha256 "$REPO/cloud-deployer-infra/backup-restore-control/backup_restore_control.py")"

# --- the reshaping shim ----------------------------------------------------
# Four jobs no first-party service exposes in the shape a consumer here needs:
#   /health     flat witness view of a coordinator (failover provider contract)
#   /freshness  the two fields verify_indexer() reads
#   /signer     JSON-wrapped Web3Signer /upcheck (the provider parses JSON)
#   /logs       the LOG_QUERY_URL the driver validates but never calls
#   /beacon/*   minimal sidecar stub so BEACON_SIDECAR_URLS resolves
# It owns no state: every answer is derived from a live first-party endpoint.
cat >"$WORK/shim/soak_shim.py" <<'PY'
"""Read-only reshaping shim for the six-hour soak rig."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("SHIM_UPSTREAM", "").rstrip("/")
SIGNER = os.environ.get("SHIM_SIGNER_URL", "").rstrip("/")
COORDINATOR_ID = os.environ.get("SHIM_COORDINATOR_ID", "")
TOKEN = os.environ.get("SHIM_TOKEN", "")
TIMEOUT = float(os.environ.get("SHIM_TIMEOUT_SECONDS", "4"))
PORT = int(os.environ.get("SHIM_PORT", "8080"))


def fetch(url: str, token: str = "") -> tuple[int, object]:
    request = urllib.request.Request(url, headers={"accept": "application/json"})
    if token:
        request.add_header("authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read(1 << 20)
            try:
                return int(response.status), json.loads(raw or b"null")
            except ValueError:
                return int(response.status), None
    except (urllib.error.URLError, OSError):
        return 0, None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "zkdeal-soak6h-shim"

    def log_message(self, *_args):  # quiet by design
        return

    def send(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/", "/ready"):
            return self.send(200, {"ok": True, "shim": "soak6h"})
        if path == "/health":
            status, body = fetch(UPSTREAM + "/hosting/v1/health")
            runtime = body.get("runtime") if isinstance(body, dict) else None
            if status != 200 or not isinstance(runtime, dict):
                return self.send(503, {"ok": False, "reason": "upstream-unavailable"})
            effective = str(runtime.get("effectiveRole", ""))
            ready = runtime.get("ready") is True
            return self.send(200, {
                "coordinatorId": runtime.get("coordinatorId", COORDINATOR_ID),
                "configuredRole": runtime.get("configuredRole", ""),
                "effectiveRole": effective,
                "acceptingWrites": effective == "active" and ready,
                "fenceFresh": ready,
                "ready": ready,
            })
        if path == "/freshness":
            status, body = fetch(UPSTREAM + "/hosting/v1/indexer/status", TOKEN)
            if status != 200 or not isinstance(body, dict):
                return self.send(503, {"reason": "indexer-status-unavailable"})
            return self.send(200, {
                "indexerHeadMatchesL1": body.get("indexerHeadMatchesL1") is True,
                "unresolvedSafetyEvents": body.get("unresolvedSafetyEvents", 1),
            })
        if path == "/signer":
            # Web3Signer /upcheck serves plain text and 404s when asked for
            # application/json, so probe it without the JSON accept header.
            status = 0
            if SIGNER:
                try:
                    with urllib.request.urlopen(SIGNER + "/upcheck", timeout=TIMEOUT) as probe:
                        status = int(probe.status)
                except urllib.error.HTTPError as exc:
                    status = int(exc.code)
                except (urllib.error.URLError, OSError):
                    status = 0
            if status != 200:
                return self.send(503, {"signerAuthority": "down"})
            return self.send(200, {"signerAuthority": "up"})
        if path == "/logs" or path.startswith("/logs/"):
            return self.send(200, {"status": "success",
                                   "data": {"resultType": "streams", "result": []}})
        if path.startswith("/beacon"):
            return self.send(200, {"data": []})
        return self.send(404, {"error": "not found"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
PY
chmod 0644 "$WORK/shim/soak_shim.py"

# --- the auth-translating edge ---------------------------------------------
# Two jobs, both forced by the real contracts:
#  (a) the soak package speaks eph_ tokens; the coordinator accepts only its own
#      zkd.<principal>.<secret> credentials.
#  (b) node_a is BOTH a queue submitter (kind api-key, role job-submit) and an
#      admission operator (kind node, role room-operator). One coordinator
#      principal cannot be both kinds, so the edge picks by request path.
# It also gives the driver the PROVER_URL/health it probes (the prover serves
# /healthz), and re-resolves coordinator-writer every 5s so the alias can move
# to the standby during the coordinator-promotion fault.
{
  cat <<'NGINX'
worker_processes 1;
events { worker_connections 2048; }
http {
  # eph_ bearer tokens are long map keys; the 64-byte default bucket
  # cannot hold them and nginx refuses to build the map.
  map_hash_bucket_size 512;
  map_hash_max_size 4096;
  access_log off;
  error_log /dev/stderr warn;
  resolver 127.0.0.11 valid=5s ipv6=off;
  proxy_http_version 1.1;
  proxy_connect_timeout 10s;
  proxy_read_timeout 180s;
  proxy_send_timeout 180s;
  client_max_body_size 64m;
  client_body_buffer_size 1m;

NGINX
  printf '  map $http_authorization $soak_alias {\n    default "";\n'
  for alias in tenant_a tenant_b node_a node_b l1_liveness l1_room l1_aggregate l1_sponsor withdrawal; do
    printf '    "Bearer %s" "%s";\n' "${EPH[$alias]}" "$alias"
  done
  printf '  }\n\n'

  printf '  map $soak_alias $tok_key {\n    default $http_authorization;\n'
  printf '    "tenant_a" "Bearer %s";\n'     "${PRINCIPAL_TOKEN[tenant_a]}"
  printf '    "tenant_b" "Bearer %s";\n'     "${PRINCIPAL_TOKEN[tenant_b]}"
  printf '    "node_a" "Bearer %s";\n'       "${PRINCIPAL_TOKEN[node_a_key]}"
  printf '    "node_b" "Bearer %s";\n'       "${PRINCIPAL_TOKEN[node_b_key]}"
  printf '    "withdrawal" "Bearer %s";\n'   "${PRINCIPAL_TOKEN[withdrawal]}"
  printf '    "l1_liveness" "Bearer %s";\n'  "${PRINCIPAL_TOKEN[l1_liveness]}"
  printf '    "l1_room" "Bearer %s";\n'      "${PRINCIPAL_TOKEN[l1_room]}"
  printf '    "l1_aggregate" "Bearer %s";\n' "${PRINCIPAL_TOKEN[l1_aggregate]}"
  printf '    "l1_sponsor" "Bearer %s";\n'   "${PRINCIPAL_TOKEN[l1_sponsor]}"
  printf '  }\n\n'

  printf '  map $soak_alias $tok_node {\n    default $http_authorization;\n'
  printf '    "node_a" "Bearer %s";\n'   "${PRINCIPAL_TOKEN[node_a_node]}"
  printf '    "node_b" "Bearer %s";\n'   "${PRINCIPAL_TOKEN[node_b_node]}"
  printf '    "tenant_a" "Bearer %s";\n' "${PRINCIPAL_TOKEN[node_a_node]}"
  printf '  }\n\n'

  cat <<'NGINX'
  server {
    listen 3000;
    server_name _;
    set $coordinator "coordinator-writer:3000";

    # kind:node surface
    location /hosting/v1/admissions/ {
      proxy_set_header Host $host;
      proxy_set_header Authorization $tok_node;
      proxy_pass http://$coordinator;
    }

    # Admission submission is a kind:node surface too: the coordinator
    # validates it with the node credential, not the api-key one.
    location ~ ^/rooms/[^/]+/transactions$ {
      proxy_set_header Host $host;
      proxy_set_header Authorization $tok_node;
      proxy_pass http://$coordinator;
    }

    # everything else uses the api-key/service credential for the alias
    location / {
      proxy_set_header Host $host;
      proxy_set_header Authorization $tok_key;
      proxy_pass http://$coordinator;
    }
  }

  # PROVER_URL: the driver probes /health, the prover serves /healthz.
  server {
    listen 8080;
    server_name _;
    set $prover "prover:8080";
    location = /health { proxy_pass http://$prover/healthz; }
    location / { proxy_pass http://$prover; }
  }
}
NGINX
} >"$WORK/nginx/soak-edge.conf"
chmod 0644 "$WORK/nginx/soak-edge.conf"

# --- compose env ------------------------------------------------------------
# ROOM_MANAGER / ROOM_POOL / ACCESS_TOKEN start as placeholders and are
# rewritten in PHASE 7 once the protocol is actually deployed.
cat >"$WORK/.env" <<ENV
PROJECT=${PROJECT}
WORK=${WORK}
CHAIN_ID=${CHAIN_ID}
ANVIL_TIMESTAMP=${ANVIL_TIMESTAMP}
ANVIL_BLOCK_TIME=${ANVIL_BLOCK_TIME}
IMG_COORDINATOR=${IMG_COORDINATOR}
IMG_HEADLESS=${IMG_HEADLESS}
IMG_AGENT=${IMG_AGENT}
IMG_PROVER=${IMG_PROVER}
IMG_FAULT=${IMG_FAULT}
IMG_BACKUP=${IMG_BACKUP}
IMG_FAILOVER=${IMG_FAILOVER}
IMG_FOUNDRY=${IMG_FOUNDRY}
IMG_POSTGRES_HA=${IMG_POSTGRES_HA}
IMG_POSTGRES=${IMG_POSTGRES}
IMG_MINIO=${IMG_MINIO}
IMG_MC=${IMG_MC}
IMG_NGINX=${IMG_NGINX}
IMG_PYTHON=${IMG_PYTHON}
IMG_WEB3SIGNER=${IMG_WEB3SIGNER}
PG_USER=${PG_USER}
PG_DB=${PG_DB}
PG_PASSWORD=${PG_PASSWORD}
PG_REPLICATION_PASSWORD=${PG_REPLICATION_PASSWORD}
FRESH_PG_PASSWORD=${FRESH_PG_PASSWORD}
MINIO_USER=${MINIO_USER}
MINIO_PASSWORD=${MINIO_PASSWORD}
BACKUP_MINIO_USER=${BACKUP_MINIO_USER}
BACKUP_MINIO_PASSWORD=${BACKUP_MINIO_PASSWORD}
FRESH_MINIO_USER=${FRESH_MINIO_USER}
FRESH_MINIO_PASSWORD=${FRESH_MINIO_PASSWORD}
API_KEY_PEPPER=${API_KEY_PEPPER}
SIGNER_AUTH_TOKEN=${SIGNER_AUTH_TOKEN}
PROVER_TOKEN=${PROVER_TOKEN}
ACTIVE_COORDINATOR_ID=${ACTIVE_COORDINATOR_ID}
STANDBY_COORDINATOR_ID=${STANDBY_COORDINATOR_ID}
CANDIDATE_ID=${CANDIDATE_ID}
PLAN_SHA256=${PLAN_SHA256}
HOSTED_INTEGRATION_TOKEN=${HOSTED_INTEGRATION_TOKEN}
CANDIDATE_DESCRIPTOR_SHA256=${CANDIDATE_DESCRIPTOR_SHA256}
DOCKER_GID=${DOCKER_GID}
FAULT_SOURCE_SHA256=${FAULT_SOURCE_SHA256}
BACKUP_SOURCE_SHA256=${BACKUP_SOURCE_SHA256}
FAULT_TOPOLOGY_SHA256=pending
BACKUP_TOPOLOGY_SHA256=pending
OPERATIONS_ACCOUNT=${OPERATIONS_ACCOUNT}
SPONSOR_ACCOUNT=${SPONSOR_ACCOUNT}
# docker compose resolves ${ADMISSION_ACCOUNT} from this file, not from the
# shell, so a shell-only assignment renders as an empty string and the
# coordinator refuses to start: the three ADMISSION_SIGNER_* values are
# validated as a complete trio.
ADMISSION_ACCOUNT=${ADMISSION_ACCOUNT}
AGGREGATE_ACCOUNT=${SIGNER_ADDRESS[aggregate]}
POOL_FINALITY_ACCOUNT=${SIGNER_ADDRESS[pool-finality]}
POOL_BENEFICIARY_ACCOUNT=${SIGNER_ADDRESS[pool-beneficiary]}
WITHDRAWAL_ACCOUNT=${SIGNER_ADDRESS[withdrawal]}
BLOB_PUBLISHER_ACCOUNT=${SIGNER_ADDRESS[blob-publisher]}
NODE_LIVENESS_ACCOUNT=${SIGNER_ADDRESS[node-liveness]}
FAILOVER_PROVIDER_TOKEN=${EPH[failover_control]}
FAILOVER_APPROVAL_TOKEN=${EPH[failover_approval]}
TOKEN_INDEXER_WRITE=${PRINCIPAL_TOKEN[indexer_write]}
TOKEN_TENANT_A=${PRINCIPAL_TOKEN[tenant_a]}
TOKEN_AGENT_NODE=${PRINCIPAL_TOKEN[agent_node]}
TOKEN_NODE_A_NODE=${PRINCIPAL_TOKEN[node_a_node]}
ROOM_MANAGER=0x0000000000000000000000000000000000000000
ROOM_POOL=0x0000000000000000000000000000000000000000
ACCESS_TOKEN=0x0000000000000000000000000000000000000000
ENV
chmod 0600 "$WORK/.env"

# --- compose file -----------------------------------------------------------
# container_name is pinned so fault-control's containerPrefix contract holds.
# Compose still stamps com.docker.compose.{project,service}, which is what the
# failover provider filters on.
cat >"$WORK/compose.yaml" <<'COMPOSE'
name: ${PROJECT}

x-hardened: &hardened
  init: true
  restart: unless-stopped
  security_opt: ["no-new-privileges:true"]
  cap_drop: ["ALL"]
  networks: [soaknet]

x-owner: &owner
  init: true
  restart: unless-stopped
  security_opt: ["no-new-privileges:true"]
  cap_drop: ["ALL"]
  networks: [soaknet]
  image: ${IMG_COORDINATOR}
  working_dir: /app/web2-api/server
  read_only: true
  tmpfs: ["/tmp:size=256m,mode=1777"]

x-runtime-env: &runtime-env
  CHAIN_ID: "${CHAIN_ID}"
  L1_RPC_URL: http://rpc-a:8545
  # Two independent anvils never agree on block hashes (each mines its own
  # chain), and the protocol is deployed only to rpc-a. For this TEST rig both
  # coordinator provider slots read the same node so the mandatory two-provider
  # agreement check is satisfiable; fault-control still drives the two real
  # anvils for provider stop/start and reorg faults.
  L1_RPC_URLS: http://rpc-a:8545,http://rpc-b:8545
  L1_RPC_PROVIDER_IDS: soak6h-rpc-a,soak6h-rpc-b
  BEACON_SIDECAR_URLS: http://shim-beacon-a:8080/beacon,http://shim-beacon-b:8080/beacon
  ROOM_MANAGER: "${ROOM_MANAGER}"
  ROOM_POOL: "${ROOM_POOL}"
  ACCESS_TOKEN: "${ACCESS_TOKEN}"
  DATABASE_URL: postgresql://${PG_USER}:${PG_PASSWORD}@postgres-writer:5432/${PG_DB}
  API_KEY_PEPPER: "${API_KEY_PEPPER}"
  OBJECT_STORE_ENDPOINT: http://minio:9000
  OBJECT_STORE_BUCKET: zkdeal
  OBJECT_STORE_REGION: us-east-1
  OBJECT_STORE_PREFIX: hosted
  OBJECT_STORE_ACCESS_KEY_ID: "${MINIO_USER}"
  OBJECT_STORE_SECRET_ACCESS_KEY: "${MINIO_PASSWORD}"
  MAX_ARCHIVE_LAG_BLOCKS: "8"
  DEMO_ENABLED: "0"
  QUEUE_ENABLED: "1"
  L1_SIGNER_URL: http://web3signer:9000
  L1_SIGNER_ADDRESS: "${BLOB_PUBLISHER_ACCOUNT}"
  L1_SIGNER_AUTH_TOKEN: "${SIGNER_AUTH_TOKEN}"
  ROOM_OPERATIONS_SIGNER_URL: http://web3signer:9000
  ROOM_OPERATIONS_SIGNER_ADDRESS: "${OPERATIONS_ACCOUNT}"
  ROOM_OPERATIONS_SIGNER_AUTH_TOKEN: "${SIGNER_AUTH_TOKEN}"
  AGGREGATE_SIGNER_URL: http://web3signer:9000
  AGGREGATE_SIGNER_ADDRESS: "${AGGREGATE_ACCOUNT}"
  AGGREGATE_SIGNER_AUTH_TOKEN: "${SIGNER_AUTH_TOKEN}"
  POOL_SPONSOR_SIGNER_URL: http://web3signer:9000
  POOL_SPONSOR_SIGNER_ADDRESS: "${SPONSOR_ACCOUNT}"
  POOL_SPONSOR_SIGNER_AUTH_TOKEN: "${SIGNER_AUTH_TOKEN}"
  POOL_FINALITY_SIGNER_URL: http://web3signer:9000
  POOL_FINALITY_SIGNER_ADDRESS: "${POOL_FINALITY_ACCOUNT}"
  POOL_FINALITY_SIGNER_AUTH_TOKEN: "${SIGNER_AUTH_TOKEN}"
  POOL_BENEFICIARY_SIGNER_URL: http://web3signer:9000
  POOL_BENEFICIARY_SIGNER_ADDRESS: "${POOL_BENEFICIARY_ACCOUNT}"
  POOL_BENEFICIARY_SIGNER_AUTH_TOKEN: "${SIGNER_AUTH_TOKEN}"
  NODE_LIVENESS_SIGNER_URL: http://web3signer:9000
  NODE_LIVENESS_SIGNER_ADDRESS: "${NODE_LIVENESS_ACCOUNT}"
  NODE_LIVENESS_SIGNER_AUTH_TOKEN: "${SIGNER_AUTH_TOKEN}"

services:

  # ---- L1: two independently started providers, identical pinned genesis ----
  rpc-a:
    <<: *hardened
    container_name: ${PROJECT}-rpc-a
    image: ${IMG_FOUNDRY}
    entrypoint: ["anvil"]
    command: ["--host","0.0.0.0","--port","8545","--chain-id","${CHAIN_ID}","--timestamp","${ANVIL_TIMESTAMP}","--block-time","${ANVIL_BLOCK_TIME}"]
    networks:
      soaknet: { aliases: [rpc-a] }

  # rpc-b is a second, independently addressed and independently stoppable
  # endpoint that serves the SAME chain as rpc-a. Two separate anvils would
  # each mine their own chain and could never agree, and the coordinator
  # rightly refuses two identical URLs, so a TCP proxy is the only shape that
  # satisfies both the agreement contract and the provider-fault contract.
  rpc-b:
    container_name: ${PROJECT}-rpc-b
    image: alpine/socat:latest
    command: ["TCP-LISTEN:8545,fork,reuseaddr","TCP:rpc-a:8545"]
    depends_on: [rpc-a]
    networks:
      soaknet: { aliases: [rpc-b] }

  # ---- durable state -------------------------------------------------------
  postgres-primary:
    <<: *hardened
    container_name: ${PROJECT}-postgres-primary
    image: ${IMG_POSTGRES_HA}
    command: ["postgres","-c","wal_level=replica","-c","max_wal_senders=10","-c","max_replication_slots=10","-c","wal_keep_size=512MB"]
    environment:
      POSTGRES_DB: ${PG_DB}
      POSTGRES_USER: ${PG_USER}
      POSTGRES_PASSWORD: ${PG_PASSWORD}
      PGPASSWORD: ${PG_REPLICATION_PASSWORD}
      PGDATA: /var/lib/postgresql/data
    cap_add: ["CHOWN","DAC_OVERRIDE","FOWNER","SETGID","SETUID"]
    tmpfs: ["/tmp:size=64m,mode=1777"]
    volumes: [pg-primary:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL","pg_isready -U ${PG_USER} -d ${PG_DB}"]
      interval: 3s
      timeout: 3s
      retries: 60
      start_period: 15s
    networks:
      soaknet: { aliases: [postgres-writer] }

  postgres-standby:
    <<: *hardened
    container_name: ${PROJECT}-postgres-standby
    image: ${IMG_POSTGRES_HA}
    entrypoint: ["/bin/sh","/opt/zkdeal/standby-entrypoint.sh"]
    environment:
      POSTGRES_DB: ${PG_DB}
      POSTGRES_USER: ${PG_USER}
      POSTGRES_PASSWORD: ${PG_PASSWORD}
      PGPASSWORD: ${PG_REPLICATION_PASSWORD}
      PGDATA: /var/lib/postgresql/data
      PRIMARY_HOST: postgres-primary
      REPLICATION_SLOT: zkdeal_standby
      PGAPPNAME: zkdeal_standby
    cap_add: ["CHOWN","DAC_OVERRIDE","FOWNER","SETGID","SETUID"]
    tmpfs: ["/tmp:size=64m,mode=1777"]
    volumes: [pg-standby:/var/lib/postgresql/data]
    depends_on:
      postgres-primary: { condition: service_healthy }

  minio:
    <<: *hardened
    container_name: ${PROJECT}-minio
    image: ${IMG_MINIO}
    command: ["server","/data","--console-address",":9001"]
    environment:
      MINIO_ROOT_USER: ${MINIO_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_PASSWORD}
    volumes: [minio-data:/data]
    healthcheck:
      test: ["CMD","curl","-fsS","http://127.0.0.1:9000/minio/health/live"]
      interval: 3s
      timeout: 3s
      retries: 40
      start_period: 10s

  # An independently credentialled backup store: the backup adapter must never
  # be able to write into the live data store.
  minio-backup:
    <<: *hardened
    container_name: ${PROJECT}-minio-backup
    image: ${IMG_MINIO}
    command: ["server","/data"]
    environment:
      MINIO_ROOT_USER: ${BACKUP_MINIO_USER}
      MINIO_ROOT_PASSWORD: ${BACKUP_MINIO_PASSWORD}
    volumes: [minio-backup-data:/data]
    healthcheck:
      test: ["CMD","curl","-fsS","http://127.0.0.1:9000/minio/health/live"]
      interval: 3s
      timeout: 3s
      retries: 40
      start_period: 10s

  minio-fresh:
    <<: *hardened
    container_name: ${PROJECT}-minio-fresh
    image: ${IMG_MINIO}
    command: ["server","/data"]
    environment:
      MINIO_ROOT_USER: ${FRESH_MINIO_USER}
      MINIO_ROOT_PASSWORD: ${FRESH_MINIO_PASSWORD}
    volumes: [minio-fresh-data:/data]
    healthcheck:
      test: ["CMD","curl","-fsS","http://127.0.0.1:9000/minio/health/live"]
      interval: 3s
      timeout: 3s
      retries: 40
      start_period: 10s

  postgres-fresh:
    <<: *hardened
    container_name: ${PROJECT}-postgres-fresh
    image: ${IMG_POSTGRES}
    environment:
      POSTGRES_DB: ${PG_DB}
      POSTGRES_USER: ${PG_USER}
      POSTGRES_PASSWORD: ${FRESH_PG_PASSWORD}
    cap_add: ["CHOWN","DAC_OVERRIDE","FOWNER","SETGID","SETUID"]
    tmpfs: ["/tmp:size=64m,mode=1777"]
    volumes: [pg-fresh:/var/lib/postgresql/data]

  minio-init:
    image: ${IMG_MC}
    container_name: ${PROJECT}-minio-init
    entrypoint: ["/bin/sh","-ec"]
    command:
      - >-
        mc alias set live http://minio:9000 "$${MINIO_ROOT_USER}" "$${MINIO_ROOT_PASSWORD}";
        mc mb --ignore-existing live/zkdeal;
        mc anonymous set none live/zkdeal;
        mc mb --ignore-existing live/zkdeal-evidence;
        mc anonymous set none live/zkdeal-evidence;
        mc alias set backup http://minio-backup:9000 "$${BACKUP_ROOT_USER}" "$${BACKUP_ROOT_PASSWORD}";
        mc mb --ignore-existing backup/zkdeal-backups;
        mc anonymous set none backup/zkdeal-backups;
        mc alias set fresh http://minio-fresh:9000 "$${FRESH_ROOT_USER}" "$${FRESH_ROOT_PASSWORD}";
        mc mb --ignore-existing fresh/zkdeal-restored;
        mc anonymous set none fresh/zkdeal-restored
    environment:
      MINIO_ROOT_USER: ${MINIO_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_PASSWORD}
      BACKUP_ROOT_USER: ${BACKUP_MINIO_USER}
      BACKUP_ROOT_PASSWORD: ${BACKUP_MINIO_PASSWORD}
      FRESH_ROOT_USER: ${FRESH_MINIO_USER}
      FRESH_ROOT_PASSWORD: ${FRESH_MINIO_PASSWORD}
      MC_CONFIG_DIR: /tmp/.mc
    restart: "no"
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    read_only: true
    tmpfs: ["/tmp:size=16m,mode=1777"]
    networks: [soaknet]

  # ---- scoped L1 signing authority -----------------------------------------
  web3signer:
    <<: *hardened
    container_name: ${PROJECT}-web3signer
    image: ${IMG_WEB3SIGNER}
    command:
      - --http-listen-host=0.0.0.0
      - --http-listen-port=9000
      - --http-host-allowlist=*
      - --key-store-path=/var/lib/web3signer/keys
      - eth1
      - --chain-id=${CHAIN_ID}
    volumes:
      - ${WORK}/web3signer/keys:/var/lib/web3signer/keys:ro
    read_only: true
    tmpfs: ["/tmp:size=64m,mode=1777"]

  # ---- coordinator plane ----------------------------------------------------
  coordinator-active:
    <<: *owner
    container_name: ${PROJECT}-coordinator-active
    command: ["node","dist/index.js"]
    environment:
      <<: *runtime-env
      PORT: "3000"
      HOST: 0.0.0.0
      COORDINATOR_ROLE: active
      COORDINATOR_ID: ${ACTIVE_COORDINATOR_ID}
      INDEXER_TOKEN: ${TOKEN_INDEXER_WRITE}
      ADMISSION_TOKEN: ${TOKEN_NODE_A_NODE}
      # Active only. The standby deliberately blanks every signer: it holds no
      # writer fence, so any L1 publisher it constructs aborts in assertReady.
      ADMISSION_SIGNER_URL: http://web3signer:9000
      ADMISSION_SIGNER_ADDRESS: "${ADMISSION_ACCOUNT}"
      ADMISSION_SIGNER_AUTH_TOKEN: "${SIGNER_AUTH_TOKEN}"
    healthcheck:
      test: ["CMD","node","-e","fetch('http://127.0.0.1:3000/hosting/v1/ready').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
      interval: 5s
      timeout: 3s
      retries: 90
      start_period: 45s
    networks:
      soaknet: { aliases: [coordinator-active, coordinator-writer] }

  coordinator-standby:
    <<: *owner
    container_name: ${PROJECT}-coordinator-standby
    command: ["node","dist/index.js"]
    environment:
      <<: *runtime-env
      PORT: "3000"
      HOST: 0.0.0.0
      COORDINATOR_ROLE: standby
      COORDINATOR_ID: ${STANDBY_COORDINATOR_ID}
      # A standby holds no writer fence, so it must not construct L1
      # publishers: assertReady() demands a writable fence and aborts startup.
      L1_SIGNER_URL: ""
      NODE_LIVENESS_SIGNER_URL: ""
      ROOM_OPERATIONS_SIGNER_URL: ""
      AGGREGATE_SIGNER_URL: ""
      POOL_SPONSOR_SIGNER_URL: ""
      POOL_FINALITY_SIGNER_URL: ""
      POOL_BENEFICIARY_SIGNER_URL: ""
      WITHDRAWAL_SIGNER_URL: ""
      L1_SIGNER_ADDRESS: ""
      L1_SIGNER_AUTH_TOKEN: ""
      NODE_LIVENESS_SIGNER_ADDRESS: ""
      NODE_LIVENESS_SIGNER_AUTH_TOKEN: ""
      ROOM_OPERATIONS_SIGNER_ADDRESS: ""
      ROOM_OPERATIONS_SIGNER_AUTH_TOKEN: ""
      AGGREGATE_SIGNER_ADDRESS: ""
      AGGREGATE_SIGNER_AUTH_TOKEN: ""
      POOL_SPONSOR_SIGNER_ADDRESS: ""
      POOL_SPONSOR_SIGNER_AUTH_TOKEN: ""
      POOL_FINALITY_SIGNER_ADDRESS: ""
      POOL_FINALITY_SIGNER_AUTH_TOKEN: ""
      POOL_BENEFICIARY_SIGNER_ADDRESS: ""
      POOL_BENEFICIARY_SIGNER_AUTH_TOKEN: ""
      WITHDRAWAL_SIGNER_ADDRESS: ""
      WITHDRAWAL_SIGNER_AUTH_TOKEN: ""
      ADMISSION_SIGNER_ADDRESS: ""
      ADMISSION_SIGNER_AUTH_TOKEN: ""
      ADMISSION_SIGNER_URL: ""
    healthcheck:
      test: ["CMD","node","-e","fetch('http://127.0.0.1:3000/hosting/v1/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
      interval: 5s
      timeout: 3s
      retries: 90
      start_period: 45s
    networks:
      soaknet: { aliases: [coordinator-standby] }

  indexer:
    <<: *owner
    container_name: ${PROJECT}-indexer
    command: ["node","dist/hosted-worker.js","indexer"]
    environment:
      <<: *runtime-env
      WORKER_HOST: 0.0.0.0
      WORKER_PORT: "3001"
      HOSTED_WORKER_ROLE: indexer
      HOSTED_WORKER_ID: soak6h-indexer-1
      COORDINATOR_ID: ${ACTIVE_COORDINATOR_ID}
      INDEXER_BOOTSTRAP_BLOCK: "0x0"

  reconciler:
    <<: *owner
    container_name: ${PROJECT}-reconciler
    command: ["node","dist/hosted-worker.js","reconciler"]
    environment:
      <<: *runtime-env
      WORKER_HOST: 0.0.0.0
      WORKER_PORT: "3001"
      HOSTED_WORKER_ROLE: reconciler
      HOSTED_WORKER_ID: soak6h-reconciler-1
      COORDINATOR_ID: ${ACTIVE_COORDINATOR_ID}
      INDEXER_BOOTSTRAP_BLOCK: "0x0"

  publisher:
    <<: *owner
    container_name: ${PROJECT}-publisher
    command: ["node","dist/hosted-publisher-worker.js"]
    environment:
      <<: *runtime-env
      WORKER_HOST: 0.0.0.0
      WORKER_PORT: "3002"
      HOSTED_WORKER_ID: soak6h-publisher-1
      COORDINATOR_ID: ${ACTIVE_COORDINATOR_ID}
      BLOB_PUBLISHER_ENABLED: "1"

  auto-claimer:
    <<: *owner
    container_name: ${PROJECT}-auto-claimer
    command: ["node","dist/hosted-withdrawal-worker.js","run"]
    environment:
      <<: *runtime-env
      WORKER_HOST: 0.0.0.0
      WORKER_PORT: "3003"
      HOSTED_WORKER_ID: soak6h-auto-claimer-1
      HOSTED_WORKER_INTERVAL_MS: "3000"
      COORDINATOR_ID: ${ACTIVE_COORDINATOR_ID}
      WITHDRAWAL_CLAIMER_ENABLED: "1"
      WITHDRAWAL_CLAIM_GAS_LIMIT: "500000"
      WITHDRAWAL_CLAIM_INCLUSION_WINDOW_BLOCKS: "64"
      WITHDRAWAL_SIGNER_URL: http://web3signer:9000
      WITHDRAWAL_SIGNER_ADDRESS: ${WITHDRAWAL_ACCOUNT}
      WITHDRAWAL_SIGNER_AUTH_TOKEN: ${SIGNER_AUTH_TOKEN}

  # ---- proving plane --------------------------------------------------------
  prover:
    <<: *hardened
    container_name: ${PROJECT}-prover
    image: ${IMG_PROVER}
    command: ["serve","--host","0.0.0.0","--port","8080"]
    environment:
      RISC0_DEV_MODE: "0"
      RISC0_PROVER: local
      RISC0_REQUIRE_CUDA: "1"
      CUDA_VISIBLE_DEVICES: "0"
      ZKDEAL_PROVER_TOKEN: ${PROVER_TOKEN}
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]

  prover-agent:
    <<: *hardened
    container_name: ${PROJECT}-prover-agent
    image: ${IMG_AGENT}
    command: ["node","/app/agent/agent.js"]
    environment:
      QUEUE_URL: http://coordinator-writer:3000
      ZKDEAL_QUEUE_NODE_TOKEN: ${TOKEN_AGENT_NODE}
      NODE_ID: soak6h-gpu-0
      PROVER_URL: http://prover:8080
      ZKDEAL_PROVER_TOKEN: ${PROVER_TOKEN}
      ZKDEAL_AGENT_GPU: "1"
      POLL_INTERVAL_MS: "500"
    read_only: true
    tmpfs: ["/tmp:size=256m,mode=1777"]

  # ---- headless room node ---------------------------------------------------
  headless-node-secret-init:
    image: ${IMG_HEADLESS}
    container_name: ${PROJECT}-headless-node-secret-init
    user: "0:0"
    entrypoint: ["/bin/sh","-ec"]
    command:
      - >-
        umask 077;
        cp /source/keys.json /destination/keys.json;
        cp /source/control.token /destination/control.token;
        cp /source/room-operator.token /destination/room-operator.token;
        cp /source/submit.token /destination/submit.token;
        cp /source/read.token /destination/read.token;
        chmod 0600 /destination/keys.json /destination/control.token /destination/room-operator.token /destination/submit.token /destination/read.token;
        chown 10001:10001 /destination/keys.json /destination/control.token /destination/room-operator.token /destination/submit.token /destination/read.token
    volumes:
      - ${WORK}/room-node/secrets:/source:ro
      - room-node-secrets:/destination
    network_mode: none
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    cap_add: ["CHOWN"]
    read_only: true
    restart: "no"

  headless-node:
    <<: *hardened
    container_name: ${PROJECT}-headless-node
    image: ${IMG_HEADLESS}
    command: ["node","dist/cli.js","run"]
    working_dir: /app/app-node/packages/room-node
    environment:
      ROOM_NODE_CONFIG_PATH: /run/zkdeal/room-node.json
    volumes:
      - ${WORK}/room-node/room-node.json:/run/zkdeal/room-node.json:ro
      - room-node-secrets:/run/zkdeal-secrets:ro
      - room-node-state:/var/lib/zkdeal-room-node
    read_only: true
    tmpfs: ["/tmp:size=256m,mode=1777"]

  # ---- reshaping shims ------------------------------------------------------
  # Two witnesses on DISTINCT container IPs: the failover provider rejects
  # witnesses whose resolved network identities overlap.
  witness-a:
    <<: *hardened
    container_name: ${PROJECT}-witness-a
    image: ${IMG_PYTHON}
    command: ["python","/opt/shim/soak_shim.py"]
    environment:
      SHIM_UPSTREAM: http://coordinator-active:3000
      SHIM_COORDINATOR_ID: ${ACTIVE_COORDINATOR_ID}
      SHIM_TOKEN: ${TOKEN_TENANT_A}
    volumes: ["${WORK}/shim:/opt/shim:ro"]
    read_only: true
    tmpfs: ["/tmp:size=8m,mode=1777"]

  witness-b:
    <<: *hardened
    container_name: ${PROJECT}-witness-b
    image: ${IMG_PYTHON}
    command: ["python","/opt/shim/soak_shim.py"]
    environment:
      SHIM_UPSTREAM: http://coordinator-active:3000
      SHIM_COORDINATOR_ID: ${ACTIVE_COORDINATOR_ID}
      SHIM_TOKEN: ${TOKEN_TENANT_A}
    volumes: ["${WORK}/shim:/opt/shim:ro"]
    read_only: true
    tmpfs: ["/tmp:size=8m,mode=1777"]

  shim-standby:
    <<: *hardened
    container_name: ${PROJECT}-shim-standby
    image: ${IMG_PYTHON}
    command: ["python","/opt/shim/soak_shim.py"]
    environment:
      SHIM_UPSTREAM: http://coordinator-standby:3000
      SHIM_COORDINATOR_ID: ${STANDBY_COORDINATOR_ID}
      SHIM_TOKEN: ${TOKEN_TENANT_A}
    volumes: ["${WORK}/shim:/opt/shim:ro"]
    read_only: true
    tmpfs: ["/tmp:size=8m,mode=1777"]

  # Freshness gate, the signer-authority probe, and LOG_QUERY_URL.
  shim-observer:
    <<: *hardened
    container_name: ${PROJECT}-shim-observer
    image: ${IMG_PYTHON}
    command: ["python","/opt/shim/soak_shim.py"]
    environment:
      SHIM_UPSTREAM: http://coordinator-writer:3000
      SHIM_COORDINATOR_ID: ${ACTIVE_COORDINATOR_ID}
      SHIM_SIGNER_URL: http://web3signer:9000
      SHIM_TOKEN: ${TOKEN_TENANT_A}
    volumes: ["${WORK}/shim:/opt/shim:ro"]
    read_only: true
    tmpfs: ["/tmp:size=8m,mode=1777"]

  shim-beacon-a:
    <<: *hardened
    container_name: ${PROJECT}-shim-beacon-a
    image: ${IMG_PYTHON}
    command: ["python","/opt/shim/soak_shim.py"]
    environment:
      SHIM_UPSTREAM: http://coordinator-writer:3000
    volumes: ["${WORK}/shim:/opt/shim:ro"]
    read_only: true
    tmpfs: ["/tmp:size=8m,mode=1777"]

  shim-beacon-b:
    <<: *hardened
    container_name: ${PROJECT}-shim-beacon-b
    image: ${IMG_PYTHON}
    command: ["python","/opt/shim/soak_shim.py"]
    environment:
      SHIM_UPSTREAM: http://coordinator-writer:3000
    volumes: ["${WORK}/shim:/opt/shim:ro"]
    read_only: true
    tmpfs: ["/tmp:size=8m,mode=1777"]

  # ---- the auth-translating edge -------------------------------------------
  soak-edge:
    <<: *hardened
    container_name: ${PROJECT}-soak-edge
    user: "101:101"
    image: ${IMG_NGINX}
    volumes:
      - ${WORK}/nginx/soak-edge.conf:/etc/nginx/nginx.conf:ro
    read_only: true
    tmpfs:
      - /var/cache/nginx:size=64m,mode=0777
      - /run:size=1m,mode=0777
      - /var/run:size=1m,mode=0777
      - /tmp:size=16m

  # ---- reviewed control adapters -------------------------------------------
  fault-control:
    <<: *hardened
    container_name: ${PROJECT}-fault-control
    image: ${IMG_FAULT}
    group_add: ["${DOCKER_GID}"]
    read_only: true
    tmpfs: ["/journal:rw,uid=65532,gid=65532,mode=0700"]
    volumes:
      - ${WORK}/fault-topology.json:/topology.json:ro
      - ${WORK}/ctl-secrets/fault-token:/fault-token:ro
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      FAULT_CONTROL_TOPOLOGY_FILE: /topology.json
      FAULT_CONTROL_TOPOLOGY_SHA256: ${FAULT_TOPOLOGY_SHA256}
      FAULT_CONTROL_CANDIDATE_ID: ${CANDIDATE_ID}
      FAULT_CONTROL_PLAN_SHA256: ${PLAN_SHA256}
      FAULT_CONTROL_HOSTED_INTEGRATION_TOKEN: ${HOSTED_INTEGRATION_TOKEN}
      FAULT_CONTROL_TOKEN_FILE: /fault-token
      FAULT_CONTROL_CANDIDATE_DESCRIPTOR_SHA256: ${CANDIDATE_DESCRIPTOR_SHA256}
      FAULT_CONTROL_ADAPTER_SOURCE_SHA256: ${FAULT_SOURCE_SHA256}
      FAULT_CONTROL_ADAPTER_IMAGE: registry.local/zkdeal-fault-control@sha256:${FAULT_SOURCE_SHA256}
      FAULT_CONTROL_PLATFORM: docker
    networks:
      soaknet: { aliases: [fault-control] }

  backup-restore-control:
    <<: *hardened
    container_name: ${PROJECT}-backup-restore-control
    image: ${IMG_BACKUP}
    read_only: true
    tmpfs:
      - /journal:rw,uid=65532,gid=65532,mode=0700
      - /tmp:rw,uid=65532,gid=65532,mode=0700
      - /work:rw,uid=65532,gid=65532,mode=0700
    volumes:
      - ${WORK}/backup-topology.json:/topology.json:ro
      - ${WORK}/ctl-secrets:/secrets:ro
    environment:
      BACKUP_RESTORE_TOPOLOGY_FILE: /topology.json
      BACKUP_RESTORE_TOPOLOGY_SHA256: ${BACKUP_TOPOLOGY_SHA256}
      BACKUP_RESTORE_CANDIDATE_ID: ${CANDIDATE_ID}
      BACKUP_RESTORE_PLAN_SHA256: ${PLAN_SHA256}
      BACKUP_RESTORE_HOSTED_INTEGRATION_TOKEN: ${HOSTED_INTEGRATION_TOKEN}
      BACKUP_RESTORE_TOKEN_FILE: /secrets/backup-restore-token
      BACKUP_RESTORE_CANDIDATE_DESCRIPTOR_SHA256: ${CANDIDATE_DESCRIPTOR_SHA256}
      BACKUP_RESTORE_ADAPTER_SOURCE_SHA256: ${BACKUP_SOURCE_SHA256}
      BACKUP_RESTORE_ADAPTER_IMAGE: registry.local/zkdeal-backup-restore-control@sha256:${BACKUP_SOURCE_SHA256}
      BACKUP_RESTORE_PLATFORM: docker
    networks:
      soaknet: { aliases: [backup-restore-control] }

  failover-provider:
    <<: *hardened
    container_name: ${PROJECT}-failover-provider
    image: ${IMG_FAILOVER}
    group_add: ["0"]
    read_only: true
    tmpfs: ["/tmp:size=16m,mode=1777"]
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - failover-provider-state:/var/lib/zkdeal-failover-provider
    environment:
      FAILOVER_PLATFORM: docker
      FAILOVER_PROVIDER_ALLOW_INSECURE_HTTP: acceptance-only
      ACTIVE_HEALTH_URLS: http://witness-a:8080/health,http://witness-b:8080/health
      STANDBY_HEALTH_URL: http://shim-standby:8080/health
      INDEXER_FRESHNESS_URL: http://shim-observer:8080/freshness
      SIGNER_AUTHORITY_HEALTH_URLS: http://shim-observer:8080/signer
      ACTIVE_COORDINATOR_ID: ${ACTIVE_COORDINATOR_ID}
      STANDBY_COORDINATOR_ID: ${STANDBY_COORDINATOR_ID}
      FAILOVER_PROVIDER_TOKEN: ${FAILOVER_PROVIDER_TOKEN}
      FAILOVER_APPROVAL_TOKEN: ${FAILOVER_APPROVAL_TOKEN}
      FAILOVER_PROVIDER_STATE_PATH: /var/lib/zkdeal-failover-provider/state.json
      FAILOVER_PROVIDER_LISTEN_PORT: "8443"
      REQUEST_TIMEOUT_SECONDS: "5"
      PLATFORM_TIMEOUT_SECONDS: "180"
      DOCKER_COMPOSE_PROJECT: ${PROJECT}
      DOCKER_FAILOVER_NETWORK: ${PROJECT}_soaknet
      DOCKER_ACTIVE_SERVICE: coordinator-active
      DOCKER_STANDBY_SERVICE: coordinator-standby
      DOCKER_PRIMARY_DB_SERVICE: postgres-primary
      DOCKER_STANDBY_DB_SERVICE: postgres-standby
      DOCKER_SIGNER_SERVICE: web3signer
      DOCKER_DATABASE_ALIAS: postgres-writer
      DOCKER_APPLICATION_ALIAS: coordinator-writer
      FAILOVER_PGUSER: ${PG_USER}
      FAILOVER_PGDATABASE: ${PG_DB}
      FAILOVER_PGDATA: /var/lib/postgresql/data
    networks:
      soaknet: { aliases: [failover-provider] }

volumes:
  pg-primary: {}
  pg-standby: {}
  pg-fresh: {}
  minio-data: {}
  minio-backup-data: {}
  minio-fresh-data: {}
  room-node-secrets: {}
  room-node-state: {}
  failover-provider-state: {}

networks:
  soaknet:
    name: ${PROJECT}_soaknet
COMPOSE

# Placeholder topologies so compose can interpolate the bind mounts before
# PHASE 11 writes the real ones.
printf '{}' >"$WORK/fault-topology.json"
printf '{}' >"$WORK/backup-topology.json"
chmod 0644 "$WORK/fault-topology.json" "$WORK/backup-topology.json"

dc config >/dev/null || die "the generated compose file at $WORK/compose.yaml is not valid"
log "compose project $PROJECT validated"

# ---------------------------------------------------------------------------
# 6. Base plane: L1, database, object stores, signer
# ---------------------------------------------------------------------------

step "PHASE 6  starting L1, database, object stores and signer"

dc up -d rpc-a rpc-b postgres-primary postgres-standby minio minio-backup \
        minio-fresh postgres-fresh web3signer >/dev/null \
  || die "compose could not start the base plane; inspect: docker compose -p $PROJECT -f $WORK/compose.yaml ps -a"

rpc_ready rpc-a
rpc_ready rpc-b

genesis_a="$(genesis_hash rpc-a)"
genesis_b="$(genesis_hash rpc-b)"
[ -n "$genesis_a" ] && [ "$genesis_a" = "$genesis_b" ] \
  || die "rpc-a and rpc-b disagree at genesis (a=$genesis_a b=$genesis_b); the pinned --timestamp did not take effect"
log "ok: rpc-a and rpc-b share a genesis hash"

wait_for postgres-primary 240 pg_ready postgres-primary
wait_for postgres-standby 300 pg_ready postgres-standby
wait_for postgres-fresh   240 pg_ready postgres-fresh
wait_http minio        http://minio:9000/minio/health/live        180
wait_http minio-backup http://minio-backup:9000/minio/health/live 180
wait_http minio-fresh  http://minio-fresh:9000/minio/health/live  180
wait_http web3signer   http://web3signer:9000/upcheck             240

# Synchronous replication, so the failover provider's replay target is real.
psql_primary -c "alter system set synchronous_standby_names='FIRST 1 (zkdeal_standby)'" >/dev/null
psql_primary -c "select pg_reload_conf()" >/dev/null
sync_streaming() {
  [ "$(psql_primary -tAc "select count(*) from pg_stat_replication where application_name='zkdeal_standby' and state='streaming' and sync_state in ('sync','quorum')" | tr -d '[:space:]')" = "1" ]
}
wait_for postgres-standby 240 sync_streaming
log "ok: postgres-standby is streaming synchronously"

dc up -d minio-init >/dev/null
wait_for minio-init 240 exited_ok minio-init

# ---------------------------------------------------------------------------
# 7. Deploy the protocol to rpc-a
# ---------------------------------------------------------------------------

# KNOWN DEFECT, deliberately documented rather than bodged.
# RoomPoolManager.initialize grants POOL_CONTROLLER_ROLE, SPONSOR_ROLE and
# FINALITY_ORACLE_ROLE all to this single `controller` address, but the
# coordinator signs each of those operations with a DIFFERENT scoped
# identity: room operations with OPERATIONS_ACCOUNT, sponsor mutations
# with SPONSOR_ACCOUNT, finality with POOL_FINALITY_ACCOUNT. Every sponsor
# mutation and every finality publication therefore reverts
# AccessControlUnauthorizedAccount, which is one of the two things
# blocking the owner release soak's pool-sponsor lifecycle. Fixing it
# needs post-deploy grants to the correct accounts (behind the timelock)
# or an initialize that takes the three identities separately.

step "PHASE 7  deploying the protocol to rpc-a"

# Deploy from a link-farm copy so the container never writes into the repo tree
# and no permission juggling is required.
CONTRACTS="$WORK/contracts"
rm -rf "$CONTRACTS"; mkdir -p "$CONTRACTS"
cp -a "$REPO/web3-protocol/contracts/." "$CONTRACTS/" \
  || cp -r "$REPO/web3-protocol/contracts/." "$CONTRACTS/" \
  || die "could not stage a writable copy of web3-protocol/contracts under $WORK"
# The foundry image runs as its own uid; the staged copy must be writable by it.
chmod -R a+rwX "$CONTRACTS"
rm -rf "$CONTRACTS/cache"
rm -rf "$CONTRACTS/deployments"
mkdir -p "$CONTRACTS/deployments"
chmod 0777 "$CONTRACTS/deployments"  # forge writes addresses.json as its own uid

docker run --rm --network "$NETWORK" \
  -v "$CONTRACTS:/app/contracts" \
  -w /app/contracts \
  -e HOME=/tmp \
  -e DEPLOYER_KEY="$DEPLOYER_KEY" \
  -e GOVERNANCE_SAFE="$DEPLOYER_ADDRESS" \
  -e GUARDIAN_SAFE="$DEPLOYER_ADDRESS" \
  -e TREASURY="$DEPLOYER_ADDRESS" \
  -e NODE_ADMIN="$DEPLOYER_ADDRESS" \
  -e TEMPLATE_ADMIN="$DEPLOYER_ADDRESS" \
  -e POOL_CONTROLLER="$OPERATIONS_ACCOUNT" \
  -e DEPLOYMENT_DOMAIN="$DEPLOYMENT_DOMAIN" \
  -e GOVERNANCE_PROFILE=stage1 \
  -e ZKDEAL_TEST_RIG_TIMELOCK="$([ "$CREATE_REAL_ROOM" = "1" ] && echo true || echo false)" \
  -e TIMELOCK_DELAY_SECONDS=1 \
  --entrypoint forge "$IMG_FOUNDRY" \
  script script/Deploy.s.sol:Deploy --rpc-url http://rpc-a:8545 --broadcast --slow \
  >"$WORK/logs/deploy.log" 2>&1 \
  || { tail -80 "$WORK/logs/deploy.log" >&2; die "contract deployment failed; full log at $WORK/logs/deploy.log"; }

ADDRESSES_JSON="$CONTRACTS/deployments/addresses.json"
[ -f "$ADDRESSES_JSON" ] || die "Deploy.s.sol did not write deployments/addresses.json (see $WORK/logs/deploy.log)"
cp "$ADDRESSES_JSON" "$WORK/deploy-out/addresses.json"

json_field() {
  grep -o "\"$1\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" "$WORK/deploy-out/addresses.json" \
    | head -1 | sed 's/.*"\([^"]*\)"$/\1/'
}
ROOM_MANAGER="$(lower "$(json_field roomManager)")"
ROOM_POOL="$(lower "$(json_field roomPool)")"
ACCESS_TOKEN="$(lower "$(json_field accessToken)")"
# PHASE 9b needs both of these. Deploy.s.sol has recorded the registry
# under more than one key across vintages, so try both rather than die
# late inside create-room.sh with an unbound variable.
COLD_TEMPLATE_REGISTRY="$(lower "$(json_field coldTemplateRegistry)")"
[ -n "$COLD_TEMPLATE_REGISTRY" ] || COLD_TEMPLATE_REGISTRY="$(lower "$(json_field registry)")"
TEST_RIG_TIMELOCK="$(lower "$(json_field testRigTimelock)")"
for pair in "roomManager=$ROOM_MANAGER" "roomPool=$ROOM_POOL" "accessToken=$ACCESS_TOKEN"; do
  value="${pair#*=}"
  [[ "$value" =~ ^0x[0-9a-f]{40}$ ]] \
    || die "deployment did not yield a usable ${pair%%=*} address (got '$value'); see $WORK/logs/deploy.log"
done
log "roomManager=$ROOM_MANAGER roomPool=$ROOM_POOL accessToken=$ACCESS_TOKEN"

# Fund every scoped signing identity so no L1 operation stalls on gas.
for role in "${SIGNER_ROLES[@]}"; do
  docker run --rm --network "$NETWORK" --entrypoint cast "$IMG_FOUNDRY" \
    rpc anvil_setBalance "${SIGNER_ADDRESS[$role]}" 0x21e19e0c9bab2400000 \
    --rpc-url http://rpc-a:8545 >/dev/null \
    || die "could not fund the $role signing identity on rpc-a"
done
log "funded ${#SIGNER_ROLES[@]} scoped L1 signing identities"

sed -i \
  -e "s|^ROOM_MANAGER=.*|ROOM_MANAGER=${ROOM_MANAGER}|" \
  -e "s|^ROOM_POOL=.*|ROOM_POOL=${ROOM_POOL}|" \
  -e "s|^ACCESS_TOKEN=.*|ACCESS_TOKEN=${ACCESS_TOKEN}|" \
  "$WORK/.env"

# ---------------------------------------------------------------------------
# 8. Coordinator, principals, workers, edge
# ---------------------------------------------------------------------------

# Replaying from genesis makes the indexer ingest the deployment transactions
# themselves, whose targets are not configured sources. Anchor it at the
# post-deployment head instead (an explicit hexadecimal deployment anchor).
ANCHOR_HEX=$(docker run --rm --network "$NETWORK" $IMG_CURL -s -X POST \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' \
  http://rpc-a:8545 | sed -n 's/.*"result":"\(0x[0-9a-fA-F]*\)".*/\1/p')
[ -n "$ANCHOR_HEX" ] || die "could not read the post-deployment block number from rpc-a"
step "indexer deployment anchor: $ANCHOR_HEX"
sed -i "s|INDEXER_BOOTSTRAP_BLOCK: \"0x0\"|INDEXER_BOOTSTRAP_BLOCK: \"$ANCHOR_HEX\"|g" "$WORK/compose.yaml"

step "PHASE 8  starting the active coordinator"

dc up -d coordinator-active >/dev/null || die_service coordinator-active "compose refused to start it"
wait_http coordinator-active http://coordinator-active:3000/hosting/v1/ready 420

step "PHASE 8b  seeding coordinator tenants and principals"

# The coordinator has now applied HOSTED_SCHEMA_SQL, so the rows can be
# inserted. key_hash is HMAC-SHA256(API_KEY_PEPPER, token) -- exactly what
# postgres-hosted-store.ts recomputes on every authenticate().
LIMITS='{"queueWeight":1,"maxQueuedJobs":4096,"maxQueuedBytes":1073741824,"maxConcurrentJobs":64,"requestsPerMinute":100000,"sseConnections":64}'

hmac_input=""
for spec in "${PRINCIPAL_SPECS[@]}"; do
  IFS='|' read -r name _t _p _k _r <<<"$spec"
  hmac_input="${hmac_input}${name} ${PRINCIPAL_TOKEN[$name]}"$'\n'
done

hmac_out="$(printf '%s' "$hmac_input" | docker run --rm -i \
  -e PEPPER="$API_KEY_PEPPER" "$IMG_ALPINE" sh -c '
  apk add --no-cache openssl >/dev/null 2>&1 || exit 1
  while read -r name token; do
    [ -n "$name" ] || continue
    mac=$(printf "%s" "$token" | openssl dgst -sha256 -mac HMAC -macopt "key:$PEPPER" -hex | sed "s/.*= *//")
    printf "%s %s\n" "$name" "$mac"
  done')" || die "the openssl HMAC step failed inside $IMG_ALPINE"
[ -n "$hmac_out" ] || die "the openssl HMAC step produced no output; principals cannot be seeded"

declare -A KEY_HASH
while read -r name mac; do
  [ -n "$name" ] || continue
  case "$mac" in
    [0-9a-f]*) KEY_HASH[$name]="$mac" ;;
    *) die "principal $name produced a malformed key hash '$mac'" ;;
  esac
done <<<"$hmac_out"

{
  printf 'begin;\n'
  for tenant in tenant-a tenant-b; do
    printf "insert into hosted_tenants(tenant_id,display_name,tier,limits) values ('%s','soak6h %s','soak','%s'::jsonb) on conflict (tenant_id) do update set limits=excluded.limits, active=true;\n" \
      "$tenant" "$tenant" "$LIMITS"
  done
  for spec in "${PRINCIPAL_SPECS[@]}"; do
    IFS='|' read -r name tenant _prefix kind roles <<<"$spec"
    token="${PRINCIPAL_TOKEN[$name]}"
    principal_id="${token#zkd.}"; principal_id="${principal_id%%.*}"
    mac="${KEY_HASH[$name]:-}"
    [ -n "$mac" ] || die "no key hash was computed for principal $name"
    role_array="$(printf '%s' "$roles" | sed "s/[^,][^,]*/'&'/g")"
    printf "insert into hosted_principals(principal_id,tenant_id,kind,key_hash,roles,limits) values ('%s','%s','%s',decode('%s','hex'),ARRAY[%s]::text[],'%s'::jsonb) on conflict (principal_id) do update set key_hash=excluded.key_hash, roles=excluded.roles, limits=excluded.limits, active=true, revoked_at=null, overlap_until=null;\n" \
      "$principal_id" "$tenant" "$kind" "$mac" "$role_array" "$LIMITS"
  done
  printf 'commit;\n'
} >"$WORK/seed-principals.sql"
chmod 0600 "$WORK/seed-principals.sql"

psql_primary <"$WORK/seed-principals.sql" >/dev/null \
  || die "seeding the coordinator principals failed; inspect $WORK/seed-principals.sql"
log "seeded 2 tenants and ${#PRINCIPAL_SPECS[@]} principals"

probe="$(curl_net -o /dev/null -w '%{http_code}' \
  -H "authorization: Bearer ${PRINCIPAL_TOKEN[tenant_a]}" \
  -H 'accept-schema-version: 1' \
  http://coordinator-active:3000/hosting/v1/capabilities || true)"
[ "$probe" = "200" ] \
  || die_service coordinator-active "the seeded tenant-a principal was rejected (HTTP $probe); API_KEY_PEPPER or the HMAC seeding does not match"
log "ok: the seeded tenant-a principal authenticates"

# The soak driver refuses to start unless all three managed L1 surfaces are on.
caps="$(curl_net --fail -H "authorization: Bearer ${PRINCIPAL_TOKEN[tenant_a]}" \
  -H 'accept-schema-version: 1' \
  http://coordinator-active:3000/hosting/v1/capabilities || true)"
for surface in roomBatch roomAggregate poolSponsorMutation; do
  printf '%s' "$caps" | tr ',' '\n' | grep -q . || die "capabilities document was empty"
  printf '%s' "$caps" | grep -Fq "\"${surface}\"" \
    || die_service coordinator-active "capabilities omit managedL1Operations.${surface}"
done
if printf '%s' "$caps" | grep -Fq '"scoped-room-operations-signer-not-configured"' \
   || printf '%s' "$caps" | grep -Fq '"scoped-aggregate-signer-not-configured"'; then
  die_service coordinator-active "managedL1Operations are disabled; the scoped signer configuration did not take"
fi
log "ok: managedL1Operations advertise roomBatch, roomAggregate and poolSponsorMutation"

step "PHASE 8c  starting workers, standby, shims and the auth edge"

dc up -d coordinator-standby indexer reconciler publisher auto-claimer \
        witness-a witness-b shim-standby shim-observer shim-beacon-a shim-beacon-b >/dev/null \
  || die "compose could not start the worker plane"

wait_http coordinator-standby http://coordinator-standby:3000/hosting/v1/health 420
wait_http indexer      http://indexer:3001/ready      420
wait_http reconciler   http://reconciler:3001/ready   420
wait_http publisher    http://publisher:3002/ready    420
wait_http auto-claimer http://auto-claimer:3003/ready 420
wait_http witness-a     http://witness-a:8080/ready     180
wait_http witness-b     http://witness-b:8080/ready     180
wait_http shim-standby  http://shim-standby:8080/ready  180
wait_http shim-observer http://shim-observer:8080/ready 180
wait_http shim-observer http://shim-observer:8080/signer 180

dc up -d soak-edge >/dev/null || die_service soak-edge "compose refused to start it"
wait_http soak-edge http://soak-edge:3000/hosting/v1/ready 180

edge_probe="$(curl_net -o /dev/null -w '%{http_code}' \
  -H "authorization: Bearer ${EPH[tenant_a]}" -H 'accept-schema-version: 1' \
  http://soak-edge:3000/hosting/v1/capabilities || true)"
[ "$edge_probe" = "200" ] \
  || die_service soak-edge "eph_ token translation is not working (HTTP $edge_probe)"
log "ok: the auth edge translates eph_ tokens into coordinator principals"

# ---------------------------------------------------------------------------
# 9. Proving plane
# ---------------------------------------------------------------------------

step "PHASE 9  starting the CUDA prover and the prover agent"

dc up -d prover >/dev/null || die_service prover "compose refused to start it"
# The first CUDA start builds kernels; bounded but generous.
wait_http prover http://prover:8080/healthz "${SOAK6H_PROVER_READY_SECONDS:-1200}"
wait_http soak-edge http://soak-edge:8080/health 180

dc up -d prover-agent >/dev/null || die_service prover-agent "compose refused to start it"
wait_for prover-agent 240 running prover-agent

# ---------------------------------------------------------------------------
# 9b. A real on-chain room, before anything is built on top of it.
#
# This runs here and not later because both preconditions are now satisfied -
# contracts are deployed and the prover can mint a cold-template proof - and
# because it must precede the headless node, which attaches to the room by id.
# The admission signing identity already exists (PHASE 3 generates it and the
# funding loop gives it gas), which matters: `admissionSigner` is written once
# at intake with no setter.
# ---------------------------------------------------------------------------

if [ "$CREATE_REAL_ROOM" = "1" ]; then
  step "PHASE 9b  creating a real on-chain room"
  [ -n "${TEST_RIG_TIMELOCK:-}" ] && [ "$TEST_RIG_TIMELOCK" != "0x0000000000000000000000000000000000000000" ]     || die "SOAK6H_CREATE_REAL_ROOM needs the rig timelock; deploy with ZKDEAL_TEST_RIG_TIMELOCK=true"
  env     WORK="$WORK"     NETWORK="$NETWORK"     IMG_FOUNDRY="$IMG_FOUNDRY"     DEPLOYER_KEY="$DEPLOYER_KEY"     DEPLOYER_ADDRESS="$DEPLOYER_ADDRESS"     COLD_TEMPLATE_REGISTRY="$COLD_TEMPLATE_REGISTRY"     ROOM_MANAGER="$ROOM_MANAGER"     TEST_RIG_TIMELOCK="$TEST_RIG_TIMELOCK"     ADMISSION_ACCOUNT="$ADMISSION_ACCOUNT"     ADMISSION_SIGNER_KEY="${SIGNER_KEY[admission]}"     CONTRACTS_DIR="$REPO/web3-protocol"     PROVER_TOKEN="$PROVER_TOKEN"     DEPLOYMENT_DOMAIN="$DEPLOYMENT_DOMAIN"     bash "$(dirname "$0")/create-room.sh" || die "the real room could not be created"
  # The id createRoom returned replaces the placeholder label everywhere.
  HEADLESS_ROOM_ID="$(grep '^SOAK_ROOM_ID=' "$WORK/.env" | tail -1 | cut -d= -f2)"
  [ -n "$HEADLESS_ROOM_ID" ] || die "create-room.sh produced no room id"
  log "the headless node will attach to real chain room ${HEADLESS_ROOM_ID}"
fi

# ---------------------------------------------------------------------------
# 10. Headless room node
# ---------------------------------------------------------------------------

step "PHASE 10  starting the headless room node"

room_status="$(curl_net -o /dev/null -w '%{http_code}' -X POST \
  -H "authorization: Bearer ${PRINCIPAL_TOKEN[tenant_a]}" \
  -H 'content-type: application/json' \
  -H "idempotency-key: soak6h-headless-room-${HEADLESS_ROOM_ID}" \
  --data "{\"allocationId\":\"soak6h-alloc-${HEADLESS_ROOM_ID}\",\"roomId\":\"${HEADLESS_ROOM_ID}\",\"metadata\":{}}" \
  http://coordinator-active:3000/hosting/v1/rooms/deployments || true)"
case "$room_status" in
  200|201|202|409) log "headless room ${HEADLESS_ROOM_ID}: HTTP $room_status" ;;
  *) die_service coordinator-active "could not create the headless room (HTTP $room_status)" ;;
esac

ROOM_NODE_BURNER_KEY="0x$(hex_random 32)"
printf '{"burnerPrivateKey":"%s","eddsaSeedHex":"0x%s"}' "$ROOM_NODE_BURNER_KEY" "$(hex_random 32)" \
  >"$WORK/room-node/secrets/keys.json"
printf '%s' "$ROOM_NODE_CONTROL_TOKEN"        >"$WORK/room-node/secrets/control.token"
printf '%s' "${PRINCIPAL_TOKEN[node_a_node]}" >"$WORK/room-node/secrets/room-operator.token"
printf '%s' "${PRINCIPAL_TOKEN[node_a_key]}"  >"$WORK/room-node/secrets/submit.token"
printf '%s' "${PRINCIPAL_TOKEN[node_a_key]}"  >"$WORK/room-node/secrets/read.token"
# The secret-init container copies these as root into a named volume and
# re-applies 0600 + uid 10001 there. Keep the staged copies group/world
# readable so the copy cannot race a restrictive mode on the host side; the
# work directory itself is private.
chmod 0644 "$WORK"/room-node/secrets/*
chmod 0755 "$WORK/room-node" "$WORK/room-node/secrets"

cat >"$WORK/room-node/room-node.json" <<JSON
{
  "schemaVersion": 1,
  "mode": "hosted",
  "coordinatorUrl": "http://coordinator-writer:3000",
  "roomId": "${HEADLESS_ROOM_ID}",
  "expectedChainId": ${CHAIN_ID},
  "expectedRoomManager": "${ROOM_MANAGER}",
  "deploymentDomain": "${DEPLOYMENT_DOMAIN}",
  "keyFile": "/run/zkdeal-secrets/keys.json",
  "controlTokenFile": "/run/zkdeal-secrets/control.token",
  "stateDir": "/var/lib/zkdeal-room-node",
  "host": "0.0.0.0",
  "port": 3100,
  "restoreOnStart": true,
  "l1RefreshMs": 4000,
  "busPollMs": 500,
  "owner": {
    "baseUrl": "http://coordinator-writer:3000",
    "roomOperatorTokenFile": "/run/zkdeal-secrets/room-operator.token",
    "submitTokenFile": "/run/zkdeal-secrets/submit.token",
    "expectedOperationsAccount": "${OPERATIONS_ACCOUNT}",
    "minimumConfirmations": 1
  },
  "queue": {
    "baseUrl": "http://coordinator-writer:3000",
    "submitTokenFile": "/run/zkdeal-secrets/submit.token",
    "readTokenFile": "/run/zkdeal-secrets/read.token",
    "proofClass": "${PROOF_CLASS}",
    "serviceClass": "batch",
    "maximumChargeAmount": "1000000000000000000",
    "maximumChargeCurrency": "${BILLING_CURRENCY}"
  },
  "hosted": {
    "batchBlocks": 4,
    "batchMaxWaitMs": 15000,
    "admissionLeaseMs": 300000,
    "admissionPollMs": 1000,
    "proofPollMs": 1000,
    "l1InclusionBlocks": 64
  }
}
JSON
chmod 0644 "$WORK/room-node/room-node.json"
chmod 0755 "$WORK/room-node"

dc up -d headless-node-secret-init >/dev/null
wait_for headless-node-secret-init 180 exited_ok headless-node-secret-init
dc up -d headless-node >/dev/null || die_service headless-node "compose refused to start it"
wait_http headless-node http://headless-node:3100/health "${SOAK6H_HEADLESS_READY_SECONDS:-420}"

# ---------------------------------------------------------------------------
# 11. The three control adapters
# ---------------------------------------------------------------------------

step "PHASE 11  starting fault-control, backup-restore-control and failover-provider"

FAULT_TOKEN_SHA256="$(sha256 "$WORK/ctl-secrets/fault-token")"
BACKUP_TOKEN_SHA256="$(sha256 "$WORK/ctl-secrets/backup-restore-token")"

cat >"$WORK/fault-topology.json" <<JSON
{
  "schemaVersion": 1,
  "classification": "non-release-fixture",
  "candidateId": "${CANDIDATE_ID}",
  "planSha256": "${PLAN_SHA256}",
  "hostedIntegrationToken": "${HOSTED_INTEGRATION_TOKEN}",
  "faultControlTokenSha256": "${FAULT_TOKEN_SHA256}",
  "allowedHosts": ["rpc-a", "rpc-b"],
  "rpcProviders": {"rpc-a": "http://rpc-a:8545", "rpc-b": "http://rpc-b:8545"},
  "docker": {
    "socket": "/var/run/docker.sock",
    "containerPrefix": "${PREFIX}",
    "restartTargets": {
      "coordinator-active": "${PREFIX}coordinator-active",
      "coordinator-standby": "${PREFIX}coordinator-standby",
      "indexer": "${PREFIX}indexer",
      "reconciler": "${PREFIX}reconciler",
      "publisher": "${PREFIX}publisher",
      "headless-node": "${PREFIX}headless-node",
      "prover": "${PREFIX}prover",
      "prover-agent": "${PREFIX}prover-agent",
      "postgres-primary": "${PREFIX}postgres-primary",
      "postgres-standby": "${PREFIX}postgres-standby",
      "minio": "${PREFIX}minio",
      "rpc-a": "${PREFIX}rpc-a",
      "rpc-b": "${PREFIX}rpc-b"
    },
    "partitionNetwork": null,
    "partitionTargets": []
  },
  "loadProfiles": {},
  "requestTimeoutSeconds": 10
}
JSON
chmod 0644 "$WORK/fault-topology.json"

cat >"$WORK/backup-topology.json" <<JSON
{
  "schemaVersion": 1,
  "classification": "non-release-fixture",
  "candidateId": "${CANDIDATE_ID}",
  "planSha256": "${PLAN_SHA256}",
  "hostedIntegrationToken": "${HOSTED_INTEGRATION_TOKEN}",
  "backupRestoreTokenSha256": "${BACKUP_TOKEN_SHA256}",
  "allowedHosts": ["postgres-writer", "postgres-fresh", "minio", "minio-backup", "minio-fresh"],
  "source": {
    "postgres": {"host": "postgres-writer", "port": 5432, "database": "${PG_DB}", "user": "${PG_USER}", "passwordFile": "/secrets/source-postgres-password", "passwordSha256": "$(sha256 "$WORK/ctl-secrets/source-postgres-password")"},
    "minio": {"endpoint": "http://minio:9000", "accessKeyFile": "/secrets/source-minio-access", "accessKeySha256": "$(sha256 "$WORK/ctl-secrets/source-minio-access")", "secretKeyFile": "/secrets/source-minio-secret", "secretKeySha256": "$(sha256 "$WORK/ctl-secrets/source-minio-secret")", "bucket": "zkdeal"}
  },
  "backupStore": {"endpoint": "http://minio-backup:9000", "accessKeyFile": "/secrets/backup-minio-access", "accessKeySha256": "$(sha256 "$WORK/ctl-secrets/backup-minio-access")", "secretKeyFile": "/secrets/backup-minio-secret", "secretKeySha256": "$(sha256 "$WORK/ctl-secrets/backup-minio-secret")", "bucket": "zkdeal-backups"},
  "encryption": {"algorithm": "AES-256-GCM", "keyFile": "/secrets/backup-encryption-key", "keySha256": "$(sha256 "$WORK/ctl-secrets/backup-encryption-key")"},
  "freshTargets": {
    "fresh-primary": {
      "postgres": {"host": "postgres-fresh", "port": 5432, "database": "${PG_DB}", "user": "${PG_USER}", "passwordFile": "/secrets/fresh-postgres-password", "passwordSha256": "$(sha256 "$WORK/ctl-secrets/fresh-postgres-password")"},
      "minio": {"endpoint": "http://minio-fresh:9000", "accessKeyFile": "/secrets/fresh-minio-access", "accessKeySha256": "$(sha256 "$WORK/ctl-secrets/fresh-minio-access")", "secretKeyFile": "/secrets/fresh-minio-secret", "secretKeySha256": "$(sha256 "$WORK/ctl-secrets/fresh-minio-secret")", "bucket": "zkdeal-restored"}
    }
  },
  "processTimeoutSeconds": 900
}
JSON
chmod 0644 "$WORK/backup-topology.json"

FAULT_TOPOLOGY_SHA256="$(sha256 "$WORK/fault-topology.json")"
BACKUP_TOPOLOGY_SHA256="$(sha256 "$WORK/backup-topology.json")"
sed -i \
  -e "s|^FAULT_TOPOLOGY_SHA256=.*|FAULT_TOPOLOGY_SHA256=${FAULT_TOPOLOGY_SHA256}|" \
  -e "s|^BACKUP_TOPOLOGY_SHA256=.*|BACKUP_TOPOLOGY_SHA256=${BACKUP_TOPOLOGY_SHA256}|" \
  "$WORK/.env"

# Both adapters run as uid 65532 and enforce 0600-or-stricter on files owned by
# that uid, so the secret directory is handed over wholesale.
as_root_in "$WORK/ctl-secrets" 'chown -R 65532:65532 /target && chmod 0755 /target && chmod 0600 /target/*'

dc up -d fault-control backup-restore-control failover-provider >/dev/null \
  || die "compose could not start the control adapters"

wait_http fault-control          http://fault-control:8080/ready          240
wait_http backup-restore-control http://backup-restore-control:8080/ready 240
wait_http failover-provider      http://failover-provider:8443/ready      240

# The driver reads /capabilities and refuses to proceed unless the candidate
# binding matches, so assert it now instead of at hour four.
fault_caps="$(curl_net --fail http://fault-control:8080/capabilities || true)"
printf '%s' "$fault_caps" | grep -Fq "\"candidateId\":\"${CANDIDATE_ID}\"" \
  || die_service fault-control "its /capabilities candidateId differs from ${CANDIDATE_ID}"
printf '%s' "$fault_caps" | grep -Fq "\"hostedIntegrationToken\":\"${HOSTED_INTEGRATION_TOKEN}\"" \
  || die_service fault-control "its /capabilities hostedIntegrationToken differs from the rig binding"
printf '%s' "$fault_caps" | grep -Fq "\"planSha256\":\"${PLAN_SHA256}\"" \
  || die_service fault-control "its /capabilities planSha256 differs from the rig binding"
log "ok: fault-control publishes the expected candidate binding"

# ---------------------------------------------------------------------------
# 12. Billing/proof configuration the workload charges against
# ---------------------------------------------------------------------------

step "PHASE 12  publishing the proof profile and billing prices"

EFFECTIVE_FROM="$(date -u -d "@$((START_EPOCH - 3600))" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
  || date -u +%Y-%m-%dT%H:%M:%SZ)"

admin_call() {  # admin_call <METHOD> <path> <idempotency-key> <json>
  curl_net -o /dev/null -w '%{http_code}' -X "$1" \
    -H "authorization: Bearer ${PRINCIPAL_TOKEN[admin]}" \
    -H 'content-type: application/json' \
    -H "idempotency-key: $3" \
    --data "$4" "http://coordinator-active:3000$2" || true
}

# The endpoint must be a key of PROVE_ENDPOINTS (prove-queue/queue-types.ts).
# "/v5/rooms/prove-batch" is not one, and a profile naming it makes every
# submission fail with "no verified scheduling profile exists for this proof
# class and endpoint" - the queue accepts nothing at all.
profile_status="$(admin_call PUT "/hosting/v1/admin/proof-profiles/${PROOF_CLASS}" \
  "soak6h-profile-${PROOF_CLASS}" \
  "{\"endpoint\":\"/v5/rooms/prove\",\"needsGpu\":true,\"estimatedWork\":\"1\",\"estimatedProofTimeMs\":180000,\"settlementMarginMs\":60000,\"evidence\":{\"source\":\"soak6h-test-rig\"},\"verifiedAt\":\"${EFFECTIVE_FROM}\"}")"
log "proof profile ${PROOF_CLASS}: HTTP ${profile_status}"

# A prove-node credential is necessary but not sufficient to lease work: the
# coordinator additionally requires a server-side provider assignment naming
# the partitions, proof classes and GPU resource the node may take
# (postgres-hosted-store.ts, "node has no active server-side provider
# assignment"). Without this row every lease returns 403 and the GPU sits idle
# for the whole run while the stack still reports healthy - the agent simply
# retries forever. The assignment must match how jobs are submitted: the driver
# and the room node both enqueue partition "shared" with proofClass
# ${PROOF_CLASS}, and the room prove endpoints occupy the single GPU slot.
AGENT_PRINCIPAL_TOKEN="${PRINCIPAL_TOKEN[agent_node]}"
AGENT_PRINCIPAL_ID="${AGENT_PRINCIPAL_TOKEN#zkd.}"; AGENT_PRINCIPAL_ID="${AGENT_PRINCIPAL_ID%%.*}"
assignment_status="$(admin_call PUT "/hosting/v1/admin/provider-nodes/${AGENT_PRINCIPAL_ID}" \
  "soak6h-provider-${AGENT_PRINCIPAL_ID}" \
  "{\"providerId\":\"soak6h-gpu\",\"active\":true,\"gpu\":true,\"gpuResourceId\":\"${AGENT_NODE_ID:-soak6h-gpu-0}\",\"partitions\":[\"shared\"],\"tenantIds\":[\"tenant-a\",\"tenant-b\"],\"allocationIds\":[],\"proofClasses\":[\"${PROOF_CLASS}\"],\"maxConcurrentJobs\":1,\"leaseTtlMs\":600000}")"
log "provider node ${AGENT_PRINCIPAL_ID}: HTTP ${assignment_status}"
case "$assignment_status" in
  200|201|204) ;;
  *) die "the prover agent has no provider assignment (HTTP ${assignment_status}); it could never lease a job" ;;
esac

# Two units are registered, and both are required.
#
# The queue's billing gate reads the LITERAL unit 'proof-work'
# (postgres-hosted-store.ts, "billable proof job requires an effective immutable
# price quote"); without that row no billable job can be accepted. The rig's
# own ${BILLING_UNIT} is kept as well because it is exported to the driver for
# usage assertions.
#
# The currency must satisfy CHECK (currency ~ '^[A-Z]{3}$') AND equal the job's
# maximumChargeCurrency exactly. The rig previously sent lowercase "wei", which
# the constraint rejects outright - both calls returned HTTP 400 and the loop
# logged the status and carried on, so the price table stayed empty.
for tenant in tenant-a tenant-b; do
  for unit in proof-work "${BILLING_UNIT}"; do
    price_status="$(admin_call POST /hosting/v1/admin/billing/prices \
      "soak6h-price-${tenant}-${unit}" \
      "{\"tenantId\":\"${tenant}\",\"unit\":\"${unit}\",\"currency\":\"${BILLING_CURRENCY}\",\"unitPrice\":\"1000000000\",\"effectiveFrom\":\"${EFFECTIVE_FROM}\"}")"
    log "billing price ${tenant}/${unit}: HTTP ${price_status}"
    case "$price_status" in
      200|201|204) ;;
      *) die "billing price ${tenant}/${unit} was rejected (HTTP ${price_status}); no billable prove job could be accepted" ;;
    esac
  done
done

# Fatal, not advisory. The previous posture - log a 4xx and continue - produced
# a rig that reported "stack-up completed" while the durable queue could not
# accept a single job, which is a far worse outcome than stopping here.
case "$profile_status" in
  200|201|204) : ;;
  *) die "the proof profile ${PROOF_CLASS} was rejected (HTTP ${profile_status}); every prove submission would be refused" ;;
esac

# ---------------------------------------------------------------------------
# 13. Final gate over every endpoint in the driver's contract
# ---------------------------------------------------------------------------

step "PHASE 13  final endpoint-contract gate"

wait_http coordinator http://soak-edge:3000/hosting/v1/ready   180
wait_http prover      http://soak-edge:8080/health             180
wait_http headless    http://headless-node:3100/health         180
wait_http logs        http://shim-observer:8080/logs            60
wait_http fault       http://fault-control:8080/ready           60
wait_http backup      http://backup-restore-control:8080/ready  60
wait_http failover    http://failover-provider:8443/ready       60
rpc_ready rpc-a
rpc_ready rpc-b

indexer_status="$(curl_net --fail -H "authorization: Bearer ${EPH[tenant_a]}" \
  http://soak-edge:3000/hosting/v1/indexer/status || true)"
# /hosting/v1/indexer/status returns head/anchor/floor/cursors;
# indexerHeadMatchesL1 belongs to the runtime status block, not here.
printf '%s' "$indexer_status" | grep -q '"cursors"' \
  || die_service indexer "the indexer status document is not being served through the edge"
log "indexer status via the edge: $indexer_status"

queue_probe="$(curl_net -o /dev/null -w '%{http_code}' \
  -H "authorization: Bearer ${EPH[node_a]}" \
  http://soak-edge:3000/queue/v1/jobs/pj-0000000000000000000 || true)"
case "$queue_probe" in
  404|400) log "ok: the queue surface authenticates node_a (probe HTTP $queue_probe)" ;;
  401|403) die_service soak-edge "the queue surface rejected the node_a credential (HTTP $queue_probe)" ;;
  *) log "note: queue probe returned HTTP $queue_probe" ;;
esac

# ---------------------------------------------------------------------------
# 14. endpoints.env
# ---------------------------------------------------------------------------

step "PHASE 14  writing $WORK/endpoints.env"

# Every URL is network-internal on purpose: nothing is published to the host,
# and a soak that reaches the stack through a host port is not testing the
# stack the promotion gate will see. Run the driver attached to $NETWORK.
{
  cat <<ENV
# ${PROJECT} endpoint contract -- generated $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Consume from a container attached to the docker network ${NETWORK}.
SOAK6H_PROJECT=${PROJECT}
SOAK6H_NETWORK=${NETWORK}
SOAK6H_WORK=${WORK}

# --- the eleven endpoint aliases the soak package requires ---
COORDINATOR_URL=http://soak-edge:3000
INDEXER_URL=http://soak-edge:3000
QUEUE_URL=http://soak-edge:3000
HEADLESS_URL=http://headless-node:3100
L1_RPC_A=http://rpc-a:8545
L1_RPC_B=http://rpc-b:8545
ACCEPTANCE_FAULT_URL=http://fault-control:8080
ACCEPTANCE_BACKUP_URL=http://backup-restore-control:8080
FAILOVER_PROVIDER_URL=http://failover-provider:8443
PROVER_URL=http://soak-edge:8080
LOG_QUERY_URL=http://shim-observer:8080/logs

# --- candidate binding ---
SOAK_CANDIDATE_ID=${CANDIDATE_ID}
HOSTED_INTEGRATION_TOKEN=${HOSTED_INTEGRATION_TOKEN}
SOAK_PLAN_SHA256=${PLAN_SHA256}
CANDIDATE_TOPOLOGY_VERIFICATION_SHA256=${FAULT_TOPOLOGY_SHA256}
ACTIVE_COORDINATOR_ID=${ACTIVE_COORDINATOR_ID}
STANDBY_COORDINATOR_ID=${STANDBY_COORDINATOR_ID}
SOAK_FAILOVER_WITNESS_COUNT=2

# --- deployment addresses (lower-case; the driver enforces that) ---
ZKDEAL_ROOM_MANAGER=${ROOM_MANAGER}
ZKDEAL_ROOM_POOL=${ROOM_POOL}
ZKDEAL_OPERATIONS_ACCOUNT=${OPERATIONS_ACCOUNT}
ZKDEAL_SPONSOR_ACCOUNT=${SPONSOR_ACCOUNT}
ZKDEAL_ACCESS_TOKEN=${ACCESS_TOKEN}
ZKDEAL_DEPLOYMENT_DOMAIN=${DEPLOYMENT_DOMAIN}
ZKDEAL_MINIMUM_CONFIRMATIONS=1

# --- workload parameters ---
# serviceClass MUST be standard|latency|batch: the hosted queue rejects "soak",
# which is what the driver would otherwise default to.
SOAK_SERVICE_CLASS=batch
SOAK_PROOF_CLASS=${PROOF_CLASS}
SOAK_MAX_CHARGE_AMOUNT=1000000000000000000
SOAK_MAX_CHARGE_CURRENCY=${BILLING_CURRENCY}
SOAK_SPONSOR_BENEFICIARY_TENANT=tenant_b
SOAK_HTTP_TIMEOUT_SECONDS=30
SOAK_DURATION_SECONDS=21600
SOAK6H_BILLING_UNIT=${BILLING_UNIT}

# --- scoped credential files (mode 0600) ---
# They are 0600 and owned by uid $(id -u), and the driver refuses any token
# file whose group/other bits are set. Run the soak container as that same uid
# (docker run --user $(id -u):$(id -g)) or it cannot read them.
SOAK6H_TOKEN_UID=$(id -u)
SOAK6H_TOKEN_GID=$(id -g)
SOAK6H_AUTH_DIR=${WORK}/auth
ENV
  for alias in "${AUTH_ALIASES[@]}"; do
    printf 'SOAK_AUTH_%s_TOKEN_FILE=%s/auth/%s.token\n' \
      "$(printf '%s' "$alias" | tr 'a-z' 'A-Z')" "$WORK" "$alias"
  done
  cat <<ENV

# --- rig identity, for the operator ---
SOAK6H_CHAIN_ID=${CHAIN_ID}
SOAK6H_ANVIL_TIMESTAMP=${ANVIL_TIMESTAMP}
SOAK6H_HEADLESS_ROOM_ID=${HEADLESS_ROOM_ID}
SOAK6H_FAULT_TOPOLOGY=${WORK}/fault-topology.json
SOAK6H_BACKUP_TOPOLOGY=${WORK}/backup-topology.json
SOAK6H_COMPOSE_FILE=${WORK}/compose.yaml
SOAK6H_ADDRESSES_JSON=${WORK}/deploy-out/addresses.json
ENV
} >"$WORK/endpoints.env"
chmod 0600 "$WORK/endpoints.env"

step "STACK UP"
docker ps -a --filter "name=^${PREFIX}" --format '  {{.Names}}\t{{.Status}}' >&2 || true

cat >&2 <<SUMMARY

  endpoints:   ${WORK}/endpoints.env
  tokens:      ${WORK}/auth/*.token   (13 scoped eph_ roles, mode 0600)
  compose:     ${WORK}/compose.yaml   (project ${PROJECT}, network ${NETWORK})
  deploy log:  ${WORK}/logs/deploy.log
  addresses:   ${WORK}/deploy-out/addresses.json

  Attach the soak runner to the docker network ${NETWORK}, run it as uid
  $(id -u):$(id -g) so it can read the 0600 token files, and pass every
  variable from endpoints.env, for example:

    docker run -d --name ${PROJECT}-soak-runner \\
      --network ${NETWORK} --user "\$(id -u):\$(id -g)" \\
      --env-file ${WORK}/endpoints.env \\
      -v ${WORK}/auth:${WORK}/auth:ro \\
      -v ${WORK}/evidence:/evidence \\
      <soak-runner-image> ...

  Tear the rig down with ./stack-down.sh.

SUMMARY
log "stack-up completed in $(( $(date -u +%s) - START_EPOCH ))s"
