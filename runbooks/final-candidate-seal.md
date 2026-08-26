# Final candidate seal

Do not regenerate references from a moving owner tree. This sequence starts
only after the owner team publishes its broad-gate evidence and the joint
room-node/current-zkVM evidence token. Every executable below runs in Docker;
the host only launches containers and transfers the resulting immutable
artifacts.

## 1. Freeze the owner byte boundary

Copy `config/final-candidate.example.json` to a candidate-specific file under
`.state/candidates/<candidate-id>/candidate.json`. Fill the SHA-256 of every
required owner artifact from `scripts/verify_artifacts.py`, the broad-gate
evidence-manifest umbrella-relative path and SHA-256, and the exact
`managedL1Operations.roomBatch.hostedIntegration.acceptanceToken`. The token
must be computed over the canonical joint evidence and have the form
`sha256:<64 lowercase hex>`.

This first invocation is phase A. Leave `zkvmGeneratedTrustRoot.passed:false`,
all three generated-output bindings empty, all three staged image references
null, and the candidate/lock/program hashes null. The phase-A validator rejects
an existing lock, minted manifest, staged image, or generated closure. The
immutable broad owner evidence—not a future generated lock—is the phase-A
source authority.

Also copy the SHA-256 of `config/owner-release-gates.json` into
`ownerBroadSeal.gateContractSha256`. The owner broad evidence must contain the
same hash. This prevents a green boolean map from silently weakening the
deployment acceptance criteria after it was reviewed.

The manifest must say all of the following before the owner phase can pass:

- the real app-node engine bridges to current zkVM `BatchInputV5`;
- queue state is durable PostgreSQL authority and the prover is external;
- restart/resume is proven and publication uses `RoomManager.submitBatch`;
- fixture preparation and hosted legacy Groth16 are both disabled;
- build, unit, PostgreSQL, PostgreSQL+MinIO, API catalog, metrics catalog,
  read-only-root, wallet/allocation authorization, shared L1 transport,
  semantic reconciliation, correlation logging, node lifecycle, and joint
  hosted-integration gates are green;
- managed aggregate, sponsorship, and withdrawal L1 operations satisfy the
  scoped-authority, exact-byte, durable-nonce, finality, reorg, restart, and
  adversarial criteria in the gate contract;
- the production proving queue and node heartbeat are owner-durable, and a
  live source-bound prover-agent trace joins lease, prover, heartbeat,
  completion, and failure records without direct signer or L1 authority.

Run the gate through the non-socket tools container:

```sh
docker compose -f compose/compose.tools.yaml run --rm deployment-tools \
  scripts/record_gate.py --name final-owner-source-seal \
  --runner-image zkdeal-deployment-tools:local \
  --runner-image-id <tools-image-id> --classification release --timeout 600 \
  --input-file .state/candidates/<candidate-id>/candidate.json -- \
  python scripts/final_candidate.py owner \
  --candidate-file .state/candidates/<candidate-id>/candidate.json
```

The referenced owner evidence must itself be a passed schema-version-1 JSON
record with the same candidate ID, gate-contract hash, exact gate map,
source-artifact hashes and hosted-integration token. The owner phase also recomputes every source hash in
the joint evidence before allowing regeneration. A hand-authored hash or chat
status cannot satisfy the gate. Current owner artifacts intentionally fail
until the replacement joint evidence is sealed after the broad source freeze.
Do not bypass or replace the evidence token with a prose status.

After the 4090 two-build ceremony, fill `zkvmGeneratedTrustRoot` in the final
candidate descriptor with the source-closure, staged-image-receipt and
generated-closure hashes, candidate-manifest and v6-lock hashes, program ID,
and exact orchestrator/toolchain/runtime digest references. From the `release`
phase onward, the validator rechecks phase A and requires this phase-B section
to pass; the final descriptor hash then remains unchanged through physical,
publication and WORM closure.

## 2. Regenerate and validate owner-derived references

After the owner phase is green, regenerate references once, sync exact owner
commands/capability pins into Compose and Helm, and run the full unit, ABI,
OpenAPI, route-example, documentation, observability, source-bundle and image-
context policies. Record each command separately with `scripts/record_gate.py`;
preserve failed records as superseded evidence.

For every candidate gate, pass `--input-file` once for the candidate descriptor
and once for every `.state` input it consumes (`runtime.env`, Helm/kind values,
Kurtosis arguments, soak manifest, or registry receipt). The evidence runner
hashes these files before launching the command. General source inventory
deliberately excludes mutable `.state`; a record without these explicit hashes
is not candidate evidence.

The acceptance arguments are a manifest of further security-sensitive inputs,
not the whole input set. Resolve its `plan_file` and every path named by
`auth_files` relative to the arguments file, and pass each as another
`--input-file` to both the release-input seal and the live acceptance record.
`record_gate.py` stores only path, byte count, and SHA-256 and rechecks those
bytes after the command; it never stores file contents. Never print, interpolate
into a command line, or copy a token value into stdout/stderr. The plan's
`plan_sha256` and `authTokenSha256` map must match these same bytes. Keep the
files mode 0600, use a different `eph_` credential for every used role, and
delete them only after a separately retained owner revocation receipt and a
post-revocation denial probe are sealed.

The ordering is mandatory: regenerate, capability-sync, full static suite,
create and verify the deterministic source bundle, then build. Transfer that
bundle and its outer manifest into a fresh 4090 namespace by following
`runbooks/4090-source-transfer.md`. A source byte change after regeneration
invalidates every later candidate record. The bundle contains
`prover-node/zkvm/source-manifest.candidate.json`, but explicitly excludes
`prover-node/zkvm/artifacts.lock.json`,
`prover-node/zkvm/source-manifest.json`, and every `prover-node/zkvm/build/**`
output. Those are generated trust roots, never source inputs.

## 3. Stage images and mint the generated trust root

On the fresh 4090 namespace, first create and verify the owner
`source-closure.json`. Then build and push the exact release orchestrator,
source-independent CUDA toolchain, and source-bound CUDA runtime described by
`prover-node/zkvm/docker/RTX4090_RELEASE_RUNBOOK.md`. Record their canonical
`repository@sha256:<64 lowercase hex>` references. These are candidate-staged
images only: do not sign, copy to another namespace, or mark them promoted.

Create the exact write-once unpromoted receipt with the owner assembler; do
not hand-author it:

```text
node zkvm/scripts/build-4090-evidence-requests.mjs staged-images \
  <candidate-manifest-sha256> <orchestrator-repository@sha256> \
  <toolchain-repository@sha256> <runtime-repository@sha256> \
  <write-once/staged-zkvm-images.json>
```

Run the staged release orchestrator's mandatory command exactly once to close
two independent CUDA builds:

```text
node zkvm/build.mjs --cuda --check-repro --bootstrap-lock \
  --toolchain-image <staged-toolchain-repository@sha256> \
  --runtime-image <staged-runtime-repository@sha256>
```

This command is the sole authorized writer of `zkvm/build/**`,
`zkvm/artifacts.lock.json`, and `zkvm/source-manifest.json`. It must prove the
same program ID and the four independently compiled artifacts (`zkdeal-r0`,
client, verifier JavaScript, and verifier WASM) across independent target and
registry volumes before writing the v6 lock and a minted manifest that is
byte-identical to the candidate manifest. The resulting lock and generated
closure additionally bind derived `capabilities-v6.json` and both frozen AMM
fixtures, for seven exact locked paths in total.

Immediately create the write-once generated-output index and verify it:

```text
node zkvm/scripts/build-4090-evidence-requests.mjs trust-root-output \
  zkvm <write-once/staged-zkvm-images.json> \
  <write-once/generated-trust-root-closure.json>
node zkvm/scripts/build-4090-evidence-requests.mjs trust-root-check \
  zkvm <write-once/staged-zkvm-images.json> \
  <write-once/generated-trust-root-closure.json>
```

The generated closure binds candidate==minted manifest, the v6 lock, program
ID, exact staged toolchain/runtime references, runtime host binary, and every
locked artifact hash. It also proves generated outputs were excluded from the
source preimage. Any later source, lock, build-output, or staged-image mutation
invalidates the candidate.

Build the coordinator, room-node, deployment-packaged owner prover-agent,
docs, backup, promotion controller, failover provider, and real Kurtosis
assertion/failover/soak runners. Runner images must execute real owner APIs and
platform faults; a substring checker or 501 conformance service is not an
acceptable candidate.

Publish all images to the candidate-scoped on-node OCI registry using
`scripts/oci_registry.py`. Record only canonical
`repository@sha256:<64 lowercase hex>` references, verify a pull and daemon
identity by digest, and put those staged references into the candidate file.
Do not promote yet. Mutable tags, tag-plus-digest references, placeholders,
and local image IDs are not candidate identities. Include pinned PostgreSQL,
MinIO/client, OpenBao, Web3Signer, front door, monitoring/logging, and alert
images.

Then run:

```sh
docker compose -f compose/compose.tools.yaml --profile orchestrator run --rm \
  deployment-orchestrator scripts/record_gate.py \
  --name final-candidate-digest-and-input-seal \
  --runner-image zkdeal-deployment-tools:local \
  --runner-image-id <tools-image-id> --classification release --timeout 600 \
  --input-file .state/candidates/<candidate-id>/candidate.json \
  --input-file .state/candidates/<candidate-id>/candidate-private-topology.json \
  --input-file fault-control/capability.json \
  --input-file backup-restore-control/capability.json \
  --input-file .state/candidates/<candidate-id>/runtime.env \
  --input-file .state/candidates/<candidate-id>/helm-values.yaml \
  --input-file .state/candidates/<candidate-id>/local.json \
  --input-file .state/candidates/<candidate-id>/failover.json \
  --input-file .state/candidates/<candidate-id>/acceptance-matrix.json \
  --input-file .state/candidates/<candidate-id>/acceptance-plan.json \
  --input-file .state/candidates/<candidate-id>/auth/<each-used-role>.token \
  --input-file .state/candidates/<candidate-id>/soak.json -- \
  python scripts/final_candidate.py release \
  --candidate-file .state/candidates/<candidate-id>/candidate.json \
  --candidate-topology-file .state/candidates/<candidate-id>/candidate-private-topology.json \
  --compose-env-file .state/candidates/<candidate-id>/runtime.env \
  --helm-values-file .state/candidates/<candidate-id>/helm-values.yaml \
  --kurtosis-local-args .state/candidates/<candidate-id>/local.json \
  --kurtosis-failover-args .state/candidates/<candidate-id>/failover.json \
  --kurtosis-acceptance-args .state/candidates/<candidate-id>/acceptance-matrix.json \
  --kurtosis-soak-args .state/candidates/<candidate-id>/soak.json
```

This second phase also requires every Compose environment image, every enabled
Helm component/operational image, and every Kurtosis server/runner image to
equal its exact candidate descriptor reference. It also requires exact
Compose/Helm commands and capabilities to match the sealed owner manifests.
Passing this phase seals the staged candidate inputs; it is not OCI promotion.

## 4. Exercise the exact candidate

Create one candidate-scoped `runtime.env` from the image fragment plus reviewed
non-secret runtime settings and operator-owned secret/configuration paths. The
image fragment alone is intentionally not runnable. Use the digest references
from the candidate file and that complete environment for every live gate:

1. production Compose policy, render, pull, and `up --no-build --wait` with
   production signers plus the observability and GPU profiles;
2. the exact packaged prover-agent digest against the owner queue/prover/
   durable-heartbeat protocol boundary, including a structured trace join
   across tenant/room/job/lease/attempt context, followed by live owner
   health/capabilities and full OpenAPI example replay;
3. synchronous PostgreSQL failover, encrypted database+object backup/restore,
   front-door controls, OpenBao/Web3Signer denial+rotation, and observability
   fire/recover rehearsals;
4. production Helm render plus semantic validation;
5. ephemeral Kubernetes install using the real owner images, health waits,
   NetworkPolicy/PDB/HPA/CronJob/secret assertions, rolling update, and clean
   uninstall;
6. the sealed Docker and Kubernetes first-party failover-provider drills;
7. digest-pinned Kurtosis local, failover, and 18-scenario acceptance matrix,
   including the first-class headless-node restart/continuity case;
8. the 12-hour stateful submit→lease→prove→publish→finalize→withdraw soak on
   the 4090, including component, storage, Docker and host restart/resume.

### Candidate-to-Kurtosis topology and credential closure

Kurtosis must exercise the already-running exact candidate; it must not start
or discover a fixture that can self-attest. Before the acceptance matrix, make
the candidate Compose or kind private APIs reachable from the enclave through
a candidate-scoped private bridge. Do not expose admin, fault, backup, signer,
database, object-store, or log-query routes on a LAN/public front door. Retain
a topology receipt mapping every endpoint alias to its normalized URL, Docker
container or Kubernetes pod identity, candidate descriptor image key, exact
`repository@sha256` reference, daemon image ID, and bridge/network identity.
The `queue` alias must be the coordinator authority; the remaining authorities
must satisfy the launcher's independence rules. External L1 RPC entries must
also bind provider identity and chain ID. A DNS name that exists only inside a
different enclave, a local tag, a 501 stub, or an unbound URL fails this gate.

Create the descriptor from
`config/candidate-private-topology.example.json` and validate it against
`config/schemas/candidate-private-topology.schema.json`. The release form must
bind the exact fault-control and backup/restore-control capability files,
source hashes, and candidate image digests. Both capabilities must advertise
production readiness, their dedicated scoped auth role, code-owned target
allowlists, and rejection of arbitrary platform targets. The topology also
requires distinct PostgreSQL primary/standby identities, persisted target and
replay LSNs, replay at or beyond target, and the former-primary fence. The
example intentionally fails these checks until the adapters and live candidate
exist.

Run the live identity recheck before launching any Kurtosis package:

```sh
python scripts/candidate_topology.py check \
  --candidate-file .state/candidates/<candidate-id>/candidate.json \
  --topology-file .state/candidates/<candidate-id>/candidate-private-topology.json \
  --output .state/candidates/<candidate-id>/candidate-private-topology.verification.json
```

Run the matrix from the socket-bearing deployment orchestrator only after the
topology receipt is green. List the arguments, plan, and every used role file as
stable inputs; angle-bracketed lines below mean one repeated flag per actual
path, not a shell expansion:

```sh
docker compose -f compose/compose.tools.yaml --profile orchestrator run --rm \
  deployment-orchestrator scripts/record_gate.py \
  --name candidate-kurtosis-acceptance-matrix \
  --runner-image <deployment-tools-repository@sha256> \
  --runner-image-id <deployment-tools-image-id> \
  --classification live --timeout 43200 \
  --input-file .state/candidates/<candidate-id>/candidate.json \
  --input-file .state/candidates/<candidate-id>/candidate-private-topology.json \
  --input-file .state/candidates/<candidate-id>/candidate-private-topology.verification.json \
  --input-file .state/candidates/<candidate-id>/acceptance-matrix.json \
  --input-file .state/candidates/<candidate-id>/acceptance-plan.json \
  --input-file .state/candidates/<candidate-id>/auth/<each-used-role>.token -- \
  python scripts/kurtosis_run.py acceptance-matrix \
  --args-file .state/candidates/<candidate-id>/acceptance-matrix.json
```

The launcher independently re-hashes the plan and every token file, mounts only
the roles needed by each scenario, and requires the live owner capability to
match candidate ID, database schema, enabled current-zkVM room-batch path, and
the sealed hosted-integration token before each scenario. When all scenario and
load/shadow records are sealed, revoke every ephemeral role through the
broad-sealed owner credential authority, prove the same credentials receive an
authorization denial, store only aliases and hashes in the revocation record,
and remove the private bridge. If the owner broad seal does not publish a real
revocation operation, the release cannot progress to the 12-hour soak.

After every physical receipt and the completed soak verifier are immutable,
create the owner's final composite seal with the exact `evidence-closure`
command. Its v2 plan must include the generated trust-root closure as both a
named hash and a member of `files[]`; the assembler cross-checks the source
closure, candidate/minted manifest, v6 lock, program ID, staged runtime digest,
owner acceptance token, physical receipts, and soak result. Rerun
`trust-root-check` and re-hash the composite immediately afterward.

Only then may the operator sign and promote the exact staged image digests.
Promotion must not rebuild an image or substitute a tag, digest, repository,
source label, or program ID. Pull the promoted digest and prove its daemon ID
is the same artifact recorded at staging. The physical evidence manifest must
bind the source closure, staged-image receipt, generated trust-root closure,
source/generated composite seal, pre-promotion check, and identical-digest
promotion receipt as separate records.

`scripts/final_candidate.py plan` prints the canonical order. Every Kurtosis
package is launched only via `scripts/kurtosis_run.py`, which validates every
image and owner assertion command before invoking Kurtosis.

The Kubernetes release rehearsal sets `KIND_CANDIDATE_MODE=1`. It accepts only
the six exact owner/headless/docs/backup/promotion/provider digest references,
pulls each by digest, proves the cluster-local alias resolves to the same image
ID, and loads images sequentially under an explicit timeout. The supplied
candidate kind values may use those aliases because the evidence records the
digest→ID→alias chain, but must enable and wait for active+standby,
indexer/reconciler/publisher, headless, capacity, auto-claimer, promotion
controller, and failover-provider Deployments. Default mode remains a local
chart/storage mechanics test and is not release evidence.

The two provider drills also have an explicit release path. Set
`FAILOVER_PROVIDER_CANDIDATE_IMAGE` to the descriptor's exact provider digest;
set `POSTGRES_HA_BASE_IMAGE` for the Docker drill and
`POSTGRES_CANDIDATE_IMAGE` for the Kubernetes drill to the descriptor's exact
PostgreSQL digest. The Docker drill starts the provider with `--no-build` and
compares the running container image ID with the pulled digest. The Kubernetes
drill loads a cluster-local alias sequentially and records both the source and
alias IDs; any mismatch aborts before the fault. Omitting these variables is a
useful local adapter regression, but is not candidate evidence.

## 5. Close evidence

The final closure includes the source bundle hash, candidate descriptor hash,
all exact OCI references, registry-volume backup hash, chain seed/trust roots,
Compose and Kubernetes object inventories, fault/soak state machines, all gate
record hashes, and unresolved-safety count zero. Publish the content-addressed
closure to the failure-independent object-locked evidence bucket, verify
retention/delete refusal and retrieval hash, and record its version ID. The
sealing key must not be stored beside the evidence.

Immediately before the final WORM seal, rerun `trust-root-check` and verify the
composite seal. Immediately after retrieving the WORM object, rerun both
read-only checks and pull every promoted digest again. Publish that final
verification as a separate immutable audit record. It cannot alter or bless a
changed candidate; any mismatch revokes the release decision.
