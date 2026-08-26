#!/usr/bin/env bash
# stack-down.sh -- remove everything stack-up.sh created for the six-hour test
# soak: the compose project, any container still carrying the zksoak6h- prefix,
# the network, the named volumes, and (optionally) the working directory.
#
#   ./stack-down.sh                 # containers, network and volumes; keep
#                                   # ~/soak6h so evidence and logs survive
#   ./stack-down.sh --purge         # also delete ~/soak6h entirely
#   ./stack-down.sh --keep-volumes  # leave the postgres/minio volumes in place
#
# Safe to run when nothing is up: every step tolerates an absent target. It
# never touches containers outside its own prefix, and never removes the
# published zkdeal images or the minted prover runtime.

set -euo pipefail

PROJECT="${SOAK6H_PROJECT:-zksoak6h}"
PREFIX="${PROJECT}-"
NETWORK="${PROJECT}_soaknet"
WORK="${SOAK6H_WORK:-$HOME/soak6h}"

PURGE=0
KEEP_VOLUMES=0
for argument in "$@"; do
  case "$argument" in
    --purge) PURGE=1 ;;
    --keep-volumes) KEEP_VOLUMES=1 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) printf 'unknown option: %s (try --help)\n' "$argument" >&2; exit 2 ;;
  esac
done

log() { printf '[soak6h-down %s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

command -v docker >/dev/null 2>&1 || { printf 'docker is not on PATH\n' >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Compose-level teardown
# ---------------------------------------------------------------------------

if [ -f "$WORK/compose.yaml" ]; then
  log "docker compose down (project $PROJECT)"
  if [ "$KEEP_VOLUMES" = "1" ]; then
    docker compose --project-name "$PROJECT" --project-directory "$WORK" \
      -f "$WORK/compose.yaml" down --remove-orphans --timeout 20 >/dev/null 2>&1 || true
  else
    docker compose --project-name "$PROJECT" --project-directory "$WORK" \
      -f "$WORK/compose.yaml" down --remove-orphans --volumes --timeout 20 >/dev/null 2>&1 || true
  fi
else
  log "no compose file at $WORK/compose.yaml; falling back to prefix cleanup"
fi

# ---------------------------------------------------------------------------
# 2. Prefix sweep
# ---------------------------------------------------------------------------
# The failover provider detaches containers from the network during an
# incident, after which compose can no longer see them. Sweep by name too.

leftovers="$(docker ps -aq --filter "name=^${PREFIX}" || true)"
if [ -n "$leftovers" ]; then
  count="$(printf '%s\n' "$leftovers" | grep -c . || true)"
  log "force-removing $count container(s) still carrying the ${PREFIX} prefix"
  # shellcheck disable=SC2086
  docker rm -f $leftovers >/dev/null 2>&1 || true
fi

# Anything the compose project still claims by label.
labelled="$(docker ps -aq --filter "label=com.docker.compose.project=${PROJECT}" || true)"
if [ -n "$labelled" ]; then
  # shellcheck disable=SC2086
  docker rm -f $labelled >/dev/null 2>&1 || true
fi

# ---------------------------------------------------------------------------
# 3. Network
# ---------------------------------------------------------------------------

if docker network inspect "$NETWORK" >/dev/null 2>&1; then
  log "removing network $NETWORK"
  docker network rm "$NETWORK" >/dev/null 2>&1 \
    || log "WARNING: $NETWORK could not be removed; something is still attached"
fi

# ---------------------------------------------------------------------------
# 4. Volumes
# ---------------------------------------------------------------------------

if [ "$KEEP_VOLUMES" = "1" ]; then
  log "keeping the named volumes (--keep-volumes)"
else
  for volume in pg-primary pg-standby pg-fresh minio-data minio-backup-data \
                minio-fresh-data room-node-secrets room-node-state \
                failover-provider-state; do
    if docker volume inspect "${PROJECT}_${volume}" >/dev/null 2>&1; then
      docker volume rm -f "${PROJECT}_${volume}" >/dev/null 2>&1 \
        && log "removed volume ${PROJECT}_${volume}" \
        || log "WARNING: could not remove volume ${PROJECT}_${volume}"
    fi
  done
fi

# ---------------------------------------------------------------------------
# 5. Working directory
# ---------------------------------------------------------------------------

if [ "$PURGE" = "1" ]; then
  if [ -d "$WORK" ]; then
    # PHASE 11 of stack-up hands $WORK/ctl-secrets to uid 65532, so a non-root
    # invoker cannot always unlink it. Give it back through a root container.
    docker run --rm -u 0:0 -v "$WORK:/target" "${SOAK6H_ALPINE_IMAGE:-alpine:3.21}" \
      sh -c 'chown -R '"$(id -u)":"$(id -g)"' /target' >/dev/null 2>&1 || true
    rm -rf "$WORK"
    log "purged $WORK"
  fi
else
  log "kept $WORK (endpoints.env, tokens, topologies, logs); use --purge to delete it"
fi

# ---------------------------------------------------------------------------
# 6. Report
# ---------------------------------------------------------------------------

remaining="$(docker ps -aq --filter "name=^${PREFIX}" || true)"
if [ -n "$remaining" ]; then
  printf '\n[soak6h-down] WARNING: containers survived the teardown:\n' >&2
  docker ps -a --filter "name=^${PREFIX}" --format '  {{.Names}}\t{{.Status}}' >&2 || true
  exit 1
fi

log "teardown complete: no ${PREFIX} containers remain"
