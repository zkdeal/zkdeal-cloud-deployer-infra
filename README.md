# zkdeal cloud deployment project

This directory is the operator-owned deployment surface for zkdeal. It does
not fork or reimplement the coordinator, queue, prover, room protocol, or
contracts. Builds and reference documentation consume artifacts from their
owner projects in the umbrella checkout.

The checked-in default Compose profile is deliberately local and
non-production. A separate `compose.hosted.yaml` overlay consumes the real
coordinator/indexer/reconciler/publisher/auto-claimer/capacity commands with PostgreSQL, object
storage and transactional epoch fencing:

- the default coordinator and standalone prove queue use inspectable local
  filesystem state; they are not the hosted authority path;
- the hosted overlay runs one active and one warm-standby coordinator plus the
  owner-published delegated indexer/reconciler/publisher/auto-claimer/capacity worker, each with
  explicit identities; tenant withdrawal APIs remain in the coordinator and
  there is no duplicate withdrawal worker;
- no admission or indexer signing key is enabled; the publisher and sole
  auto-claimer receive distinct scoped remote-signer URLs, expected addresses,
  tokens, network policies, and no provider-payout authority;
- the local prover agent uses an explicit stub profile unless a real prover
  image and GPU are supplied;
- the production prover agent leases only from the active coordinator's
  PostgreSQL/MinIO queue API and submits heartbeat intents through its durable
  `l1-liveness` operation; direct L1 RPC, raw keys, and Web3Signer access are
  local-development-only;
- the front door binds loopback over HTTP; the TLS examples require operator
  certificates or an explicitly configured ACME hostname;
- production validation rejects unpinned images, inline secrets, missing TLS,
  single-RPC critical paths, and multi-replica filesystem authority.

## Quick start

From `cloud-deployer-infra`:

```powershell
Copy-Item .env.example .env
docker compose -f compose/compose.tools.yaml build deployment-tools
docker compose -f compose/compose.tools.yaml run --rm deployment-tools scripts/validate.py --profile config/profiles/local.json --check-owner-artifacts
docker compose -f compose/compose.tools.yaml run --rm deployment-tools scripts/bootstrap.py --profile config/profiles/local.json
docker compose --env-file .env -f compose/compose.yaml up --build --wait
docker compose -f compose/compose.tools.yaml run --rm deployment-tools scripts/smoke.py --profile config/profiles/local.json --base-url http://host.docker.internal:8088 --queue-url http://host.docker.internal:3005
```

Enable local observability with `--profile observability`. Enable the queue's
CPU-only stub consumer with `--profile smoke`. Neither profile claims GPU or
mainnet readiness.

Stop the stack with:

```powershell
docker compose --env-file .env -f compose/compose.yaml down
```

State is bind-mounted under `.state/local`; backups and restore rehearsals are
therefore inspectable from the host. `.state` and `.env` are intentionally
excluded from source-control inputs.

## Deployment surfaces

| Surface | Purpose | Readiness boundary |
|---|---|---|
| `compose/` | Local, dependency, hosted, security, failure and evidence rehearsals | Hosted workers consume negotiated owner commands |
| `helm/zkdeal/` | Kubernetes packaging with health, disruption, fence and network gates | Production requires transactional storage and explicit identities |
| `kurtosis/` | Local, failover, 18-scenario acceptance and stateful-soak packages | Release launchers require exact candidate image and input hashes |
| `acceptance-runner/` | Source-bound real owner/API assertion engine | Local builds are fixtures until registry-published with the sealed candidate |
| `failover-runner/` | Promotion controller plus independent fence/RTO assertion closure | Requires a live scoped failover provider and exact candidate acceptance image |
| `soak-runner/` | Restart-safe soak state machine and independent closure verifier | Deliberately lacks the moving owner lifecycle driver until broad seal |
| `scripts/candidate_topology.py` | Re-inspects the private Compose/Kubernetes stack shared with Kurtosis | Requires distinct scoped fault and backup/restore adapters plus independent PostgreSQL HA |
| `config/` | Environment profiles, policy schema, image/artifact manifests | Production policy is fail-closed |
| `scripts/` | Bootstrap, migration orchestration, backup/restore, smoke/soak and evidence | Destructive restore requires explicit confirmation |
| `observability/` | Prometheus rules and Grafana provisioning | Alerts only for metrics owner services currently expose |
| `front-door/` | Loopback HTTP, certificate TLS and reverse-proxy examples | Trusted proxy ranges must be operator-owned |
| `docs/` | Operator portal plus generated API/ABI reference | Generated from owner sources with source hashes |
| `runbooks/` | Operations, recovery, security, and evidence procedures | Calls out external prerequisites |

## Required owner artifacts

`config/artifacts.json` is the source-of-truth map. The default umbrella layout
expects sibling directories `web2-api`, `app-node`, `prover-node`,
`web3-protocol`, and `kurtosis-testing`. `scripts/verify_artifacts.py` checks
existence and content hashes without modifying those projects.

The hosted API reference consumes the owner-published static
`web2-api/server/capabilities/hosting-v1.openapi.json`. Route scanning is only a
coverage inventory: it may add a missing route summary but can never overwrite
an authoritative owner request, response, error, authentication, or
idempotency contract. Reference freshness remains fail-closed until that static
artifact matches the live owner endpoint and the owner indexer catalogs every
current protocol lifecycle event and call.

The coordinator image is built with the owner Dockerfile
`../web2-api/server/Dockerfile` and umbrella build context. The same image runs
the owner's standalone prove queue only in the explicit local loop. Production
disables that service: the packaged prover agent uses the active coordinator's
durable `/queue/v1` API and owner-mediated heartbeat operation. A real GPU
prover must be supplied as a digest-pinned image; the deployment project never
synthesizes a proof implementation.

The prover agent is a distinct image built by `agent/Dockerfile` from the
owner's `prover-node/agent` source and lockfile. The build type-checks those
bytes, bundles the entrypoint, and labels the source, liveness-capability, and
structured-trace-capability hashes. Candidate validation requires all three
labels plus a live tenant/room/job/correlation trace join. The coordinator
image does not contain that package and is rejected as an agent-image
substitute.

## Production gate

Before using a production profile:

1. Resolve every `requiredInProduction` image in `config/images.lock.json` to a
   verified `sha256` digest.
2. Supply secrets through the target platform and name the existing Kubernetes
   Secret in Helm values. Do not put secret values in a profile or values file.
3. Supply at least two independently operated L1 RPC endpoints for critical
   paths.
4. Terminate TLS and set only the proxy CIDRs actually controlled by the
   operator.
5. Run exactly one active coordinator identity. A standby may share PostgreSQL
   only without signer authority; indexer/reconciler/publisher/auto-claimer/capacity use
   unique delegated worker identities bound to the active epoch. The publisher
   and auto-claimer receive distinct blob and withdrawal-relayer signer
   boundaries. The capacity worker receives only its scoped provider URL and
   bearer token and shares the active coordinator epoch. The standalone file
   queue is production-disabled; the agent must use the hosted coordinator
   queue and must never receive a liveness signer. The headless room-node and
   full proving graph remain production-blocked until
   `managedL1Operations.roomBatch.hostedIntegration` declares and live-tests
   the app-engine to current-zkVM `BatchInputV5` bridge, durable PostgreSQL
   queue, authenticated external prover, restart/resume and
   `RoomManager.submitBatch`. Fixture preparation and hosted legacy Groth16
   must be false, and Helm must pin the SHA-256 token of the joint evidence. A
   standalone local-artifact `prove` command is not that evidence.
6. Complete the backup/restore rehearsal and capture evidence.
7. Configure the digest-pinned promotion controller with two independent HTTPS
   witnesses and a first-party failover-provider v1 adapter. Docker and
   Kubernetes live drills prove real synchronous-PostgreSQL fencing, route
   switching, post-fence signer activation, and durable replay; repeat the
   appropriate drill with the exact candidate image on its target platform.
8. Follow the two-phase 4090 trust-root gate. Phase A seals owner/source bytes
   with no generated lock or minted manifest. Stage—but do not promote—the
   exact orchestrator/toolchain/runtime digests. The double-CUDA bootstrap is
   the sole writer of the v6 lock and minted manifest; it compares the program
   plus four compiled artifacts across builds, while the resulting closure
   pins seven total artifact paths. The generated closure and final
   source/generated composite must pass before the identical staged digests
   may be signed or promoted.

Production Compose must go through the containerized preflight/launcher; raw
`docker compose ... up` is not a release entrypoint because Compose's `${VAR:?}`
syntax rejects only empty values and cannot distinguish a mutable tag from an
immutable digest. `compose/release-images.example.env` is an image and
promotion-settings fragment, not a complete runtime environment. Build one
candidate-scoped operator environment containing that fragment plus reviewed
non-secret chain/endpoint settings and paths to the distinct signer
configuration directories; keep credential contents in referenced files.
Replace every placeholder, then run the full production signer,
observability, and GPU graph:

```powershell
docker compose -f compose/compose.tools.yaml --profile orchestrator run --rm deployment-orchestrator scripts/production_compose.py check --env-file /workspace/cloud-deployer-infra/.state/candidates/CANDIDATE/runtime.env --with-signer --profile observability --profile gpu
docker compose -f compose/compose.tools.yaml --profile orchestrator run --rm deployment-orchestrator scripts/production_compose.py pull --env-file /workspace/cloud-deployer-infra/.state/candidates/CANDIDATE/runtime.env --with-signer --profile observability --profile gpu
docker compose -f compose/compose.tools.yaml --profile orchestrator run --rm deployment-orchestrator scripts/production_compose.py up --env-file /workspace/cloud-deployer-infra/.state/candidates/CANDIDATE/runtime.env --with-signer --profile observability --profile gpu
```

The gate requires every owner and dependency reference in canonical
`repository@sha256:<64 lowercase hex>` form, rejects tags and reserved
placeholder registries, cross-checks the rendered service images, rejects all
build fallbacks, and verifies daemon digest identities before starting.

See `runbooks/production-readiness.md` for the full gate and
`runbooks/final-candidate-seal.md` for the immutable owner→image→Compose→Helm→
Kubernetes→Kurtosis→soak ordering.

Kurtosis acceptance runs only after a candidate-scoped private bridge maps all
endpoint aliases to the already-running exact Compose/kind image identities.
The args file, its hash-bound scenario plan, and every independently revocable
role-token file are explicit pre/post-hashed evidence inputs. Fixture URLs,
different-enclave DNS names, mutable tags, token values in logs, and missing
post-run revocation evidence are release failures.

`config/candidate-private-topology.example.json` is intentionally invalid. A
release copy must bind every private endpoint to an exact candidate digest and
live container/pod identity, bind the two independent L1 providers, and prove
primary/standby replay and fencing. It also requires separate
`faultController` and `backupRestoreController` image/capability/source hashes;
their null locks remain an explicit release blocker until the scoped adapters
are published and exercised live.

`config/final-candidate.example.json` separates the pre-build
`ownerBroadSeal` from `zkvmGeneratedTrustRoot`. The latter is required by every
release, physical, and publication validation. `config/physical-evidence.example.json`
then binds the staged-image receipt, generated closure, final composite,
pre-promotion check and identical-digest publication as separate records;
`config/final-publication.example.json` binds the post-physical yellow-paper
and investor-deck artifacts without changing the executable candidate.

## Container-only execution

All executable scripts fail closed outside a container. Validators, reference
generation, tests, evidence capture, smoke and soak therefore run through the
`deployment-tools` service. The host is used only to orchestrate containers and
edit files. The tools service mounts the umbrella read-only and overlays only
this deployment directory as writable.

The default tools service has no Docker socket. Evidence collection that must
inspect local image IDs uses the separate `deployment-orchestrator` profile;
do not use that socket-bearing service for ordinary validation or document
generation.

Run the complete Python conformance suite with:

```powershell
docker compose -f compose/compose.tools.yaml run --rm deployment-tools scripts/test_all.py
```

Render Helm inside its official container:

```powershell
docker run --rm -v "${PWD}/helm/zkdeal:/chart:ro" alpine/helm@sha256:e7ecbf4a200dea73d64bfb8cb0936829164945f2b4d02a0274093073ee8d264f template zkdeal /chart
```
