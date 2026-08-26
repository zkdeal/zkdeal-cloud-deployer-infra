# No-history source transfer to the 4090 node

This workflow creates two deterministic archives: a reproducible source-input
bundle and a separate hash-bound acceptance-evidence bundle. The source bundle
contains the reviewed umbrella-root build policy plus owner sources, lock files,
fixtures, and only the two generated runtime inputs copied by the owner image.
It excludes repository history, dependency trees, generic build output, runtime
state, publication output, and `cloud-deployer-infra/evidence`. It never invokes
Git.

The zkVM boundary is intentionally non-circular. The archive contains
`prover-node/zkvm/source-manifest.candidate.json` but excludes
`prover-node/zkvm/artifacts.lock.json`,
`prover-node/zkvm/source-manifest.json`, and
`prover-node/zkvm/build/**`. A fresh extraction therefore has no generated
trust root. Only the mandatory two-CUDA-build command on the 4090 may create
those paths, using `--bootstrap-lock`; an existing or transferred generated
lock is a stop condition.

## Create and verify at the source

Before bundling, prepare and immediately verify the non-authoritative zkVM
candidate manifest inside Docker. This is the last permitted source-tree write;
it does not mint either trust root. Any later source edit requires repeating
this step and creating a new umbrella archive.

```powershell
$nodeRef = 'node:22-bookworm@sha256:0557ac14e0d45d02ed563067b82856ca5e7aa3437fa28d98d4350ea9c3d9494a'
docker run --rm -v C:\work\zkdeal\prover-node:/workspace -w /workspace `
  $nodeRef node zkvm/scripts/check-lock-freshness.mjs --prepare-build-input
docker run --rm -v C:\work\zkdeal\prover-node:/workspace:ro -w /workspace `
  $nodeRef node zkvm/scripts/check-lock-freshness.mjs --check-build-input
```

Then run through the deployment-tools container from
`cloud-deployer-infra`:

```powershell
docker compose -f compose/compose.tools.yaml run --rm deployment-tools scripts/source_bundle.py create --umbrella /workspace --output /workspace/cloud-deployer-infra/.state/bundles/zkdeal-source.tar.gz
docker compose -f compose/compose.tools.yaml run --rm deployment-tools scripts/source_bundle.py verify --archive /workspace/cloud-deployer-infra/.state/bundles/zkdeal-source.tar.gz --manifest /workspace/cloud-deployer-infra/.state/bundles/zkdeal-source.tar.gz.manifest.json --output /workspace/cloud-deployer-infra/.state/bundles/zkdeal-source.verification.json
docker compose -f compose/compose.tools.yaml run --rm deployment-tools scripts/source_bundle.py create-evidence --output /workspace/cloud-deployer-infra/.state/bundles/zkdeal-evidence.tar.gz
docker compose -f compose/compose.tools.yaml run --rm deployment-tools scripts/source_bundle.py verify --archive /workspace/cloud-deployer-infra/.state/bundles/zkdeal-evidence.tar.gz --manifest /workspace/cloud-deployer-infra/.state/bundles/zkdeal-evidence.tar.gz.manifest.json
```

The source verification report must show the app-node, prover-node,
web3-protocol, web2-api, and cloud-deployer-infra projects; all 13
release-critical source bindings; `historyIncluded:false`;
`secretsIncluded:false`; and concrete embedded-manifest/entries SHA-256 values.
File modes are part of the embedded manifest and are verified against the tar
headers.

Copy exactly these six files to a new incoming directory on the 4090 node:

- `zkdeal-source.tar.gz`
- `zkdeal-source.tar.gz.manifest.json`
- `zkdeal-source.verification.json`
- `zkdeal-evidence.tar.gz`
- `zkdeal-evidence.tar.gz.manifest.json`
- `cloud-deployer-infra/scripts/source_bundle.py`

Use the operator's approved transport. The archive manifest is the transfer
identity; do not infer identity from timestamps or filenames.

## Verify and extract on the target

The target must be timestamped, new, and empty. Do not use the stale
`C:\work\zkdeal` path. The extractor refuses a populated target and validates
every path, member type, size, and SHA-256 before extraction. The pinned Python
image below is the exact image recorded in `config/images.lock.json`.

```powershell
$transferStamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$targetName = "zkdeal-$transferStamp"
$pythonRef = 'python@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d'
if (Test-Path -LiteralPath "C:\work\$targetName") { throw "target already exists: C:\work\$targetName" }
docker run --rm -v C:\incoming\zkdeal:/incoming:ro $pythonRef python /incoming/source_bundle.py verify --archive /incoming/zkdeal-source.tar.gz --manifest /incoming/zkdeal-source.tar.gz.manifest.json
if ($LASTEXITCODE -ne 0) { throw 'source verification failed' }
docker run --rm -v C:\incoming\zkdeal:/incoming:ro $pythonRef python /incoming/source_bundle.py verify --archive /incoming/zkdeal-evidence.tar.gz --manifest /incoming/zkdeal-evidence.tar.gz.manifest.json
if ($LASTEXITCODE -ne 0) { throw 'evidence verification failed' }
docker run --rm -v C:\incoming\zkdeal:/incoming:ro -v C:\work:/target $pythonRef python /incoming/source_bundle.py extract --archive /incoming/zkdeal-source.tar.gz --manifest /incoming/zkdeal-source.tar.gz.manifest.json --target "/target/$targetName"
if ($LASTEXITCODE -ne 0) { throw 'source extraction failed' }
docker run --rm -v C:\incoming\zkdeal:/incoming:ro -v C:\work:/target $pythonRef python /incoming/source_bundle.py extract --archive /incoming/zkdeal-evidence.tar.gz --manifest /incoming/zkdeal-evidence.tar.gz.manifest.json --target "/target/$targetName/cloud-deployer-infra/imported-evidence"
if ($LASTEXITCODE -ne 0) { throw 'evidence extraction failed' }
```

Each `verify` result is the remote transfer closure: retain its JSON with both
the archive, outer-manifest, embedded-manifest, and entries SHA-256. Bind the
source-side report, remote result, critical source bindings, and timestamped
target path into the first deployment evidence record. The GPU node runs only
`--check-build-input`; it must not regenerate the candidate manifest after the
archive has been hashed.

Before staging any zkVM image, assert inside the pinned tools container that
the extracted tree has the candidate manifest and does not have the generated
lock, minted manifest, or `zkvm/build` directory. Preserve that negative check
with the source-closure record. After staging the exact orchestrator,
toolchain, and runtime digest refs, follow the owner 4090 runbook; do not
replace first-write `--bootstrap-lock` with an update of historical bytes.

Immediately before starting a GPU workload, replace the placeholder below with
the resolved release prover digest from the deployment lock and run the check
through that pinned container. Any listed compute process is a competing GPU
workload and blocks the start.

```powershell
$proverRef = 'zkdeal-risc0-cuda-runtime@sha256:<resolved-release-digest>'
$gpuUsers = docker run --rm --gpus all --entrypoint nvidia-smi $proverRef --query-compute-apps=pid,process_name,used_memory --format=csv,noheader
if ($LASTEXITCODE -ne 0 -or -not [string]::IsNullOrWhiteSpace($gpuUsers)) { throw "GPU is unavailable or busy: $gpuUsers" }
```

Re-run both `verify` commands against the retained incoming archives before
every rebuild. Run all validators and builds through containers on the 4090.
Do not overlay the bundle onto an existing workspace and do not add repository
history as part of the transfer.

## Default on-node OCI registry

No external registry credential is assumed. Start the pinned, non-GPU registry
inside the fresh candidate workspace; its named volume survives Compose and
Docker Desktop restarts. Set a candidate-specific volume name and never reuse a
candidate identifier from an earlier physical run.

```powershell
$candidateId = $targetName.ToLowerInvariant()
$env:OCI_REGISTRY_VOLUME = "zkdeal-registry-$candidateId"
$env:OCI_REGISTRY_PORT = '5000'
docker compose -f compose/compose.registry.yaml up -d --wait registry
if ($LASTEXITCODE -ne 0) { throw 'pinned local registry did not become healthy' }
```

Publish each locally built image through the socket-bearing orchestrator. The
script uses a short-lived transport tag because the Docker push protocol needs
one, refuses any existing candidate transport reference, removes the local tag,
pulls by digest, and writes only the `repository@sha256:...` identity to the
publication manifest. Mutable tags are never release evidence.

```powershell
$toolsId = docker image inspect zkdeal-deployment-tools:local --format '{{.Id}}'
docker compose -f compose/compose.tools.yaml --profile orchestrator run --rm deployment-orchestrator scripts/record_gate.py --name publish-coordinator-oci --runner-image zkdeal-deployment-tools:local --runner-image-id $toolsId --classification release --timeout 1800 -- python scripts/oci_registry.py publish --source zkdeal-coordinator:local --candidate $candidateId --artifact coordinator --registry localhost:5000 --output .state/registry/coordinator.publication.json
docker compose -f compose/compose.tools.yaml --profile orchestrator run --rm deployment-orchestrator scripts/record_gate.py --name verify-coordinator-oci --runner-image zkdeal-deployment-tools:local --runner-image-id $toolsId --classification release --timeout 600 --input-file .state/registry/coordinator.publication.json -- python scripts/oci_registry.py verify-image --manifest .state/registry/coordinator.publication.json
```

Repeat for the headless node, the distinct `zkdeal-prover-agent` artifact,
prover, docs, and every enabled dependency. The agent publication must retain
the owner source/capability labels emitted by `agent/Dockerfile`; a coordinator
digest is not a substitute. Copy
each immutable reference into the release lock and Helm/Compose inputs. An
external registry is an override, not a different trust model: authenticate the
Docker daemon separately and pass `--registry registry.example`; publication
and verification still require the digest reference.

Before registry-volume backup, stop the registry so its filesystem snapshot is
quiescent. The backup command emits a deterministic archive, per-member hashes,
outer SHA-256, and hashes of the referenced publication manifests. Restore is
allowed only into a new volume. Start a temporary pinned registry on the
restored volume, pull every artifact by its recorded digest, and retain daemon
inspection results in the release evidence closure.

```powershell
docker compose -f compose/compose.registry.yaml stop registry
docker compose -f compose/compose.tools.yaml --profile orchestrator run --rm deployment-orchestrator scripts/oci_registry.py backup --volume $env:OCI_REGISTRY_VOLUME --output .state/registry/registry.tar.gz --manifest .state/registry/registry.tar.gz.manifest.json --publication-manifest .state/registry/coordinator.publication.json
docker compose -f compose/compose.tools.yaml --profile orchestrator run --rm deployment-orchestrator scripts/oci_registry.py verify-backup --archive .state/registry/registry.tar.gz --manifest .state/registry/registry.tar.gz.manifest.json
docker compose -f compose/compose.tools.yaml --profile orchestrator run --rm deployment-orchestrator scripts/oci_registry.py restore --archive .state/registry/registry.tar.gz --manifest .state/registry/registry.tar.gz.manifest.json --volume "zkdeal-registry-restore-$candidateId" --output .state/registry/restore.json
```

Back up the registry archive and outer manifest outside the node as part of the
remote closure. Record both hashes, every immutable image reference, the fresh
restore-volume name, and the result of pulling each digest from the restored
registry. The local registry is intentionally deletion-disabled, but digest
identity—not tag immutability—is the release authority.

## Promote only after the source/generated composite seal

Candidate publication above is staging, not release promotion. After all
physical receipts and the 12-hour soak are complete, create and verify the
owner's `zkdeal/4090-evidence-closure/v2` composite. Mount an operator-owned
32-byte-or-longer MAC key read-only at `/run/secrets/oci-promotion-mac`; its
path must be outside the deployment, `.state`, and evidence trees, and its mode
must deny group/world access. Never pass the key bytes on a command line or add
the key file as an evidence input.

Promote each staged publication into a fresh release namespace. The command
contacts the registry to prove the target digest is absent, copies through a
short-lived transport tag, requires the pushed manifest digest and daemon image
ID to remain identical, removes that tag locally, and writes a content-bound
HMAC envelope. The receipt contains no key or mutable reference.

```powershell
docker compose -f compose/compose.tools.yaml --profile orchestrator run --rm `
  -v C:\operator-secrets\oci-promotion-mac:/run/secrets/oci-promotion-mac:ro `
  deployment-orchestrator scripts/record_gate.py `
  --name promote-prover-exact-digest --runner-image zkdeal-deployment-tools:local `
  --runner-image-id $toolsId --classification release --timeout 1800 `
  --input-file .state/candidates/$candidateId/candidate.json `
  --input-file .state/registry/prover.publication.json `
  --input-file .state/physical/evidence-closure.json -- `
  python scripts/oci_registry.py promote `
  --manifest .state/registry/prover.publication.json `
  --candidate-file .state/candidates/$candidateId/candidate.json `
  --composite-seal .state/physical/evidence-closure.json `
  --image-key prover --release "release-$candidateId" `
  --registry localhost:5000 --key-file /run/secrets/oci-promotion-mac `
  --output .state/registry/prover.promotion.json

docker compose -f compose/compose.tools.yaml --profile orchestrator run --rm `
  -v C:\operator-secrets\oci-promotion-mac:/run/secrets/oci-promotion-mac:ro `
  deployment-orchestrator scripts/record_gate.py `
  --name verify-prover-exact-promotion --runner-image zkdeal-deployment-tools:local `
  --runner-image-id $toolsId --classification release --timeout 900 `
  --input-file .state/candidates/$candidateId/candidate.json `
  --input-file .state/registry/prover.publication.json `
  --input-file .state/physical/evidence-closure.json `
  --input-file .state/registry/prover.promotion.json -- `
  python scripts/oci_registry.py verify-promotion `
  --receipt .state/registry/prover.promotion.json `
  --manifest .state/registry/prover.publication.json `
  --candidate-file .state/candidates/$candidateId/candidate.json `
  --composite-seal .state/physical/evidence-closure.json `
  --image-key prover --key-file /run/secrets/oci-promotion-mac `
  --output .state/registry/prover.promotion-verification.json
```

Repeat without rebuilding for every candidate image. Preserve one receipt per
image and include all receipt hashes, key ID, promoted immutable references,
the composite-seal hash, and the registry backup/retrieval hashes in WORM
evidence. The release verifier must retain the prover receipt as
`proverRuntimePublication`. Losing the external MAC key prevents new promotion
or re-verification; it never authorizes reconstructing a receipt.
