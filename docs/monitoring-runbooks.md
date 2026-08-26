# Monitoring catalog and runbooks

This guide is the operational map for `observability/metric-catalog.json`, the
Prometheus rules, and the Grafana dashboards. Metrics are internal-only:
Kubernetes permits ingress only from the labeled monitoring namespace/pod, and
Compose exposes no metric port publicly. Scrapes are read-only and idempotent.
An HTTP 404 means a wrong path; connection refusal or an absent series means a
missing process/scrape; malformed text is a release-blocking owner regression.

The catalog distinguishes `owner-published`, `standalone-only`, and
`required-owner-contract`. A required-contract alert intentionally stays firing
until the owner publishes a real low-cardinality metric. Do not silence it by
creating a deployment-side synthetic business metric.

Example checks (run from an authorized monitoring container):

```sh
curl -fsS http://coordinator:3000/metrics
curl -fsS http://indexer:3001/metrics
curl -fsS http://reconciler:3001/metrics
curl -fsS http://publisher:3002/metrics
curl -fsS http://auto-claimer:3003/metrics
curl -fsS http://capacity-controller:3004/metrics
promtool check rules /etc/prometheus/alerts.yml
```

Dashboards cover L1/finality, indexer/reconciler, admission WAL, rooms,
proving/queue, tenant/billing, storage, sponsorship, blobs, withdrawals, capacity,
partial aggregates, and transport/SSE. No dashboard variable or metric label
may carry tenant, room, job, principal, or transaction identifiers.

## Service endpoint matrix

| Service | Port | Liveness | Readiness | Metrics | Capabilities | Exposure |
|---|---:|---|---|---|---|---|
| Coordinator | 3000 | `/hosting/v1/health` | `/hosting/v1/ready` | `/metrics` | `/hosting/v1/capabilities` | Public health/capabilities through the edge; mutation routes authenticated |
| Indexer | 3001 | `/health` | `/ready` | `/metrics` | `/capabilities` | Internal monitoring and operator networks only |
| Reconciler | 3001 | `/health` | `/ready` | `/metrics` | `/capabilities` | Internal monitoring and operator networks only |
| Blob publisher | 3002 | `/health` | `/ready` | `/metrics` | `/capabilities` | Internal; scoped blob signer and L1/object-store egress only |
| Withdrawal auto-claimer | 3003 | `/health` | `/ready` | `/metrics` | `/capabilities` | Internal; scoped withdrawal signer and L1/object-store egress only |
| Capacity controller | 3004 | `/health` | `/ready` | `/metrics` | `/capabilities` | Internal; scoped capacity-provider bearer token and explicit provider egress only |
| Headless room node | 3100 | `/health` | `/ready` | `/metrics` | `/capabilities` | Internal; its control API is private and file-secret authenticated |

Liveness says only that the process can answer. Readiness is the traffic gate:
workers must hold a delegation bound to the active coordinator epoch and have a
fresh successful pass. A 404 means the deployment command or port is wrong;
401/403 on a private control route is an authorization failure; 503 requires
the operator to preserve the fence and repair the named dependency.

## ZkdealCoordinatorMetricsAbsent

Critical, boolean. Freeze mutations at the front door, confirm the active
coordinator pod and `/hosting/v1/health`, then inspect PostgreSQL/object-store
connectivity and the writer lease. Promote only through the automated
promotion controller, or through the separately rendered one-shot recovery Job
when an incident explicitly enables it.

## ZkdealHostedIndexerAbsent

Critical, boolean. Confirm the published indexer command and negotiated capability response,
then verify its `COORDINATOR_ID` matches the active coordinator and its
`HOSTED_WORKER_ID` is unique. A stale worker must not be manually unfenced.

## ZkdealHostedReconcilerAbsent

Critical, boolean. Apply the same delegation checks as the indexer. Keep room
state publication paused until the reconciler is current and ready.

## ZkdealHostedPublisherAbsent

Critical, boolean. Keep blob-dependent settlement closed. Confirm the dedicated
publisher command, its delegated worker lease and active `COORDINATOR_ID`, and
the scoped blob-publisher signer boundary. Never fall back to a coordinator or
payout signer.

## ZkdealBlobPublisherStale

Critical, seconds. Check two-RPC agreement, object archive access, durable nonce
journal state, signer reachability, and the current coordinator epoch. Restart
only after confirming the archived exact transaction bytes are available for
replay.

## ZkdealBlobPublisherErrors

Warning, errors. Inspect bounded retry/backoff/deadline state. A retry must use
the persisted nonce and exact archived transaction bytes; do not synthesize a
replacement payload.

## ZkdealPostFinalitySurprise

Critical, events. Freeze related publication and settlement immediately. The
publisher reports `RECOVERY_REQUIRED`; follow the post-finality surprise
runbook and do not auto-rebroadcast or rewrite the canonical audit record.

## ZkdealWithdrawalAutoClaimerAbsent

Critical, boolean. Stop automatic claim admission and confirm that exactly one
`autoClaimer` pod is configured for the active coordinator epoch. Check its
delegated `withdrawal` component lease, stable `HOSTED_WORKER_ID`, scoped
withdrawal-relayer signer, and `/health`. Do not start a second withdrawal
worker: proof, request, and status APIs remain in the coordinator.

## ZkdealWithdrawalAutoClaimerStale

Critical, seconds. Inspect `/ready`, two-provider canonical/finality status,
object-store witness access, PostgreSQL lease/nonce rows, signer reachability,
and the active `COORDINATOR_ID`. A restart must reuse the persisted nonce and
exact signed bytes; never create a replacement claim transaction merely to
recover freshness.

## ZkdealWithdrawalClaimErrors

Warning, operations. Inspect the bounded `status="errors"` counter and the
durable claim row before retrying. Confirm the finalized canonical withdrawal
root, locally reconstructed leaf/proof, calldata preflight, inclusion window,
and L1 RPC agreement. A repeated client request must use the same idempotency
key; a worker retry must remain bound to the existing L1 operation.

## ZkdealWithdrawalRecoveryRequired

Critical, operations. Freeze automatic claims for the affected root and retain
all signed bytes, receipts, independent finality observations, and lease
history. Resume only after the canonical `WithdrawalClaimed` event and the
independent block floor agree. Never overwrite a finalized audit result or
infer an external claim solely from one RPC response.

## ZkdealCapacityControllerAbsent

Critical, boolean, after two minutes. Freeze new capacity intents and verify
that exactly one `capacityController` pod is enabled for the active coordinator
epoch. Confirm its delegated `capacity` lease, unique `HOSTED_WORKER_ID`,
`CAPACITY_CONTROLLER_ENABLED=1`, private provider URL, and scoped bearer-token
secret. Never start a second controller to clear the alert.

## ZkdealCapacityControllerStale

Critical, seconds, when the last successful pass is older than 60 seconds for
two minutes. Check `/ready`, the active coordinator fence, PostgreSQL provider
operation rows, queue-demand facts, and provider health. A restart may resume
only the existing immutable idempotency keys; do not synthesize replacement
scale operations.

## ZkdealCapacityOperationErrors

Warning, operations, on any `failures` or `terminal` result in ten minutes.
Inspect the durable provider operation and its bounded retry state. A terminal
operation requires operator correction; a retry must preserve the original
method, body, and idempotency key. Token rotation may use the explicitly
configured previous token only for the bounded overlap window.

## ZkdealCapacityDeadlineRisk

Critical, operations, on any `deadlineRisk` result in five minutes. Stop
non-urgent admissions, verify the deadline came from a canonical
allocation-matched fact, and compare current queue demand with the audited
proof profile and provider capacity. Caller hints must never create global
priority. Escalate or reserve capacity through the existing durable intent;
do not edit the canonical deadline or bypass tenant fairness.
Continue with `/runbooks/deadline-risk.md` and seal its required provenance.

## ZkdealRoomNodeAbsent

Critical, boolean. Stop new work for the affected room. Confirm the dedicated
room-node pod/image and `/health`, then check that the monitoring selector can
reach port 3100. Do not substitute a generic conformance or multi-room process.

## ZkdealRoomNodeNotReady

Critical, boolean. Inspect `/ready` for connection, room attachment, L1
finality, `restoredUnsigned`, and persistence error fields. Verify the state
volume checksum and exclusive lock, run the owner `restore` command if prior
blocks exist, and compare sealed block hashes before admitting traffic.

## ZkdealRoomNodeControlErrors

Warning, operations. Group only by the bounded `operation` label. Inspect the
corresponding private control response and persisted `/v1/state`; retry only
after determining whether the original join/checkpoint/finalize/claim was
accepted. Issuing a new transaction without that check risks a duplicate
deposit or claim.

## ZkdealRoomNodeFinalityUnavailable

Critical, boolean. Stop checkpoint, finalize, and claim operations. Check the
coordinator's two-provider agreement and canonical/archive readiness; never
override room-node readiness or treat a provisional state as finalized.

## ZkdealHostedWorkerStale

Critical, seconds. Check two-provider RPC agreement, the active coordinator
epoch, PostgreSQL lease rows, and `/ready`. Restart only one Recreate worker at
a time; a promotion requires rolling both workers onto the new active
`COORDINATOR_ID` after the old epoch is fenced.

## ZkdealSseDrops

Warning, events. Inspect subscriber count, proxy idle timeout, client resume via
`Last-Event-ID`, and outbox retention. Clients must refetch on `refetch` or
`service-error`; never treat a disconnected stream as proof of finality.

## ZkdealProverErrorRatio

Warning, ratio. Break down the bounded `route` and `outcome` labels, verify GPU
health and the proof trust-root digest, and stop admissions whose latest-start
budget no longer covers proof plus settlement margin.

## ZkdealL1FinalityMetricContractMissing

Critical, boolean. The owner must publish quorum health, reorg depth, finality
undercuts, post-finality surprises, and deadline-risk metrics. Until then, the
L1/finality dashboard is intentionally incomplete and production alert closure
is blocked. Follow the post-finality surprise runbook for any contradictory L1
observation.
Use `/runbooks/pre-finality-reorg.md` for ordinary forks and
`/runbooks/post-finality-surprise.md` for contradictions below the finalized
anchor.

## ZkdealIndexerMetricContractMissing

Critical, blocks. Require canonical lag and retraction counters from the owner.
Do not infer them by scraping tenant/room documents, which would create
unbounded cardinality and cross-tenant exposure.

## ZkdealAdmissionWalMetricContractMissing

Critical, records. Require global unacknowledged/recovered WAL aggregates. Until
published, use the authenticated admin recovery endpoint only during a bounded
rehearsal with an `Idempotency-Key`.

## ZkdealRoomMetricContractMissing

Critical, rooms. Require bounded lifecycle-state and deadline histograms. Room
IDs must never be labels. Freeze new room admission when deadline safety cannot
be measured.

## ZkdealHostedQueueMetricContractMissing

Critical, jobs. The standalone file queue metrics are not evidence for the
hosted PostgreSQL queue. Production queue alerting remains blocked until the
owner publishes hosted waiting/lease/fairness/deadline metrics.
Until then, `/runbooks/hosted-queue-stall.md` is the fail-closed recovery path.

## ZkdealTenantBillingMetricContractMissing

Critical, seconds. Require bounded tier/unit aggregates for usage-ledger lag and
reconciliation failures. Never export tenant IDs. Pause billing closure if the
ledger cannot be reconciled to idempotency keys.

## ZkdealStorageMetricContractMissing

Critical, seconds. Require PostgreSQL replay lag, object archive failures, and
backup success time. Retain the live primary+standby and fresh-database restore
drills as independent evidence.
Use `/runbooks/database-restore.md` or `/runbooks/object-store-loss.md` and
restore into a fresh namespace rather than overwriting the active authority.

## ZkdealSponsorshipMetricContractMissing

Critical, wei. Require bounded sponsorship class/reason aggregates. Do not place
sponsor addresses in labels. Disable new sponsorship when balance or denial
signals are unavailable.

## ZkdealBlobMetricContractMissing

Critical, blobs. The owner now publishes the fenced archive-before-broadcast
worker and low-cardinality process/retry/finality counters, but not the required
pending/archive-verification contract. Blob-dependent release closure remains
blocked until those remaining business-state metrics are published.

## ZkdealAggregateMetricContractMissing

Critical, aggregates. Require bounded proof-class/reason aggregates before
partial aggregate operations are admitted in production.

## ZkdealQueueDown

Critical, boolean, local rehearsal only. Restart the standalone queue, validate
its filesystem mount, then verify queued IDs/results survived. It is not a
hosted PostgreSQL queue signal.

## ZkdealQueueStalled

Critical, seconds, local rehearsal only. Inspect agent leases and the prover;
never delete a durable request to clear the gauge.

## ZkdealQueueBacklog

Warning, jobs, local rehearsal only. Reduce admission or add an accepted prover
only after checking the physical GPU exclusivity and trust-root gate.

## ZkdealQueueLeaseExpiryBurst

Warning, leases, local rehearsal only. Check agent restart loops, lease TTL, and
result idempotency. Re-submission must preserve the durable job ID.

## Acceptance-only signal

`ZkdealAcceptanceSignal` exists only in the isolated observability acceptance
stack. Its live fire/recover webhook result proves Prometheus → Alertmanager →
webhook plumbing; it is never loaded by production Prometheus.
