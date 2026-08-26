#!/bin/bash
# Create one genuinely real VALIDITY_ONLY room in the acceptance rig.
#
# Runs between contract deployment and service start-up, because two things
# must be true before anything else comes up:
#   - the admission signing identity has to exist, since `admissionSigner` is
#     written once at room intake and there is no setter; and
#   - the governance cycle has to complete, and a chain time-warp after the
#     indexer is running would put archived observations days ahead of wall
#     clock and instantly inactivity-expire every open room.
#
# The rig hands governance to a stock OpenZeppelin timelock at a one-block
# delay (Deploy.s.sol, ZKDEAL_TEST_RIG_TIMELOCK), so the schedule/execute cycle
# below is real - it just does not wait out the Stage-1 window. That deviation
# belongs in the run's evidence.
#
# What is NOT faked here: the verifier is the real RiscZeroGroth16Verifier
# behind the production adapter, and the cold-template seal is a real Groth16
# seal minted by the live CUDA prover over the real genesis package. The
# registry recomputes the statement and verifies it, so a stub seal reverts.
set -u

: "${WORK:?WORK is required}"
: "${NETWORK:?NETWORK is required}"
: "${IMG_FOUNDRY:?IMG_FOUNDRY is required}"
: "${DEPLOYER_KEY:?DEPLOYER_KEY is required}"
: "${DEPLOYER_ADDRESS:?DEPLOYER_ADDRESS is required}"
: "${COLD_TEMPLATE_REGISTRY:?COLD_TEMPLATE_REGISTRY is required}"
: "${ROOM_MANAGER:?ROOM_MANAGER is required}"
: "${TEST_RIG_TIMELOCK:?TEST_RIG_TIMELOCK is required}"
: "${ADMISSION_ACCOUNT:?ADMISSION_ACCOUNT is required}"
: "${ADMISSION_SIGNER_KEY:?ADMISSION_SIGNER_KEY is required}"
RPC="${RPC:-http://rpc-a:8545}"

say() { printf '[create-room %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { printf '[create-room] FATAL: %s\n' "$*" >&2; exit 1; }

cast() {
  docker run --rm --network "$NETWORK" --entrypoint cast "$IMG_FOUNDRY" "$@"
}

VERIFIER_ADMIN_ROLE="$(cast keccak 'VERIFIER_ADMIN_ROLE')"
TEMPLATE_ADMIN_ROLE="$(cast keccak 'TEMPLATE_ADMIN_ROLE')"

# ---------------------------------------------------------------------------
# 1. Take the two registry admin roles through the timelock.
#
# Rather than routing every registry call through schedule/execute, one cycle
# grants the roles to the deployer and the forge script then calls directly.
# The governance mechanics are exercised either way, and the resulting script
# is far easier to read.
# ---------------------------------------------------------------------------
say "scheduling the registry role grants through the rig timelock"

grant_calldata() { cast calldata 'grantRole(bytes32,address)' "$1" "$DEPLOYER_ADDRESS"; }
SALT="0x$(printf '%064x' "$(date -u +%s)")"

for role in "$VERIFIER_ADMIN_ROLE" "$TEMPLATE_ADMIN_ROLE"; do
  data="$(grant_calldata "$role")"
  cast send --rpc-url "$RPC" --private-key "$DEPLOYER_KEY" "$TEST_RIG_TIMELOCK" \
    'schedule(address,uint256,bytes,bytes32,bytes32,uint256)' \
    "$COLD_TEMPLATE_REGISTRY" 0 "$data" \
    "0x$(printf '%064x' 0)" "$SALT" 1 >/dev/null \
    || die "could not schedule the role grant"
done

# One block is the whole delay. Mining rather than warping keeps every clock in
# the stack consistent with every other.
cast rpc --rpc-url "$RPC" evm_mine >/dev/null || die "could not mine past the timelock delay"
sleep 2

for role in "$VERIFIER_ADMIN_ROLE" "$TEMPLATE_ADMIN_ROLE"; do
  data="$(grant_calldata "$role")"
  cast send --rpc-url "$RPC" --private-key "$DEPLOYER_KEY" "$TEST_RIG_TIMELOCK" \
    'execute(address,uint256,bytes,bytes32,bytes32)' \
    "$COLD_TEMPLATE_REGISTRY" 0 "$data" \
    "0x$(printf '%064x' 0)" "$SALT" >/dev/null \
    || die "could not execute the role grant"
done

for role in "$VERIFIER_ADMIN_ROLE" "$TEMPLATE_ADMIN_ROLE"; do
  held="$(cast call --rpc-url "$RPC" "$COLD_TEMPLATE_REGISTRY" \
    'hasRole(bytes32,address)(bool)' "$role" "$DEPLOYER_ADDRESS")"
  [ "$held" = "true" ] || die "the timelock cycle did not grant $role"
done
say "registry admin roles granted through a real schedule/execute cycle"

# ---------------------------------------------------------------------------
# 2. Mint a real cold-template proof.
# ---------------------------------------------------------------------------
say "minting a cold-template proof on the live prover (this needs the GPU)"
docker run --rm --network "$NETWORK" \
  -v "$(dirname "$0"):/app:ro" -v "$WORK:/work" \
  -e PROVER_URL="${PROVER_INTERNAL_URL:-http://prover:8080}" \
  -e ZKDEAL_PROVER_TOKEN="${PROVER_TOKEN:-}" \
  -e ZKDEAL_DEPLOYMENT_DOMAIN="${DEPLOYMENT_DOMAIN:-}" \
  node:22-bookworm node /app/mint-cold-template.mjs /work/cold-template.json \
  || die "the cold-template proof could not be minted"

read_field() { python3 -c "import json,sys;print(json.load(open('$WORK/cold-template.json'))['$1'])"; }

COLD_TEMPLATE_ID="$(read_field templateId)"
COLD_INITIAL_STATE_ROOT="$(read_field initialStateRoot)"
COLD_POLICY_HASH="$(read_field policyHash)"
COLD_PROOF_PROGRAM_ID="$(read_field programId)"
COLD_PROOF_SYSTEM_VERSION="$(read_field proofSystemVersion)"
COLD_GENESIS_DATA_HASH="$(read_field genesisDataHash)"
COLD_SEAL="$(read_field seal)"
COLD_CANONICAL_DATA="$(read_field canonicalColdTemplateData)"
# createRoom rejects a zero participant root, and the capacity has to match
# what the template was prepared with. Both are known only to prepare, so
# they travel in the mint record rather than being guessed here.
ROOM_PARTICIPANT_ROOT="$(read_field initialParticipantRoot)"
ROOM_PARTICIPANT_CAPACITY="$(read_field participantCapacity)"
[ "${#COLD_SEAL}" -gt 8 ] || die "the minted seal is implausibly short; refusing to register a stub"

# ---------------------------------------------------------------------------
# 3. Register the template and create the room.
# ---------------------------------------------------------------------------
say "registering the template and creating the room"
docker run --rm --network "$NETWORK" \
  -v "$CONTRACTS_DIR:/workspace" -w /workspace/contracts \
  -e DEPLOYER_KEY="$DEPLOYER_KEY" \
  -e COLD_TEMPLATE_REGISTRY="$COLD_TEMPLATE_REGISTRY" \
  -e ROOM_MANAGER="$ROOM_MANAGER" \
  -e ADMISSION_SIGNER_ADDRESS="$ADMISSION_ACCOUNT" \
  -e COLD_TEMPLATE_ID="$COLD_TEMPLATE_ID" \
  -e COLD_INITIAL_STATE_ROOT="$COLD_INITIAL_STATE_ROOT" \
  -e COLD_POLICY_HASH="$COLD_POLICY_HASH" \
  -e COLD_PROOF_PROGRAM_ID="$COLD_PROOF_PROGRAM_ID" \
  -e COLD_PROOF_SYSTEM_VERSION="$COLD_PROOF_SYSTEM_VERSION" \
  -e COLD_GENESIS_DATA_HASH="$COLD_GENESIS_DATA_HASH" \
  -e COLD_SEAL="$COLD_SEAL" \
  -e COLD_CANONICAL_DATA="$COLD_CANONICAL_DATA" \
  -e ROOM_PARTICIPANT_ROOT="$ROOM_PARTICIPANT_ROOT" \
  -e ROOM_PARTICIPANT_CAPACITY="$ROOM_PARTICIPANT_CAPACITY" \
  -e SOAK_ROOM_OUT=deployments/soak-room.env \
  --entrypoint forge "$IMG_FOUNDRY" \
  script script/SoakRoom.s.sol:SoakRoom --rpc-url "$RPC" --broadcast -vv \
  || die "room creation failed"

set -a; . "$CONTRACTS_DIR/contracts/deployments/soak-room.env"; set +a
[ -n "${SOAK_ROOM_ID:-}" ] || die "the room script produced no room id"
say "room ${SOAK_ROOM_ID} created with admission signer ${SOAK_ROOM_ADMISSION_SIGNER}"

# ---------------------------------------------------------------------------
# 4. Fund the service bond.
#
# Only the room's own admission signer may do this - no managed L1 operation
# covers it - and until it is funded every queueDeposit on the room reverts
# BondUnavailable.
# ---------------------------------------------------------------------------
say "funding the service bond from the admission signer"
cast send --rpc-url "$RPC" --private-key "$ADMISSION_SIGNER_KEY" \
  --value "$SOAK_ROOM_SERVICE_BOND_WEI" \
  "$ROOM_MANAGER" 'fundServiceBond(uint64)' "$SOAK_ROOM_ID" >/dev/null \
  || die "the service bond could not be funded"

# ---------------------------------------------------------------------------
# 5. Prove the room is genuinely open before anything is built on it.
# ---------------------------------------------------------------------------
state="$(cast call --rpc-url "$RPC" "$ROOM_MANAGER" 'roomState(uint64)' "$SOAK_ROOM_ID")"
[ -n "$state" ] || die "roomState returned nothing"
case "$state" in
  0x0000000000000000000000000000000000000000000000000000000000000000*)
    die "roomState(${SOAK_ROOM_ID}) is all zeros; the room does not exist on chain" ;;
esac
say "roomState(${SOAK_ROOM_ID}) is non-zero"

printf 'SOAK_ROOM_ID=%s\n' "$SOAK_ROOM_ID" >>"$WORK/.env"
say "done"
