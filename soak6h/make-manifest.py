#!/usr/bin/env python3
"""Generate a valid SIX-HOUR (21600s) zkdeal owner-soak manifest.

THIS IS A TEST-SOAK GENERATOR. The release gate is 43200 seconds (12 hours).
A 21600-second manifest is rejected by an unpatched
`cloud-deployer-infra/scripts/soak.py` and `soak-runner/zkdeal_soak.py`; see
`relax-duration-floor.sh` in this directory for the reversible TEST-ONLY floor
patch that must be reverted before any release gate.

The output is written to --out and is exactly the field set the release-soak
manifest schema allows (the schema is `additionalProperties: false` at the top
level and inside physicalScenario, so nothing extra is ever added to the
manifest itself). Generator provenance goes to the optional --provenance-out
sidecar instead.

Every SHA-256 input can be supplied in one of two ways:

  --<name>-sha256 HEX     use this exact 64-hex digest
  --<name>-file PATH      compute sha256 over the file bytes

Multi-file trust roots (--contracts-abi-file may be repeated) collapse to one
digest with a documented, reproducible closure recipe:

    lines = sorted("<sha256 of file>  <path as given>\\n")
    digest = sha256("".join(lines))

The generated trust-root closure, when not supplied explicitly, is:

    digest = sha256("contractsAbiSha256=<hex>\\n"
                    "circuitManifestSha256=<hex>\\n"
                    "zkvmArtifactsSha256=<hex>\\n")

The chain seed digest, when not supplied explicitly, is:

    digest = sha256("zkdeal-soak-chain-seed/v1\\nchainId=<id>\\n"
                    "genesisHash=<0x...>\\n" + "".join("rpc=<url>\\n" ...))

After building the manifest the generator re-validates it with the repository's
own `scripts/soak.py:validate_manifest` when that module is importable, so the
manifest is checked by the real gate code rather than by a copy of its rules.
The duration-floor error is reported separately because it is expected until
`relax-duration-floor.sh` has been applied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
HASH32_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

DEFAULT_DURATION = 21600
RELEASE_DURATION = 43200

# Fixed counts inside owner-soak-driver/zkdeal_owner_soak.py. They are counts,
# not offsets: the driver spreads them across manifest durationSeconds.
PULSE_COUNT = 36
AGGREGATE_CYCLES = 3

IMAGE_ROLES = (
    "coordinator", "indexer", "reconciler", "headless", "prover",
    "ownerAcceptanceRunner",
)

REQUIRED_FAULTS = (
    "headless-restart", "prover-restart", "coordinator-promotion",
    "indexer-rollback", "rpc-split", "object-store-restart",
    "database-restart", "docker-host-restart-resume",
)

# Offsets chosen for a 21600-second window. Rationale:
#   * none is a multiple of 600, so no fault shares a second with a pulse
#     (pulse interval at 6h is 21600 // 36 = 600);
#   * none collides with the aggregate cycles (1800 / 10800 / 18900), the
#     sponsor cycle (7200), the withdrawal cycle (14400) or reconcile (21540);
#   * indexer-rollback (11700) lands well after the first pulse, which is the
#     driver's precondition: it replays the most recent durable pulse
#     operation from driver state ("reorgReference");
#   * docker-host-restart-resume (13500) sits between the aggregate cycle at
#     10800 and the one at 18900, so a full aggregate finalizes both before
#     and after the SIGKILL/resume.
DEFAULT_FAULT_OFFSETS = {
    "headless-restart": 2100,
    "prover-restart": 3900,
    "object-store-restart": 5700,
    "database-restart": 7500,
    "rpc-split": 9300,
    "indexer-rollback": 11700,
    "docker-host-restart-resume": 13500,
    "coordinator-promotion": 16500,
}

# Exactly the physicalScenario literals that scripts/soak.py:validate_manifest
# demands (its `exact` dict) plus the four digest fields.
SCENARIO_CONSTANTS = {
    "hostedBatchInput": "BatchInputV5",
    "fixturePrepare": False,
    "realCudaProof": True,
    "aggregateMembers": 8,
    "transactionBlobs": 6,
    "partialSuccessApplied": 7,
    "partialSuccessFailed": 1,
    "successOnlyCharging": True,
    "withdrawalClaim": True,
    "sponsorship": True,
    "preFinalityReorg": True,
    "freshDeployment": True,
    "restartResume": True,
    "ownerDurablePublishing": True,
    "castEncodingOnly": True,
    "directBroadcastAllowed": False,
}

TOP_LEVEL_KEYS = frozenset({
    "schemaVersion", "kind", "durationSeconds", "umbrellaSourceManifestSha256",
    "sourceBundleArchiveSha256", "sourceClosureSha256", "physicalScenario",
    "images", "trustRoots", "chainSeed", "expected", "budgets",
    "scheduledFaults",
})

SCENARIO_KEYS = frozenset({
    "settlementScenarioSha256", "deploymentAddressesSha256",
    "ownerDurableCapabilitiesSha256", "ownerAcceptanceToken",
    *SCENARIO_CONSTANTS,
})

# Default source paths, relative to the repository root (~/zkdeal-rc on the
# node). Each one is overridable on the command line.
DEFAULT_PATHS = {
    "settlement_scenario": "prover-node/zkvm/docker/release-settlement-scenario.json",
    "deployment_addresses": "web3-protocol/contracts/deployments/addresses.json",
    "zkvm_artifacts": "prover-node/zkvm/artifacts.lock.json",
    "circuit_manifest": "web3-protocol/circuits/card-artifacts.lock.json",
}
DEFAULT_CONTRACTS_ABI = (
    "web3-protocol/contracts/deployments/room-manager.abi.json",
    "web3-protocol/contracts/deployments/room-pool.abi.json",
    "web3-protocol/contracts/deployments/contract-capabilities.generated.json",
)

TEST_BINDING_PREFIX = "zkdeal-test-soak-binding/v1"


class GeneratorError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# digest helpers
# ---------------------------------------------------------------------------


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def closure_digest(paths: list[Path], labels: list[str]) -> str:
    lines = sorted(
        "%s  %s\n" % (sha256_file(path), label)
        for path, label in zip(paths, labels)
    )
    return sha256_bytes("".join(lines).encode("ascii"))


def test_binding(field: str, duration: int) -> str:
    """Deterministic, obviously synthetic stand-in for a release binding."""
    return sha256_bytes(("%s:%s:%d" % (TEST_BINDING_PREFIX, field, duration)).encode("ascii"))


# ---------------------------------------------------------------------------
# input resolution
# ---------------------------------------------------------------------------


class Resolver:
    def __init__(self, root: Path, duration: int, allow_fallback: bool):
        self.root = root
        self.duration = duration
        self.allow_fallback = allow_fallback
        self.provenance: dict[str, Any] = {}
        self.synthesized: list[str] = []

    def resolve_path(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        return path.resolve()

    def digest(
        self,
        field: str,
        explicit: str | None,
        file_value: str | None,
        default_relative: str | None = None,
    ) -> str:
        if explicit:
            candidate = explicit.strip().lower()
            if not SHA256_RE.fullmatch(candidate):
                raise GeneratorError("%s: --...-sha256 must be 64 lowercase hex characters" % field)
            self.provenance[field] = {"source": "explicit", "sha256": candidate}
            return candidate
        chosen = file_value or default_relative
        if chosen:
            path = self.resolve_path(chosen)
            if path.is_file():
                value = sha256_file(path)
                self.provenance[field] = {
                    "source": "file", "path": str(path), "sha256": value,
                    "default": file_value is None,
                }
                return value
            if file_value:
                raise GeneratorError("%s: file not found: %s" % (field, path))
        if not self.allow_fallback:
            raise GeneratorError(
                "%s is unresolved: pass an explicit digest, or a readable file, "
                "or re-run with --test-binding-fallback (TEST SOAK ONLY)" % field
            )
        value = test_binding(field, self.duration)
        self.synthesized.append(field)
        self.provenance[field] = {
            "source": "test-binding-fallback", "sha256": value,
            "recipe": "sha256(\"%s:%s:%d\")" % (TEST_BINDING_PREFIX, field, self.duration),
            "releaseEvidence": False,
        }
        return value

    def closure(self, field: str, explicit: str | None, files: list[str], defaults: tuple[str, ...]) -> str:
        if explicit:
            candidate = explicit.strip().lower()
            if not SHA256_RE.fullmatch(candidate):
                raise GeneratorError("%s: --...-sha256 must be 64 lowercase hex characters" % field)
            self.provenance[field] = {"source": "explicit", "sha256": candidate}
            return candidate
        labels = list(files) if files else list(defaults)
        paths = [self.resolve_path(label) for label in labels]
        missing = [str(path) for path in paths if not path.is_file()]
        if missing:
            if files:
                raise GeneratorError("%s: file(s) not found: %s" % (field, ", ".join(missing)))
            if not self.allow_fallback:
                raise GeneratorError(
                    "%s is unresolved (missing %s): pass --contracts-abi-file/--...-sha256 "
                    "or re-run with --test-binding-fallback (TEST SOAK ONLY)"
                    % (field, ", ".join(missing))
                )
            value = test_binding(field, self.duration)
            self.synthesized.append(field)
            self.provenance[field] = {
                "source": "test-binding-fallback", "sha256": value,
                "missing": missing, "releaseEvidence": False,
            }
            return value
        value = closure_digest(paths, labels)
        self.provenance[field] = {
            "source": "file-closure",
            "recipe": "sha256 over sorted lines '<sha256>  <label>\\n'",
            "files": [
                {"label": label, "path": str(path), "sha256": sha256_file(path)}
                for label, path in zip(labels, paths)
            ],
            "sha256": value,
            "default": not files,
        }
        return value


def resolve_token(resolver: Resolver, explicit_token: str | None, explicit_hex: str | None,
                  file_value: str | None) -> str:
    if explicit_token:
        candidate = explicit_token.strip().lower()
        if not TOKEN_RE.fullmatch(candidate):
            raise GeneratorError("--owner-acceptance-token must be sha256:<64 lowercase hex>")
        resolver.provenance["ownerAcceptanceToken"] = {"source": "explicit", "token": candidate}
        return candidate
    digest = resolver.digest("ownerAcceptanceToken", explicit_hex, file_value)
    return "sha256:" + digest


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------


def load_images(images_file: str | None, overrides: list[str], root: Path) -> dict[str, str]:
    images: dict[str, str] = {}
    if images_file:
        path = Path(images_file)
        if not path.is_absolute():
            path = root / path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GeneratorError("--images-file could not be read: %s" % exc) from exc
        if isinstance(raw, dict) and isinstance(raw.get("images"), dict):
            raw = raw["images"]
        if not isinstance(raw, dict):
            raise GeneratorError("--images-file must be a JSON object of role -> repository@sha256:...")
        for role, reference in raw.items():
            if isinstance(reference, dict):
                reference = reference.get("reference") or reference.get("image") or ""
            if not isinstance(reference, str):
                raise GeneratorError("--images-file role %s is not a string reference" % role)
            images[str(role)] = reference.strip()
    for role in IMAGE_ROLES:
        env_name = "ZKDEAL_SOAK_IMAGE_" + re.sub(r"(?<!^)(?=[A-Z])", "_", role).upper()
        value = os.environ.get(env_name, "").strip()
        if value and role not in images:
            images[role] = value
    for item in overrides:
        if "=" not in item:
            raise GeneratorError("--image expects role=repository@sha256:<64 hex>, got %r" % item)
        role, _, reference = item.partition("=")
        images[role.strip()] = reference.strip()
    missing = [role for role in IMAGE_ROLES if not images.get(role)]
    if missing:
        raise GeneratorError(
            "missing image reference(s) for: %s (use --images-file, repeated --image role=ref, "
            "or ZKDEAL_SOAK_IMAGE_<ROLE> environment variables)" % ", ".join(sorted(missing))
        )
    bad = sorted(role for role, reference in images.items() if not IMAGE_RE.fullmatch(reference))
    if bad:
        raise GeneratorError(
            "image reference(s) must be repository@sha256:<64 hex>, tags are refused: %s"
            % ", ".join("%s=%s" % (role, images[role]) for role in bad)
        )
    return dict(sorted(images.items()))


# ---------------------------------------------------------------------------
# chain seed
# ---------------------------------------------------------------------------


def genesis_from_rpc(url: str, timeout: float) -> str:
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "eth_getBlockByNumber",
        "params": ["0x0", False],
    }).encode("ascii")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"content-type": "application/json", "accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - any transport failure is fatal here
        raise GeneratorError("--genesis-hash-from-rpc failed against %s: %s" % (url, exc)) from exc
    result = payload.get("result") if isinstance(payload, dict) else None
    value = result.get("hash") if isinstance(result, dict) else None
    if not isinstance(value, str) or not HASH32_RE.fullmatch(value):
        raise GeneratorError("--genesis-hash-from-rpc did not return a 32-byte block hash")
    return value.lower()


def chain_seed_digest(chain_id: int, genesis_hash: str, endpoints: list[str]) -> str:
    text = "zkdeal-soak-chain-seed/v1\nchainId=%d\ngenesisHash=%s\n" % (chain_id, genesis_hash)
    text += "".join("rpc=%s\n" % endpoint for endpoint in endpoints)
    return sha256_bytes(text.encode("ascii"))


# ---------------------------------------------------------------------------
# scheduled faults and timeline
# ---------------------------------------------------------------------------


def load_faults(faults_file: str | None, overrides: list[str], duration: int, root: Path) -> list[dict[str, Any]]:
    offsets = dict(DEFAULT_FAULT_OFFSETS)
    if faults_file:
        path = Path(faults_file)
        if not path.is_absolute():
            path = root / path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GeneratorError("--faults-file could not be read: %s" % exc) from exc
        if isinstance(raw, list):
            raw = {item.get("kind"): item.get("atSecond") for item in raw if isinstance(item, dict)}
        if not isinstance(raw, dict):
            raise GeneratorError("--faults-file must be an object or a list of {kind, atSecond}")
        for kind, at_second in raw.items():
            offsets[str(kind)] = at_second
    for item in overrides:
        if "=" not in item:
            raise GeneratorError("--fault expects kind=second, got %r" % item)
        kind, _, value = item.partition("=")
        offsets[kind.strip()] = value.strip()
    faults: list[dict[str, Any]] = []
    for kind, value in offsets.items():
        try:
            at_second = int(value)
        except (TypeError, ValueError) as exc:
            raise GeneratorError("fault %s: atSecond %r is not an integer" % (kind, value)) from exc
        if at_second < 0 or at_second > duration:
            raise GeneratorError(
                "fault %s: atSecond %d is outside the 0..%d soak window" % (kind, at_second, duration)
            )
        faults.append({"atSecond": at_second, "kind": kind})
    kinds = [fault["kind"] for fault in faults]
    missing = sorted(set(REQUIRED_FAULTS) - set(kinds))
    if missing:
        raise GeneratorError("scheduledFaults is missing required kind(s): %s" % ", ".join(missing))
    unknown = sorted(set(kinds) - set(REQUIRED_FAULTS))
    if unknown:
        raise GeneratorError(
            "scheduledFaults carries kind(s) the owner driver refuses: %s" % ", ".join(unknown)
        )
    if len(set(kinds)) != len(kinds):
        raise GeneratorError("scheduledFaults repeats a fault kind")
    return sorted(faults, key=lambda item: (item["atSecond"], item["kind"]))


def driver_timeline(duration: int, docker_at: int) -> dict[str, Any]:
    """Recompute owner-soak-driver build_plan offsets for this duration."""
    pulse_interval = max(1, duration // PULSE_COUNT)
    pulses = [index * pulse_interval for index in range(PULSE_COUNT)]
    aggregates = [duration // 12, duration // 2, (duration * 7) // 8]
    adjusted = False
    if not any(second < docker_at for second in aggregates):
        aggregates[0] = max(0, docker_at - 60)
        adjusted = True
    if not any(second > docker_at for second in aggregates):
        aggregates[-1] = min(duration - 60, docker_at + 60)
        adjusted = True
    return {
        "pulseInterval": pulse_interval,
        "pulses": pulses,
        "aggregates": sorted(aggregates),
        "aggregatesAdjusted": adjusted,
        "sponsor": duration // 3,
        "withdraw": (duration * 2) // 3,
        "reconcile": max(0, duration - 60),
    }


def timeline_warnings(duration: int, faults: list[dict[str, Any]]) -> list[str]:
    offsets = {fault["kind"]: fault["atSecond"] for fault in faults}
    docker_at = offsets["docker-host-restart-resume"]
    plan = driver_timeline(duration, docker_at)
    warnings: list[str] = []
    if plan["aggregatesAdjusted"]:
        warnings.append(
            "docker-host-restart-resume at %ds forced the driver to move an aggregate cycle "
            "so the restart stays bracketed; prefer an offset between %ds and %ds"
            % (docker_at, duration // 2, (duration * 7) // 8)
        )
    before = [second for second in plan["aggregates"] if second < docker_at]
    after = [second for second in plan["aggregates"] if second > docker_at]
    if not before or not after:
        warnings.append("docker-host-restart-resume is not bracketed by aggregate cycles")
    busy = {
        "sponsor": plan["sponsor"], "withdraw": plan["withdraw"], "reconcile": plan["reconcile"],
    }
    for index, second in enumerate(plan["aggregates"]):
        busy["aggregate-%d" % index] = second
    for kind, at_second in sorted(offsets.items()):
        for name, second in busy.items():
            if at_second == second:
                warnings.append(
                    "fault %s shares second %d with the %s cycle; the fault runs after it, "
                    "but the timeline has no slack there" % (kind, at_second, name)
                )
        if at_second in plan["pulses"]:
            warnings.append(
                "fault %s shares second %d with a pulse cycle; the fault runs after it, "
                "but the timeline has no slack there" % (kind, at_second)
            )
    if offsets["indexer-rollback"] < plan["pulseInterval"]:
        warnings.append(
            "indexer-rollback at %ds may precede a completed pulse; the driver requires a prior "
            "durable pulse operation" % offsets["indexer-rollback"]
        )
    return warnings


# ---------------------------------------------------------------------------
# manifest assembly and self-validation
# ---------------------------------------------------------------------------


def build_manifest(args: argparse.Namespace, resolver: Resolver) -> dict[str, Any]:
    duration = args.duration_seconds
    root = resolver.root

    scenario = {
        "settlementScenarioSha256": resolver.digest(
            "settlementScenarioSha256", args.settlement_scenario_sha256,
            args.settlement_scenario_file, DEFAULT_PATHS["settlement_scenario"],
        ),
        "deploymentAddressesSha256": resolver.digest(
            "deploymentAddressesSha256", args.deployment_addresses_sha256,
            args.deployment_addresses_file, DEFAULT_PATHS["deployment_addresses"],
        ),
        "ownerDurableCapabilitiesSha256": resolver.digest(
            "ownerDurableCapabilitiesSha256", args.owner_durable_capabilities_sha256,
            args.owner_durable_capabilities_file,
        ),
        "ownerAcceptanceToken": resolve_token(
            resolver, args.owner_acceptance_token, args.owner_acceptance_token_sha256,
            args.owner_acceptance_token_file,
        ),
    }
    scenario.update(SCENARIO_CONSTANTS)

    trust_roots = {
        "contractsAbiSha256": resolver.closure(
            "contractsAbiSha256", args.contracts_abi_sha256,
            args.contracts_abi_file, DEFAULT_CONTRACTS_ABI,
        ),
        "circuitManifestSha256": resolver.digest(
            "circuitManifestSha256", args.circuit_manifest_sha256,
            args.circuit_manifest_file, DEFAULT_PATHS["circuit_manifest"],
        ),
        "zkvmArtifactsSha256": resolver.digest(
            "zkvmArtifactsSha256", args.zkvm_artifacts_sha256,
            args.zkvm_artifacts_file, DEFAULT_PATHS["zkvm_artifacts"],
        ),
    }
    if args.generated_trust_root_closure_sha256 or args.generated_trust_root_closure_file:
        trust_roots["generatedTrustRootClosureSha256"] = resolver.digest(
            "generatedTrustRootClosureSha256", args.generated_trust_root_closure_sha256,
            args.generated_trust_root_closure_file,
        )
    else:
        text = "".join(
            "%s=%s\n" % (name, trust_roots[name])
            for name in ("contractsAbiSha256", "circuitManifestSha256", "zkvmArtifactsSha256")
        )
        value = sha256_bytes(text.encode("ascii"))
        trust_roots["generatedTrustRootClosureSha256"] = value
        resolver.provenance["generatedTrustRootClosureSha256"] = {
            "source": "derived-closure",
            "recipe": "sha256 over 'contractsAbiSha256=..\\ncircuitManifestSha256=..\\nzkvmArtifactsSha256=..\\n'",
            "sha256": value,
        }

    endpoints = [item.strip() for item in args.rpc_endpoint if item.strip()]
    if len(endpoints) < 2:
        raise GeneratorError("at least two --rpc-endpoint values are required (two independent providers)")
    if len(set(endpoints)) != len(endpoints):
        raise GeneratorError("--rpc-endpoint values must be independent; duplicates are refused")
    for endpoint in endpoints:
        if not endpoint.startswith(("http://", "https://")):
            raise GeneratorError("--rpc-endpoint %s must be an http:// or https:// URL" % endpoint)

    if args.genesis_hash:
        genesis = args.genesis_hash.strip().lower()
        if not HASH32_RE.fullmatch(genesis):
            raise GeneratorError("--genesis-hash must be 0x followed by 64 hex characters")
        resolver.provenance["genesisHash"] = {"source": "explicit", "genesisHash": genesis}
    elif args.genesis_hash_from_rpc:
        genesis = genesis_from_rpc(args.genesis_hash_from_rpc, args.rpc_timeout_seconds)
        resolver.provenance["genesisHash"] = {
            "source": "eth_getBlockByNumber", "endpoint": args.genesis_hash_from_rpc,
            "genesisHash": genesis,
        }
    else:
        raise GeneratorError("pass --genesis-hash 0x<64 hex> or --genesis-hash-from-rpc <url>")

    if args.seed_sha256 or args.seed_file:
        seed = resolver.digest("chainSeedSha256", args.seed_sha256, args.seed_file)
    else:
        seed = chain_seed_digest(args.chain_id, genesis, endpoints)
        resolver.provenance["chainSeedSha256"] = {
            "source": "derived-chain-seed",
            "recipe": "sha256('zkdeal-soak-chain-seed/v1\\nchainId=..\\ngenesisHash=..\\nrpc=..\\n')",
            "sha256": seed,
        }

    if args.expected_from_calibration:
        path = Path(args.expected_from_calibration)
        if not path.is_absolute():
            path = root / path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GeneratorError("--expected-from-calibration could not be read: %s" % exc) from exc
        block = raw.get("expected") if isinstance(raw, dict) else None
        if not isinstance(block, dict):
            raise GeneratorError("--expected-from-calibration has no 'expected' object")
        usage_units = block.get("usageUnits")
        charges_wei = block.get("chargesWei")
        resolver.provenance["expected"] = {"source": "calibration", "path": str(path)}
    else:
        usage_units = args.expected_usage_units
        charges_wei = args.expected_charges_wei
        resolver.provenance["expected"] = {"source": "explicit"}
    if usage_units is None or charges_wei is None:
        raise GeneratorError(
            "expected usage/charges are required: pass --expected-usage-units and "
            "--expected-charges-wei, or --expected-from-calibration <calibrate_expected.py output>"
        )
    try:
        usage_units = int(usage_units)
    except (TypeError, ValueError) as exc:
        raise GeneratorError("expected.usageUnits must be an integer") from exc
    charges_wei = str(charges_wei).strip()
    if usage_units < 0:
        raise GeneratorError("expected.usageUnits must be non-negative")
    if not re.fullmatch(r"[0-9]+", charges_wei):
        raise GeneratorError("expected.chargesWei must be a non-negative integer string")

    faults = load_faults(args.faults_file, args.fault, duration, root)

    manifest = {
        "schemaVersion": 1,
        "kind": "zkdeal-release-soak",
        "durationSeconds": duration,
        "umbrellaSourceManifestSha256": resolver.digest(
            "umbrellaSourceManifestSha256", args.umbrella_source_manifest_sha256,
            args.umbrella_source_manifest_file,
        ),
        "sourceBundleArchiveSha256": resolver.digest(
            "sourceBundleArchiveSha256", args.source_bundle_archive_sha256,
            args.source_bundle_archive_file,
        ),
        "sourceClosureSha256": resolver.digest(
            "sourceClosureSha256", args.source_closure_sha256, args.source_closure_file,
        ),
        "physicalScenario": scenario,
        "images": load_images(args.images_file, args.image, root),
        "trustRoots": trust_roots,
        "chainSeed": {
            "chainId": args.chain_id,
            "genesisHash": genesis,
            "seedSha256": seed,
            "rpcEndpoints": endpoints,
        },
        "expected": {"usageUnits": usage_units, "chargesWei": charges_wei},
        "budgets": {
            "maxUnresolvedSafetyEvents": 0,
            "maxUnresolvedClaims": 0,
            "maxDuplicateNonces": 0,
            "maxDuplicateCharges": 0,
            "maxFairnessWaitMs": args.max_fairness_wait_ms,
            "maxDeadlineMisses": args.max_deadline_misses,
        },
        "scheduledFaults": faults,
    }
    return manifest


def structural_check(manifest: dict[str, Any]) -> list[str]:
    """Enforce the schema's additionalProperties:false key sets locally."""
    errors: list[str] = []
    keys = set(manifest)
    if keys != TOP_LEVEL_KEYS:
        for name in sorted(TOP_LEVEL_KEYS - keys):
            errors.append("manifest is missing required key %s" % name)
        for name in sorted(keys - TOP_LEVEL_KEYS):
            errors.append("manifest carries key %s that the schema forbids" % name)
    scenario_keys = set(manifest.get("physicalScenario", {}))
    if scenario_keys != SCENARIO_KEYS:
        for name in sorted(SCENARIO_KEYS - scenario_keys):
            errors.append("physicalScenario is missing required key %s" % name)
        for name in sorted(scenario_keys - SCENARIO_KEYS):
            errors.append("physicalScenario carries key %s that the schema forbids" % name)
    return errors


def repository_validate(manifest: dict[str, Any], root: Path) -> tuple[list[str], list[str], str]:
    """Run scripts/soak.py:validate_manifest; split off the duration-floor error."""
    scripts = root / "cloud-deployer-infra" / "scripts"
    if not (scripts / "soak.py").is_file():
        return [], [], "unavailable (cloud-deployer-infra/scripts/soak.py not found)"
    saved = list(sys.path)
    saved_bytecode = sys.dont_write_bytecode
    # Never leave a __pycache__ entry behind in the repository checkout.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(scripts))
    try:
        import importlib

        soak = importlib.import_module("soak")
        importlib.reload(soak)
        try:
            soak.validate_manifest(manifest)
        except Exception as exc:  # noqa: BLE001 - DeploymentError joins all errors
            parts = [item.strip() for item in str(exc).split(";") if item.strip()]
            floor = [item for item in parts if "duration must be at least" in item]
            other = [item for item in parts if item not in floor]
            return other, floor, "ran cloud-deployer-infra/scripts/soak.py:validate_manifest"
        return [], [], "ran cloud-deployer-infra/scripts/soak.py:validate_manifest"
    except Exception as exc:  # noqa: BLE001 - import problems must not be fatal
        return [], [], "unavailable (%s)" % exc
    finally:
        sys.path[:] = saved
        sys.dont_write_bytecode = saved_bytecode


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="make-manifest.py",
        description="Generate a 6-hour (21600s) TEST soak manifest for the zkdeal owner soak driver.",
    )
    parser.add_argument("--out", required=True, help="manifest output path")
    parser.add_argument("--force", action="store_true", help="overwrite an existing --out")
    parser.add_argument("--repo-root", default="", help="repository root (default: two levels above this script)")
    parser.add_argument("--duration-seconds", type=int, default=DEFAULT_DURATION,
                        help="soak duration; default %d (6 hours)" % DEFAULT_DURATION)
    parser.add_argument("--test-binding-fallback", action="store_true",
                        help="TEST SOAK ONLY: synthesize any unresolved source/acceptance binding "
                             "digest deterministically instead of failing")
    parser.add_argument("--provenance-out", default="",
                        help="optional sidecar JSON recording where every digest came from")
    parser.add_argument("--env-out", default="",
                        help="optional shell env file with SOAK_MANIFEST_FILE/SHA256 and SOAK_DURATION_SECONDS")
    parser.add_argument("--quiet", action="store_true", help="suppress the human-readable report")

    source = parser.add_argument_group("source bindings")
    source.add_argument("--umbrella-source-manifest-sha256")
    source.add_argument("--umbrella-source-manifest-file")
    source.add_argument("--source-bundle-archive-sha256")
    source.add_argument("--source-bundle-archive-file")
    source.add_argument("--source-closure-sha256")
    source.add_argument("--source-closure-file")

    scenario = parser.add_argument_group("physical scenario")
    scenario.add_argument("--settlement-scenario-sha256")
    scenario.add_argument("--settlement-scenario-file",
                          help="default: " + DEFAULT_PATHS["settlement_scenario"])
    scenario.add_argument("--deployment-addresses-sha256")
    scenario.add_argument("--deployment-addresses-file",
                          help="default: " + DEFAULT_PATHS["deployment_addresses"])
    scenario.add_argument("--owner-durable-capabilities-sha256")
    scenario.add_argument("--owner-durable-capabilities-file")
    scenario.add_argument("--owner-acceptance-token", help="sha256:<64 hex>")
    scenario.add_argument("--owner-acceptance-token-sha256")
    scenario.add_argument("--owner-acceptance-token-file")

    images = parser.add_argument_group("images (all six roles must be repository@sha256:<64 hex>)")
    images.add_argument("--images-file", help="JSON object role -> reference, or {\"images\": {...}}")
    images.add_argument("--image", action="append", default=[], metavar="ROLE=REFERENCE")

    roots = parser.add_argument_group("trust roots")
    roots.add_argument("--contracts-abi-sha256")
    roots.add_argument("--contracts-abi-file", action="append", default=[],
                       help="repeatable; default: " + ", ".join(DEFAULT_CONTRACTS_ABI))
    roots.add_argument("--circuit-manifest-sha256")
    roots.add_argument("--circuit-manifest-file", help="default: " + DEFAULT_PATHS["circuit_manifest"])
    roots.add_argument("--zkvm-artifacts-sha256")
    roots.add_argument("--zkvm-artifacts-file", help="default: " + DEFAULT_PATHS["zkvm_artifacts"])
    roots.add_argument("--generated-trust-root-closure-sha256")
    roots.add_argument("--generated-trust-root-closure-file")

    chain = parser.add_argument_group("chain seed")
    chain.add_argument("--chain-id", type=int, default=31337)
    chain.add_argument("--genesis-hash")
    chain.add_argument("--genesis-hash-from-rpc", help="JSON-RPC URL; reads block 0 hash")
    chain.add_argument("--rpc-timeout-seconds", type=float, default=10.0)
    chain.add_argument("--seed-sha256")
    chain.add_argument("--seed-file")
    chain.add_argument("--rpc-endpoint", action="append", default=[],
                       help="repeatable; at least two independent providers")

    expected = parser.add_argument_group("expected results (from calibration)")
    expected.add_argument("--expected-usage-units", type=int)
    expected.add_argument("--expected-charges-wei")
    expected.add_argument("--expected-from-calibration",
                          help="JSON printed by owner-soak-driver/calibrate_expected.py")

    budgets = parser.add_argument_group("budgets")
    budgets.add_argument("--max-fairness-wait-ms", type=int, default=5000)
    budgets.add_argument("--max-deadline-misses", type=int, default=0,
                         help="release shape is 0; a first 6h TEST run usually wants headroom")

    faults = parser.add_argument_group("scheduled faults")
    faults.add_argument("--fault", action="append", default=[], metavar="KIND=SECOND")
    faults.add_argument("--faults-file", help="JSON object kind -> second, or list of {kind, atSecond}")
    return parser


def report(manifest: dict[str, Any], resolver: Resolver, out_path: Path,
           manifest_sha: str, validator_note: str, warnings: list[str]) -> None:
    duration = manifest["durationSeconds"]
    offsets = {item["kind"]: item["atSecond"] for item in manifest["scheduledFaults"]}
    plan = driver_timeline(duration, offsets["docker-host-restart-resume"])
    lines = [
        "",
        "=" * 78,
        "ZKDEAL 6-HOUR TEST SOAK MANIFEST",
        "=" * 78,
        "manifest              : %s" % out_path,
        "SOAK_MANIFEST_SHA256  : %s" % manifest_sha,
        "durationSeconds       : %d (release gate is %d)" % (duration, RELEASE_DURATION),
        "validator             : %s" % validator_note,
        "",
        "Driver timeline derived from durationSeconds (owner soak driver build_plan):",
        "  pulse cycles        : %d, every %ds (first %ds, last %ds)"
        % (PULSE_COUNT, plan["pulseInterval"], plan["pulses"][0], plan["pulses"][-1]),
        "  aggregate cycles    : %d at %s" % (
            AGGREGATE_CYCLES, ", ".join("%ds" % second for second in plan["aggregates"])),
        "  sponsor cycle       : %ds" % plan["sponsor"],
        "  withdrawal cycle    : %ds" % plan["withdraw"],
        "  reconcile           : %ds" % plan["reconcile"],
        "",
        "Scheduled faults:",
    ]
    for item in manifest["scheduledFaults"]:
        lines.append("  %6ds  %s" % (item["atSecond"], item["kind"]))
    lines += [
        "",
        "expected.usageUnits   : %d" % manifest["expected"]["usageUnits"],
        "expected.chargesWei   : %s" % manifest["expected"]["chargesWei"],
        "budgets.maxDeadlineMisses : %d" % manifest["budgets"]["maxDeadlineMisses"],
        "budgets.maxFairnessWaitMs : %d" % manifest["budgets"]["maxFairnessWaitMs"],
    ]
    if resolver.synthesized:
        lines += [
            "",
            "!" * 78,
            "TEST-BINDING FALLBACK USED for: %s" % ", ".join(sorted(resolver.synthesized)),
            "These digests are deterministic placeholders, NOT release provenance.",
            "They are shape-valid only. Never reuse this manifest for a release gate.",
            "!" * 78,
        ]
    if warnings:
        lines += ["", "Timeline advisories:"]
        lines += ["  - " + item for item in warnings]
    lines += [
        "",
        "REMINDER: this manifest is only accepted after relax-duration-floor.sh has",
        "lowered the 43200s floor. Revert that patch before any release gate.",
        "=" * 78,
        "",
    ]
    sys.stderr.write("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parents[2]
        if not (root / "cloud-deployer-infra").is_dir():
            raise GeneratorError(
                "repository root %s does not contain cloud-deployer-infra; pass --repo-root" % root
            )
        if args.duration_seconds < 1:
            raise GeneratorError("--duration-seconds must be positive")
        if args.duration_seconds >= RELEASE_DURATION:
            sys.stderr.write(
                "NOTE: --duration-seconds %d is at or above the release floor; this generator "
                "is intended for the %ds TEST soak.\n" % (args.duration_seconds, DEFAULT_DURATION)
            )
        if args.max_fairness_wait_ms < 1:
            raise GeneratorError("--max-fairness-wait-ms must be a positive integer")
        if args.max_deadline_misses < 0:
            raise GeneratorError("--max-deadline-misses must be non-negative")

        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = Path.cwd() / out_path
        out_path = out_path.resolve()
        if out_path.exists() and not args.force:
            raise GeneratorError("%s already exists; pass --force to overwrite" % out_path)

        resolver = Resolver(root, args.duration_seconds, args.test_binding_fallback)
        manifest = build_manifest(args, resolver)

        errors = structural_check(manifest)
        if errors:
            raise GeneratorError("; ".join(errors))
        blocking, floor, validator_note = repository_validate(manifest, root)
        if blocking:
            raise GeneratorError("manifest rejected by the repository validator: " + "; ".join(blocking))

        payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="ascii")
        manifest_sha = sha256_bytes(payload.encode("ascii"))

        warnings = timeline_warnings(args.duration_seconds, manifest["scheduledFaults"])
        if floor:
            warnings.append(
                "the duration floor is still %ds in this checkout (%s); run "
                "relax-duration-floor.sh before starting the soak" % (RELEASE_DURATION, floor[0])
            )

        if args.provenance_out:
            provenance_path = Path(args.provenance_out)
            if not provenance_path.is_absolute():
                provenance_path = Path.cwd() / provenance_path
            provenance = {
                "schemaVersion": 1,
                "kind": "zkdeal-test-soak-manifest-provenance",
                "releaseEvidence": False,
                "note": "TEST soak only; the release gate requires a 43200s manifest with real "
                        "source/acceptance bindings.",
                "manifestPath": str(out_path),
                "manifestSha256": manifest_sha,
                "durationSeconds": args.duration_seconds,
                "repositoryRoot": str(root),
                "generator": str(Path(__file__).resolve()),
                "validator": validator_note,
                "durationFloorErrors": floor,
                "timelineWarnings": warnings,
                "synthesizedBindings": sorted(resolver.synthesized),
                "driverTimeline": driver_timeline(
                    args.duration_seconds,
                    {item["kind"]: item["atSecond"] for item in manifest["scheduledFaults"]}
                    ["docker-host-restart-resume"],
                ),
                "digests": resolver.provenance,
            }
            provenance_path.parent.mkdir(parents=True, exist_ok=True)
            provenance_path.write_text(
                json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="ascii",
            )

        if args.env_out:
            env_path = Path(args.env_out)
            if not env_path.is_absolute():
                env_path = Path.cwd() / env_path
            env_path.parent.mkdir(parents=True, exist_ok=True)
            env_path.write_text(
                "# generated by cloud-deployer-infra/soak6h/make-manifest.py (TEST SOAK)\n"
                "SOAK_MANIFEST_FILE=%s\n"
                "SOAK_MANIFEST_SHA256=%s\n"
                "SOAK_DURATION_SECONDS=%d\n" % (out_path, manifest_sha, args.duration_seconds),
                encoding="ascii",
            )

        if not args.quiet:
            report(manifest, resolver, out_path, manifest_sha, validator_note, warnings)
        print(json.dumps({
            "manifest": str(out_path),
            "manifestSha256": manifest_sha,
            "durationSeconds": args.duration_seconds,
            "scheduledFaults": manifest["scheduledFaults"],
            "synthesizedBindings": sorted(resolver.synthesized),
            "durationFloorPending": bool(floor),
            "releaseEvidence": False,
        }, indent=2, sort_keys=True))
        return 0
    except GeneratorError as exc:
        sys.stderr.write("ERROR: %s\n" % exc)
        return 1
    except OSError as exc:
        sys.stderr.write("ERROR: %s\n" % exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
