# Backup and restore control

`zkdeal-backup-restore-control` owns the acceptance-only PostgreSQL and MinIO
backup/restore boundary. It never accepts an owner administrator token; every
request requires the dedicated `backup_restore` bearer token,
`Idempotency-Key`, `X-Correlation-Id`, and the exact candidate/plan/hosted-token
binding.

The topology file fixes the source PostgreSQL database and MinIO bucket, a
second separately credentialed backup object store, the AES-256-GCM master key,
and a small allowlist of fresh logical targets. It is SHA-256 bound at startup.
A request cannot provide a host, port, URL, database, bucket, command, SQL,
path, or object body.

Backups never land in the primary data MinIO. The `backupStore` is an
independent object authority whose endpoint and credentials must both differ
from the source; identical configuration fails closed at startup. Every backup
artifact is encrypted with an AES-256-GCM AEAD (`algorithm`, `keyId`, per-artifact
`nonce`, and the GCM tag, recorded in the write-once closure) and bound to its
candidate, backup id, and logical name through additional authenticated data.
The `keyId` is a non-secret HMAC fingerprint of the master key, so a wrong key
is rejected before any fresh target is touched.

A restore downloads each ciphertext from the independent backup store and
authenticates and decrypts it in a private workspace BEFORE any fresh database
or object store is mutated. A tampered artifact fails the ciphertext-digest or
authenticated-decryption check with `BACKUP_ARTIFACT_TAMPERED`; a wrong key
fails the key-id check with `RESTORE_KEY_ID_MISMATCH`. Both are distinct,
fail-closed errors that leave no partial restore side effect.

HTTP contract:

- public `GET /health`, `/ready`, and `/capabilities`;
- `POST /v1/backups` and authenticated `GET /v1/backups/{backupId}`;
- `POST /v1/restores` and authenticated `GET /v1/restores/{restoreId}`.

A restore accepts only a completed candidate-bound `backupId` and an allowlisted
fresh target. It refuses PostgreSQL targets with existing user objects and
MinIO targets with existing objects. The closure returns source/restored
database and object digests, `freshDatabase`, `freshObjectStore`,
`hashesVerified`, and `serviceReady`. PostgreSQL backup material, a canonical
object manifest, and the complete deterministic object bundle are uploaded to
the fixed MinIO backup bucket and downloaded again to verify their hashes. A
restore downloads those remote artifacts afresh; it never restores from a
caller path or an incidental local source copy.

The image runs as UID/GID 65532 and is read-only-root compatible. Mount a
dedicated journal at `/journal`, a tmpfs at `/tmp`, and reviewed topology/token/
database/object credentials as read-only files. The example topology is a
non-release fixture. Release evidence requires two distinct PostgreSQL targets,
two distinct MinIO endpoints, and a digest-pinned adapter image.
