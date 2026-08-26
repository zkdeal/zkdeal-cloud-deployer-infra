# PostgreSQL restore drill

Use this runbook only with a backup created by `scripts/live_backup.sh` and
stored in the failure-independent backup account/bucket. A backup in the same
MinIO instance as production proves the mechanism only; it is not recovery from
object-store loss.

## Preconditions and freeze

1. Freeze public mutations, publisher, auto-claimer, capacity controller, and
   prover leasing at the edge. Record the active coordinator ID and fence epoch.
2. Select a new database/namespace. Never restore over the active database.
3. Resolve the backup object version, retention state, encrypted manifest HMAC,
   source image digests, and `RESTORE_ID`. Keep the master backup key outside
   the evidence/workspace volume.
4. Verify the restore image is an exact `repository@sha256:<64 hex>` reference
   and the target database/object credentials are least privilege.

## Restore and verify

Enable the chart restore Job for the selected `RESTORE_ID`. The Job runs
`/usr/local/bin/live_restore.sh`; it authenticates before decrypting, checks the
manifest/database/object hashes, rejects unsafe archive members, performs
`pg_restore --exit-on-error`, and uploads objects only to the fresh target.

From the pinned deployment-tools container, wait for the Job and capture logs:

```sh
kubectl -n "$RESTORE_NAMESPACE" wait --for=condition=complete \
  --timeout=20m job/zkdeal-restore
kubectl -n "$RESTORE_NAMESPACE" logs job/zkdeal-restore
```

Verify schema migration level, row counts, fenced coordinator state, durable
idempotency/outbox/lease/nonce cursors, object inventory hashes, and a random
sample of content-addressed objects. Start one standby coordinator with no
signer access, then the indexer/reconciler. Do not promote until both RPCs agree,
the indexer is within eight blocks with matching hash, and the promotion gate
proves standby replay reached the durable primary target LSN.

Abort on any HMAC/hash mismatch, unsafe archive member, missing object, schema
error, stale replay LSN, conflicting fence, or post-finality surprise. Preserve
the failed Job and logs; do not retry with a different backup under the same
evidence ID.

## Closure

Seal the backup object version/retention, encrypted manifest hash, restore Job
image ID/digest, target namespace/database identity, row/object verification,
promotion response and RTO. Resume traffic gradually and confirm no duplicate
nonce, charge, claim, outbox event, or proof result.
