# Canonical deadline risk

Trigger this runbook for `ZkdealCapacityDeadlineRisk` or the future hosted-queue
deadline alert. Freeze non-urgent admissions first; never accelerate one tenant
by editing another tenant's priority.

Verify the urgent fact is an allocation-matched canonical `AllocationUsed` or
`AllocationRenewed` record, still canonical at lease time, and derived with the
measured block interval, audited proof duration and settlement margin. Caller
hints are not global authority. Record fact provenance, latest safe start,
queue position, available proof class/capacity, provider operation and current
fence.

Use the existing durable capacity intent and its immutable provider
idempotency key to add/reserve capacity. Do not bypass per-tenant EDF, reuse a
GPU lease, lower proof evidence, or suppress the alert. If the safe start is
already missed, stop automatic settlement and expose the terminal state rather
than promising completion.

Resume after the canonical fact is rechecked, audited capacity is ready,
deadline slack is positive for every admitted job, and fairness/deadline
budgets pass. Seal the fact/hash, timing inputs, provider result, affected job
IDs, and decision outcome without using those IDs as metric labels.
