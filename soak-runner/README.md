# First-party release-soak orchestrator

`/opt/zkdeal-soak` is the deployment-owned restart/resume and evidence-closure
boundary for the 12-hour physical soak. It invokes `scripts/soak.py` against a
hash-bound manifest and a source-bound `/opt/zkdeal-owner-soak` driver, then
independently validates the append-only journal, usage/charges, durable IDs,
fault recovery, sealed outputs, fairness/deadlines, nonce/charge uniqueness,
and unresolved-safety/claim zero closure.

The default `runner` image target intentionally does not contain a synthetic
owner driver. The `candidate` Docker build target is the machine-enforced path
for adding the broad-sealed owner implementation: it copies
`/opt/zkdeal-owner-soak` from the exact digest-pinned owner image named by
`OWNER_SOAK_DRIVER_IMAGE`, fails closed unless the copied bytes match
`OWNER_SOAK_DRIVER_SOURCE_SHA256`, and binds that hash in the
`org.zkdeal.owner-soak-driver.source.sha256` image label. The runner then
re-checks the same hash against `OWNER_SOAK_DRIVER_SOURCE_SHA256` and
`OWNER_SOAK_DRIVER_IMAGE_LABEL_SHA256` at execution time and requires a
hash-bound JSON argv file. Until that owner driver exists and the physical
12-hour run passes, the soak image is runnable only far enough to fail closed
and is not release evidence.

All mutable journal/state/output paths must live under a durable
`SOAK_EVIDENCE_DIR`. A resumed run reuses the exact manifest and state; a
changed manifest or existing closure is rejected rather than overwritten.
