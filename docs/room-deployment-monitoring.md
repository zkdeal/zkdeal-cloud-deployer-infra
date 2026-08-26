# Room deployment and monitoring

Room allocation is a two-authority workflow: the tenant control plane records
an idempotent capacity intent, and RoomPool/RoomManager contracts make the
canonical allocation and room transitions. The indexer observes both contracts
through two agreeing RPC providers and writes only through an epoch-bound
delegation to the active coordinator.

Release status: the coordinator APIs described here are real owner interfaces,
and the current headless room-node capability declares automatic admission
leasing, the remote PostgreSQL/MinIO queue, a content-hash-bound current-zkVM
`BatchInputV5` bridge, authenticated external proving, restart/resume, and
owner-durable `RoomManager.submitBatch`. The joint live HTTP path has passed,
but its earlier acceptance token was superseded by subsequent sender-source
hardening. Deployment therefore remains fail-closed until the broad owner
source freezes and a replacement evidence manifest hashes every current source
artifact. The endpoint examples below are the implemented control-plane
contracts; they are not a substitute for that replacement release seal.

## Prepare tenant authority

Use a bearer principal with `capacity-manage` for intents and `usage-read` for
metering. A room node uses a separate `node` principal with `room-operator`.
Every request sends `Accept-Schema-Version: 1`; a 406 response means the client
must stop and negotiate a supported schema.

```sh
curl --fail-with-body https://zkdeal.example/hosting/v1/capacity/intents \
  -H "authorization: Bearer $ZKDEAL_CAPACITY_TOKEN" \
  -H 'idempotency-key: reserve:alloc-20260821-001:v1' \
  -H 'accept-schema-version: 1' -H 'content-type: application/json' \
  --data '{"allocationId":"alloc-20260821-001","roomId":"42","desiredState":"RESERVED","providerNodeId":null,"deadlineAt":"2026-08-21T12:00:00Z","idempotencyKey":"reserve:alloc-20260821-001:v1","metadata":{"source":"operator"}}'
```

The tuple `(tenant, allocationId, idempotencyKey)` is durable. Retry the exact
same body with the same key after a timeout. A changed desired state requires a
new key and should carry `previousIdempotencyKey`. Valid states are `RESERVED`,
`ACTIVE`, `RENEW`, `HANDOFF`, and `RELEASED`; accepted intents return HTTP 202.

## Fenced capacity execution

The tenant request does not call a cloud API directly. Exactly one owner
`capacityController` worker holds the delegated `capacity` component lease for
the active coordinator epoch. It reads the durable intent and canonical queue
demand, then calls the private `zkdeal-capacity-provider-v1` interface with a
scoped bearer token and a complete immutable `Idempotency-Key`. The provider
OpenAPI source is
`web2-api/server/capabilities/capacity-provider-v1.openapi.json`; deployment
configuration consumes that owner artifact and does not reimplement the
provider protocol.

```sh
# Run only from the internal operator/monitoring network.
curl -fsS http://capacity-controller:3004/capabilities
curl -fsS http://capacity-controller:3004/ready
curl -fsS http://capacity-controller:3004/metrics
```

HTTP 401/403 from the provider is a scoped token or rotation error. A provider
409 is an idempotency conflict and must not be retried with a changed body. A
429/503 remains in the PostgreSQL retry/backoff journal; the worker reuses the
same method, body, and key. `terminal` requires operator repair,
`deadlineRisk` freezes non-urgent admissions, and a stale audited proof profile
forbids scale-down. Caller-provided deadlines are tenant-local hints; only the
canonical allocation-matched deadline may affect global urgency.

## Contract quote and transaction

Use original ABI artifacts from `web3-protocol/contracts/out`; do not copy
selectors into deployment code. A read-only quote is idempotent:

```sh
cast call "$ROOM_POOL" 'quote(bytes32,bytes32,uint64,uint64)' \
  "$NODE_ID" "$SLOT_ID" "$DEADLINE_BLOCKS" "$PRICE_EPOCH" \
  --rpc-url "$L1_RPC_URL"
```

TypeScript:

```ts
const quote = await publicClient.readContract({
  address: roomPool,
  abi: roomPoolAbi,
  functionName: 'quote',
  args: [nodeId, slotId, deadlineBlocks, priceEpoch],
})
```

For state changes, persist calldata hash, account, nonce, and returned
transaction hash before waiting. `BadInput`, `CapacityUnavailable`,
`DeadlineOutOfRange`, `StalePrice`, `Unauthorized`, and `WrongState` require
operator correction; a missing receipt is resolved by the concrete sender's
same-calldata settlement watcher, never by constructing a different request.

## Funding, entitlement and sponsorship paths

Choose exactly one funding path and retain its canonical identity:

1. **Public Web3 payer.** The user signs the access-token permit and calls
   `reserveRoomWithPermit` or `reserveAndStartWithDataAvailabilityWithPermit`.
2. **On-chain sponsor.** An address with `SPONSOR_ROLE` calls
   `reserveRoomForWithPermit` or a `reserveAndStartFor*` variant. The allocation
   beneficiary remains the user, while unused escrow returns to the stored
   sponsor/payer; verify `SponsoredEscrowFunded`.
3. **External entitlement.** A hosting administrator records an effective,
   expiring entitlement with an immutable idempotency key. This is off-chain
   authorization and does not manufacture an on-chain allocation.
4. **Off-chain sponsorship.** A tenant administrator records a bounded
   sponsor→beneficiary allowance. This does not grant an on-chain role or move
   access tokens by itself.

```sh
curl --fail-with-body https://zkdeal.example/hosting/v1/entitlements \
  -H "authorization: Bearer $ZKDEAL_HOSTING_ADMIN" \
  -H 'idempotency-key: entitlement:enterprise-order-884:v1' \
  -H 'accept-schema-version: 1' -H 'content-type: application/json' \
  --data '{"entitlementId":"enterprise-order-884","tenantId":"acme","allocationId":"alloc-20260821-001","unit":"proof-second","quantity":"7200","startsAt":"2026-08-21T12:00:00Z","expiresAt":"2026-09-21T12:00:00Z","metadata":{"source":"signed-order"}}'

curl --fail-with-body https://zkdeal.example/hosting/v1/sponsorships \
  -H "authorization: Bearer $ZKDEAL_SPONSOR_TENANT_ADMIN" \
  -H 'idempotency-key: sponsorship:acme-to-beneficiary:001' \
  -H 'accept-schema-version: 1' -H 'content-type: application/json' \
  --data '{"sponsorshipId":"sponsor-001","beneficiaryTenantId":"beneficiary","allocationId":"alloc-20260821-001","maximumQuantity":"7200","unit":"proof-second","expiresAt":"2026-09-21T12:00:00Z","metadata":{"payer":"acme"}}'
```

The same key with the same body replays the stored result; a changed body is a
409 conflict. HTTP 401/403 is an authority failure, 404 is an unknown scoped
resource, 429 is the principal budget, and 503 means the fenced PostgreSQL
writer is unavailable. Preserve the external order/allowance hash and the
on-chain allocation ID together; neither is a substitute for the other.

## Create the room with an immutable DA policy

The contract `ReservationRequest` contains `nodeId`, `slotId`, `presetId`,
`deadlineBlocksFromStart`, `priceEpoch`, and `maxTokenCharge`. The permit
contains `value`, `deadline`, `v`, `r`, and `s`. A TypeScript transaction using
the owner ABI keeps the nested structs typed:

```ts
const hash = await walletClient.writeContract({
  address: roomPool,
  abi: roomPoolAbi,
  functionName: 'reserveAndStartWithDataAvailabilityWithPermit',
  args: [
    { nodeId, slotId, presetId, deadlineBlocksFromStart, priceEpoch, maxTokenCharge },
    roomCreation,
    { policy: 1, fallbackAuthority: zeroAddress, equivalenceProgramId }, // BLOB_REQUIRED
    permit,
  ],
})
const receipt = await publicClient.waitForTransactionReceipt({ hash })
if (receipt.status !== 'success') throw new Error('room creation reverted')
```

The DA enum is immutable for the room: `0 = CALLDATA_REQUIRED`, `1 =
BLOB_REQUIRED`, `2 = BLOB_PREFERRED`. Only `BLOB_PREFERRED` may configure a
fallback authority. Verify `AllocationReserved`, `AllocationUsed`, the room
creation event, and `DataAvailabilityConfigured` from a canonical receipt. A
room node is then attached by deploying its one-room config with that positive
decimal `roomId`; `/ready` must confirm the expected chain and RoomManager
before work is leased.

## Admissions, imports and forced transactions

The active fenced coordinator exposes PostgreSQL WAL admission lease/ack
operations to a scoped node principal. The headless hosted client binds these
leases to its persisted construction plan and acknowledges only the durable
contiguous prefix. The following direct calls are useful for protocol diagnosis
but must not race a running room node. Production remains blocked on the
replacement source-bound joint acceptance token, not on a permission to use a
fixture or local queue:

```sh
curl --fail-with-body -X POST \
  "http://coordinator:3000/hosting/v1/admissions/$ROOM_ID/lease" \
  -H "authorization: Bearer $ZKDEAL_ROOM_NODE_TOKEN" \
  -H 'accept-schema-version: 1' -H 'content-type: application/json' \
  --data '{"limit":64,"leaseMs":30000}'

curl --fail-with-body -X POST \
  "http://coordinator:3000/hosting/v1/admissions/$ROOM_ID/ack" \
  -H "authorization: Bearer $ZKDEAL_ROOM_NODE_TOKEN" \
  -H 'accept-schema-version: 1' -H 'content-type: application/json' \
  --data '{"admissionIds":["101","102"]}'
```

Ambiguous lease/ack responses are resolved from the WAL and node state; do not
invent IDs. Imported state enters through the contract's
`publishL1StateInput`; forced transactions use `forceTransaction`. Preserve the
source block hash/header/proof or exact raw transaction/deadline and wait for
`L1StateInputPublished` or `ForcedTransactionQueued`. Inclusion in an SSE or
indexer observation is provisional until the exact canonical block reaches the
configured finality floor.

## Prove, settle and handle partial aggregates

The required production path submits one durable proof job ID to the hosted
PostgreSQL queue, and the agent leases it only while its one physical GPU
prover is healthy. A restart must reuse the job ID, request hash, proof
trust-root digest, and result identity. The current hosted capability publishes
this path and explicitly forbids fixture preparation and hosted legacy
Groth16. Do not infer release acceptance from the standalone local-artifact
`prove` command: require the replacement joint token and the exact candidate
images. Never submit a second job merely because a lease expired.

For calldata settlement, the operations sender submits the exact owner ABI
calldata. For blob settlement, the `l1-publish` service posts the exact
calldata/blob bundle to `/hosting/v1/data-availability/publish` with one
`Idempotency-Key`, then polls `/hosting/v1/l1-transactions/{operationId}`. The
fenced publisher archives and verifies before signing/broadcast and replays the
same signed bytes after a crash.

An aggregate transaction can succeed while individual members fail current L1
validation. Treat `AggregateMemberOutcome.applied=true` as success for only
that member; failed members remain retryable. Persist the aggregate hash,
ordered member indices, job IDs, accepted outcomes, failure selectors, usage
and charge reservations. Close the plan only when every intended member has an
accepted canonical outcome, without recharging or resubmitting an accepted
member.

## Renewal, handoff and withdrawal

Renewal requires a RoomPool finalized checkpoint newer than the checkpoint
already consumed by the allocation. Record the capacity intent with a fresh
idempotency key, then execute `renewRoomWithPermit` or the sponsor-authorized
`renewRoomForWithPermit` using the exact quoted price epoch:

```sh
curl --fail-with-body -X POST \
  "https://zkdeal.example/hosting/v1/rooms/$ROOM_ID/renewals" \
  -H "authorization: Bearer $ZKDEAL_CAPACITY_TOKEN" \
  -H 'idempotency-key: renewal:room42:checkpoint17:v1' \
  -H 'accept-schema-version: 1' -H 'content-type: application/json' \
  --data '{"allocationId":"alloc-20260821-002","providerNodeId":"provider-a","deadlineAt":"2026-08-21T15:00:00Z","metadata":{"previousAllocationId":"alloc-20260821-001","checkpointBatchIndex":"17"}}'
```

Changing slot or node enters the explicit capacity-profile handoff; do not
route new work until both release/acquire facts and
`CapacityProfileConfirmed` are canonical. Verify `AllocationDisposed`,
`AllocationUsed`, and `AllocationRenewed` together.

After a finalized `WithdrawalRootPublished`, retrieve the positional proof and
either claim manually with the owner ABI or opt into the fenced auto-claimer:

```sh
curl --fail-with-body \
  "https://zkdeal.example/hosting/v1/withdrawals/$ROOM_ID/$OUTBOX_EPOCH/$WITHDRAWAL_INDEX/proof" \
  -H "authorization: Bearer $ZKDEAL_WITHDRAWAL_TOKEN" -H 'accept-schema-version: 1'
curl --fail-with-body -X POST \
  "https://zkdeal.example/hosting/v1/withdrawals/$ROOM_ID/$OUTBOX_EPOCH/$WITHDRAWAL_INDEX/claims" \
  -H "authorization: Bearer $ZKDEAL_WITHDRAWAL_TOKEN" \
  -H 'idempotency-key: claim:room42:epoch7:index3' -H 'accept-schema-version: 1'
```

Poll `/hosting/v1/withdrawal-claims/{claimId}` and verify canonical
`WithdrawalClaimed` plus `isWithdrawalClaimed` before treating a sponsored-gas
claim as complete. A changed leaf/root/recipient under the same key is a 409;
an ambiguous send keeps the original claim and L1-operation identity.

## Monitor and resume

SSE requires a scoped bearer principal. It is an ordered, durable outbox but not
an L1-finality oracle. Persist each numeric event ID only after processing, then
reconnect with `Last-Event-ID`:

```sh
curl -N --fail-with-body https://zkdeal.example/hosting/v1/events \
  -H "authorization: Bearer $ZKDEAL_ROOM_TOKEN" \
  -H "last-event-id: ${LAST_EVENT_ID:-0}" \
  -H 'accept: text/event-stream'
```

The edge disables buffering, compression and caching for this route. A
`refetch` event means the cursor predates retained data; `service-error` requires
a full authenticated refetch. `statusRetracted` reverses provisional state after
a canonical reorg. HTTP 401/403 are credential/scope errors, 429 is the bounded
principal rate limit, and 503 means SSE capacity or active authority is
unavailable. Retry 503 with bounded exponential backoff and the same cursor.

Before declaring a room active, require the allocation event, matching indexed
room head, owner worker readiness, L1 quorum health, proof capacity, and no
deadline-risk alert. Blob-dependent rooms additionally require the negotiated
prepublish archive/finality gate and the fenced publisher `/ready` surface. A
post-finality surprise moves the operation to `RECOVERY_REQUIRED` and blocks
automatic rebroadcast.

Use the named views rather than an opaque local file projection:

| Question | Authoritative query |
|---|---|
| Room head/lifecycle | `GET /hosting/v1/rooms/{roomId}` or `zkdeal_getRoom` |
| Canonical event history | `zkdeal_getRoomEvents` / durable SSE cursor |
| Reconciliation safety | `GET /hosting/v1/rooms/{roomId}/reconciliation` |
| Capacity intent/provider status | `GET /hosting/v1/capacity/intents` and internal capacity-worker readiness |
| Queue/proof state | Durable queue job ID plus owner queue status/result surface |
| L1 publish/receipt state | `GET /hosting/v1/l1-transactions/{operationId}` |
| Finality/archive gate | `zkdeal_getFinalityStatus` |
| Usage, invoice, refund | `zkdeal_getUsage`, `zkdeal_getInvoices`, `zkdeal_getRefunds` |

`included` means observed in a hash-bound canonical block, not final.
`provisional` may retract. `statusRetracted` means the prior fact lost canonical
provenance. `final-settlement` requires the named finality/archive gate and a
matching canonical receipt block/hash. Queue position and deadline slack must
come from owner-published typed fields/metrics; if the current negotiated schema
does not provide them, admission remains closed rather than inferring them from
timestamps or tenant documents.
