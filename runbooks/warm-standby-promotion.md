# Warm-standby promotion

Promotion is a two-boundary operation: PostgreSQL must first become the sole
writable primary, then the owner coordinator may acquire the transactional
writer fence. The coordinator's own schema/L1/indexer checks do not prove that
physical WAL replay reached the last acknowledged primary commit, so the Helm
promotion controls independently enforce that boundary.

Production also runs the deployment-owned `promotion-controller` as a
single, Recreate-strategy process. It does not receive a database URL, signer
credential, Kubernetes API token, or Docker socket. After at least three
consecutive rounds in which *both* independently operated HTTPS witnesses fail,
it requires a signerless standby health response and calls the separately
authenticated provider contract in
`promotion-controller/failover-provider-v1.openapi.json`. The selected
deployment-owned Docker/Compose or Kubernetes/Helm provider adapter must
prove former-writer fencing/termination, provider-captured durable target LSN,
standby replay at or beyond the target, physical database promotion, canonical
indexer freshness, and stable database routing while signer authority is still
off. The controller then calls the owner promotion with its own idempotency key
and finally asks the provider to commit the stable application route and
post-fence signer boundary. Any missing/false field stops the sequence.

The included controller fixture proves protocol and negative paths and remains
test-only. Separately, the first-party Docker and Kubernetes provider live
drills exercise synchronous PostgreSQL replication, witness veto, active
termination, provider-captured LSN, physical promotion, stable database and
application route changes, signer activation only after commit, exact replay,
and durable provider restart. A release must repeat the appropriate live drill
with the exact candidate provider image and target-platform resource names;
prior local evidence does not substitute for that run. Cloud-vendor APIs are
extension points behind the same provider contract, not a reason to give the
credential-free controller platform authority. The one-shot Job below remains
a reviewed manual recovery tool; it is not evidence that automatic failover
ran. Routine release values keep `operations.promotion.manualJobEnabled=false`,
so installing or upgrading the chart cannot accidentally launch the one-shot
Job or race the controller.

## Required inputs

- A digest-pinned promotion-controller image and a unique incident candidate
  ID. Candidate reuse is permitted only as an exact provider/owner replay.
- Two independent HTTPS active-health witness URLs. One healthy/fresh witness
  vetoes automation.
- Separate scoped provider bearer, incident approval, and owner promotion
  principal secrets. The provider and approval credentials must differ.
- The digest-pinned first-party Docker or Kubernetes failover-provider v1
  adapter, its fixed scoped resource configuration, and explicit
  witness/provider egress CIDRs. The adapter alone receives the narrow platform
  authority needed to fence, promote, and repoint infrastructure; the
  controller cannot.
- For an explicitly rendered manual recovery Job only, a scoped database URL
  for the already-promoted standby, stored under
  `operations.promotion.databaseUrlSecretKey`.
- For that manual Job, a new 8–200 character `Idempotency-Key`, stored under
  `operations.promotion.idempotencyKeySecretKey`. Never reuse it.
- For that manual Job, a scoped hosting administrator token stored under
  `operations.promotion.principalTokenSecretKey`.
- For that manual Job, the hexadecimal flush LSN captured from the fenced
  primary, supplied as
  `operations.promotion.requiredReplayLsn`.
- For that manual Job, explicit database CIDRs on TCP 5432 in
  `networkPolicy.operationalFlows.promotion`; the Job otherwise remains under
  default-deny egress.

## Ordered procedure

For the automated path:

1. Create a new incident candidate and arm the controller through the reviewed
   release values. Confirm its two witness network identities and three secret
   refs are distinct.
2. Observe a healthy-witness veto in normal operation. Do not simulate health
   loss by weakening the witness contract.
3. On threshold, retain the controller state/evidence for provider prepare,
   owner promotion and provider commit. Confirm the exact event order and an
   elapsed RTO at or below the configured objective.
4. Verify the old application route is removed, the stable writer route points
   to the promoted coordinator, former database/writer credentials are denied,
   and signer authority was activated only by the post-fence commit.
5. Reseed the former primary as a fresh replica. Never rejoin its old data
   directory.

For manual recovery with the one-shot Job:

1. Create an incident-scoped values file that sets
   `operations.promotion.manualJobEnabled=true`. Render this file separately;
   never enable the manual Job in routine install/upgrade values and never run
   it concurrently with the automated controller.
2. Stop admission and fence the active application writer. Confirm no process
   can renew the old coordinator lease or write directly to the old primary.
3. While the old primary is still readable, confirm the designated standby is
   synchronously streaming. Capture `SELECT pg_current_wal_flush_lsn();` only
   after the writer fence and record it in the incident/evidence record.
4. Remove the old primary from every writer endpoint. Promote the physical
   standby and retain `pg_last_wal_replay_lsn()`; never rejoin the old primary
   without destroying/reseeding its data directory.
5. Set the captured LSN and a newly generated idempotency key in the
   incident-scoped
   inputs. Render the chart and inspect the Promotion Job before applying it.
6. Apply only the reviewed, incident-scoped Job resources. Run the Job once.
   Its independent preflight requires
   `pg_is_in_recovery() = false`, compares the last replay LSN against the
   operator-recorded fenced-primary LSN, requires the owner runtime to still
   report standby/standby, reserves the operation in the durable owner
   idempotency journal, and only then POSTs the promotion with the exact
   `Idempotency-Key` header. The request body is `{}`: the caller never supplies
   an LSN to the owner service.
7. Independently require the owner capability to advertise its own durable
   former-primary WAL checkpoint, `pg_last_wal_replay_lsn` comparison, and an
   atomic replay-check/fence transfer. Require the response to include the
   server-read target/replay evidence, active role, matched canonical indexer
   head, monotonic transactional fencing, and the eight-block freshness gate.
   Verify old coordinators and delegated workers remain fenced before reopening
   admission.
8. Remove the incident-scoped Job and restore
   `operations.promotion.manualJobEnabled=false` before the next routine chart
   operation.

## Failure rules

- `still-in-recovery`, `missing-replay-lsn`, or `stale` blocks promotion.
- Missing secrets block Pod startup; malformed or absent values block the
  script before any request.
- A duplicate/replayed key loses the journal insert and is rejected before the
  owner endpoint. An owner-call failure records `RECOVERY_REQUIRED`; do not
  retry with a different key until the database and coordinator state have been
  audited.
- A preflight owner status other than configured `standby` and effective
  `standby` is a replay or split-brain signal. Stop and investigate.
- The whole Job is bounded to at most 300 seconds. Exceeding that budget is an
  RTO failure, not permission to bypass the gates.

Evidence must include the fenced-primary LSN, promoted database replay LSN,
one-time key hash (not the bearer token or database URL), owner capability and
response hashes, old-writer denial, elapsed RTO, and the fresh-replica reseed
result.
For automation also include both witness identities and failure rounds,
provider operation/candidate IDs, the hash-bound owner response, provider
prepare/commit response hashes, stable-route target, post-fence signer result,
controller image digest, and the controller state-file hash. Never record any
bearer or approval secret.
