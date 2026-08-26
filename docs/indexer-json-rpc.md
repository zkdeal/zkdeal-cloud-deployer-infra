# Indexer and hosted JSON-RPC

The owner image publishes a coordinator plus fenced `indexer`, `reconciler`,
and blob `publisher` workers. Each worker uses the active coordinator's stable
`COORDINATOR_ID`, a unique `HOSTED_WORKER_ID`, and a PostgreSQL component lease
bound to the coordinator epoch. Promotion invalidates old delegations in the
same transaction, so a stale worker cannot continue canonical writes.

The indexer reads from at least two independent L1 RPC identities, persists
block hashes, uses EIP-1898 hash-bound state reads, retains reorg audit facts,
and refuses readiness when its canonical/archive gate is unsafe. The publisher
archives and verifies the exact EIP-4844 bundle before signing or broadcast and
enters `RECOVERY_REQUIRED` on a post-finality surprise.

## Transport, authentication and idempotency

Send requests to `POST /hosting/v1/json-rpc` with `Content-Type:
application/json` and `Accept-Schema-Version: 1`. Responses return
`Content-Schema-Version: 1`. `hosting_capabilities` is the only unauthenticated
compatibility method. Every canonical `zkdeal_*` method and the compatibility
alias `hosting_usage` requires `Authorization: Bearer <principal>`; data is
tenant-scoped unless a hosting administrator calls an explicitly administrative
method.

The capability manifest calls the non-admin method set "public" because it is
the supported external interface, not because it bypasses authentication.
Reads are idempotent. The four mutations below additionally require an
`Idempotency-Key` header of 8-200 characters; replay with the same request is
safe, while reuse for different input is rejected:

- `zkdeal_requestWithdrawalClaim`
- `zkdeal_adminBackfill`
- `zkdeal_adminReconcile`
- `zkdeal_adminRetention`

Decimal identifiers and cursors are strings, never JSON numbers. Page using the
last returned `factId` or `usageId`; do not use array position. The complete
request/error grammar and named result records are in
[`hosting-json-rpc.schema.json`](schemas/hosting-json-rpc.schema.json).

## Canonical and tenant-scoped reads

| Method | Required authority | Params | Result |
|---|---|---|---|
| `zkdeal_getIndexerStatus` | Any valid principal | `{}` | Chain head/anchor/floor, per-source cursors, unresolved safety count, unreconciled room count |
| `zkdeal_getCanonicalBlock` | Any valid principal | `number` decimal string | Canonical block or `-32004` |
| `zkdeal_getCanonicalBlocks`, `zkdeal_getBlocks` | Any valid principal | `fromBlock` decimal, `limit` 1-1000 | Canonical blocks in ascending order |
| `zkdeal_getRoom` | Any valid principal | `roomId` decimal | Tenant-visible observation or `-32004` |
| `zkdeal_getRoomEvents` | Any valid principal | Optional `roomId`, `after`, `limit`, `kinds[]` | Canonical, tenant-filtered fact page |
| `zkdeal_getDeposits` | Any valid principal | Optional `roomId`, `after`, `limit` | Facts of kind `deposit` |
| `zkdeal_getImports` | Any valid principal | Optional `roomId`, `after`, `limit` | Facts of kind `import` |
| `zkdeal_getAdmissions` | Any valid principal | Optional `roomId`, `after`, `limit` | Facts of kind `admission` |
| `zkdeal_getBatches` | Any valid principal | Optional `roomId`, `after`, `limit` | Batch, aggregate, and data-availability facts |
| `zkdeal_getTransaction`, `zkdeal_getTransactions` | Any valid principal | `transactionHash` as 32-byte hex | Canonical logs and tenant-visible facts or `-32004` |
| `zkdeal_getFinalityStatus` | Any valid principal | `{}` | Indexer status, canonical head/floor and `blobArchiveReady` |
| `zkdeal_getReconciliationStatus` | Any valid principal | `roomId` decimal | Durable reconciliation state or `-32004` |

`zkdeal_getRoomEvents` is the JSON-RPC query surface. The independent SSE
surface at `/hosting/v1/events` uses durable IDs, audience filtering, and
`Last-Event-ID` reconnect; see
[`room-deployment-monitoring.md`](room-deployment-monitoring.md).

## Withdrawal, usage and capacity methods

| Method | Required role | Params | Result |
|---|---|---|---|
| `zkdeal_getWithdrawals` | `withdrawal-read` | Optional `roomId`, `status`, `limit` | Tenant-scoped withdrawal records |
| `zkdeal_getWithdrawalProof`, `zkdeal_getProof` | `withdrawal-read` | `roomId`, `epoch`, `withdrawalIndex` | Finalized positional proof or `-32004` |
| `zkdeal_requestWithdrawalClaim` | Tenant `withdrawal-claim` plus idempotency header | `roomId`, `epoch`, `withdrawalIndex` | Durable claim request/replay record |
| `zkdeal_getUsage`, `hosting_usage` | `usage-read` | Optional admin-only `tenantId`, `after`, `limit` | Usage ledger entries |
| `zkdeal_getEntitlements` | Tenant `usage-read` | Optional `limit` | Tenant entitlements; administrators use the scoped REST query |
| `zkdeal_getSponsorships` | Tenant `usage-read` | Optional `limit` | Tenant sponsorships; administrators use the scoped REST query |
| `zkdeal_getCapacity` | Tenant `capacity-manage` | Optional `limit` | Tenant capacity intents |

Payout authority is not granted by any read or claim request. The separate
withdrawal relayer/sponsor identity submits claims; provider-revenue payout
authority remains isolated.

## Administrative mutations

| Method | Required authority | Params | Result |
|---|---|---|---|
| `zkdeal_adminBackfill` | Hosting administrator plus idempotency header | `fromBlock` decimal | Accepted/replayed fenced backfill; cannot rewind at or below finalized archive floor |
| `zkdeal_adminReconcile` | Hosting administrator plus idempotency header | `roomId` decimal | Accepted/replayed durable reconciliation request |
| `zkdeal_adminRetention` | Hosting administrator plus idempotency header | Optional integer days: `transientRetentionDays` minimum 30 (default 30), `auditRetentionDays` minimum 365 (default 365), `resolvedSafetyRetentionDays` minimum 30 (default 30) | Reap counts plus durable audit record |

The retention minimums are the exact owner-store policy floors: transient and
reindexable records keep at least 30 days, audit and billing records keep at
least 365 days, and resolved safety records keep at least 30 days after
resolution. The owner API rejects any shorter window with an error; there is
no documented upper bound beyond safe-integer days.

These methods are blocked at the public front door. Operators call the active
coordinator over the private network; a standby has no signer path until
promotion completes its database fence and freshness gate.

## Errors

JSON-RPC failures use `{jsonrpc,id,error:{code,message,data?}}`:

| Code / HTTP | Meaning |
|---|---|
| `-32600` / 400 | Invalid JSON-RPC envelope |
| `-32601` / 200 | Unknown method |
| `-32602` / 200 | Invalid or missing params, including a required idempotency header |
| `-32003` / 200 | Missing role, wrong tenant/admin scope |
| `-32004` / 200 | Canonical tenant-visible resource not found |
| HTTP 401 | Missing, expired, or revoked principal |
| HTTP 403 | REST/auth boundary denied before dispatch |
| HTTP 429 | Principal request budget exhausted |
| HTTP 503 | PostgreSQL authority, fence, or required archive readiness unavailable |

Treat both a non-2xx HTTP status and a JSON-RPC `error` as failure. Never retry a
mutation without retaining its original idempotency key.

## Executable examples

Read a fact page with curl:

```sh
curl --fail-with-body https://zkdeal.example/hosting/v1/json-rpc \
  -H 'content-type: application/json' \
  -H 'accept-schema-version: 1' \
  -H "authorization: Bearer $ZKDEAL_INDEXER_TOKEN" \
  --data '{"jsonrpc":"2.0","id":"events-1","method":"zkdeal_getRoomEvents","params":{"roomId":"42","after":"0","limit":200,"kinds":["batch","data-availability"]}}'
```

Submit an idempotent claim request:

```sh
curl --fail-with-body https://zkdeal.example/hosting/v1/json-rpc \
  -H 'content-type: application/json' \
  -H 'accept-schema-version: 1' \
  -H "authorization: Bearer $ZKDEAL_WITHDRAWAL_TOKEN" \
  -H 'idempotency-key: claim-room42-epoch7-leaf3' \
  --data '{"jsonrpc":"2.0","id":"claim-1","method":"zkdeal_requestWithdrawalClaim","params":{"roomId":"42","epoch":"7","withdrawalIndex":"3"}}'
```

The equivalent owner CLI talks to the same coordinator-owned REST surface;
the bearer token is read from a mode-restricted file and never passed on the
command line as secret data:

```sh
node dist/hosted-withdrawal-worker.js proof \
  --api https://zkdeal.example \
  --token-file /run/secrets/tenant-withdrawal.token \
  --room 42 --epoch 7 --index 3

node dist/hosted-withdrawal-worker.js claim \
  --api https://zkdeal.example \
  --token-file /run/secrets/tenant-withdrawal.token \
  --room 42 --epoch 7 --index 3 \
  --idempotency-key claim-room42-epoch7-leaf3
```

The proof call requires `withdrawal-read`; the claim call requires tenant
`withdrawal-claim`, returns HTTP 202, and persists the caller's idempotency
key. Poll `GET /hosting/v1/withdrawal-claims/{claimId}` with
`withdrawal-read`. HTTP 401/403 means authentication/scope denial, 404 means
the finalized proof or tenant-visible claim is absent, 409 means a conflicting
idempotency replay or fence conflict, and 503 means the proof materializer or
active writer is unavailable. Do not submit a new key after an ambiguous 5xx;
poll the original claim first.

Only one fenced `autoClaimer` process executes
`src/hosted-withdrawal-worker.ts run` for the active coordinator epoch. Its
delegated component is `withdrawal`; it is not a second tenant API. It requires
the owner coordinator image, PostgreSQL/object-store/shared-L1 environment,
the active `COORDINATOR_ID`, a unique `HOSTED_WORKER_ID`, and the scoped
`WITHDRAWAL_SIGNER_*` identity. Provider payout and blob publisher signer
identities are never accepted as substitutes.

TypeScript response and schema-version check:

```ts
const request = { jsonrpc: '2.0', id: 'finality-1', method: 'zkdeal_getFinalityStatus', params: {} }
const response = await fetch(`${baseUrl}/hosting/v1/json-rpc`, {
  method: 'POST',
  headers: {
    'content-type': 'application/json',
    'accept-schema-version': '1',
    authorization: `Bearer ${indexerToken}`,
  },
  body: JSON.stringify(request),
})
if (response.headers.get('content-schema-version') !== '1') throw new Error('schema negotiation failed')
const body = await response.json()
if (!response.ok || body.error) throw new Error(JSON.stringify(body.error ?? body))
if (!body.result.blobArchiveReady) throw new Error('finality blocked: blob archive incomplete')
```

Cross-check an indexed on-chain event against the owner ABI:

```sh
cast logs --rpc-url "$L1_RPC_URL" --address "$ROOM_MANAGER" \
  'BatchAccepted(uint64,uint64,bytes32,bytes32,uint64,bool)'
```

The generated, ABI-hash-bound event/call category map is
[`event-to-indexer.md`](generated/event-to-indexer.md) with a machine-readable
JSON companion. The deployment project consumes that owner artifact; it does
not duplicate event decoding or protocol business logic.
