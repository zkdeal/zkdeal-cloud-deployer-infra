# Owner-driven release soak

The release soak is a 12-hour stateful acceptance run, not a health poll. The
deployment harness validates an immutable run manifest, invokes a
content-addressed owner acceptance runner, persists resume state on durable
storage, and independently checks the runner's append-only JSONL journal.

The manifest separately binds the umbrella source manifest, source archive,
cross-project source closure, physical settlement-scenario hash, fresh
deployment-address hash, final hosted-integration acceptance token, exact
images, contract/circuit/zkVM trust roots, chain seed, expected usage and
charges, budgets, and scheduled faults. It must
name concrete digests for coordinator, indexer, reconciler, headless, prover,
and the owner runner. The headless room-node lifecycle is now published, but a
single end-to-end owner acceptance runner covering the prover, settlement,
withdrawal, restart, and fault schedule is still required; until it is
digest-pinned, a release manifest cannot pass and no soak readiness is claimed.

The deployment-owned `soak-runner` image supplies `/opt/zkdeal-soak`, the
append-only state/resume orchestration, and an independent closure verifier. It
intentionally does **not** embed a synthetic lifecycle implementation. The
final candidate build must add the broad-sealed, source-bound executable at
`/opt/zkdeal-owner-soak`, and the manifest's owner-command hash must match those
exact bytes. The current wrapper-only local image is useful static evidence but
cannot be staged or described as physical/release soak evidence.

The owner runner must continuously create a room and execute
submit → lease → live non-fixture `BatchInputV5` prepare → real CUDA Groth16
proof → verify → blob archive → six-blob/eight-room 7+1 aggregate settle →
sponsored renewal/refund → pre-finality reorg recovery → finalize → real
withdrawal claim/replay rejection → reconcile. Its journal persists room, job,
nonce, transaction, event, and cursor identifiers and proves seven successful
charges plus zero failed-member charge. It must induce and recover from
headless and prover restarts, coordinator promotion, indexer rollback, RPC
disagreement, object-store and database restarts, and one Docker/host
restart-resume.

The immutable soak manifest binds the validated
`owner-durable-capabilities.json` SHA-256. Every L1 mutation must pass through a
capability-enabled owner durable
operation. The owner owns nonce reservation, scoped remote signing, exact-byte
archival, broadcast, reorg recovery, and canonical finality. `cast` is limited
to ABI encoding comparison: `cast send`, direct RPC senders, ad-hoc keystores,
and a prover/room-node signer are forbidden. The journal binds the aggregate
operation (`0x5e8b37ac`), withdrawal claim (`0xb051a9f8`), sponsored reserve
(`0x827ac259`), sponsored renewal (`0xf180fe5d`), finalized checkpoint
(`0xe19bc67e`), and beneficiary disposal (`0xed97f11a`) to their owner operation
IDs. Sponsor, finality-oracle, and beneficiary sender authorities remain
separate; the beneficiary signs disposal while the unused escrow returns to the
stored payer.

Run only in the deployment container, with the manifest, journal, state, and
owner command file on durable mounts:

```text
python scripts/soak.py run --manifest /evidence/soak-manifest.json --journal /evidence/soak.jsonl --state /evidence/soak-state.json --owner-command-file /evidence/owner-command.json
python scripts/soak.py run --resume --manifest /evidence/soak-manifest.json --journal /evidence/soak.jsonl --state /evidence/soak-state.json --owner-command-file /evidence/owner-command.json
python scripts/soak.py verify --manifest /evidence/soak-manifest.json --journal /evidence/soak.jsonl
```

The closure fails unless the live prepare is non-fixture, the proof is real CUDA
Groth16, the verified receipt/journal/seal identity is exact, aggregate outcome
is 7+1 across six transaction blobs, sponsor refund returns to the distinct
payer without double billing, the orphaned pre-finality operation recovers
canonically without duplicate nonce/charge, and the withdrawal uses a real
proof with one successful claim and rejected replay. It also fails unless sealed output hashes remain unchanged; nonce and
charge duplication and unresolved safety/claim counts are zero; observed usage
and charges match the manifest; fairness/deadline budgets hold; every fault has
a recovery assertion; and restart-resume is explicitly verified. Publish the
manifest, state, journal, verifier output, and their hashes in the immutable
release evidence closure.
