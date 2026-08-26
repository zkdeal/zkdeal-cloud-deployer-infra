# Object-store loss

## Contain

Freeze blob publication, settlement, proof finalization, withdrawals, and new
room admission. Keep PostgreSQL and the lost store read-only for evidence. Do
not repoint the owner services to an empty bucket and do not mark missing blobs
as agreed absence.

Confirm the failure from at least two clients and record endpoint, bucket,
versioning/object-lock state, last verified backup ID, PostgreSQL archive
cursor, pending L1 operations, and current coordinator fence.

## Recover to an independent store

Provision a new bucket/account in a failure-independent store with versioning,
object lock, encryption, least-privilege credentials, and an empty prefix. Use
the database/object restore Job described in `database-restore.md`; the source
is the immutable backup store, not the failed primary. Verify every restored
object against the encrypted manifest and inventory before configuring a
standby to read it.

For content published after the last backup, reconcile only from independently
verified L1/beacon data and the owner archive journal. Never manufacture KZG
proof material or substitute a different blob. Any unavailable exact bundle
keeps the affected operation in recovery-required state.

## Resume and evidence

Require two-RPC agreement, beacon fallback, random object/KZG verification,
zero unresolved archive requirements, and owner `/ready` for indexer,
reconciler, publisher, and coordinator. Rotate object credentials before
promotion. Seal object versions, inventory hashes, gap reconciliation, test
reads, RTO/RPO, and the old-store disposition. Resume publisher last.
