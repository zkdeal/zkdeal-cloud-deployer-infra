# soak6h: six-hour TEST soak tooling

```
==============================================================================
  THIS IS A TEST SOAK. IT IS NOT THE RELEASE GATE.

  The release gate is 43200 seconds (12 hours). Everything in this directory
  exists to run a 21600-second (6-hour) shakeout of the owner soak driver
  against a live stack.

  Nothing produced by a 6-hour run is release evidence. Do not stage it,
  publish it, or reference it from a candidate seal.

  REVERT REMINDER: relax-duration-floor.sh edits two files that are part of
  the release gate itself. Run

      sh cloud-deployer-infra/soak6h/relax-duration-floor.sh --revert

  as soon as the test soak is over, and always before any release-gate run.
  "--check" tells you the current state of the tree.
==============================================================================
```

## Contents

| File | What it does |
| --- | --- |
| `make-manifest.py` | Generates a schema-complete 21600-second soak manifest and self-validates it with the repository's own `scripts/soak.py:validate_manifest`. |
| `relax-duration-floor.sh` | Reversible TEST-ONLY patch that lowers the 43200-second duration floor to 21600 in `scripts/soak.py` and `soak-runner/zkdeal_soak.py` (and, with `--with-schema`, in the JSON Schema). Backs originals up to `*.release-floor.bak`. |
| `README.md` | This file. |

Nothing else in the repository is modified by these scripts except the two (or
three) floor-patched files, and those are restored byte for byte by `--revert`.

---

## What the owner soak driver actually does in 21600 seconds

This was checked against `cloud-deployer-infra/owner-soak-driver/zkdeal_owner_soak.py`
(`build_plan`, `Pacer`, `Workload`), not assumed.

**The driver derives its whole timeline from `manifest.durationSeconds`. There
are no hardcoded 12-hour offsets.** `build_plan` computes:

| Slot | Formula | 43200s (release) | 21600s (this test) |
| --- | --- | --- | --- |
| setup (9 rooms) | second 0 | 0 | 0 |
| pulse cycles | `PULSE_COUNT = 36`, interval `duration // 36` | 36 pulses every 1200s (0 .. 42000) | 36 pulses every **600s** (0 .. 21000) |
| aggregate cycles | `duration//12`, `duration//2`, `7*duration//8` | 3600 / 21600 / 37800 | **1800 / 10800 / 18900** |
| sponsor cycle | `duration // 3` | 14400 | **7200** |
| withdrawal cycle | `2*duration // 3` | 28800 | **14400** |
| scheduled faults | manifest `atSecond` | 8 faults | 8 faults (see below) |
| reconcile | `duration - 60` | 43140 | **21540** |

So the honest answer to "what will not fire in 6 hours" is: **nothing is
dropped**. `PULSE_COUNT = 36` and the three aggregate cycles are fixed *counts*,
not fixed offsets, so all 36 pulse cycles, all 3 aggregate cycles, the sponsor
cycle, the withdrawal cycle, every scheduled fault and the reconcile all still
run. The manifest `expected` block therefore has the *same* value at 6h as at
12h (see "Calibration" below).

**The real difference is density, and that is the risk.** The same physical
workload is compressed into half the wall clock. Counting the real CUDA proving
jobs from the driver source: 60 live prepare/prove/verify room chains (36 pulse
cycles plus 3 x 8 aggregate members), and per aggregate cycle 6
data-availability equivalence proofs and 1 recursive aggregate proof, so about
81 proving jobs plus their prepare and verify jobs. Concretely:

* `Pacer.run` executes slots strictly sequentially in `(second, order, key)`
  order and `wait_until`s each slot's second. It never skips or parallelises.
* A slot that starts more than `grace_seconds = 300` after its scheduled
  second increments `deadline_misses`. The slot still runs.
* Once one cycle overruns its interval, every later slot inherits the lag, so
  deadline misses accumulate monotonically for the rest of the run.
* `write_closure` raises if `deadline_misses > budgets.maxDeadlineMisses`.
  **That check happens only at the very end**, so an under-budgeted run burns
  the full six hours and then fails at closure.
* At 6 hours the pulse interval is 600s. If one pulse cycle (lease -> live
  prepare -> real CUDA Groth16 prove -> verify -> owner durable publish ->
  finalize -> charge) takes longer than ~600s on the 5090, the timeline cannot
  keep up. At 12 hours it had 1200s of room for the same work.
* `scripts/soak.py:run_owner` gives the owner command a hard subprocess
  timeout of `min(durationSeconds + 7200, 93600)` = **28800s (8 hours)** for a
  6-hour manifest. An overrun beyond 8 hours is SIGKILLed, not tolerated.

**Recommendation for the first 6-hour run:** measure the calibration run's wall
clock (it executes exactly one pulse, one aggregate, one sponsor and one
withdrawal cycle), then check

```
36 * t_pulse + 3 * t_aggregate + t_sponsor + t_withdraw + t_faults  <  21600
```

If that does not hold with margin, either keep the 12-hour duration or give the
test run headroom with `--max-deadline-misses` (see below). The generator
defaults `maxDeadlineMisses` to `0`, which is the release shape; for a first
TEST soak `--max-deadline-misses 12` is a reasonable, explicitly non-release
choice and is what the command sequence below uses.

### Default fault schedule (all 8 required kinds inside 0..21600)

| atSecond | kind | why here |
| --- | --- | --- |
| 2100 | `headless-restart` | after the first pulses, off the pulse grid |
| 3900 | `prover-restart` | after aggregate-0 (1800) has finalized |
| 5700 | `object-store-restart` | quiet stretch |
| 7500 | `database-restart` | just after the sponsor cycle (7200) |
| 9300 | `rpc-split` | before aggregate-1 (10800) |
| 11700 | `indexer-rollback` | needs a prior durable pulse operation; also carries the pre-finality reorg assertions |
| 13500 | `docker-host-restart-resume` | bracketed: aggregate-1 finalizes at ~10800 **before** it, aggregate-2 at 18900 **after** it |
| 16500 | `coordinator-promotion` | before aggregate-2, so the last aggregate runs on the promoted coordinator |

None of these offsets is a multiple of 600, so no fault shares a second with a
pulse cycle, and none collides with 1800 / 7200 / 10800 / 14400 / 18900 / 21540.
`make-manifest.py` prints an advisory if an override breaks any of that, and a
loud one if `docker-host-restart-resume` is placed where `build_plan` would have
to move an aggregate cycle to keep the restart bracketed.

Override with `--fault kind=second` (repeatable) or `--faults-file`.

### Calibration is duration-independent, but needs a fresh ledger

`owner-soak-driver/calibrate_expected.py` multiplies its measured per-cycle
usage/charges by `{pulse: 36, aggregate: 3, sponsor: 1, withdrawal: 1}` -- the
same fixed counts `build_plan` uses at any duration. **A calibration taken for
the 12-hour manifest is valid for the 6-hour manifest unchanged.**

Two things to be careful about:

* A full run journals 62 charge events: 36 pulse charges, 3 x (7 applied + 1
  stale pre-aggregate) = 24 aggregate charges, 1 sponsor, 1 withdrawal.
* `assert_expected` compares the **entire live billing ledger from cursor 0**
  against the soak's own journaled charge IDs. Calibration mints real ledger
  entries. **Run the soak against a freshly deployed stack with an empty
  ledger**, otherwise the calibration charges will make the soak fail at
  closure. Either calibrate on a throwaway stack and redeploy before the soak,
  or reuse a previously measured `expected` block.

---

## Operator command sequence (node: `sesterce@`, tree at `~/zkdeal-rc`)

All commands run from the umbrella root unless stated otherwise.

```sh
cd ~/zkdeal-rc
```

### 1. Inspect and apply the TEST-ONLY duration floor patch

```sh
sh cloud-deployer-infra/soak6h/relax-duration-floor.sh --check
sh cloud-deployer-infra/soak6h/relax-duration-floor.sh
```

If the checkout arrived with CRLF line endings, `sh` will reject the script with
something like `set: Illegal option`; fix it once with
`sed -i 's/\r$//' cloud-deployer-infra/soak6h/*.sh`. The patch itself tolerates
either line ending in the files it edits and preserves whatever it finds.

It prints which files it changed, where the backups are, and the recomputed
`SOAK_RUNNER_SOURCE_SHA256`. Add `--with-schema` if you also want
`config/schemas/release-soak-manifest.schema.json` to agree with the code (its
`minimum: 43200` is not enforced at runtime; the two Python files are).

### 2. Rebuild the soak-runner candidate image

The soak-runner image **bakes** `soak-runner/zkdeal_soak.py`, `scripts/common.py`,
`scripts/soak.py` and the schema, and binds their concatenated SHA-256 at build
time. Patching the floor without rebuilding changes nothing inside the
container.

```sh
# the owner driver image (built from the umbrella root)
OWNER_SHA="$(sha256sum cloud-deployer-infra/owner-soak-driver/zkdeal_owner_soak.py | awk '{print $1}')"
docker build -f cloud-deployer-infra/owner-soak-driver/Dockerfile \
  --build-arg "OWNER_SOAK_DRIVER_SOURCE_SHA256=$OWNER_SHA" \
  -t zkdeal-owner-soak-driver:test6h .

# the runner image (built from cloud-deployer-infra)
cd cloud-deployer-infra
RUNNER_SHA="$(cat soak-runner/zkdeal_soak.py scripts/common.py scripts/soak.py \
  config/schemas/release-soak-manifest.schema.json | sha256sum | awk '{print $1}')"
docker build --pull=false -f soak-runner/Dockerfile --target candidate \
  --build-arg "SOAK_RUNNER_SOURCE_SHA256=$RUNNER_SHA" \
  --build-arg "OWNER_SOAK_DRIVER_IMAGE=zkdeal-owner-soak-driver:test6h" \
  --build-arg "OWNER_SOAK_DRIVER_SOURCE_SHA256=$OWNER_SHA" \
  -t zkdeal-soak-runner:test6h .
cd ..
```

`relax-duration-floor.sh` prints `$RUNNER_SHA` for you after applying (and
again after reverting, so you can rebuild the release image back).

At run time the runner also requires `OWNER_SOAK_DRIVER_SOURCE_SHA256` and
`OWNER_SOAK_DRIVER_IMAGE_LABEL_SHA256` in the environment, both equal to
`$OWNER_SHA`.

### 3. Collect the six image digests

The manifest refuses tags; every role must be `repository@sha256:<64 hex>`.

```sh
digest() { docker image inspect "$1" --format '{{index .RepoDigests 0}}'; }
cat > /ephemeral/soak6h/images.json <<EOF
{
  "coordinator":          "$(digest zkdeal/coordinator:20260823-5090rc)",
  "indexer":              "$(digest zkdeal/coordinator:20260823-5090rc)",
  "reconciler":           "$(digest zkdeal/coordinator:20260823-5090rc)",
  "headless":             "$(digest zkdeal/headless-room-node:20260823-5090rc)",
  "prover":               "$(digest zkdeal/prover-cuda:sm120-20260823-5090rc)",
  "ownerAcceptanceRunner": "REPLACE_WITH_zkdeal-soak-runner:test6h@sha256:..."
}
EOF
```

A locally built image has no `RepoDigests` entry until it is pushed. Push
`zkdeal-soak-runner:test6h` to the local OCI registry on `:5000` and use the
digest the push reports, e.g.
`localhost:5000/zkdeal-soak-runner@sha256:...`. The same applies to the prover
runtime: `~/sm120-digests.env` already carries a `RUNTIME=` digest reference
that can be used verbatim.

### 4. Bring up a fresh stack and calibrate

Stack bring-up is out of scope for this directory. Once the stack is live and
the contracts are freshly deployed:

```sh
python cloud-deployer-infra/owner-soak-driver/calibrate_expected.py \
  --manifest /ephemeral/soak6h/manifest.seed.json \
  --work-dir /ephemeral/soak6h/calibrate \
  | tee /ephemeral/soak6h/calibration.json
```

`calibrate_expected.py` needs the driver's endpoint/token environment
(`COORDINATOR_URL`, `INDEXER_URL`, `QUEUE_URL`, `HEADLESS_URL`, `PROVER_URL`,
`L1_RPC_A`, `L1_RPC_B`, `ACCEPTANCE_FAULT_URL`, `ACCEPTANCE_BACKUP_URL`,
`FAILOVER_PROVIDER_URL`, `LOG_QUERY_URL` and the `SOAK_AUTH_*_TOKEN_FILE`
mounts) and a manifest to read `chainSeed.chainId` from. Generate that seed
manifest with step 5 using placeholder `--expected-usage-units 0
--expected-charges-wei 0`, then regenerate the real one from the calibration
output.

**Redeploy the stack after calibration** so the billing ledger is empty again.

### 5. Generate the 6-hour manifest

```sh
python cloud-deployer-infra/soak6h/make-manifest.py \
  --out /ephemeral/soak6h/soak-manifest.json \
  --provenance-out /ephemeral/soak6h/soak-manifest.provenance.json \
  --env-out /ephemeral/soak6h/soak-manifest.env \
  --images-file /ephemeral/soak6h/images.json \
  --chain-id 31337 \
  --genesis-hash-from-rpc "$L1_RPC_A" \
  --rpc-endpoint "$L1_RPC_A" --rpc-endpoint "$L1_RPC_B" \
  --owner-durable-capabilities-file /ephemeral/soak6h/owner-durable-capabilities.json \
  --owner-acceptance-token "sha256:<final hosted-integration acceptance token>" \
  --umbrella-source-manifest-file /ephemeral/soak6h/SOURCE-MANIFEST.json \
  --source-bundle-archive-file /ephemeral/soak6h/source-bundle.tar.gz \
  --source-closure-file /ephemeral/soak6h/source-closure.json \
  --expected-from-calibration /ephemeral/soak6h/calibration.json \
  --max-deadline-misses 12 \
  --force
```

Defaults it fills in for you, all overridable:

* `--duration-seconds 21600`
* `physicalScenario.settlementScenarioSha256` from
  `prover-node/zkvm/docker/release-settlement-scenario.json`
* `physicalScenario.deploymentAddressesSha256` from
  `web3-protocol/contracts/deployments/addresses.json`
* `trustRoots.zkvmArtifactsSha256` from `prover-node/zkvm/artifacts.lock.json`
* `trustRoots.circuitManifestSha256` from
  `web3-protocol/circuits/card-artifacts.lock.json`
* `trustRoots.contractsAbiSha256` as a closure over
  `deployments/room-manager.abi.json`, `deployments/room-pool.abi.json` and
  `deployments/contract-capabilities.generated.json`
* `trustRoots.generatedTrustRootClosureSha256` as a closure over the other
  three trust roots
* `chainSeed.seedSha256` derived from chainId + genesisHash + the RPC list
* the eight scheduled faults, `maxFairnessWaitMs 5000`, the four zero budgets

Every derivation recipe is printed into `--provenance-out` so it is
reproducible. If a real source/acceptance binding is not available for the test
run, `--test-binding-fallback` synthesizes deterministic, obviously-synthetic
placeholders for exactly the fields that are missing, lists them loudly on
stderr, and records them in the provenance file. **Never use a manifest that
had to fall back for anything but a test soak.**

The command prints `SOAK_MANIFEST_SHA256`; `--env-out` writes
`SOAK_MANIFEST_FILE`, `SOAK_MANIFEST_SHA256` and `SOAK_DURATION_SECONDS=21600`
in shell form, which is exactly the hash-bound contract the runner enforces via
`bound_file()`.

The generator re-runs `scripts/soak.py:validate_manifest` on its own output. If
the floor patch has not been applied yet it reports the duration-floor error
separately as `durationFloorPending: true` and still writes the manifest.

### 6. Owner command file

```sh
printf '["/opt/zkdeal-owner-soak"]\n' > /ephemeral/soak6h/owner-command.json
sha256sum /ephemeral/soak6h/owner-command.json
```

`zkdeal_soak.py:validate_owner_command` refuses anything whose argv[0] is not
`/opt/zkdeal-owner-soak`, and `bound_file` requires
`SOAK_OWNER_COMMAND_SHA256` to match this file.

### 7. Launch unattended

If a launcher script (`run-soak.sh`) is present in this directory, prefer it: it
owns the full environment contract end to end. The invocation below is the
minimal shape that contract has to produce.

The runner requires the exact reviewed env contract (`REQUIRE_REAL_PROOF_JOBS`,
`REQUIRE_FULL_LIFECYCLE`, `REQUIRE_INDUCED_RESTARTS`,
`REQUIRE_DURABLE_ASSERTIONS`, `REQUIRE_APPEND_ONLY_JOURNAL`,
`REQUIRE_RESTART_RESUME`) plus `SOAK_EVIDENCE_DIR` and the journal/state/closure
paths inside it. Run it under tmux or nohup so an SSH drop does not kill it:

```sh
tmux new-session -d -s soak6h \
  'docker run --rm --name zkdeal-soak6h \
     --env-file /ephemeral/soak6h/soak-runner.env \
     -v /ephemeral/soak6h/evidence:/evidence \
     zkdeal-soak-runner:test6h \
     --submit-real-proof-jobs --restart \
     --assert-durable-results,cursors,nonces,charges,sealed-output,safety,claims,fairness,deadlines \
     --bounded-backoff --emit-evidence-closure \
     >> /ephemeral/soak6h/soak6h.log 2>&1'
tmux ls
tail -f /ephemeral/soak6h/soak6h.log
```

Expect roughly 6 hours of wall clock, with a hard 8-hour subprocess ceiling. The
`docker-host-restart-resume` fault at t=13500 deliberately SIGKILLs the driver's
worker subprocess; the supervisor inside the same container respawns it. If you
also want to exercise a real container restart, restart the container after the
journal shows `fault docker-host-restart-resume` and re-run the same command --
`SOAK_STATE_FILE` already exists, so the runner resumes instead of restarting the
timeline.

### 8. Verify the closure

```sh
docker run --rm -v /ephemeral/soak6h/evidence:/evidence \
  zkdeal-soak-runner:test6h-tools \
  python /opt/zkdeal/soak.py verify \
    --manifest /evidence/soak-manifest.json --journal /evidence/soak.jsonl
```

(`scripts/soak.py` is container-only by `require_container()`; run it from any
first-party image that carries it, e.g. `zkdeal/deployment-tools`.) The runner
already writes a write-once `SOAK_CLOSURE_FILE` on success, so this is a second
independent read of the same journal.

### 9. REVERT THE FLOOR PATCH

```sh
sh cloud-deployer-infra/soak6h/relax-duration-floor.sh --revert
sh cloud-deployer-infra/soak6h/relax-duration-floor.sh --check   # expect RELEASE on every line
```

Then rebuild `zkdeal-soak-runner` with the `SOAK_RUNNER_SOURCE_SHA256` printed
by `--revert`, so no image with a relaxed floor survives, and delete or clearly
quarantine `/ephemeral/soak6h/evidence` so a 6-hour journal can never be mistaken
for release evidence.

---

## Known environment constraints

These are not bugs in the product; they are properties of the product or of the
container environment that a rig must respect. Each one cost a full bring-up
cycle to rediscover, so they are recorded here.

* **Two independent anvils can never satisfy the coordinator.** Critical L1
  reads require two independently identified providers that agree, and two
  anvils each mine their own chain, so their block hashes diverge immediately.
  The coordinator also rejects two identical URLs outright. `rpc-b` is therefore
  a TCP proxy onto `rpc-a`: separately addressed and separately stoppable, but
  serving one chain.
* **A standby coordinator must not receive signer configuration.** It holds no
  writer fence, so any L1 publisher it constructs aborts startup in
  `assertReady()`. Signer URL, address and token are validated as a complete
  trio, so all three must be cleared, not just the URL.
* **The indexer must be anchored at the deployment block.** `0x0` replays the
  deployment transactions, whose targets are not configured event sources, and
  every ingest cycle then fails. The rig reads `eth_blockNumber` after deploying
  and rewrites `INDEXER_BOOTSTRAP_BLOCK` before the workers start. The value is
  hexadecimal; a decimal `0` is rejected.
* **Web3Signer `/upcheck` 404s when asked for JSON.** It serves plain text, so a
  health probe that always sends `accept: application/json` reports a perfectly
  healthy signer as down.
* **nginx under a hardened profile.** The token map needs
  `map_hash_bucket_size` well above the 64-byte default because `eph_` bearer
  tokens are long keys; and with capabilities dropped and a read-only root the
  master cannot chown its cache, so it runs as the image's own uid with tmpfs
  for both `/var/cache/nginx` and `/run`.
* **Containers write as their own uid.** The foundry image and the headless
  secret-init both write into staged directories, so a rerun as the login user
  cannot always clean up after them. The orchestrator reclaims ownership before
  each bring-up, and staged inputs are readable by the uid that consumes them.
* **PostgreSQL hot standby needs parameter parity.** The standby entrypoint is
  baked into the fixture image with fixed settings, so the primary must not be
  started with a higher `max_connections` than the standby can match. The
  replication role password is also baked into that image.
* **`/ephemeral` is root-owned.** Give the work directory a user-owned parent,
  or the harness cannot manage its own scratch space. Docker's data-root *and*
  containerd's root both belong on the large disk; moving only `data-root`
  leaves the image store behind on `/`.

## Known risks and sharp edges

1. **The floor patch touches release-gate code.** Two of the four files the
   soak-runner image hashes are edited. Forgetting `--revert` silently weakens
   the release gate. `--check` is the one-command answer to "is this tree
   clean?".
2. **Deadline budget is only enforced at closure.** An under-budgeted 6-hour
   run fails after six hours, not early. Use `--max-deadline-misses` with
   headroom for a first run and treat the miss count as the primary test result.
3. **Density, not omission, is what 6 hours changes.** All 36 pulses and all 3
   aggregate cycles still run; they run twice as densely. If the stack cannot
   sustain that, the failure mode is deadline misses and possibly the 8-hour
   subprocess ceiling.
4. **The ledger must be empty at soak start.** `assert_expected` compares the
   whole live ledger against the soak journal. Calibration charges, or a reused
   stack, will fail the run at closure.
5. **Locally built images have no digest until pushed.** The manifest refuses
   tags, so `ownerAcceptanceRunner` (and any locally rebuilt role) must be
   pushed to the local registry on `:5000` first.
6. **Trust roots are derived, not ceremonial.** `contractsAbiSha256` and
   `generatedTrustRootClosureSha256` are computed here with a documented,
   reproducible recipe because this repository does not publish an authoritative
   closure file for them. That is adequate for a test soak and is *not* the
   release trust-root closure; supply the real digests explicitly for a release
   manifest.
7. **`--test-binding-fallback` produces shape-valid, meaningless digests.** It
   exists so an unattended test run is not blocked on release provenance. Any
   manifest that used it is permanently disqualified from release use, which is
   why it is recorded in the provenance sidecar and shouted on stderr.
