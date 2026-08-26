# Kurtosis scenarios

Each package consumes image references supplied in its argument object. No
protocol or service implementation is copied into these packages.

- `local/` provisions the development-shape hosted plane from the exact
  candidate image set: PostgreSQL, MinIO plus bucket init, the hosted active
  coordinator (which serves the durable PostgreSQL/MinIO prove queue), the
  indexer worker, the production headless room node, and either the CUDA
  prover with its agent or a declared fixture agent. It never starts the
  standalone filesystem queue and returns
  `standalone_queue_started: False` with `release_evidence: False`.
- `failover/` starts only the digest-pinned failover assertion runner against
  a candidate stack that must already exist and be verified by
  `scripts/candidate_topology.py check`. The runner drives the promotion
  controller against the real primary/standby replicated PostgreSQL pair in
  the verified topology, persists the primary target LSN, proves standby
  replay at or beyond that target, promotes through the fenced/idempotent
  owner endpoint, rejects the stale writer, and measures an RTO of at most
  300 seconds. Health-only setup is not failover evidence.
- `acceptance-matrix/` starts only the digest-pinned owner acceptance runner
  and refuses to run without executable assertions for reorg, RPC
  disagreement, split brain/promotion, database/object restore, sponsorship,
  queue congestion, blob restart safety, partial aggregates, renewal,
  withdrawal, six independent load/shadow domains, and the
  coordinator-agent-prover heartbeat trace join. Its endpoints are never
  free-form: the launcher expands them from the write-once candidate-topology
  verification receipt, so every URL the runner consumes is served by an
  inspected candidate-image workload (fault and backup controllers from the
  first-party `fault-control/` and `backup-restore-control/` images, two
  independently identified L1 RPC providers, hosted coordinator queue). A
  candidate-bound plan and role-scoped ephemeral token files are mandatory;
  the launcher verifies their hashes and the package stores separate evidence
  artifacts for all 18 scenarios.
- `soak/` refuses health-only polling. Its digest-pinned runner requires the
  hash-bound release manifest, the owner command argv file, the source-bound
  `/opt/zkdeal-owner-soak` driver (added only by the soak-runner `candidate`
  Docker target from the sealed owner image), journal/state/closure files
  under the evidence directory, and the exact lifecycle/restart/durability
  contract strings. The release duration is 43,200 through 86,400 seconds.

Example arguments are deliberately invalid placeholders. Replace every image
with an exact `repository@sha256:<64 lowercase hex>` reference from a real
registry. Mutable tags, tag-plus-digest forms, `REPLACE` values, and reserved
`registry.invalid`/`registry.example` hosts are rejected both by the package and
the mandatory launcher.

Run all packages through the containerized launcher so an unvalidated image or
an unverified topology cannot reach Kurtosis. The launcher expands failover,
soak, and acceptance-matrix args against the candidate topology receipt before
`kurtosis run`; the raw args file never reaches those packages directly:

```powershell
docker compose -f compose/compose.tools.yaml --profile orchestrator run --rm deployment-orchestrator scripts/kurtosis_run.py local --args-file kurtosis/local/args.json --check-only
docker compose -f compose/compose.tools.yaml --profile orchestrator run --rm deployment-orchestrator scripts/kurtosis_run.py local --args-file kurtosis/local/args.json --enclave zkdeal-local
```

The first-party acceptance runner is implemented and source-bound under
`acceptance-runner/`, but it is not release-published and cannot execute until
the final owner stack, plan, ephemeral principals, and immutable image digest
exist. The soak runner remains independently release-gated. No static Starlark
inspection, unit fixture, or health-only run is represented as scenario
evidence.
