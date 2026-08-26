# First-party owner acceptance runner

`/opt/zkdeal-acceptance` drives the real hosted control plane and independently
joins evidence from the coordinator, indexer, queue, L1 RPC providers and the
deployment fault boundary. It is the executable used by the Kurtosis
acceptance matrix. It is not a health-only probe and it has no embedded service
implementation.

The runner consumes one candidate-bound scenario plan through
`ACCEPTANCE_PLAN_FILE` (preferred) or `ACCEPTANCE_PLAN_JSON`. The exact bytes
must match `ACCEPTANCE_PLAN_SHA256`. The plan also binds the SHA-256 of every
role credential it uses. Bearer credentials are accepted only from the
role-specific `*_TOKEN_FILE` variables, and the mounted value must match that
binding. Token values are never included in request summaries or evidence.
Fault injection, backup/restore, failover control, and failover approval use
four distinct scoped authorities; every failover mutation requires both the
control bearer and the separately mounted approval credential. The runner
rejects an owner-administrator credential on those control endpoints. Their
normalized endpoint identities are hash-bound into every applicable evidence
record.
Before any scenario action, the runner reads the live owner capability and
requires the exact database schema, enabled current-zkVM room-batch path, and
enabled source-bound hosted-integration token recorded by the plan. The same
preflight requires the managed room-aggregate and sponsor-mutation publishers;
a fixture, stale owner image, or coordinator with any disabled acceptance
boundary cannot enter the scenario assertions.

Each plan step calls an allowlisted endpoint alias. Evidence fields are JSON
pointers into actual responses, and every scenario has code-owned invariants
and source-separation rules. A plan cannot turn a failed assertion into a pass
by changing an expected boolean. The write-once result records plan hash,
request-body hashes, endpoint identities, captured values and the final record
SHA-256.

The required scenarios are:

- reorg and deterministic rollback/retraction;
- independent-RPC disagreement and fail-closed recovery;
- split-brain promotion and stale-writer fencing;
- fresh database/object restore with hash equality;
- tenant-isolated, idempotent sponsorship;
- congested-queue fairness, EDF, lease recovery and caps;
- headless room-node restart with state/admission-journal continuity, exclusive
  crash-lock recovery, preserved queued work and zero duplicate sequencing;
- blob archive/hash/restart/finality;
- eight-room 7+1 partial aggregate and success-only charging;
- finalized renewal/handoff with maximum-charge/refund protection;
- root-checked, owner-managed withdrawal and replay denial;
- coordinator-to-agent-to-prover-to-heartbeat structured trace joining;
- six independent RPC, SSE, indexer, admission, scheduler and projection
  load/shadow gates. Their release bounds are 1,000+ observations, zero
  mismatch/fail-open/loss/starvation, an eight-block indexer-lag ceiling, and
  bounded backpressure/fairness assertions.

The exact trace command is:

```text
/opt/zkdeal-acceptance prover-agent-trace-join --assert-coordinator-agent-prover-heartbeat
```

The Kurtosis launcher reads the plan and independently revocable `eph_` token
files, validates their hashes and scenario use, then creates a mode-0600
temporary argument bundle. Kurtosis renders one input artifact per scenario,
mounts only that scenario's credentials, and stores one write-once evidence
artifact per run. Production keys and long-lived tokens are forbidden; the
enclave-scoped credentials must be revoked when the matrix closes.

The candidate plan supplies real endpoints and actions. Unit fixtures exercise
the verifier only and are never release evidence.
