#!/usr/bin/env bash
# zkdeal SIX-HOUR TEST SOAK launcher.
#
# This is NOT the 12-hour release gate. It builds the hash-bound owner soak
# driver image, builds the soak-runner `candidate` target against that image by
# digest, writes the owner command argv file, and launches the owner soak
# against an already-running candidate stack for SOAK_DURATION_SECONDS
# (default 21600 = 6h) with durable evidence on the host.
#
# Companion scripts written by the sibling task (assumed present next to this
# file / already run by the operator):
#   soak6h/stack-up.sh            brings the candidate stack up and writes
#                                 ~/soak6h/endpoints.env
#   soak6h/make-manifest.py       writes ~/soak6h/manifest.json
#   soak6h/relax-duration-floor.sh  lowers the hard 43200s floors in
#                                 soak-runner/zkdeal_soak.py, scripts/soak.py
#                                 and the manifest schema so a 6h TEST soak is
#                                 accepted. Run it BEFORE this script: the
#                                 source SHA-256 build args are computed from
#                                 the files on disk, so the relaxed sources are
#                                 what the image gate binds to.
#
# Modes:
#   run-soak.sh                 build + launch (default)
#   run-soak.sh --resume        relaunch against existing durable state
#   run-soak.sh --build-only    build and gate-check images, do not launch
#   run-soak.sh --check         validate inputs and print the env contract
#   run-soak.sh --status        elapsed / journal tail / fault pairs / liveness
#   run-soak.sh --verify        scripts/soak.py verify + closure verdict
#
# Everything fails closed with a named reason.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration (every value is overridable from the environment).
# ---------------------------------------------------------------------------

ZKDEAL_ROOT="${ZKDEAL_ROOT:-$HOME/zkdeal-rc}"
INFRA="$ZKDEAL_ROOT/cloud-deployer-infra"
WORK="${SOAK6H_DIR:-$HOME/soak6h}"

EVIDENCE_HOST="${SOAK_EVIDENCE_HOST:-$WORK/evidence}"
# Resolved by resolve_manifest(): SOAK_MANIFEST_HOST wins, then an exported
# SOAK_MANIFEST_FILE that names an existing host file (soak6h/make-manifest.py
# --env-out emits one), then $WORK/manifest.json, then $WORK/soak-manifest.json.
MANIFEST_HOST=""
ENDPOINTS_ENV="${SOAK_ENDPOINTS_ENV:-$WORK/endpoints.env}"
OWNER_COMMAND_HOST="${SOAK_OWNER_COMMAND_HOST:-$WORK/owner-command.json}"
TOKENS_HOST="${SOAK_TOKENS_HOST:-$WORK/tokens}"
SPONSOR_PROFILE_HOST="${SOAK_SPONSOR_PROFILE_HOST:-$WORK/sponsor-profile.json}"
LOG="${SOAK_LOG:-$WORK/soak.log}"
RUN_ENV="${SOAK_RUN_ENV:-$WORK/soak.run.env}"

DURATION="${SOAK_DURATION_SECONDS:-21600}"
REGISTRY="${SOAK_LOCAL_REGISTRY:-localhost:5000}"
OWNER_REPO="${SOAK_OWNER_DRIVER_REPO:-$REGISTRY/zkdeal-owner-soak-driver}"
CANDIDATE_TAG="${SOAK_CANDIDATE_IMAGE:-zkdeal-soak-runner:soak6h-candidate}"
CONTAINER="${SOAK_CONTAINER_NAME:-zkdeal-soak6h}"
TOOLS_IMAGE="${DEPLOYMENT_TOOLS_IMAGE:-zkdeal-deployment-tools:local}"

# The runner writes SOAK_STATE_FILE through scripts/common.atomic_write, which
# refuses any path outside ROOT = parents[1] of /opt/zkdeal/common.py = /opt.
# Mounting the durable evidence directory at /opt/evidence (instead of the
# image default /evidence) keeps the state write inside that boundary. Override
# with SOAK_EVIDENCE_MOUNT=/evidence only if common.py has been changed too.
EVIDENCE_MOUNT="${SOAK_EVIDENCE_MOUNT:-/opt/evidence}"

# Host-uid execution: the token files and the evidence directory live on the
# host and the driver refuses any token file with group/other permission bits,
# so the container runs as the invoking user rather than the image's 65532.
CONTAINER_USER="${SOAK_CONTAINER_USER:-$(id -u):$(id -g)}"

# The exact reviewed release-soak runtime contract (soak-runner/zkdeal_soak.py
# require_runtime_contract). These strings are compared byte for byte.
REQUIRE_FULL_LIFECYCLE="room-create,submit,lease,live-prepare,prove,verify,blob-archive,aggregate-settle,sponsor,reorg,finalize,withdraw,reconcile"
REQUIRE_INDUCED_RESTARTS="headless-restart,prover-restart,coordinator-promotion,indexer-rollback,rpc-split,object-store-restart,database-restart,docker-host-restart-resume"
REQUIRE_DURABLE_ASSERTIONS="source,image,trust-root,chain-seed,room,job,nonce,tx,event,cursor,usage,charge,sealed-output,safety,claim,fairness,deadline"

# The five reviewed argv markers, used both for the container argv (entrypoint
# /opt/zkdeal-soak) and for the owner command argv (/opt/zkdeal-owner-soak).
MARKER_1="--submit-real-proof-jobs"
MARKER_2="--restart"
MARKER_3="--assert-durable-results,cursors,nonces,charges,sealed-output,safety,claims,fairness,deadlines"
MARKER_4="--bounded-backoff"
MARKER_5="--emit-evidence-closure"

OWNER_ENTRYPOINT="/opt/zkdeal-owner-soak"

ENDPOINT_VARS="COORDINATOR_URL INDEXER_URL QUEUE_URL HEADLESS_URL L1_RPC_A L1_RPC_B ACCEPTANCE_FAULT_URL ACCEPTANCE_BACKUP_URL FAILOVER_PROVIDER_URL PROVER_URL LOG_QUERY_URL"
IDENTITY_VARS="SOAK_CANDIDATE_ID ACTIVE_COORDINATOR_ID STANDBY_COORDINATOR_ID HOSTED_INTEGRATION_TOKEN CANDIDATE_TOPOLOGY_VERIFICATION_SHA256 SOAK_DOCKER_NETWORK"
ADDRESS_VARS="ZKDEAL_ROOM_MANAGER ZKDEAL_OPERATIONS_ACCOUNT ZKDEAL_ROOM_POOL ZKDEAL_SPONSOR_ACCOUNT"
OPTIONAL_VARS="SOAK_FAILOVER_WITNESS_COUNT SOAK_HTTP_TIMEOUT_SECONDS SOAK_PROOF_CLASS SOAK_SERVICE_CLASS SOAK_MAX_CHARGE_AMOUNT SOAK_MAX_CHARGE_CURRENCY SOAK_SPONSOR_BENEFICIARY_TENANT SOAK_WITHDRAWAL_ROOM SOAK_WITHDRAWAL_EPOCH SOAK_WITHDRAWAL_INDEX ZKDEAL_MINIMUM_CONFIRMATIONS"
AUTH_ALIASES="tenant_a tenant_b node_a node_b l1_liveness l1_room l1_aggregate l1_sponsor withdrawal fault_control backup_restore failover_control failover_approval"

# ---------------------------------------------------------------------------
# Small helpers.
# ---------------------------------------------------------------------------

die() {
  printf 'FATAL[%s]: %s\n' "${2:-soak6h}" "$1" >&2
  exit 1
}

log() {
  printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$1"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command '$1' is not on PATH" "toolchain"
}

iso() {
  date -u -d "@$1" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u '+%Y-%m-%dT%H:%M:%SZ'
}

sha256_of() {
  sha256sum "$1" | awk '{print $1}'
}

is_sha256() {
  printf '%s' "${1:-}" | grep -Eq '^[0-9a-f]{64}$'
}

label_of() {
  # label_of <image-ref> <label-name>
  docker image inspect "$1" --format "{{index .Config.Labels \"$2\"}}" 2>/dev/null || true
}

usage() {
  sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'
}

# ---------------------------------------------------------------------------
# Preflight: tree, inputs, endpoints.env, duration floor, tokens.
# ---------------------------------------------------------------------------

preflight_tree() {
  need_cmd docker
  need_cmd sha256sum
  need_cmd awk
  need_cmd python3
  [ -d "$ZKDEAL_ROOT" ] || die "umbrella tree is absent: $ZKDEAL_ROOT (set ZKDEAL_ROOT)" "tree"
  [ -d "$INFRA" ] || die "cloud-deployer-infra is absent: $INFRA" "tree"
  [ -f "$ZKDEAL_ROOT/LICENSE" ] || die "umbrella root license is absent; the owner-soak-driver build context needs it" "tree"
  [ -f "$INFRA/owner-soak-driver/zkdeal_owner_soak.py" ] || die "owner driver source is absent: $INFRA/owner-soak-driver/zkdeal_owner_soak.py" "tree"
  [ -f "$INFRA/owner-soak-driver/Dockerfile" ] || die "owner driver Dockerfile is absent" "tree"
  [ -f "$INFRA/soak-runner/Dockerfile" ] || die "soak-runner Dockerfile is absent" "tree"
  [ -f "$INFRA/soak-runner/zkdeal_soak.py" ] || die "soak-runner entrypoint source is absent" "tree"
  [ -f "$INFRA/scripts/common.py" ] || die "scripts/common.py is absent" "tree"
  [ -f "$INFRA/scripts/soak.py" ] || die "scripts/soak.py is absent" "tree"
  [ -f "$INFRA/config/schemas/release-soak-manifest.schema.json" ] || die "release-soak manifest schema is absent" "tree"
  docker version >/dev/null 2>&1 || die "the docker daemon is not reachable from this shell" "docker"
  mkdir -p "$WORK" "$EVIDENCE_HOST" "$TOKENS_HOST"
  chmod 0700 "$TOKENS_HOST"
}

resolve_manifest() {
  [ -n "$MANIFEST_HOST" ] && return 0
  local candidate hint=""
  for candidate in \
    "${SOAK_MANIFEST_HOST:-}" \
    "${SOAK_MANIFEST_FILE:-}" \
    "$WORK/manifest.json" \
    "$WORK/soak-manifest.json"
  do
    if [ -n "$candidate" ] && [ -f "$candidate" ]; then
      MANIFEST_HOST="$candidate"
      return 0
    fi
  done
  for candidate in /ephemeral/soak6h/soak-manifest.json /ephemeral/soak6h/manifest.json; do
    if [ -f "$candidate" ]; then
      hint="$hint (found $candidate -- pass SOAK_MANIFEST_HOST=$candidate or SOAK6H_DIR=$(dirname "$candidate"))"
    fi
  done
  die "release soak manifest is absent: looked at \${SOAK_MANIFEST_HOST}, \${SOAK_MANIFEST_FILE}, $WORK/manifest.json and $WORK/soak-manifest.json -- run soak6h/make-manifest.py first$hint" "manifest"
}

preflight_inputs() {
  resolve_manifest
  if [ -L "$MANIFEST_HOST" ]; then
    die "the manifest must be a regular file, not a symlink: $MANIFEST_HOST" "manifest"
  fi
  [ -f "$ENDPOINTS_ENV" ] || die "endpoints file is absent: $ENDPOINTS_ENV (run soak6h/stack-up.sh first)" "endpoints"

  MANIFEST_SHA="$(sha256_of "$MANIFEST_HOST")"
  is_sha256 "$MANIFEST_SHA" || die "could not hash the manifest" "manifest"

  MANIFEST_DURATION="$(python3 - "$MANIFEST_HOST" <<'PY'
import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as exc:  # noqa: BLE001
    print("PARSE_ERROR:%s" % exc)
    raise SystemExit(0)
if not isinstance(value, dict):
    print("PARSE_ERROR:manifest is not a JSON object")
    raise SystemExit(0)
if value.get("kind") != "zkdeal-release-soak" or value.get("schemaVersion") != 1:
    print("KIND_ERROR:%s/%s" % (value.get("kind"), value.get("schemaVersion")))
    raise SystemExit(0)
faults = value.get("scheduledFaults")
if not isinstance(faults, list) or not faults:
    print("FAULT_ERROR:scheduledFaults is absent or empty")
    raise SystemExit(0)
worst = max(int(item.get("atSecond", 0)) for item in faults if isinstance(item, dict))
print("%s %s" % (value.get("durationSeconds"), worst))
PY
)"
  case "$MANIFEST_DURATION" in
    PARSE_ERROR:*|KIND_ERROR:*|FAULT_ERROR:*)
      die "manifest is unusable (${MANIFEST_DURATION})" "manifest" ;;
  esac
  MANIFEST_SECONDS="${MANIFEST_DURATION%% *}"
  WORST_FAULT_SECOND="${MANIFEST_DURATION##* }"
  printf '%s' "$DURATION" | grep -Eq '^[0-9]+$' || die "SOAK_DURATION_SECONDS must be an integer, got '$DURATION'" "duration"
  printf '%s' "$MANIFEST_SECONDS" | grep -Eq '^[0-9]+$' || die "manifest durationSeconds is not an integer: '$MANIFEST_SECONDS'" "manifest"
  printf '%s' "$WORST_FAULT_SECOND" | grep -Eq '^[0-9]+$' || die "manifest scheduledFaults[].atSecond is not an integer: '$WORST_FAULT_SECOND'" "manifest"
  [ "$MANIFEST_SECONDS" = "$DURATION" ] || die "manifest durationSeconds=$MANIFEST_SECONDS but SOAK_DURATION_SECONDS=$DURATION; the runner requires them to be equal" "manifest"
  if [ "$WORST_FAULT_SECOND" -ge "$DURATION" ]; then
    die "the manifest schedules a fault at ${WORST_FAULT_SECOND}s which is outside the ${DURATION}s test soak; regenerate it with soak6h/make-manifest.py" "manifest"
  fi
  log "manifest accepted: ${MANIFEST_SECONDS}s, last scheduled fault at ${WORST_FAULT_SECOND}s, sha256 $MANIFEST_SHA"
}

preflight_duration_floor() {
  # A 6h TEST soak is below the reviewed 12h (43200s) floor that both
  # soak-runner/zkdeal_soak.py and scripts/soak.py enforce. Detect the
  # un-relaxed sources here instead of failing 30 seconds into the container.
  if [ "$DURATION" -ge 43200 ]; then
    return 0
  fi
  local offenders=""
  if grep -q '43_200' "$INFRA/soak-runner/zkdeal_soak.py"; then
    offenders="$offenders soak-runner/zkdeal_soak.py"
  fi
  if grep -q '43_200' "$INFRA/scripts/soak.py"; then
    offenders="$offenders scripts/soak.py"
  fi
  if [ -n "$offenders" ]; then
    die "SOAK_DURATION_SECONDS=$DURATION is below the 43200s release floor still present in:$offenders -- run soak6h/relax-duration-floor.sh on the node before building" "duration-floor"
  fi
  # The JSON Schema minimum is documentation only: scripts/soak.py validates the
  # manifest with hand-written checks and never loads this schema, so an
  # unpatched schema (relax-duration-floor.sh skips it without --with-schema) is
  # a note, not a blocker. It is still hashed into SOAK_RUNNER_SOURCE_SHA256,
  # which this script recomputes from disk either way.
  if grep -Eq '"minimum":[[:space:]]*43200' "$INFRA/config/schemas/release-soak-manifest.schema.json"; then
    log "NOTE: release-soak-manifest.schema.json still declares minimum 43200 (not enforced at runtime; harmless for this TEST soak)"
  fi
  log "duration floor relaxed in the node sources; running a ${DURATION}s TEST soak"
}

load_endpoints() {
  # shellcheck disable=SC1090
  set -a
  . "$ENDPOINTS_ENV"
  set +a

  local name value missing=""
  for name in $ENDPOINT_VARS $IDENTITY_VARS $ADDRESS_VARS; do
    value="$(eval "printf '%s' \"\${$name:-}\"")"
    if [ -z "$value" ]; then
      missing="$missing $name"
    fi
  done
  if [ -n "$missing" ]; then
    die "endpoints file $ENDPOINTS_ENV omits required variable(s):$missing" "endpoints"
  fi

  for name in $ENDPOINT_VARS; do
    value="$(eval "printf '%s' \"\${$name}\"")"
    case "$value" in
      http://*|https://*) ;;
      *) die "$name must be an http(s) URL, got '$value'" "endpoints" ;;
    esac
  done
  [ "$L1_RPC_A" != "$L1_RPC_B" ] || die "L1_RPC_A and L1_RPC_B must be distinct provider URLs (the rpc-split fault depends on it)" "endpoints"
  if [ "$QUEUE_URL" != "$COORDINATOR_URL" ]; then
    log "WARNING: QUEUE_URL differs from COORDINATOR_URL; the reviewed release soak uses the hosted coordinator queue"
  fi

  for name in $ADDRESS_VARS; do
    value="$(eval "printf '%s' \"\${$name}\"")"
    printf '%s' "$value" | grep -Eq '^0x[0-9a-f]{40}$' || die "$name must be 0x plus 40 lowercase hex characters, got '$value'" "addresses"
  done

  printf '%s' "$HOSTED_INTEGRATION_TOKEN" | grep -Eq '^sha256:[0-9a-f]{64}$' || die "HOSTED_INTEGRATION_TOKEN must be the candidate plan's sha256:<64 hex> acceptance token" "identity"
  is_sha256 "$CANDIDATE_TOPOLOGY_VERIFICATION_SHA256" || die "CANDIDATE_TOPOLOGY_VERIFICATION_SHA256 must be 64 lowercase hex characters" "identity"
  if [ "${#SOAK_CANDIDATE_ID}" -lt 8 ] || [ "${#SOAK_CANDIDATE_ID}" -gt 80 ]; then
    die "SOAK_CANDIDATE_ID must be 8..80 characters, got ${#SOAK_CANDIDATE_ID}" "identity"
  fi
  [ "$ACTIVE_COORDINATOR_ID" != "$STANDBY_COORDINATOR_ID" ] || die "ACTIVE_COORDINATOR_ID and STANDBY_COORDINATOR_ID must differ (coordinator-promotion fault)" "identity"

  docker network inspect "$SOAK_DOCKER_NETWORK" >/dev/null 2>&1 || die "docker network '$SOAK_DOCKER_NETWORK' does not exist; bring the stack up with soak6h/stack-up.sh" "network"
}

materialize_tokens() {
  # Accepts either SOAK_AUTH_<ALIAS>_TOKEN values in endpoints.env or existing
  # files at $TOKENS_HOST/<alias>.token. Both end up as 0600 files owned by the
  # invoking user, which is the uid the container runs as.
  local alias upper var value file seen=""
  umask 077
  for alias in $AUTH_ALIASES; do
    upper="$(printf '%s' "$alias" | tr 'a-z' 'A-Z')"
    var="SOAK_AUTH_${upper}_TOKEN"
    value="$(eval "printf '%s' \"\${$var:-}\"")"
    file="$TOKENS_HOST/$alias.token"
    if [ -z "$value" ] && [ -f "$file" ]; then
      value="$(tr -d '\r\n' < "$file")"
    fi
    [ -n "$value" ] || die "soak auth token for '$alias' is absent: set $var in $ENDPOINTS_ENV or place $file" "auth"
    printf '%s' "$value" | grep -Eq '^eph_[A-Za-z0-9_-]{28,120}$' \
      || die "soak auth token for '$alias' is not an independently revocable eph_ token" "auth"
    case " $seen " in
      *" $value "*) die "soak auth token for '$alias' duplicates another role's token; every authority needs a distinct token" "auth" ;;
    esac
    seen="$seen $value"
    printf '%s' "$value" > "$file"
    chmod 0600 "$file"
  done
  chmod 0700 "$TOKENS_HOST"
  log "materialized 13 scoped eph_ token files under $TOKENS_HOST"
}

write_owner_command() {
  # argv[0] MUST be exactly /opt/zkdeal-owner-soak (soak-runner
  # validate_owner_command), followed by the five reviewed markers.
  python3 - "$OWNER_COMMAND_HOST" "$OWNER_ENTRYPOINT" \
    "$MARKER_1" "$MARKER_2" "$MARKER_3" "$MARKER_4" "$MARKER_5" <<'PY'
import json, sys
path, argv = sys.argv[1], sys.argv[2:]
assert argv[0] == "/opt/zkdeal-owner-soak", argv[0]
assert len(argv) == 6, argv
with open(path, "w", encoding="ascii", newline="\n") as stream:
    json.dump(argv, stream)
    stream.write("\n")
PY
  chmod 0644 "$OWNER_COMMAND_HOST"
  OWNER_COMMAND_SHA="$(sha256_of "$OWNER_COMMAND_HOST")"
  is_sha256 "$OWNER_COMMAND_SHA" || die "could not hash $OWNER_COMMAND_HOST" "owner-command"
  log "owner command written: $OWNER_COMMAND_HOST (sha256 $OWNER_COMMAND_SHA)"
}

# ---------------------------------------------------------------------------
# Image builds: owner driver -> local registry digest -> soak-runner candidate.
# ---------------------------------------------------------------------------

build_owner_driver() {
  OWNER_SOURCE_SHA="$(sha256_of "$INFRA/owner-soak-driver/zkdeal_owner_soak.py")"
  is_sha256 "$OWNER_SOURCE_SHA" || die "could not hash the owner driver source" "owner-image"
  local tag="$OWNER_REPO:src-$(printf '%s' "$OWNER_SOURCE_SHA" | cut -c1-12)"

  # The Dockerfile addresses cloud-deployer-infra/owner-soak-driver/... and the
  # root license, so the context is the umbrella root. On a large working tree
  # that context upload dominates the build; SOAK_OWNER_BUILD_CONTEXT=minimal
  # stages a byte-identical two-file context instead.
  local context="$ZKDEAL_ROOT"
  if [ "${SOAK_OWNER_BUILD_CONTEXT:-umbrella}" = "minimal" ]; then
    context="$WORK/.owner-build-context"
    rm -rf "$context"
    mkdir -p "$context/cloud-deployer-infra/owner-soak-driver"
    cp "$INFRA/owner-soak-driver/zkdeal_owner_soak.py" "$context/cloud-deployer-infra/owner-soak-driver/zkdeal_owner_soak.py"
    cp "$ZKDEAL_ROOT/LICENSE" "$context/LICENSE"
    log "using the staged minimal build context $context"
  fi

  log "building owner soak driver (source sha256 $OWNER_SOURCE_SHA)"
  DOCKER_BUILDKIT=1 docker build --pull=false \
    --file "$INFRA/owner-soak-driver/Dockerfile" \
    --build-arg "OWNER_SOAK_DRIVER_SOURCE_SHA256=$OWNER_SOURCE_SHA" \
    --tag "$tag" \
    "$context" \
    || die "owner soak driver build failed (the in-image sha256 gate rejects any source drift)" "owner-image"

  log "pushing $tag to the local registry"
  local push_output push_digest
  push_output="$(docker push "$tag" 2>&1)" || {
    printf '%s\n' "$push_output" >&2
    die "pushing $tag failed; is the local OCI registry on $REGISTRY running (compose/compose.registry.yaml)?" "registry"
  }
  push_digest="$(printf '%s\n' "$push_output" | sed -n 's/.*digest: \(sha256:[0-9a-f]\{64\}\).*/\1/p' | tail -n 1)"
  [ -n "$push_digest" ] || die "docker push did not report a manifest digest for $tag" "registry"
  OWNER_IMAGE_REF="$OWNER_REPO@$push_digest"

  docker image inspect "$OWNER_IMAGE_REF" --format '{{.Id}}' >/dev/null 2>&1 \
    || die "the pushed digest reference $OWNER_IMAGE_REF is not resolvable locally" "registry"

  local label
  label="$(label_of "$OWNER_IMAGE_REF" 'org.zkdeal.owner-soak-driver.source.sha256')"
  [ "$label" = "$OWNER_SOURCE_SHA" ] \
    || die "owner driver image label '$label' differs from the source sha256 $OWNER_SOURCE_SHA" "hash-gate"
  OWNER_IMAGE_LABEL_SHA="$label"
  log "owner driver digest reference: $OWNER_IMAGE_REF"
}

build_candidate_runner() {
  RUNNER_SOURCE_SHA="$(cat \
    "$INFRA/soak-runner/zkdeal_soak.py" \
    "$INFRA/scripts/common.py" \
    "$INFRA/scripts/soak.py" \
    "$INFRA/config/schemas/release-soak-manifest.schema.json" | sha256sum | awk '{print $1}')"
  is_sha256 "$RUNNER_SOURCE_SHA" || die "could not compute the concatenated soak-runner source sha256" "runner-image"

  log "building soak-runner --target candidate (source sha256 $RUNNER_SOURCE_SHA)"
  DOCKER_BUILDKIT=1 docker build --pull=false \
    --file "$INFRA/soak-runner/Dockerfile" \
    --target candidate \
    --build-arg "SOAK_RUNNER_SOURCE_SHA256=$RUNNER_SOURCE_SHA" \
    --build-arg "OWNER_SOAK_DRIVER_SOURCE_SHA256=$OWNER_SOURCE_SHA" \
    --build-arg "OWNER_SOAK_DRIVER_IMAGE=$OWNER_IMAGE_REF" \
    --tag "$CANDIDATE_TAG" \
    "$INFRA" \
    || die "soak-runner candidate build failed (source hash gate or missing owner driver at $OWNER_IMAGE_REF)" "runner-image"

  local runner_label owner_label entrypoint user
  runner_label="$(label_of "$CANDIDATE_TAG" 'org.zkdeal.soak-runner.source.sha256')"
  [ "$runner_label" = "$RUNNER_SOURCE_SHA" ] \
    || die "candidate image soak-runner label '$runner_label' differs from $RUNNER_SOURCE_SHA" "hash-gate"
  owner_label="$(label_of "$CANDIDATE_TAG" 'org.zkdeal.owner-soak-driver.source.sha256')"
  [ "$owner_label" = "$OWNER_SOURCE_SHA" ] \
    || die "candidate image owner-driver label '$owner_label' differs from $OWNER_SOURCE_SHA" "hash-gate"
  entrypoint="$(docker image inspect "$CANDIDATE_TAG" --format '{{json .Config.Entrypoint}}')"
  [ "$entrypoint" = '["/opt/zkdeal-soak"]' ] || die "candidate entrypoint is $entrypoint, expected [\"/opt/zkdeal-soak\"]" "runner-image"

  docker run --rm --entrypoint /bin/sh "$CANDIDATE_TAG" -c \
    'test -f /opt/zkdeal-owner-soak && test -x /opt/zkdeal-owner-soak && test ! -L /opt/zkdeal-owner-soak' \
    || die "the candidate image does not carry an executable, non-symlink /opt/zkdeal-owner-soak" "hash-gate"

  CANDIDATE_IMAGE_ID="$(docker image inspect "$CANDIDATE_TAG" --format '{{.Id}}')"
  log "candidate soak-runner image: $CANDIDATE_TAG ($CANDIDATE_IMAGE_ID)"
}

# ---------------------------------------------------------------------------
# Environment contract for the running container.
# ---------------------------------------------------------------------------

compose_env() {
  ENV_ARGS=()
  local name value alias upper

  add_env() { ENV_ARGS+=("-e" "$1=$2"); }

  # require_runtime_contract (byte-exact).
  add_env REQUIRE_REAL_PROOF_JOBS "1"
  add_env REQUIRE_FULL_LIFECYCLE "$REQUIRE_FULL_LIFECYCLE"
  add_env REQUIRE_INDUCED_RESTARTS "$REQUIRE_INDUCED_RESTARTS"
  add_env REQUIRE_DURABLE_ASSERTIONS "$REQUIRE_DURABLE_ASSERTIONS"
  add_env REQUIRE_APPEND_ONLY_JOURNAL "1"
  add_env REQUIRE_RESTART_RESUME "1"

  # Hash-bound inputs.
  add_env SOAK_MANIFEST_FILE "/run/zkdeal-soak/manifest.json"
  add_env SOAK_MANIFEST_SHA256 "$MANIFEST_SHA"
  add_env SOAK_OWNER_COMMAND_FILE "/run/zkdeal-soak/owner-command.json"
  add_env SOAK_OWNER_COMMAND_SHA256 "$OWNER_COMMAND_SHA"
  add_env OWNER_SOAK_DRIVER_SOURCE_SHA256 "$OWNER_SOURCE_SHA"
  add_env OWNER_SOAK_DRIVER_IMAGE_LABEL_SHA256 "$OWNER_IMAGE_LABEL_SHA"

  # Durable evidence layout.
  add_env SOAK_DURATION_SECONDS "$DURATION"
  add_env SOAK_EVIDENCE_DIR "$EVIDENCE_MOUNT"
  add_env SOAK_JOURNAL_FILE "$EVIDENCE_MOUNT/journal.ndjson"
  add_env SOAK_STATE_FILE "$EVIDENCE_MOUNT/state.json"
  add_env SOAK_CLOSURE_FILE "$EVIDENCE_MOUNT/closure.json"
  add_env ZKDEAL_OWNER_SOAK_STATE "$EVIDENCE_MOUNT/owner-state.json"

  # Candidate identity and topology binding.
  add_env SOAK_CANDIDATE_ID "$SOAK_CANDIDATE_ID"
  add_env HOSTED_INTEGRATION_TOKEN "$HOSTED_INTEGRATION_TOKEN"
  add_env CANDIDATE_TOPOLOGY_VERIFICATION_SHA256 "$CANDIDATE_TOPOLOGY_VERIFICATION_SHA256"
  add_env ACTIVE_COORDINATOR_ID "$ACTIVE_COORDINATOR_ID"
  add_env STANDBY_COORDINATOR_ID "$STANDBY_COORDINATOR_ID"

  for name in $ENDPOINT_VARS $ADDRESS_VARS; do
    value="$(eval "printf '%s' \"\${$name}\"")"
    add_env "$name" "$value"
  done
  for name in $OPTIONAL_VARS; do
    value="$(eval "printf '%s' \"\${$name:-}\"")"
    if [ -n "$value" ]; then
      add_env "$name" "$value"
    fi
  done
  for alias in $AUTH_ALIASES; do
    upper="$(printf '%s' "$alias" | tr 'a-z' 'A-Z')"
    add_env "SOAK_AUTH_${upper}_TOKEN_FILE" "/run/zkdeal-soak/tokens/$alias.token"
  done
  if [ -f "$SPONSOR_PROFILE_HOST" ]; then
    add_env SOAK_SPONSOR_PROFILE_FILE "/run/zkdeal-soak/sponsor-profile.json"
  fi
}

compose_mounts() {
  MOUNT_ARGS=(
    "-v" "$EVIDENCE_HOST:$EVIDENCE_MOUNT"
    "-v" "$MANIFEST_HOST:/run/zkdeal-soak/manifest.json:ro"
    "-v" "$OWNER_COMMAND_HOST:/run/zkdeal-soak/owner-command.json:ro"
    "-v" "$TOKENS_HOST:/run/zkdeal-soak/tokens:ro"
  )
  if [ -f "$SPONSOR_PROFILE_HOST" ]; then
    MOUNT_ARGS+=("-v" "$SPONSOR_PROFILE_HOST:/run/zkdeal-soak/sponsor-profile.json:ro")
  fi
}

print_env_contract() {
  local item redacted
  printf '\n--- runtime env contract (%s vars) ---\n' "$(( ${#ENV_ARGS[@]} / 2 ))"
  for item in "${ENV_ARGS[@]}"; do
    [ "$item" = "-e" ] && continue
    case "$item" in
      HOSTED_INTEGRATION_TOKEN=*|*TOKEN=*) redacted="${item%%=*}=<redacted>" ;;
      *) redacted="$item" ;;
    esac
    printf '  %s\n' "$redacted"
  done
  printf -- '--- end env contract ---\n\n'
}

# ---------------------------------------------------------------------------
# Launch.
# ---------------------------------------------------------------------------

guard_previous_run() {
  local resume="${1:-0}" status
  if [ -e "$EVIDENCE_HOST/closure.json" ]; then
    die "$EVIDENCE_HOST/closure.json already exists; the closure is write-once. Archive $EVIDENCE_HOST and rerun." "evidence"
  fi
  if [ -e "$EVIDENCE_HOST/state.json" ] && [ "$resume" != "1" ]; then
    die "durable state already exists at $EVIDENCE_HOST/state.json; rerun with --resume or archive the evidence directory" "evidence"
  fi
  if [ "$resume" = "1" ] && [ ! -e "$EVIDENCE_HOST/state.json" ]; then
    die "--resume was requested but $EVIDENCE_HOST/state.json does not exist" "evidence"
  fi
  if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
    status="$(docker container inspect "$CONTAINER" --format '{{.State.Status}}')"
    if [ "$status" = "running" ]; then
      die "container '$CONTAINER' is already running; use --status, or stop it first" "container"
    fi
    log "removing previous exited container '$CONTAINER' (status $status)"
    docker rm "$CONTAINER" >/dev/null
  fi
}

launch() {
  local resume="${1:-0}"
  local start_epoch end_epoch ceiling_epoch container_id follow_pid

  compose_env
  compose_mounts

  start_epoch="$(date -u +%s)"
  end_epoch=$(( start_epoch + DURATION ))
  ceiling_epoch=$(( end_epoch + 7200 ))

  touch "$LOG" || die "cannot write the soak log at $LOG" "log"
  printf '\n===== zkdeal 6h TEST soak session start %s (resume=%s) =====\n' \
    "$(iso "$start_epoch")" "$resume" >> "$LOG"

  container_id="$(docker run -d \
    --name "$CONTAINER" \
    --network "$SOAK_DOCKER_NETWORK" \
    --user "$CONTAINER_USER" \
    --restart no \
    --label "zkdeal.soak=test-6h" \
    --label "zkdeal.soak.candidate=$SOAK_CANDIDATE_ID" \
    --label "zkdeal.soak.durationSeconds=$DURATION" \
    "${MOUNT_ARGS[@]}" \
    "${ENV_ARGS[@]}" \
    "$CANDIDATE_TAG" \
    "$MARKER_1" "$MARKER_2" "$MARKER_3" "$MARKER_4" "$MARKER_5")" \
    || die "docker run failed for the candidate soak-runner" "container"

  # Detached log capture: the container is daemon-owned, the follower is only a
  # convenience writer for $LOG and may be killed without affecting the soak.
  nohup docker logs -f "$CONTAINER" >>"$LOG" 2>&1 </dev/null &
  follow_pid="$!"
  disown "$follow_pid" 2>/dev/null || true

  {
    printf 'CONTAINER=%s\n' "$CONTAINER"
    printf 'CONTAINER_ID=%s\n' "$container_id"
    printf 'CANDIDATE_IMAGE=%s\n' "$CANDIDATE_TAG"
    printf 'CANDIDATE_IMAGE_ID=%s\n' "$CANDIDATE_IMAGE_ID"
    printf 'OWNER_IMAGE_REF=%s\n' "$OWNER_IMAGE_REF"
    printf 'OWNER_SOURCE_SHA256=%s\n' "$OWNER_SOURCE_SHA"
    printf 'RUNNER_SOURCE_SHA256=%s\n' "$RUNNER_SOURCE_SHA"
    printf 'MANIFEST_HOST_PATH=%s\n' "$MANIFEST_HOST"
    printf 'MANIFEST_SHA256=%s\n' "$MANIFEST_SHA"
    printf 'OWNER_COMMAND_SHA256=%s\n' "$OWNER_COMMAND_SHA"
    printf 'DURATION_SECONDS=%s\n' "$DURATION"
    printf 'RESUMED=%s\n' "$resume"
    printf 'START_EPOCH=%s\n' "$start_epoch"
    printf 'START_ISO=%s\n' "$(iso "$start_epoch")"
    printf 'EXPECTED_END_EPOCH=%s\n' "$end_epoch"
    printf 'EXPECTED_END_ISO=%s\n' "$(iso "$end_epoch")"
    printf 'HARD_CEILING_ISO=%s\n' "$(iso "$ceiling_epoch")"
    printf 'EVIDENCE_HOST=%s\n' "$EVIDENCE_HOST"
    printf 'EVIDENCE_MOUNT=%s\n' "$EVIDENCE_MOUNT"
    printf 'LOG=%s\n' "$LOG"
    printf 'LOG_FOLLOW_PID=%s\n' "$follow_pid"
  } > "$RUN_ENV"

  printf '\n'
  log "SIX-HOUR TEST SOAK LAUNCHED (not the 12h release gate)"
  printf '  container         : %s (%s)\n' "$CONTAINER" "$(printf '%s' "$container_id" | cut -c1-12)"
  printf '  image             : %s\n' "$CANDIDATE_TAG"
  printf '  owner driver      : %s\n' "$OWNER_IMAGE_REF"
  printf '  started (UTC)     : %s\n' "$(iso "$start_epoch")"
  printf '  expected closure  : %s  (start + %ss)\n' "$(iso "$end_epoch")" "$DURATION"
  printf '  hard timeout      : %s  (owner command timeout = duration + 7200s)\n' "$(iso "$ceiling_epoch")"
  printf '  evidence          : %s\n' "$EVIDENCE_HOST"
  printf '  log               : %s\n' "$LOG"
  printf '  run record        : %s\n' "$RUN_ENV"
  printf '\n  status : %s --status\n' "$0"
  printf '  verify : %s --verify\n\n' "$0"
}

# ---------------------------------------------------------------------------
# Status.
# ---------------------------------------------------------------------------

cmd_status() {
  [ -f "$RUN_ENV" ] || die "no run record at $RUN_ENV; the soak has not been launched from this directory" "status"
  # shellcheck disable=SC1090
  . "$RUN_ENV"

  local now elapsed remaining state exit_code alive="no"
  now="$(date -u +%s)"
  elapsed=$(( now - START_EPOCH ))
  remaining=$(( EXPECTED_END_EPOCH - now ))
  [ "$remaining" -lt 0 ] && remaining=0

  if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
    state="$(docker container inspect "$CONTAINER" --format '{{.State.Status}}')"
    exit_code="$(docker container inspect "$CONTAINER" --format '{{.State.ExitCode}}')"
    [ "$state" = "running" ] && alive="yes"
  else
    state="absent"
    exit_code="n/a"
  fi

  printf 'zkdeal 6h TEST soak status\n'
  printf '  started (UTC)  : %s\n' "$START_ISO"
  printf '  now (UTC)      : %s\n' "$(iso "$now")"
  printf '  elapsed        : %02d:%02d:%02d of %ss\n' \
    $(( elapsed / 3600 )) $(( (elapsed % 3600) / 60 )) $(( elapsed % 60 )) "$DURATION_SECONDS"
  printf '  remaining      : %02d:%02d:%02d (to expected closure %s)\n' \
    $(( remaining / 3600 )) $(( (remaining % 3600) / 60 )) $(( remaining % 60 )) "$EXPECTED_END_ISO"
  printf '  container      : %s (state=%s exit=%s)\n' "$CONTAINER" "$state" "$exit_code"
  printf '  process alive  : %s\n' "$alive"
  if [ -n "${LOG_FOLLOW_PID:-}" ]; then
    if kill -0 "$LOG_FOLLOW_PID" 2>/dev/null; then
      printf '  log follower   : pid %s alive\n' "$LOG_FOLLOW_PID"
    else
      printf '  log follower   : pid %s gone (log capture stopped; the soak itself is unaffected)\n' "$LOG_FOLLOW_PID"
    fi
  fi

  local journal="$EVIDENCE_HOST/journal.ndjson"
  if [ ! -f "$journal" ]; then
    printf '  journal        : not created yet (%s)\n' "$journal"
  else
    python3 - "$journal" <<'PY'
import json, sys
path = sys.argv[1]
events, broken = [], 0
with open(path, encoding="utf-8", errors="replace") as stream:
    for raw in stream:
        raw = raw.strip()
        if not raw:
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            broken += 1
            continue
        if isinstance(value, dict):
            events.append(value)
print("  journal        : %d events (%d unparsable lines)" % (len(events), broken))
kinds = [str(item.get("kind", "?")) for item in events]
print("  last 5 kinds   : %s" % (", ".join(kinds[-5:]) if kinds else "(none)"))
faults = [str(item.get("fault")) for item in events if item.get("kind") == "fault"]
recovered = {str(item.get("fault")) for item in events if item.get("kind") == "recovered"}
pairs = [name for name in faults if name in recovered]
print("  fault/recovered: %d injected, %d recovered, %d complete pairs" % (
    len(faults), len(recovered), len(pairs)))
if faults:
    print("  faults injected: %s" % ", ".join(faults))
open_faults = [name for name in faults if name not in recovered]
if open_faults:
    print("  UNRECOVERED    : %s" % ", ".join(open_faults))
closure = [item for item in events if item.get("kind") == "closure"]
print("  closure event  : %s" % ("present" if closure else "not written yet"))
PY
  fi

  if [ -f "$EVIDENCE_HOST/closure.json" ]; then
    printf '  closure file   : %s (runner closure written)\n' "$EVIDENCE_HOST/closure.json"
  else
    printf '  closure file   : absent\n'
  fi
  printf '  log tail       : tail -f %s\n' "$LOG"
}

# ---------------------------------------------------------------------------
# Verify.
# ---------------------------------------------------------------------------

cmd_verify() {
  if [ -f "$RUN_ENV" ]; then
    # Prefer the exact paths the launch recorded.
    # shellcheck disable=SC1090
    . "$RUN_ENV"
    MANIFEST_HOST="${MANIFEST_HOST_PATH:-$MANIFEST_HOST}"
  fi
  resolve_manifest
  local manifest="$MANIFEST_HOST"
  local journal="$EVIDENCE_HOST/journal.ndjson"
  [ -f "$manifest" ] || die "manifest is absent: $manifest" "verify"
  [ -f "$journal" ] || die "journal is absent: $journal (nothing to verify)" "verify"
  docker image inspect "$TOOLS_IMAGE" >/dev/null 2>&1 \
    || die "deployment-tools image '$TOOLS_IMAGE' is absent (set DEPLOYMENT_TOOLS_IMAGE)" "verify"

  # Mount the manifest and the journal at fixed paths so a relocated
  # SOAK_EVIDENCE_HOST / SOAK_MANIFEST_HOST still verifies.
  local rel_manifest="/verify/manifest.json"
  local rel_journal="/verify/journal.ndjson"
  local rc=0
  VERIFY_MOUNTS=(
    "-v" "$manifest:$rel_manifest:ro"
    "-v" "$journal:$rel_journal:ro"
  )

  log "running scripts/soak.py verify inside $TOOLS_IMAGE"
  set +e
  docker run --rm \
    --user "$CONTAINER_USER" \
    -v "$ZKDEAL_ROOT:/workspace:ro" \
    "${VERIFY_MOUNTS[@]}" \
    -w /workspace/cloud-deployer-infra \
    "$TOOLS_IMAGE" scripts/soak.py verify \
    --manifest "$rel_manifest" --journal "$rel_journal"
  rc=$?
  set -e

  printf '\n'
  if [ "$rc" -eq 0 ]; then
    log "CLOSURE VERDICT: PASS -- the journal satisfies scripts/soak.py verify_closure"
  else
    log "CLOSURE VERDICT: FAIL -- scripts/soak.py verify exited $rc (see the error above)"
  fi

  if [ -f "$EVIDENCE_HOST/closure.json" ]; then
    printf '\nrunner closure (%s):\n' "$EVIDENCE_HOST/closure.json"
    python3 - "$EVIDENCE_HOST/closure.json" <<'PY'
import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as exc:  # noqa: BLE001
    print("  unreadable: %s" % exc)
    raise SystemExit(0)
for name in ("passed", "classification", "resumeUsed", "manifestSha256",
             "ownerCommandSha256", "ownerDriverSourceSha256", "journalSha256"):
    if name in value:
        print("  %-24s %s" % (name, value[name]))
verification = value.get("verification")
if isinstance(verification, dict):
    for name in ("durationSeconds", "events", "sealedOutputs", "usageUnits",
                 "chargesWei", "restartResumeVerified", "faults"):
        if name in verification:
            print("  verification.%-11s %s" % (name, verification[name]))
PY
  else
    printf '\nrunner closure file %s is absent: the runner did not reach its write-once closure.\n' \
      "$EVIDENCE_HOST/closure.json"
  fi

  if docker image inspect "$CANDIDATE_TAG" >/dev/null 2>&1; then
    printf '\ncross-check with the hash-bound candidate image copy of soak.py:\n'
    set +e
    docker run --rm \
      --user "$CONTAINER_USER" \
      --entrypoint /usr/local/bin/python3 \
      "${VERIFY_MOUNTS[@]}" \
      "$CANDIDATE_TAG" /opt/zkdeal/soak.py verify \
      --manifest "$rel_manifest" --journal "$rel_journal" >/dev/null
    local cross=$?
    set -e
    if [ "$cross" -eq "$rc" ]; then
      printf '  cross-check agrees with the deployment-tools verdict (exit %s)\n' "$cross"
    else
      printf '  WARNING: candidate-image verify exited %s while deployment-tools exited %s;\n' "$cross" "$rc"
      printf '           the two source copies disagree -- treat the run as unverified.\n'
    fi
  fi

  return "$rc"
}

# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------

main() {
  case "${1:-}" in
    ""|--launch|launch)
      preflight_tree
      preflight_inputs
      preflight_duration_floor
      load_endpoints
      guard_previous_run 0
      materialize_tokens
      write_owner_command
      build_owner_driver
      build_candidate_runner
      launch 0
      ;;
    --resume)
      preflight_tree
      preflight_inputs
      preflight_duration_floor
      load_endpoints
      guard_previous_run 1
      materialize_tokens
      write_owner_command
      build_owner_driver
      build_candidate_runner
      launch 1
      ;;
    --build-only)
      preflight_tree
      preflight_duration_floor
      build_owner_driver
      build_candidate_runner
      log "build only: images are gate-checked and ready; nothing was launched"
      ;;
    --check|--dry-run)
      preflight_tree
      preflight_inputs
      preflight_duration_floor
      load_endpoints
      materialize_tokens
      write_owner_command
      OWNER_SOURCE_SHA="$(sha256_of "$INFRA/owner-soak-driver/zkdeal_owner_soak.py")"
      OWNER_IMAGE_LABEL_SHA="$OWNER_SOURCE_SHA"
      compose_env
      print_env_contract
      log "check only: inputs validated, no image was built and nothing was launched"
      ;;
    --status)
      need_cmd docker
      need_cmd python3
      cmd_status
      ;;
    --verify)
      need_cmd docker
      need_cmd python3
      cmd_verify
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      die "unknown mode '$1' (try --help)" "usage"
      ;;
  esac
}

main "$@"
