# Provider node lifecycle

The control plane can issue node principals and record provider assignments,
while the RoomPool contracts own registration, delegation, heartbeat, stale,
quarantine, and fee authority. The owner now publishes a separate, pruned
`zkdeal-headless-room-node` image and capability manifest. Its exact entrypoint
is `node dist/cli.js run`; health, readiness, metrics, and
capabilities are `/health`, `/ready`, `/metrics`, and `/capabilities` on port
3100. Conformance stubs remain test-only and must never receive traffic.

Current deployment status is deliberately fail-closed. The owner publishes
`managedL1Operations.roomBatch.hostedIntegration`, but keeps it disabled while
the replacement source-bound joint seal waits for the broad owner freeze. The
prior joint live path passed and its token was explicitly superseded after
sender hardening changed a bound source artifact. Production requires the real app engine to
current zkVM `BatchInputV5` bridge, durable PostgreSQL queue, authenticated
external prover, restart/resume, and `RoomManager.submitBatch`; fixture witness
preparation and hosted legacy Groth16 checkpoints are forbidden. Helm and
production Compose reject promotion until all exact booleans are green and the
manifest carries the `sha256:<64 lowercase hex>` token of the canonical joint
acceptance evidence.

## Authority and credentials

- A hosting administrator provisions a `node` principal with the allowed
  `prove-node` and/or `room-operator` roles.
- Only a hosting administrator may create provider-node principals or call the
  internal provider assignment endpoint.
- The on-chain node admin/owner signs registration and delegate changes. The
  room-node reads its burner and EdDSA material only from an operator-mounted
  regular file with mode 0600; the Helm init container copies a Kubernetes
  Secret into an in-memory volume with that exact mode. No private key appears
  in an environment variable or durable checkpoint.
- The current prover agent reports on-chain heartbeat only while the local
  prover is healthy. It never holds a signer or L1 RPC. It leases proof work
  from the hosted coordinator using `QUEUE_URL` and a prove-agent token, then
  asks the same coordinator to durably publish heartbeat operations using
  `NODE_LIVENESS_COORDINATOR_URL`, a service principal carrying only
  `l1-liveness`, `NODE_LIVENESS_ACCOUNT`, `ROOM_POOL`, and `L1_CHAIN_ID`.
  The coordinator binds the principal to the exact node/account, owns the
  nonce, remote Web3Signer, archive, broadcast, canonical receipt, and finality
  watch. `NODE_SERVICE_KEY`, `NODE_LIVENESS_SIGNER_*`, development private-key
  inputs, and direct L1 RPC are forbidden in the production agent. Payout
  authority is reserved for provider revenue and is never given to the agent
  or headless room-node.
- Bearer credentials are shown once, returned with `Cache-Control: no-store`,
  and rotated with at most 24 hours overlap. Revocation is immediate.

Provision a node principal from an internal admin session:

```sh
curl --fail-with-body http://coordinator:3000/hosting/v1/tenants/acme/principals \
  -H "authorization: Bearer $ZKDEAL_HOSTING_ADMIN" \
  -H 'accept-schema-version: 1' -H 'content-type: application/json' \
  --data '{"kind":"node","roles":["prove-node","room-operator"],"limits":{"maxConcurrentJobs":1}}'
```

Repeated provisioning is not idempotent: retain the returned `principalId`
and secret, and rotate that principal rather than calling create again. Rotation
of the same principal is fenced and bounded; revocation is safe to retry and
returns whether the credential was revoked.

Register the physical provider and split its on-chain identities. `nodeId`,
`metadataHash`, and all accounts are operator-controlled immutable evidence;
the heartbeat timeout must be at least 50 L1 blocks.

```sh
cast send "$ROOM_POOL" \
  'registerNode(bytes32,address,address,bytes32,uint64)' \
  "$NODE_ID" "$SERVICE_ACCOUNT" "$BOUND_ACCOUNT" "$METADATA_HASH" 64 \
  --rpc-url "$L1_RPC_URL" --account zkdeal-node-admin
cast send "$ROOM_POOL" \
  'configureNodeAuthorities(bytes32,address,address,address)' \
  "$NODE_ID" "$LIVENESS_ACCOUNT" "$OPERATIONS_ACCOUNT" "$PAYOUT_ACCOUNT" \
  --rpc-url "$L1_RPC_URL" --account zkdeal-node-admin
cast call "$ROOM_POOL" 'nodeState(bytes32)' "$NODE_ID" --rpc-url "$L1_RPC_URL"
```

Verify `NodeRegistered` and `NodeAuthoritiesConfigured` in the canonical
receipt. The liveness, operations, and payout accounts must be nonzero and
pairwise distinct in production.

## Proof classes, GPU assignment and capacity

The hosting administrator first records an evidence-backed proof profile, then
binds one node principal to one physical GPU resource. These internal mutations
are PostgreSQL-fenced but not idempotent-keyed; read the provider assignment
before retrying an ambiguous response and submit the identical body only when
the desired row is absent.

```sh
curl --fail-with-body -X PUT \
  "http://coordinator:3000/hosting/v1/admin/proof-profiles/risc0-groth16" \
  -H "authorization: Bearer $ZKDEAL_HOSTING_ADMIN" \
  -H 'accept-schema-version: 1' -H 'content-type: application/json' \
  --data '{"endpoint":"/prove/groth16","needsGpu":true,"estimatedWork":"risc0-groth16-v1","estimatedProofTimeMs":120000,"settlementMarginMs":45000,"verifiedAt":"2026-08-21T12:00:00Z","evidence":{"artifactLock":"sha256:<64-lowercase-hex>","acceptance":"gpu-smoke"}}'

curl --fail-with-body -X PUT \
  "http://coordinator:3000/hosting/v1/admin/provider-nodes/$PRINCIPAL_ID" \
  -H "authorization: Bearer $ZKDEAL_HOSTING_ADMIN" \
  -H 'accept-schema-version: 1' -H 'content-type: application/json' \
  --data '{"providerId":"provider-a","active":true,"gpu":true,"gpuResourceId":"host4090:gpu0","partitions":["reserved","dedicated"],"tenantIds":["acme"],"allocationIds":[],"proofClasses":["risc0-groth16"],"maxConcurrentJobs":1,"leaseTtlMs":60000}'
```

`partitions` is restricted to `shared`, `reserved`, and `dedicated`;
`maxConcurrentJobs` is 1-64 and `leaseTtlMs` is 60,000-3,600,000 ms. A physical
GPU resource ID must not be assigned to competing provider identities.

Configure the on-chain preset, slot, capacity profile, and effective-dated
prices with their distinct roles:

```sh
cast send "$ROOM_POOL" 'registerPreset(bytes32,bytes32,bytes32)' \
  "$PRESET_ID" "$COLD_TEMPLATE_ID" "$POLICY_HASH" \
  --rpc-url "$L1_RPC_URL" --account zkdeal-template-admin
cast send "$ROOM_POOL" \
  'configureSlot(bytes32,bytes32,bytes32,uint64,uint64,uint64,uint32)' \
  "$NODE_ID" "$SLOT_ID" "$PRESET_ID" 120 7200 120 1 \
  --rpc-url "$L1_RPC_URL" --account zkdeal-node-admin
cast send "$ROOM_POOL" 'requestCapacityProfile(bytes32,bytes32)' \
  "$NODE_ID" "$PROFILE_HASH" --rpc-url "$L1_RPC_URL" --account zkdeal-pool-controller
cast send "$ROOM_POOL" 'confirmCapacityProfile(bytes32,bytes32,bytes32[],uint32[])' \
  "$NODE_ID" "$PROFILE_HASH" "[$SLOT_ID]" '[1]' \
  --rpc-url "$L1_RPC_URL" --account zkdeal-pool-controller
cast send "$ROOM_POOL" \
  'publishPriceEpoch(bytes32,bytes32,uint64,uint128,uint128,uint128,uint128)' \
  "$NODE_ID" "$SLOT_ID" "$PRICE_VALID_UNTIL_BLOCK" \
  "$ACCESS_PRICE" "$COLD_PREP_PRICE" "$DEADLINE_PRICE" "$RUNNING_PRICE" \
  --rpc-url "$L1_RPC_URL" --account zkdeal-operations
```

Require `CapacityProfileRequested`, `CapacityProfileConfirmed`,
`NodeStatusChanged(READY)`, and `PriceEpochPublished` before admission. Read
`nodeState`, `slotState`, and `quote` at an explicit block hash; never accept a
caller-authored proof profile or capacity hash.

Start the GPU prover and pull agent only with digest-addressed images. The
release preflight rejects a mutable tag. Neither process receives a signer;
the coordinator alone owns the scoped liveness signer boundary.

```sh
case "$PROVER_IMAGE" in *@sha256:????????????????????????????????????????????????????????????????) ;; *) exit 64;; esac
docker run --rm --gpus '"device=0"' --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --network zkdeal-internal --env-file /run/secrets/prover.env "$PROVER_IMAGE" \
  serve --host 0.0.0.0 --port 8080

case "$AGENT_IMAGE" in *@sha256:????????????????????????????????????????????????????????????????) ;; *) exit 64;; esac
docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --network zkdeal-internal --env-file /run/zkdeal/agent.env "$AGENT_IMAGE"
```

The deployment-owned agent image compiles and packages the owner source at
`prover-node/agent/src`; its content labels bind that source tree and
`liveness-capability.json`. It starts `node /app/agent/agent.js` as numeric
non-root `10001:10001`. The coordinator image is not a valid substitute.

The agent environment includes queue/prover endpoints and tokens, `NODE_ID`,
`ZKDEAL_AGENT_GPU=1`, `ROOM_POOL`, `L1_CHAIN_ID`, `NODE_LIVENESS_ACCOUNT`, and
the scoped `NODE_LIVENESS_COORDINATOR_*` values. `QUEUE_URL` and
`NODE_LIVENESS_COORDINATOR_URL` must name the same active-writer coordinator.
The agent leases through `POST /queue/v1/lease`, heartbeats/completes/fails the
same durable job, and publishes via
`POST /hosting/v1/l1-operations/node-heartbeats`; retries reuse the exact
idempotency and correlation IDs and poll
`GET /hosting/v1/l1-transactions/{operationId}`. It must report no more than one
concurrent GPU lease and must stop on local prover health failure. The agent
has no inbound HTTP server. Kubernetes therefore uses an exec readiness probe
that checks coordinator readiness, authenticated heartbeat capability, and
prover `/healthz`; the chart publishes no fictitious agent Service or port.

## Deploy and control one room node

Each deployment is bound to one positive decimal room ID and one state volume;
replicas must remain exactly one. Mount a non-secret config at
`/run/zkdeal/room-node.json`, secrets at
`/run/zkdeal-secrets/{keys.json,control.token}`, SHA-256-bound proving artifacts
under `/opt/zkdeal/artifacts`, and durable state at
`/var/lib/zkdeal-room-node`. A representative config is:

This describes the current standalone owner process. It must not be interpreted
as proof that the process consumes the hosted coordinator's admission lease or
durable proving queue; those integrations are the release blocker stated
above.

```json
{
  "schemaVersion": 1,
  "coordinatorUrl": "http://zkdeal-coordinator-active:3000",
  "roomId": "42",
  "expectedChainId": 1,
  "expectedRoomManager": "0x1111111111111111111111111111111111111111",
  "keyFile": "/run/zkdeal-secrets/keys.json",
  "controlTokenFile": "/run/zkdeal-secrets/control.token",
  "stateDir": "/var/lib/zkdeal-room-node",
  "host": "0.0.0.0",
  "port": 3100,
  "restoreOnStart": true,
  "l1RefreshMs": 4000,
  "busPollMs": 400,
  "artifacts": {
    "wasmPath": "/opt/zkdeal/artifacts/settle.wasm",
    "zkeyPath": "/opt/zkdeal/artifacts/settle.zkey",
    "wasmSha256": "<64-lowercase-hex>",
    "zkeySha256": "<64-lowercase-hex>"
  }
}
```

The local CLI reads the scoped control token from its file. The control calls
are not blindly idempotent: inspect `/v1/state` after an ambiguous response and
bind restart evidence to its canonical `stateSha256`,
`hostedLifecycleSha256`, and `exclusiveStateLockHeld` fields; then
resume the persisted lifecycle instead of issuing a second join/checkpoint.

```sh
node dist/cli.js status --config /run/zkdeal/room-node.json
node dist/cli.js join --deposit-wei 1000000000000000 --config /run/zkdeal/room-node.json
node dist/cli.js checkpoint --config /run/zkdeal/room-node.json
node dist/cli.js restore --config /run/zkdeal/room-node.json
node dist/cli.js finalize --config /run/zkdeal/room-node.json
node dist/cli.js claim --config /run/zkdeal/room-node.json
```

Equivalent REST control (private network only):

```sh
curl --fail-with-body http://headless-node:3100/v1/checkpoint \
  -H "authorization: Bearer $(cat /run/zkdeal-secrets/control.token)" \
  -H 'content-type: application/json' --data '{}'
```

The control body limit is 16 KiB. Missing/wrong control tokens return 401,
unknown routes return 404, and lifecycle conflicts, proof failures, corrupt
state, or a mismatched room return 409. `/ready` returns 503 until connected,
attached to the configured room, L1 finality is available, any prior blocks
have been restored, and state persistence is healthy.

## On-chain lifecycle

Read before write. Production heartbeat publication is owner-durable: the
client supplies one idempotency key and correlation ID per logical heartbeat,
while the coordinator owns the transaction nonce and exact signed bytes. Never
blindly create a second operation after an ambiguous response.

```sh
cast call "$ROOM_POOL" 'nodes(bytes32)' "$NODE_ID" --rpc-url "$L1_RPC_URL"
curl --fail-with-body -X POST \
  http://coordinator:3000/hosting/v1/l1-operations/node-heartbeats \
  -H "authorization: Bearer $NODE_LIVENESS_COORDINATOR_AUTH_TOKEN" \
  -H 'accept-schema-version: 1' \
  -H "idempotency-key: heartbeat:$NODE_ID:$PROFILE_EPOCH" \
  -H "x-correlation-id: heartbeat:$NODE_ID:$PROFILE_EPOCH" \
  -H 'content-type: application/json' \
  --data "{\"schemaVersion\":1,\"chainId\":$L1_CHAIN_ID,\"poolAddress\":\"$ROOM_POOL\",\"expectedLivenessAccount\":\"$NODE_LIVENESS_ACCOUNT\",\"nodeId\":\"$NODE_ID\",\"profileHash\":\"$CAPACITY_HASH\",\"confirmationPolicy\":{\"minimumConfirmations\":64}}"
```

Equivalent TypeScript read with the owner ABI:

```ts
import { createPublicClient, http } from 'viem'
import { roomPoolAbi } from '@zkdeal/room-client'
const client = createPublicClient({ transport: http(process.env.L1_RPC_URL!) })
const node = await client.readContract({ address: roomPool, abi: roomPoolAbi, functionName: 'nodes', args: [nodeId] })
```

Contract transitions are surfaced by `NodeRegistered`,
`NodeAuthoritiesConfigured`, and `NodeStatusChanged`. The indexer categorizes
them as `node`; contract custom errors such as `Unauthorized`, `BadInput`,
`WrongState`, and `CapacityUnavailable` are terminal until operator input
changes. HTTP 400 means malformed role/kind, 401 invalid credential, 403 wrong
tenant/authority, 404 unknown principal, 429 rate limit, 503 unfenced owner
authority. Do not retry 4xx automatically; retry 503 only with the same intended
principal/transaction identity after readiness is restored.

The supported status progression is explicit: registration creates
`REGISTERED`; slot/profile changes enter `REBALANCING`; a matching capacity
confirmation enters `READY`; a permissionless expired-heartbeat observation
enters `OFFLINE`; quarantine enters `DEGRADED`; a valid heartbeat from offline
or degraded returns to `REBALANCING`, after which capacity must be confirmed
again. `beginNodeDrain` moves any non-retired node irreversibly to `DRAINING`
(enum value 7); `retireNode` is its only terminal transition and moves a fully
drained node to `RETIRED` (the preserved enum value 6). A draining node can
finish or hand off existing allocations but cannot accept new reservations,
publish a price, configure a slot, request/confirm a new capacity profile, be
quarantined, or be marked stale. A retired node cannot heartbeat, delegate,
publish, or re-enter capacity. Recovery applies to `DEGRADED` and `OFFLINE`,
never to `DRAINING` or `RETIRED`.

## Drain, quarantine and replacement

Quarantine is immediate, reversible containment. `quarantineNode` (selector
`0xa4588ca0`) requires `MONITOR_ROLE`, moves the node to `DEGRADED`, and is
idempotent while already degraded. It rejects draining or retired nodes.
`markNodeStale` is permissionless but succeeds only after the configured
heartbeat timeout and moves an eligible node to `OFFLINE`.

```sh
cast send "$ROOM_POOL" 'quarantineNode(bytes32)' "$NODE_ID" \
  --rpc-url "$L1_RPC_URL" --account zkdeal-monitor
cast call "$ROOM_POOL" 'nodeState(bytes32)' "$NODE_ID" --rpc-url "$L1_RPC_URL"
```

Recover a quarantined or offline node only after the physical fault is fixed.
The scoped agent asks the owner to report a nonzero, freshly measured profile
hash. That heartbeat moves the node to `REBALANCING`, increments the profile
nonce, and emits `CapacityProfileRequested`; admission remains closed. The pool
controller must confirm that exact hash and current nonce/slot capacities to
return the node to `READY`. A zero recovery hash is `BadInput`, and an old
confirmation cannot reopen capacity.

```sh
# Reuse the owner-durable heartbeat call above with
# profileHash=$RECOVERY_PROFILE_HASH and a new logical idempotency key.
cast send "$ROOM_POOL" 'confirmCapacityProfile(bytes32,bytes32,bytes32[],uint32[])' \
  "$NODE_ID" "$RECOVERY_PROFILE_HASH" "[$SLOT_ID]" '[1]' \
  --rpc-url "$L1_RPC_URL" --account zkdeal-pool-controller
```

Drain is planned, one-way removal. First set the hosted provider assignment
`active:false` so the PostgreSQL scheduler stops offering work, then call
`beginNodeDrain(bytes32)` (selector `0xd7ceb78e`) as `POOL_CONTROLLER_ROLE`.
The call clears `pendingProfileHash`, increments `profileNonce` to invalidate
every prepared confirmation, records the current `activeAllocations`, emits
`NodeDrainStarted`, and enters `DRAINING`. Repeating it while draining is an
idempotent no-op. Existing reservations may start and finish; no new reservation
may be created.

```sh
cast send "$ROOM_POOL" 'beginNodeDrain(bytes32)' "$NODE_ID" \
  --rpc-url "$L1_RPC_URL" --account zkdeal-pool-controller
cast logs --rpc-url "$L1_RPC_URL" --address "$ROOM_POOL" \
  'NodeDrainStarted(bytes32,uint64,bytes32,uint64)'
```

Wait until every leased job is durably sealed or handed off, every allocation
is disposed, `activeAllocations == 0`, and `pendingProfileHash == 0`. Then the
node administrator calls `retireNode(bytes32)` (selector `0x13ca0607`). Calling
it before `DRAINING` is `WrongState`; calling it with live allocations or a
confirmable profile raises
`UnsafeNodeRetirement(activeAllocations,pendingProfileHash)`. Successful
retirement emits `NodeRetired` and `NodeStatusChanged`; repeating a completed
retirement is an idempotent no-op.

```sh
cast call "$ROOM_POOL" 'nodeState(bytes32)' "$NODE_ID" --rpc-url "$L1_RPC_URL"
cast send "$ROOM_POOL" 'retireNode(bytes32)' "$NODE_ID" \
  --rpc-url "$L1_RPC_URL" --account zkdeal-node-admin
cast logs --rpc-url "$L1_RPC_URL" --address "$ROOM_POOL" \
  'NodeRetired(bytes32,uint64)'
```

The owner OpenAPI now publishes idempotent, fenced admin routes
`POST /hosting/v1/admin/provider-nodes/{principalId}/drain`,
`POST /hosting/v1/admin/provider-nodes/{principalId}/retire`, and
`GET /hosting/v1/admin/provider-nodes/{principalId}/lifecycle`. Drain disables
server-side eligibility immediately but remains `VERIFYING` until the exact
transaction is finalized and indexed as `NodeDrainStarted`; retirement requires
the prior drain idempotency key and finalized fact. After retirement revoke the
hosted node principal and coordinator service-principal binding. A replacement
gets a new node ID, principal, worker identity, and physical GPU resource
identity; never clone the retired secret or worker ID.

On restart, the node takes an exclusive state lock, verifies the atomic state
checksum and room ID, and requires a verified coordinator snapshot restore when
prior blocks exist. A checksum mismatch, stale lock owner, artifact digest
mismatch, or `restoreOnStart=false` with prior blocks is fail-closed; repair or
restore the volume before admitting traffic. Verify
`zkdeal_room_node_ready == 1`, unchanged sealed block hashes, and an incremented
`zkdeal_room_node_restores_total` before returning it to service.
