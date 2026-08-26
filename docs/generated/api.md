# Owner API reference

Generated from the owner's static OpenAPI artifact plus TypeScript route registration inventory. Exact owner operations and schemas are authoritative and are never replaced by route-scan summaries; source hash changes make this reference stale and fail its container gate.

## Authentication, schema negotiation and errors

Hosted protected routes use `Authorization: Bearer <principal>`. Send `Accept-Schema-Version: 1`; every hosted response returns `Content-Schema-Version`. Error bodies use `{"error":"..."}` with 400 malformed, 401 invalid credential, 403 role/tenant denial, 404 scoped resource missing, 406 unsupported schema, 409 canonical/freshness conflict, 429 bounded rate limit, and 503 unfenced/unconfigured authority.

Capacity intents are idempotent by `(tenant, allocationId, idempotencyKey)`. Usage pages resume from decimal `usageId`. SSE resumes from `Last-Event-ID`. Principal creation is intentionally non-idempotent; retain the returned secret and rotate/revoke that principal. Indexer mutations are fenced and canonical/hash-bound.

## Request and response schemas

The generated OpenAPI document contains concrete schemas for tenant provisioning, principal issuance, capacity intents, canonical blocks, room observations, admission lease/ack, and every owner-published hosted JSON-RPC method. `docs/schemas/hosting-json-rpc.schema.json` is the standalone JSON Schema for JSON-RPC replay tests.

## Route inventory

| Method | Path | Owner source |
|---|---|---|
| `GET` | `/api` | `app-assets.ts` |
| `GET` | `/artifacts/contracts.json` | `app-assets.ts` |
| `GET` | `/artifacts/zkvm/{relative}` | `app-assets.ts` |
| `GET` | `/config` | `app-config-routes.ts` |
| `GET` | `/demo/v1/jobs/{id}` | `demo-routes.ts` |
| `GET` | `/demo/v1/l1/blocks` | `demo-routes.ts` |
| `GET` | `/demo/v1/machine/{kind}/{id}` | `demo-routes.ts` |
| `GET` | `/demo/v1/presets` | `demo-routes.ts` |
| `GET` | `/demo/v1/room-settings` | `demo-routes.ts` |
| `GET` | `/demo/v1/rooms` | `demo-routes.ts` |
| `POST` | `/demo/v1/rooms` | `demo-routes.ts` |
| `GET` | `/demo/v1/rooms/{id}` | `demo-routes.ts` |
| `POST` | `/demo/v1/rooms/{id}/actions` | `demo-routes.ts` |
| `GET` | `/demo/v1/rooms/{id}/checkpoints` | `demo-routes.ts` |
| `POST` | `/demo/v1/rooms/{id}/checkpoints` | `demo-routes.ts` |
| `POST` | `/demo/v1/rooms/{id}/close` | `demo-routes.ts` |
| `POST` | `/demo/v1/rooms/{id}/deploy` | `demo-routes.ts` |
| `GET` | `/demo/v1/stream` | `demo-routes.ts` |
| `GET` | `/demo/v1/system` | `demo-routes.ts` |
| `GET` | `/demo/v1/templates` | `demo-routes.ts` |
| `POST` | `/demo/v1/templates` | `demo-routes.ts` |
| `GET` | `/demo/v1/templates/{id}` | `demo-routes.ts` |
| `POST` | `/faucet` | `app.ts` |
| `GET` | `/health` | `app-config-routes.ts` |
| `GET` | `/health` | `standalone.ts` |
| `POST` | `/hosting/v1/admin/admissions/recover` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/admin/billing/prices` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/admin/indexer/backfill` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/admin/invoices` | `hosting-routes.ts` |
| `PUT` | `/hosting/v1/admin/l1-service-bindings/{principalId}` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/admin/promote` | `hosting-routes.ts` |
| `PUT` | `/hosting/v1/admin/proof-profiles/{proofClass}` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/admin/provider-nodes/{principalId}` | `hosting-routes.ts` |
| `PUT` | `/hosting/v1/admin/provider-nodes/{principalId}` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/admin/provider-nodes/{principalId}/lifecycle` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/admin/reap` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/admin/reconcile` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/admin/refunds` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/admin/safety-events/{eventId}/resolve` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/admin/sla/policies` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/admissions/{roomId}/ack` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/admissions/{roomId}/lease` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/auth/wallet/challenges` | `hosting-routes.ts` |
| `DELETE` | `/hosting/v1/auth/wallet/session` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/auth/wallet/session` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/auth/wallet/sessions` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/billing/ledger` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/capabilities` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/capacity/intents` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/capacity/intents` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/data-availability/publish` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/entitlements` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/entitlements` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/events` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/health` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/indexer/blocks` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/indexer/blocks` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/indexer/events` | `hosting-routes.ts` |
| `PUT` | `/hosting/v1/indexer/rooms/{roomId}` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/indexer/status` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/indexer/transactions/{transactionHash}` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/internal/aggregate-billing-manifests` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/internal/post-finality-recoveries` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/internal/post-finality-recoveries/{recoveryId}/finalize` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/internal/withdrawal-witnesses` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/invoices` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/json-rpc` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/l1-operations/node-heartbeats` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/l1-operations/pool-beneficiary-disposals` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/l1-operations/pool-finalized-checkpoints` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/l1-operations/pool-sponsor-mutations` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/l1-operations/room-aggregates` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/l1-operations/room-batches` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/l1-transactions/{operationId}` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/openapi.json` | `hosting-routes.ts` |
| `DELETE` | `/hosting/v1/principals/{principalId}` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/principals/{principalId}/rotate` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/ready` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/refunds` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/rooms/deployments` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/rooms/{roomId}` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/rooms/{roomId}/proving-context` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/rooms/{roomId}/proving-policy` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/rooms/{roomId}/reconciliation` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/rooms/{roomId}/renewals` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/sponsorships` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/sponsorships` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/tenants` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/tenants/{tenantId}/principals` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/usage` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/withdrawal-claims/{claimId}` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/withdrawals` | `hosting-routes.ts` |
| `POST` | `/hosting/v1/withdrawals/{roomId}/{epoch}/{withdrawalIndex}/claims` | `hosting-routes.ts` |
| `GET` | `/hosting/v1/withdrawals/{roomId}/{epoch}/{withdrawalIndex}/proof` | `hosting-routes.ts` |
| `GET` | `/metrics` | `metrics.ts` |
| `GET` | `/metrics` | `standalone.ts` |
| `PUT` | `/observer/v1/rooms/{id}` | `observer-write.ts` |
| `PATCH` | `/observer/v1/rooms/{id}/freshness` | `observer-write.ts` |
| `POST` | `/queue/v1/jobs` | `postgres-queue-routes.ts` |
| `POST` | `/queue/v1/jobs` | `queue-routes.ts` |
| `GET` | `/queue/v1/jobs/{id}` | `postgres-queue-routes.ts` |
| `GET` | `/queue/v1/jobs/{id}` | `queue-routes.ts` |
| `POST` | `/queue/v1/jobs/{id}/complete` | `postgres-queue-routes.ts` |
| `POST` | `/queue/v1/jobs/{id}/complete` | `queue-routes.ts` |
| `POST` | `/queue/v1/jobs/{id}/fail` | `postgres-queue-routes.ts` |
| `POST` | `/queue/v1/jobs/{id}/fail` | `queue-routes.ts` |
| `POST` | `/queue/v1/jobs/{id}/heartbeat` | `postgres-queue-routes.ts` |
| `POST` | `/queue/v1/jobs/{id}/heartbeat` | `queue-routes.ts` |
| `GET` | `/queue/v1/jobs/{id}/result` | `postgres-queue-routes.ts` |
| `GET` | `/queue/v1/jobs/{id}/result` | `queue-routes.ts` |
| `POST` | `/queue/v1/lease` | `postgres-queue-routes.ts` |
| `POST` | `/queue/v1/lease` | `queue-routes.ts` |
| `GET` | `/queue/v1/metrics` | `queue-routes.ts` |
| `GET` | `/queue/v1/status` | `postgres-queue-routes.ts` |
| `GET` | `/queue/v1/status` | `queue-routes.ts` |
| `GET` | `/rooms/{id}` | `observer.ts` |
| `GET` | `/rooms/{id}/admissions` | `observer.ts` |
| `GET` | `/rooms/{id}/applications` | `observer.ts` |
| `GET` | `/rooms/{id}/approvers` | `observer.ts` |
| `GET` | `/rooms/{id}/batches` | `observer.ts` |
| `GET` | `/rooms/{id}/blocks` | `observer.ts` |
| `GET` | `/rooms/{id}/deposits` | `observer.ts` |
| `GET` | `/rooms/{id}/forced-transactions` | `observer.ts` |
| `GET` | `/rooms/{id}/imports` | `observer.ts` |
| `GET` | `/rooms/{id}/latest` | `observer.ts` |
| `GET` | `/rooms/{id}/machine` | `observer.ts` |
| `POST` | `/rooms/{id}/pending-transactions` | `admission.ts` |
| `GET` | `/rooms/{id}/state` | `observer.ts` |
| `GET` | `/rooms/{id}/stream` | `observer.ts` |
| `GET` | `/rooms/{id}/transactions` | `observer.ts` |
| `POST` | `/rooms/{id}/transactions` | `admission.ts` |
| `GET` | `/rooms/{id}/withdrawals` | `observer.ts` |
| `POST` | `/rpc` | `app-rpc-route.ts` |

## Source hashes

- `web2-api/server/src/admission.ts` — `sha256:1d13d8228afc638e9d3f9a577454a8ba957878e1e486490fb2725f0684bf0424`
- `web2-api/server/src/app-assets.ts` — `sha256:fe25dcab8a3156765cca107cff578acdce5d7ab6dd8acab5bebc1427b2d33e05`
- `web2-api/server/src/app-config-routes.ts` — `sha256:08d966e592f6e05491a94d588c7440f1137c84b5a4a7936485de2309220dd4c8`
- `web2-api/server/src/app-rpc-route.ts` — `sha256:11bcc5d0e1544af31eafa24b95c8ed879605de2e39ef147c6a5ba37ee772c4a5`
- `web2-api/server/src/app.ts` — `sha256:954b36d0162a64c809ed6737ccea1c62db6cd4787dc300e28df3e9899dddf114`
- `web2-api/server/src/demo-routes.ts` — `sha256:8437de6de65e4d272d936cffb843f56c9d89e547d0486b4e145fedf1de16bddf`
- `web2-api/server/src/hosting-routes.ts` — `sha256:efa0883ec74aeddb65bda24f36f372f696d797fa02959b8d625b973b0869520d`
- `web2-api/server/src/metrics.ts` — `sha256:25f9fce8e8954ca3fae8c86d2b857059ccc97bbafb45c1d90e0edc028aa13bbc`
- `web2-api/server/src/observer-write.ts` — `sha256:4d904cd645de7af5cfe35d75440adae4190972c1a7fdb8ada033c6b5e0f76a70`
- `web2-api/server/src/observer.ts` — `sha256:3129625f7c68201017673b3840bc06158b54f9d55f99d28ee7050a71cd182c3c`
- `web2-api/server/src/prove-queue/postgres-queue-routes.ts` — `sha256:4da9561b1da476196779f19dcc4d4d779d9a21c93d1167fa85e1a5c993ec5ab8`
- `web2-api/server/src/prove-queue/queue-routes.ts` — `sha256:5008a5554a79a0ea93a90c7ff8064bc57f56ffeff8f6ff8e6f05d91138e60772`
- `web2-api/server/src/prove-queue/standalone.ts` — `sha256:bde3b920736fe63b2abf4888f1838f2cd1b0c8ce6fdb2e0a071415246a34738f`
