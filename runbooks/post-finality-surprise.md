# Post-finality surprise: fail-closed response

A post-finality surprise is any later observation that contradicts a settlement
already returned by the watcher as finalized. This is outside the ordinary
identical-calldata retry path and must be treated as a safety incident, not an
automatic reorg.

The concrete production sender is the fenced
`src/hosted-publisher-worker.ts` process. It submits the `publish-blob`
operation through `BlobPublisher` only after the exact transaction body and
KZG-bound bundle are archived. Do not claim coverage for any other sender or
operation unless it appears in the current owner capability manifest and live
acceptance record.

## Immediate containment

1. Block new batch/admission/capacity mutations at the front door. Keep health,
   metrics, evidence retrieval, and tenant-scoped reads available.
2. Do not rebroadcast, promote, reconcile, reap, or overwrite canonical room
   observations. Preserve the active writer epoch and every worker lease.
3. Capture both independently identified RPC responses for the finalized tag,
   receipt, transaction calldata, inclusion block, and current canonical block.
4. Seal coordinator/indexer/reconciler logs, outbox cursor bounds, PostgreSQL
   lease/journal rows, and object versions into the immutable evidence bucket.
5. Page protocol/security operators and the RPC providers. Record exact sender
   (`hosted-publisher-worker` / `publish-blob`), operation ID, transaction hash,
   exact signed-byte hash, blob bundle hash, block number/hash, finalized
   checkpoint, first contradictory observation, and correlation ID.

## Decision gate

- If providers disagree, remain frozen and quarantine the disagreeing endpoint;
  never choose a majority from fewer than two independent agreeing identities.
- If the transaction calldata differs, treat it as key/transport compromise and
  revoke the operations-settlement signer role before any recovery.
- If two independent providers agree the finalized placement disappeared,
  require an explicit protocol incident decision. The normal watcher retry
  budget is exhausted once finality was reported.
- Database promotion is unrelated and forbidden unless the active database is
  also lost and the normal fencing/freshness promotion gates independently pass.

## Recovery and closure

Recovery requires an owner-approved reconciliation command, two-provider
canonical agreement, an indexer head within eight blocks with matching hash,
zero unresolved safety events, and a replay from the last uncontested finalized
anchor. Run the full reorg/finality/deadline acceptance matrix, verify tenant SSE
receives the appropriate retraction/refetch sequence, then publish a signed/WORM
closure. Never manually edit the journal or mark an event resolved only to clear
an alert.
