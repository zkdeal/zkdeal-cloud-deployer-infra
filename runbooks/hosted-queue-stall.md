# Hosted PostgreSQL queue stall

The standalone file-queue process and its metrics are not production recovery
tools. This runbook applies only to the PostgreSQL hosted queue owned by the
active coordinator fence.

## Contain and diagnose

Freeze new non-urgent job admission while preserving already accepted durable
job IDs. Record the coordinator epoch, queue cursor, lease holders/expiry,
oldest waiting age, deadline class, active GPU resource IDs, and provider
assignments. Check PostgreSQL health/WAL replay, object-store reads, prover-agent
readiness, one-GPU exclusivity, and two-RPC finality.

Do not delete rows, shorten another worker's live lease, rewrite priority or
deadline, or start a second worker with the same identity. Caller deadline
hints remain tenant-local; global urgency must come from canonical
allocation-matched facts.

## Recover

Let expired leases return through the owner transaction. Restart at most one
fenced worker at a time with the same active `COORDINATOR_ID` and a new unique
pod identity. Re-enable capacity operations only through the existing durable
intent and provider idempotency key. A promotion uses the gated standby path;
never operate two writers to clear backlog.

Resume when wait age and deadline slack are within budget, every active lease
maps to one live worker/GPU resource, sealed results re-verify, and duplicate
nonce/charge/result counts are zero. Seal queue/lease cursors, fault cause,
recovered job IDs and fairness/deadline evidence.
