# Contract and indexer reference

The authoritative ABI files are Foundry artifacts owned by `web3-protocol`.
This deployment generates signatures and event/category maps with SHA-256 source
hashes; it never copies bytecode or reimplements decoding. See
[`abi.md`](generated/abi.md),
[`event-to-indexer.md`](generated/event-to-indexer.md), and the JSON mapping for
machine consumption.

## Authentication and idempotency

Contract reads authenticate to the RPC provider and are idempotent at an
explicit block hash. State changes authenticate with the role-specific remote
signer: operations/settlement, blob publisher, finality oracle, sponsorship
relayer, withdrawal relayer, node liveness, and provider payout are isolated identities.
Standby/indexer/reconciler receive no signer. Persist chain ID, sender, nonce,
calldata hash, transaction hash, and intended operation. An ambiguous write is
looked up by hash/nonce before any same-calldata retry.

CLI ABI check:

```sh
cast call "$ROOM_MANAGER" 'supportsInterface(bytes4)(bool)' 0x01ffc9a7 --rpc-url "$L1_RPC_URL"
cast logs --rpc-url "$L1_RPC_URL" --address "$ROOM_POOL" 'FinalizedCheckpointRecorded(uint64,uint64,bytes32,uint64,bytes32)'
```

TypeScript event decode using the consumed owner artifact:

```ts
import { decodeEventLog } from 'viem'
const decoded = decodeEventLog({ abi: roomManagerAbi, data: log.data, topics: log.topics })
```

REST inspection of the exact runtime capability/fence contract:

```sh
curl --fail-with-body https://zkdeal.example/hosting/v1/capabilities \
  -H 'accept-schema-version: 1'
```

The machine-readable capability snapshot in
`generated/hosted-service-capabilities.reference.json` is hash-bound to the
owner manifest. The current negotiated capability verifies the EIP-4844
prepublish archive primitive, c-kzg proof, beacon fallback, canonical
transaction binding, archive-gated finality, reorg retention, and the fenced
archive-before-broadcast publisher with durable exact-byte retry state.

## Errors and finality

Decode custom errors from the same ABI artifact. `Unauthorized`, `BadInput`,
`WrongState`, `BadProof`, `BadWithdrawal`, `DataAvailabilityUnavailable`, and
`StalePrice` are not transport retries. RPC disagreement is a safety failure.
The concrete watcher sender is the fenced `hosted-publisher-worker` processing
the durable `publish-blob` operation; the runbook claims no other sender
coverage. A contradiction after reported
finality follows the post-finality surprise runbook and never enters automatic
retry.

The generated event mapping records both emitted facts and state-changing
calldata categories for RoomManager and RoomPool. Reconciliation stores hashes,
block provenance, decoded fields, and errors under the owner schema; dashboards
must aggregate categories and never label raw room or transaction identifiers.

## Roles, permissions and signer boundaries

The proxy and facets share AccessControl storage. The permission matrix below
is extracted from the current owner source; the generated selector tables bind
the methods to the exact ABI artifact and implementation facet.

| Contract | Role or account check | Authorized methods |
|---|---|---|
| RoomManager | `DEFAULT_ADMIN_ROLE` | `setProtocolFee`, `setAggregateProofConfig`, and AccessControl role administration |
| RoomManager | `SERVICE_MANAGER_ROLE` | `createManagedRoom`, `createManagedRoomWithDataAvailability` |
| RoomManager | `TREASURY_ROLE` | `claimProtocolFees` |
| RoomManager | `UPGRADER_ROLE` | `configureFacets`, UUPS upgrade authorization |
| RoomPool | `DEFAULT_ADMIN_ROLE` | `configureHostingFacet` and AccessControl role administration |
| RoomPool | `NODE_ADMIN_ROLE` | `registerNode`, `configureSlot`, `configureNodeAuthorities`, `retireNode` |
| RoomPool | `TEMPLATE_ADMIN_ROLE` | `registerPreset`, `completeColdPreparation` |
| RoomPool | `POOL_CONTROLLER_ROLE` | `requestCapacityProfile`, `confirmCapacityProfile`, `beginNodeDrain` |
| RoomPool | `MONITOR_ROLE` | `quarantineNode` |
| RoomPool | `SPONSOR_ROLE` | `reserveRoomForWithPermit`, `reserveAndStartForWithPermit`, `reserveAndStartForWithDataAvailabilityWithPermit`, `requestColdPreparationForWithPermit`, `renewRoomForWithPermit` |
| RoomPool | `FINALITY_ORACLE_ROLE` | `recordFinalizedCheckpoint` |
| RoomPool | `PAUSER_ROLE` | `pause`, `unpause` |
| RoomPool | `TREASURY_ROLE` | `claimTreasuryFees` |
| RoomPool | `UPGRADER_ROLE` | UUPS upgrade authorization |
| RoomPool | configured node liveness account | `reportNodeHeartbeat` and liveness-bound recovery |
| RoomPool | configured node operations account | node price and settlement operations that check the operations authority |
| RoomPool | configured node payout account | `claimServiceFees` for accrued provider revenue |

`DEFAULT_ADMIN_ROLE` is the default administrator for the named roles unless a
governance deployment explicitly changes a role admin. Grant/revoke/admin
changes are on-chain governance operations and must be observed through
`RoleGranted`, `RoleRevoked`, and `RoleAdminChanged` before an operator treats
them as effective. Never infer authorization from an off-chain service name.

Read the role constants from the deployed contracts and verify the split node
accounts after configuration:

```sh
NODE_ADMIN_ROLE="$(cast call "$ROOM_POOL" 'NODE_ADMIN_ROLE()(bytes32)' --rpc-url "$L1_RPC_URL")"
cast call "$ROOM_POOL" 'hasRole(bytes32,address)(bool)' "$NODE_ADMIN_ROLE" "$NODE_ADMIN" \
  --rpc-url "$L1_RPC_URL"
cast send "$ROOM_POOL" \
  'configureNodeAuthorities(bytes32,address,address,address)' \
  "$NODE_ID" "$LIVENESS_ACCOUNT" "$OPERATIONS_ACCOUNT" "$PAYOUT_ACCOUNT" \
  --rpc-url "$L1_RPC_URL" --account zkdeal-node-admin
cast call "$ROOM_POOL" 'nodeState(bytes32)' "$NODE_ID" --rpc-url "$L1_RPC_URL"
```

The three node accounts must be nonzero and pairwise distinct in production.
The service identity used for heartbeats cannot settle operations or claim
revenue; the payout identity cannot operate a room. Deployment signer isolation
is narrower still:

| Signer identity | Only intended sender surface |
|---|---|
| liveness | Provider heartbeat/liveness transaction |
| operations-settlement | Active coordinator settlement; never standby/workers |
| blob-publisher | Fenced archive-before-broadcast worker only |
| finality-oracle | Finalized checkpoint recording |
| sponsor-relayer | Sponsored allocation/escrow transactions |
| withdrawal-relayer | Tenant-authorized withdrawal/auto-claim relay |
| payout | Provider revenue claim only |

The remote signer validates its own account/method/path policy. Network policy
alone is not authorization. A standby receives no signer address, credential,
or egress until post-fence promotion. Indexer, reconciler, capacity controller,
and read-only monitoring receive no L1 signer. The withdrawal auto-claimer uses
the dedicated withdrawal-relayer identity and never the provider payout
identity.

## Room configuration and data availability

RoomManager supports unmanaged/managed creation and immutable
data-availability choices:

| Enum value | Contract rule |
|---|---|
| `CALLDATA_REQUIRED` | Canonical batch bytes must be supplied in calldata; blob evidence is rejected. |
| `BLOB_REQUIRED` | A verified blob manifest is mandatory; calldata fallback is not authorized. |
| `BLOB_PREFERRED` | Blob evidence is preferred. One exact calldata fallback is accepted only under the configured authority, deadline, equivalence-program, and signature binding. |

The immutable `DataAvailabilityConfig` tuple is
`(policy, fallbackAuthority, equivalenceProgramId)`. Only `BLOB_PREFERRED` may
have a nonzero fallback authority. The `DataAvailabilityManifest` tuple is:

| Field | Type | Boundary |
|---|---|---|
| `canonicalDataHash` | `bytes32` | Hash of the exact canonical room bytes. |
| `canonicalDataLength` | `uint64` | Nonzero for blob settlement and bounded by encoded blob capacity. |
| `blobStartIndex` | `uint8` | Transaction-wide first blob; zero for single-room or calldata-only settlement. |
| `blobVersionedHashes` | `bytes32[]` | Must bind each transaction blob at the corresponding global index. |
| `commitments` | `bytes[]` | One canonical compressed 48-byte BLS12-381 G1 commitment per blob. |
| `evaluationPoints`, `evaluations` | `bytes32[]` | Point-evaluation inputs, one pair per blob. |
| `kzgProofs` | `bytes[]` | One canonical compressed 48-byte proof per blob. |
| `equivalenceSeal` | `bytes` | Receipt for the configured equivalence program and exact canonical bytes. |
| `fallbackDeadlineBlock` | `uint64` | Exact fallback authorization boundary; not a general grace window. |
| `fallbackSignature` | `bytes` | Signature by the configured fallback authority over the contract-defined statement. |

Configuration facts include `FacetConfigured`, `AggregateProofConfigured`,
`DataAvailabilityConfigured`, and `ColdTemplateDataPublished`. Validate template
and circuit IDs against the consumed trust-root locks before sending. The
negotiated owner capability requires transaction/body hash binding, c-kzg
verification, archive prepublish, beacon fallback, and canonical-chain
retention/retraction behavior. A blob archive object is content-addressed and
retained through a reorg; the canonical requirement is retracted instead of
deleting evidence. The publisher archives before signing/broadcast, persists
nonce and exact signed bytes, and enters `RECOVERY_REQUIRED` instead of
automatically retrying a post-finality surprise.

```sh
cast logs --rpc-url "$L1_RPC_URL" --address "$ROOM_MANAGER" \
  'DataAvailabilityAccepted(uint64,uint64,uint8,bool,bool,bytes32)'
```

## Node, capacity, pricing, escrow, sponsorship, renewal and handoff

The RoomPool lifecycle is ABI-bound:

- node: `registerNode`, `setNodeDelegate`, `reportNodeHeartbeat`,
  `markNodeStale`, `quarantineNode`, `beginNodeDrain`, `retireNode`;
- capacity/template: `registerPreset`, `requestCapacityProfile`,
  `confirmCapacityProfile`, `configureSlot`, cold preparation functions;
- pricing/allocation: `publishPriceEpoch`, `quote`, reservation/start/disposal;
- escrow/metering: running-credit, fee settlement, service/treasury claims;
- sponsorship: `SponsoredEscrowFunded` and the `*For*WithPermit` paths;
- renewal: renewal permit calls and `AllocationRenewed`;
- handoff: RoomManager ownership assignment plus the control-plane `HANDOFF`
  intent, which must share a durable transition key.

The typed reservation request is
`(nodeId, slotId, presetId, deadlineBlocksFromStart, priceEpoch,
maxTokenCharge)`. The token permit is `(value, deadline, v, r, s)`. The
contract rejects a changed/stale epoch, deadline outside the configured slot
bounds, unavailable capacity, or a computed charge above `maxTokenCharge`.
Preserve the exact quote block/hash, request tuple, permit owner/value/deadline,
signature and receipt.

The node removal state machine is ABI-bound and intentionally asymmetric.
`quarantineNode(bytes32)` (`0xa4588ca0`, `MONITOR_ROLE`) is reversible through a
nonzero recovery heartbeat followed by exact capacity-profile confirmation.
`beginNodeDrain(bytes32)` (`0xd7ceb78e`, `POOL_CONTROLLER_ROLE`) clears the
pending profile, increments its nonce, blocks new capacity, emits
`NodeDrainStarted`, and enters `DRAINING` (enum 7) while existing allocations
finish. `retireNode(bytes32)` (`0x13ca0607`, `NODE_ADMIN_ROLE`) admits only
`DRAINING` with `activeAllocations == 0` and `pendingProfileHash == 0`; otherwise
it raises `WrongState` or
`UnsafeNodeRetirement(activeAllocations,pendingProfileHash)`. It emits
`NodeRetired` and enters the irreversible preserved enum value `RETIRED` (6).
Drain and completed retirement retries are idempotent; no heartbeat, capacity,
slot, price, quarantine, stale, or delegation path may recover a draining or
retired node.

Sponsored paths are exact, not wildcard APIs:

| Operation | Beneficiary | Token payer/refund destination | Required authority |
|---|---|---|---|
| `reserveRoomWithPermit(request,permit)` | caller | caller | permit owner |
| `reserveRoomForWithPermit(beneficiary,request,permit)` | explicit beneficiary | sponsor/caller | `SPONSOR_ROLE` |
| `reserveAndStartForWithPermit(beneficiary,request,creation,permit)` | explicit beneficiary | sponsor/caller | `SPONSOR_ROLE` |
| `reserveAndStartForWithDataAvailabilityWithPermit(beneficiary,request,creation,da,permit)` | explicit beneficiary | sponsor/caller | `SPONSOR_ROLE` |
| `requestColdPreparationForWithPermit(beneficiary,...,maxCharge,permit)` | explicit beneficiary | sponsor/caller | `SPONSOR_ROLE` |
| `renewRoomForWithPermit(previousAllocationId,beneficiary,request,permit)` | unchanged room beneficiary | sponsor/caller | `SPONSOR_ROLE` |

The allocation stores `user` separately from `payer`; every unused-token refund
returns to the stored payer. `SponsoredEscrowFunded(payer, beneficiary,
escrowReference, amount)` is the accounting fact. A sponsored caller never
becomes room owner merely by funding escrow.

Renewal requires a canonical finalized checkpoint strictly newer than the
previous allocation's consumed checkpoint. The checkpoint must still match the
open room's batch/state root and a canonical L1 block within the 256-block
`blockhash` window. One checkpoint cannot renew the same allocation twice.
Changing capacity releases/acquires ready slots and emits a new capacity-profile
handoff request; changing nodes creates separate release and acquire hashes.
`AllocationDisposed`, `AllocationUsed`, and `AllocationRenewed` must all be
verified before the control plane treats the handoff as complete.

Never derive a price, expiry, finalized checkpoint, or capacity decision from
an unfinalized single RPC. Preserve permit replay protection, deadline, nonce,
owner, asset, maximum charge and typed signature in evidence.

## Single-room and aggregate proofs

Single-room settlement uses the owner ABI's verified batch structures and
`submitBatch`/data-availability variants. `submitAggregate` accepts
`AggregateSubmission { AggregateMember[] members, bytes aggregateSeal }`; each
member is `{ roomId, BatchSubmission submission, DataAvailabilityManifest
dataAvailability }`, and the member's individual `submission.seal` must be
empty because the aggregate receipt is the only proof on this path.

Protocol-wide aggregate limits and layout invariants are:

- one to `MAX_AGGREGATE_ROOMS = 8` members, each with a distinct nonzero room;
- at most `MAX_BLOBS_PER_BATCH = 6` transaction blobs across all members;
- blob ranges are ordered, transaction-global, contiguous and exhaustive;
- calldata-only members use `blobStartIndex = 0` and consume no blob range;
- every member's journal room ID equals the containing member room ID;
- the aggregate verifier/code hash/program ID and statement hash match the
  deployed trust root.

After the aggregate proof verifies, each member is applied in an isolated call
frame. The **transaction can succeed with member-level partial success**:
successful members advance, failed members remain retryable, and every member
emits `AggregateMemberOutcome(aggregateHash, memberIndex, roomId, batchIndex,
applied, failureSelector)`. Therefore the coordinator must persist the exact
aggregate hash, ordered member list, accepted members, retryable members and
failure selectors. It closes the plan only when every intended member has an
accepted outcome; it never resubmits a member already accepted under the same
plan.

## Admission, forced transactions, imports, finality and reorgs

Admission reservation/commit/lease/ack is a PostgreSQL WAL boundary; on-chain
events `AdmissionRecorded`, `AdmissionReceiptDischarged`, and
`AdmissionOmissionChallenged` connect WAL state to contract state. Forced paths
use `forceTransaction`/`ForcedTransactionQueued`/`ForcedOutcomeRecorded`.
Imported L1 state uses `publishL1StateInput` and `L1StateInputPublished`.
`FinalizedCheckpointRecorded` is a RoomPool finality-oracle fact, while an SSE
status remains retractable until its exact canonical provenance is safe.

For ordinary pre-finality reorgs, the indexer records the corroborated fork,
rolls canonical projections back, retains blob archives, and emits
`statusRetracted`. An observation below the prior finalized anchor is a
post-finality surprise and follows the incident runbook; no automated retry or
manual journal edit is permitted.

## Withdrawals and replay protection

Withdrawal publication/claim paths are ABI-defined by
`WithdrawalRootPublished`, `claimWithdrawal`, and `WithdrawalClaimed`. The exact
claim signature is
`claimWithdrawal(uint64 roomId,uint64 outboxEpoch,Withdrawal withdrawal,bytes32[] proof)`
with `Withdrawal { uint64 index; uint64 approverEpoch; address recipient;
address asset; uint256 amount; }`.

The leaf is:

```text
keccak256(abi.encode(
  deploymentDomain, roomId, outboxEpoch, index, approverEpoch,
  recipient, asset, amount
))
```

The proof is positional. At level `i`, hash `value || proof[i]` when the current
index bit is zero, otherwise hash `proof[i] || value`, then shift the index
right. The contract accepts at most `MAX_WITHDRAWAL_PROOF_DEPTH = 15`, and
`index < MAX_WITHDRAWALS_PER_EPOCH = 32768`. It rejects a zero root/amount,
already-claimed index, bad positional proof, or insufficient claimable
liability. It marks the index claimed and debits claimable liability **before**
the vault release; non-reentrancy and transaction atomicity protect the state.

Read before any send:

```sh
cast call "$ROOM_MANAGER" \
  'isWithdrawalClaimed(uint64,uint64,uint64)(bool)' \
  "$ROOM_ID" "$OUTBOX_EPOCH" "$WITHDRAWAL_INDEX" --rpc-url "$L1_RPC_URL"
cast call "$ROOM_MANAGER" \
  'verifyWithdrawalProof(uint64,uint64,(uint64,uint64,address,address,uint256),bytes32[])(bool)' \
  "$ROOM_ID" "$OUTBOX_EPOCH" \
  "($WITHDRAWAL_INDEX,$APPROVER_EPOCH,$RECIPIENT,$ASSET,$AMOUNT)" \
  "$PROOF" --rpc-url "$L1_RPC_URL"
```

Bind deployment domain, chain, room, epoch/index, approver epoch, recipient,
asset, amount, root and proof to one durable claim record, then verify the
`WithdrawalClaimed` event and `isWithdrawalClaimed` post-state. `BadWithdrawal`,
`BadAccounting`, `UnsupportedAsset`, and `WrongState` are terminal until
canonical state changes. The withdrawal relayer never holds provider payout
authority.

The coordinator owns tenant proof/request/status REST and JSON-RPC. A single
owner `autoClaimer` deployment holds the delegated `withdrawal` component lease
for the active coordinator epoch and uses only the withdrawal-relayer signer.
Its restart path is bound to the durable claim/L1-operation record, persisted
nonce, and exact signed bytes. Before accepting an externally completed claim,
it requires canonical `WithdrawalClaimed` evidence plus an independent
finalized block floor; contradictory post-finality evidence is
`RECOVERY_REQUIRED`, not an automatic resubmission. The same idempotency key
must replay the same tenant request; a changed leaf/root/recipient under that
key is a conflict.

```sh
# Internal operator check; the worker port is never edge-published.
curl -fsS http://auto-claimer:3003/capabilities
curl -fsS http://auto-claimer:3003/ready
```

## Pause, upgrades, events and errors

Pause/unpause, facet configuration, role changes, and UUPS upgrades are
governance mutations. Capture implementation/facet code hashes and verify the
post-transaction ABI/interface before reopening traffic. Every event, function,
and custom error in `generated/abi.md` is extracted from the exact owner
artifact and accompanied by SHA-256; the shorter category mapping is generated
from the owner indexer constants. Release validation must fail when either
source hash changes until examples, schemas and replay tests are refreshed.

The machine event map must include, for each event, the ABI signature/topic,
indexed and data fields, decoder source, destination projection, canonicality
stage, finality gate, retraction behavior and retention class. The concise
category view is useful for dashboards but is not a field-level decoding
contract. Release conformance replays a fixture for every mapped event and
state-changing selector against the exact hash-bound ABI.

## Cross-contract invariants

- **Escrow solvency:** user escrow plus service/treasury claimable balances
  remain covered; refunds return to the stored payer, not implicitly to the
  beneficiary.
- **No duplicate outcome charge:** a member/job is charged only for its one
  accepted canonical outcome; a retryable aggregate failure is not charged as
  success.
- **One-time renewal checkpoint:** renewal consumes a strictly newer finalized
  room checkpoint and persists the consumed batch index on the new allocation.
- **One-time withdrawal index:** a `(roomId, outboxEpoch, index)` is claimed at
  most once, independent of relayer retries.
- **Blob equivalence:** canonical hash/length, EIP-4844 versioned hashes,
  commitment/opening proofs, equivalence receipt and transaction-global indices
  bind the same exact bytes.
- **Finality before irreversibility:** reorgable observations can retract;
  provider payout, invoice closure, withdrawal acceptance and capacity handoff
  use their named finalized canonical facts.
- **Fence before mutation:** a standby or stale worker cannot produce hosted
  mutations or use an L1 signer after its coordinator epoch is fenced.
