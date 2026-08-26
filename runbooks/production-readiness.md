# Production readiness gate

This is a fail-closed checklist. A rendered chart, a conformance stub, or a
local tag is not release evidence. Run every command from the pinned deployment
tools container and publish the resulting evidence closure to the independent
object-locked evidence store.

## 1. Bind owner artifacts and immutable images

- Verify the owner capability manifests, OpenAPI, ABI locks, zkVM candidate
  manifest, and source-bundle manifest by SHA-256. Do not accept a pre-existing
  generated zkVM artifact lock as phase-A source. A changed owner byte invalidates generated
  reference freshness until the containerized generator and example replay
  gates are rerun.
- Require the static owner
  `web2-api/server/capabilities/hosting-v1.openapi.json`; compare it byte-
  semantically with the live `/hosting/v1/openapi.json` response. Route-scan
  summaries must never replace exact owner schemas, status codes,
  authentication, idempotency, or error contracts.
- Build the coordinator and room-node images from their owner Dockerfiles.
  Publish each into the candidate-scoped OCI namespace and record only its
  `repository@sha256:...` identity. Resolve every
  `requiredInProduction` entry in `config/images.lock.json`; nulls and
  `sha256:REPLACE` intentionally block production.
- Verify a pull and daemon inspection by digest. Do not treat a mutable tag or
  local image ID alone as release provenance.
- On the fresh 4090 namespace, stage the exact orchestrator/toolchain/runtime
  refs without promotion, run the two independent CUDA builds with
  `--bootstrap-lock`; require equal program ID and four compiled artifacts
  across the two builds. Then require the owner `trust-root-output` closure to
  bind the candidate==minted manifest, v6 lock, program ID, all seven exact
  locked artifact paths, and the three-image receipt. The final `evidence-closure` v2
  is the source/generated composite seal. Rerun `trust-root-check` before
  promotion and after WORM retrieval; only identical staged digests may be
  signed/promoted, with no rebuild.
- Run production Compose only through `scripts/production_compose.py` inside
  the socket-bearing `deployment-orchestrator` profile. Its `check` action
  renders the canonical overlays and rejects tag-only, tag-plus-digest,
  placeholder, malformed, mismatched, or build-backed services. Its `pull` and
  `up` actions additionally verify every requested digest in the daemon before
  `up --no-build --wait`.

## 2. Prove authority and role topology

- PostgreSQL is the transactional authority. Start one active coordinator and
  one signerless standby with different `COORDINATOR_ID` values.
- Start exactly one indexer, reconciler, blob publisher, and withdrawal
  `autoClaimer` per active epoch. Each uses the same active `COORDINATOR_ID`, a
  unique `HOSTED_WORKER_ID`, a Recreate rollout, and an owner-published command.
  Promotion must transactionally invalidate old delegations.
- Tenant withdrawal proof/request/status REST and JSON-RPC remain in the
  coordinator. Do not deploy a second `withdrawal` worker. The auto-claimer
  alone holds component lease `withdrawal`.
- Never deploy the standalone file queue in production. The packaged prover
  agent leases from the active coordinator's PostgreSQL/MinIO `/queue/v1` API;
  keep the agent/prover, tenant/billing/sponsorship/capacity, or other proving
  surfaces disabled whenever their owner capability, command, metrics, digest,
  or live acceptance is unpublished. A 501 conformance service is never a
  promotion candidate.
- Treat the standalone room-node as non-production until
  `managedL1Operations.roomBatch.hostedIntegration` is enabled and explicitly
  proves the live app engine to current zkVM `BatchInputV5` witness bridge,
  durable PostgreSQL queue, authenticated external prover, restart/resume, and
  `RoomManager.submitBatch`. `fixturePrepare` and `hostedLegacyGroth16` must be
  false. Pin the exact `sha256:<64 lowercase hex>` acceptance token computed
  from the joint owner/headless live evidence. A local-artifact proof or
  structural Helm render is not this evidence.
- Bind the broad owner evidence to `config/owner-release-gates.json`. Managed
  aggregate, sponsorship, and withdrawal writes remain disabled until their
  exact owner API, scoped role, durable nonce/exact-byte archive, independent
  receipt/finality, reorg/restart, and adversarial results satisfy that
  hash-bound contract. Deployment code must not invent provisional routes or
  environment variables.

## 3. Enforce signer and secret boundaries

- Use distinct liveness, operations/settlement, payout, finality-oracle,
  sponsor/relayer, withdrawal-relayer, and blob-publisher signer identities,
  addresses, credentials, CIDRs, and method policies.
- Standby, indexer, and reconciler receive no signer endpoint, credential, or
  egress. Provider payout is never shared with the withdrawal auto-claimer.
- The prover agent receives no signer or direct L1 RPC. It uses a service
  principal carrying only `l1-liveness` to ask the coordinator for a durable
  node-heartbeat operation; only the active coordinator receives the scoped
  node-liveness Web3Signer endpoint/address/token.
- The auto-claimer receives only `WITHDRAWAL_SIGNER_URL`,
  `WITHDRAWAL_SIGNER_ADDRESS`, and a secret-referenced auth token. The publisher
  receives only its `L1_SIGNER_*` boundary. Known development keys/tokens are
  forbidden outside local acceptance.
- OpenBao audit logging, encrypted/swap-safe storage, credential rotation, and
  unauthorized-path/method denial must have live evidence.

## 4. Exercise data loss and promotion

- Complete a PostgreSQL primary/streaming-standby drill. Persist the fenced
  primary target LSN; reject an unreplayed/stale standby; require standby replay
  at or beyond that target, schema/writability/freshness gates, and the former
  lease before atomic fence transfer. Use a one-time `Idempotency-Key`.
- Run the digest-pinned automated promotion controller with two independent
  HTTPS health witnesses and a real implementation of
  `promotion-controller/failover-provider-v1.openapi.json`. Prove healthy-
  witness veto, former-writer fence/termination, provider-captured target LSN,
  replay and canonical-indexer freshness, owner promotion, stable route commit,
  and signer activation only after the fence. The fixture-live result proves
  protocol mechanics only and is not release evidence.
- Complete encrypted, authenticated PostgreSQL plus object backup to a
  failure-independent object store. Restore into a fresh database/bucket,
  verify manifest/object hashes and transaction boundary, and retain tamper and
  archive-traversal negative results. A same-MinIO drill proves mechanism only,
  not primary-store-loss recovery.
- Meet the configured warm-standby RTO of at most five minutes. Follow
  `warm-standby-promotion.md`; never grant standby signer authority before the
  fence and freshness gates complete.

## 5. Exercise edge, observability, and runtime behavior

- Prove TLS, explicit trusted-proxy CIDRs, spoofed-XFF/correlation-ID defense,
  protected admin/indexer/admission routes, JSON body/rate/WAF limits, cache and
  circuit-breaker behavior, and dedicated unbuffered SSE resume via
  `Last-Event-ID`.
- Render and install the chart in an ephemeral Kubernetes cluster. Wait for
  actual owner health, then verify least-privilege NetworkPolicies, PDB/HPA,
  backup CronJob/restore and promotion Jobs, secret refs, rolling update, and
  uninstall cleanup.
- Validate all Prometheus rules with `promtool`, dashboard JSON and catalog
  links/cardinality. Fire and recover the live webhook path. Required-owner
  metric-contract alerts stay firing until the owner broad seal publishes an
  exact machine catalog and live scrapes prove family type, unit, bounded
  labels, and query coverage.
- Join structured correlation records across coordinator, durable queue,
  packaged agent, prover, managed-L1 operation, signer, and final watcher.
  Require the effective sanitized ID in the response and prove spoofed or
  conflicting IDs cannot split the trace. A source scan or front-door header
  test alone is not the live trace-join gate.
- Complete the executable fault matrix and the restart-resume hosting/proving
  soak described in `release-soak.md`. Release closure requires real jobs,
  faults, durable IDs/nonces/cursors/charges, sealed-output revalidation, and
  zero unresolved safety or claim records—not health polling.

## 6. Seal the release record

Create a content-addressed closure containing the exact source manifest, image
digests and IDs, configuration hashes, chain/trust-root seed, command/image ID,
timestamps, exit codes, pass/fail classification, and every immutable failed or
superseded record. Sign or MAC the closure with a key not stored beside the
evidence. Upload it to the distinct versioned object-lock bucket, record object
version/retention, verify overwrite/delete refusal, retrieve it, and recheck its
hash. Only then may an operator mark the candidate deployable.
