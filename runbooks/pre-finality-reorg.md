# Ordinary pre-finality reorg

This path applies only above the last independently agreed finalized anchor. A
contradiction at or below that anchor is a post-finality surprise and must use
`post-finality-surprise.md`.

## Detect and contain

Require disagreement or a parent/hash mismatch from two independently operated
RPC identities. Pause affected room publication, settlement, admissions and
claims; preserve the active coordinator fence. Record old/new heads, fork
point, finalized anchor, provider identities, object versions, outbox cursor,
and impacted operation IDs.

## Reconcile

Allow the owner indexer to roll back only projections above the finalized
floor, retain append-only reorg/audit facts, and re-fetch by block hash. The
reconciler retracts provisional status and the tenant SSE stream emits
`statusRetracted`; clients resume from the same `Last-Event-ID` and refetch when
instructed. Blob archives remain retained even when their canonical requirement
is retracted.

Do not delete canonical history manually, lower the finalized floor, rewrite an
invoice/charge, or rebroadcast a finalized transaction. Retry only an existing
durable operation with its original idempotency key/nonce/exact bytes.

## Resume

Resume after both RPCs agree, the new head is within the freshness budget, all
reconciliation rows are terminal, affected SSE retractions were observed, and
no post-finality surprise exists. Seal fork facts, rollback/replay cursors,
retraction IDs, before/after state hashes, and the recovery duration.
