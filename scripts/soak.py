#!/usr/bin/env python3
"""Run or verify the durable owner-driven 12-hour release-soak protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from common import DeploymentError, atomic_write_json, require_container, sha256_file


SHA256 = re.compile(r"^[0-9a-f]{64}$")
IMAGE_DIGEST = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
REQUIRED_IMAGES = {
    "coordinator", "indexer", "reconciler", "headless", "prover", "ownerAcceptanceRunner",
}
REQUIRED_TRUST_ROOTS = {
    "contractsAbiSha256", "circuitManifestSha256", "zkvmArtifactsSha256",
    "generatedTrustRootClosureSha256",
}
REQUIRED_LIFECYCLE = {
    "room-create", "submit", "lease", "live-prepare", "prove", "verify",
    "blob-archive", "aggregate-settle", "sponsor", "reorg", "finalize",
    "withdraw", "reconcile",
}
REQUIRED_FAULTS = {
    "headless-restart", "prover-restart", "coordinator-promotion",
    "indexer-rollback", "rpc-split", "object-store-restart",
    "database-restart", "docker-host-restart-resume",
}
REQUIRED_ZERO_BUDGETS = {
    "maxUnresolvedSafetyEvents", "maxUnresolvedClaims",
    "maxDuplicateNonces", "maxDuplicateCharges",
}


def canonical_event_bytes(events: list[dict]) -> bytes:
    return b"".join(
        (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()
        for event in events
    )


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise DeploymentError(f"expected JSON object: {path}")
    return value


def validate_manifest(manifest: dict) -> None:
    errors: list[str] = []
    if manifest.get("schemaVersion") != 1 or manifest.get("kind") != "zkdeal-release-soak":
        errors.append("manifest must be zkdeal-release-soak schemaVersion 1")
    if int(manifest.get("durationSeconds", 0)) < 43_200:
        errors.append("release soak duration must be at least 43200 seconds (12 hours)")
    if not SHA256.fullmatch(str(manifest.get("umbrellaSourceManifestSha256", ""))):
        errors.append("umbrellaSourceManifestSha256 must be a concrete SHA-256")
    if not SHA256.fullmatch(str(manifest.get("sourceBundleArchiveSha256", ""))):
        errors.append("sourceBundleArchiveSha256 must be a concrete SHA-256")
    if not SHA256.fullmatch(str(manifest.get("sourceClosureSha256", ""))):
        errors.append("sourceClosureSha256 must be a concrete SHA-256")

    scenario = manifest.get("physicalScenario")
    if not isinstance(scenario, dict):
        errors.append("physicalScenario must be an object")
    else:
        for name in (
            "settlementScenarioSha256", "deploymentAddressesSha256",
            "ownerDurableCapabilitiesSha256",
        ):
            if not SHA256.fullmatch(str(scenario.get(name, ""))):
                errors.append(f"physicalScenario.{name} must be a concrete SHA-256")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(scenario.get("ownerAcceptanceToken", ""))):
            errors.append("physicalScenario.ownerAcceptanceToken must be a final sha256 acceptance token")
        exact = {
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
        for name, expected in exact.items():
            if scenario.get(name) != expected:
                errors.append(f"physicalScenario.{name} must equal {expected!r}")

    images = manifest.get("images")
    if not isinstance(images, dict):
        errors.append("images must be an object")
    else:
        missing = REQUIRED_IMAGES - set(images)
        if missing:
            errors.append(f"missing required images: {sorted(missing)}")
        for role, reference in images.items():
            if not isinstance(reference, str) or not IMAGE_DIGEST.fullmatch(reference):
                errors.append(f"image {role} must be repository@sha256:<64 hex>")

    roots = manifest.get("trustRoots")
    if not isinstance(roots, dict):
        errors.append("trustRoots must be an object")
    else:
        for name in REQUIRED_TRUST_ROOTS:
            if not SHA256.fullmatch(str(roots.get(name, ""))):
                errors.append(f"trust root {name} must be a concrete SHA-256")

    seed = manifest.get("chainSeed")
    if not isinstance(seed, dict):
        errors.append("chainSeed must be an object")
    else:
        if not isinstance(seed.get("chainId"), int) or seed["chainId"] < 1:
            errors.append("chainSeed.chainId must be a positive integer")
        if not re.fullmatch(r"0x[0-9a-fA-F]{64}", str(seed.get("genesisHash", ""))):
            errors.append("chainSeed.genesisHash must be a 32-byte hash")
        if not SHA256.fullmatch(str(seed.get("seedSha256", ""))):
            errors.append("chainSeed.seedSha256 must be a concrete SHA-256")
        endpoints = seed.get("rpcEndpoints")
        if not isinstance(endpoints, list) or len(endpoints) < 2 or not all(isinstance(item, str) and item.startswith(("http://", "https://")) for item in endpoints):
            errors.append("chainSeed.rpcEndpoints must contain at least two HTTP(S) endpoints")

    expected = manifest.get("expected")
    if not isinstance(expected, dict) or not isinstance(expected.get("usageUnits"), int) or expected.get("usageUnits", -1) < 0:
        errors.append("expected.usageUnits must be a non-negative integer")
    try:
        if int((expected or {}).get("chargesWei", -1)) < 0:
            errors.append("expected.chargesWei must be a non-negative integer string")
    except (TypeError, ValueError):
        errors.append("expected.chargesWei must be a non-negative integer string")

    budgets = manifest.get("budgets")
    if not isinstance(budgets, dict):
        errors.append("budgets must be an object")
    else:
        for name in REQUIRED_ZERO_BUDGETS:
            if budgets.get(name) != 0:
                errors.append(f"{name} must be zero")
        if not isinstance(budgets.get("maxFairnessWaitMs"), int) or budgets.get("maxFairnessWaitMs", 0) < 1:
            errors.append("maxFairnessWaitMs must be a positive integer")
        if not isinstance(budgets.get("maxDeadlineMisses"), int) or budgets.get("maxDeadlineMisses", -1) < 0:
            errors.append("maxDeadlineMisses must be a non-negative integer")

    faults = manifest.get("scheduledFaults")
    if not isinstance(faults, list):
        errors.append("scheduledFaults must be a list")
    else:
        kinds = {item.get("kind") for item in faults if isinstance(item, dict)}
        missing = REQUIRED_FAULTS - kinds
        if missing:
            errors.append(f"missing scheduled faults: {sorted(missing)}")
        for fault in faults:
            if not isinstance(fault, dict) or not isinstance(fault.get("atSecond"), int) or fault.get("atSecond", -1) < 0:
                errors.append("every scheduled fault requires a non-negative atSecond")
                break
    if errors:
        raise DeploymentError("; ".join(errors))


def load_journal(path: Path) -> list[dict]:
    events: list[dict] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise DeploymentError(f"journal line {line_number} is not an object")
        events.append(value)
    if not events:
        raise DeploymentError("soak journal is empty")
    for expected, event in enumerate(events, 1):
        if event.get("seq") != expected:
            raise DeploymentError(f"journal sequence is not contiguous at event {expected}")
    return events


def verify_closure(manifest_path: Path, journal_path: Path) -> dict:
    manifest = load_json(manifest_path)
    validate_manifest(manifest)
    events = load_journal(journal_path)
    if events[-1].get("kind") != "closure":
        raise DeploymentError("last journal event must be the closure")
    closure = events[-1]
    body = events[:-1]
    kinds = {event.get("kind") for event in body}
    missing_lifecycle = REQUIRED_LIFECYCLE - kinds
    if missing_lifecycle:
        raise DeploymentError(f"journal is missing lifecycle events: {sorted(missing_lifecycle)}")

    def one(kind: str) -> dict:
        matches = [event for event in body if event.get("kind") == kind]
        if not matches:
            raise DeploymentError(f"journal is missing {kind} evidence")
        return matches[-1]

    prepared = one("live-prepare")
    if (
        prepared.get("preparedFrom") != "live-room-engine-state"
        or prepared.get("batchInput") != "BatchInputV5"
        or prepared.get("fixture") is not False
    ):
        raise DeploymentError("live preparation is not a non-fixture BatchInputV5 artifact")
    proved = one("prove")
    if proved.get("realCuda") is not True or proved.get("proofMode") != "groth16":
        raise DeploymentError("prove event is not a real CUDA Groth16 proof")
    if one("verify").get("realReceipt") is not True:
        raise DeploymentError("verify event does not cover the real receipt")
    aggregate = one("aggregate-settle")
    if any(aggregate.get(name) != expected for name, expected in {
        "members": 8, "transactionBlobs": 6, "applied": 7, "failed": 1,
        "successfulCharges": 7, "failedCharges": 0,
    }.items()):
        raise DeploymentError("aggregate event is not the six-blob 7+1 partial-success case")
    if (
        aggregate.get("publisher") != "owner-durable-operation"
        or not isinstance(aggregate.get("ownerOperationId"), str)
        or not aggregate["ownerOperationId"]
        or aggregate.get("selector") != "0x5e8b37ac"
        or aggregate.get("transactionType") != 3
        or aggregate.get("finalizedCanonical") is not True
        or aggregate.get("castBroadcast") is not False
    ):
        raise DeploymentError("aggregate event bypasses the finalized owner durable type-3 operation")
    sponsored = one("sponsor")
    if (
        not isinstance(sponsored.get("payer"), str)
        or sponsored.get("payer") == sponsored.get("beneficiary")
        or sponsored.get("refundRecipient") != sponsored.get("payer")
        or sponsored.get("doubleBilled") is not False
    ):
        raise DeploymentError("sponsorship event does not prove payer refund ownership and no double billing")
    sponsor_operations = sponsored.get("ownerOperations")
    if (
        sponsored.get("publisher") != "owner-durable-operation"
        or sponsored.get("senderAuthoritySplit") is not True
        or not isinstance(sponsor_operations, dict)
        or any(not isinstance(sponsor_operations.get(name), str) or not sponsor_operations[name]
               for name in ("reserve", "renew", "checkpoint", "dispose"))
        or sponsored.get("selectors") != {
            "reserve": "0x827ac259",
            "renew": "0xf180fe5d",
            "checkpoint": "0xe19bc67e",
            "dispose": "0xed97f11a",
        }
        or sponsored.get("finalizedCanonical") is not True
    ):
        raise DeploymentError("sponsorship event lacks the split finalized owner durable pool operations")
    reorg = one("reorg")
    if (
        reorg.get("preFinalityOrphaned") is not True
        or reorg.get("canonicalRecovery") is not True
        or reorg.get("duplicateNonce") is not False
        or reorg.get("duplicateCharge") is not False
    ):
        raise DeploymentError("reorg event does not prove canonical duplicate-free recovery")
    withdrawal = one("withdraw")
    if (
        withdrawal.get("realProof") is not True
        or withdrawal.get("claimed") is not True
        or withdrawal.get("replayRejected") is not True
    ):
        raise DeploymentError("withdrawal event does not prove a real claim and rejected replay")
    if (
        withdrawal.get("publisher") != "owner-durable-operation"
        or not isinstance(withdrawal.get("ownerOperationId"), str)
        or not withdrawal["ownerOperationId"]
        or withdrawal.get("selector") != "0xb051a9f8"
        or withdrawal.get("finalizedCanonical") is not True
    ):
        raise DeploymentError("withdrawal event bypasses the finalized owner durable claim operation")

    scheduled = {item["kind"]: item["atSecond"] for item in manifest["scheduledFaults"]}
    fault_events = {event.get("fault"): event for event in body if event.get("kind") == "fault"}
    recovered = {event.get("fault") for event in body if event.get("kind") == "recovered"}
    for name, at_second in scheduled.items():
        event = fault_events.get(name)
        if event is None or event.get("scheduledAtSecond") != at_second:
            raise DeploymentError(f"fault {name} was not executed at its scheduled offset")
        if name not in recovered:
            raise DeploymentError(f"fault {name} has no recovery assertion")

    durable_fields = ("roomId", "jobId", "nonceId", "txHash", "eventId", "cursor")
    for field in durable_fields:
        if not any(isinstance(event.get(field), (str, int)) and str(event[field]) for event in body):
            raise DeploymentError(f"journal does not persist any {field}")

    broadcasts = [event for event in body if event.get("kind") == "tx-broadcast"]
    if any(
        event.get("publisher") != "owner-durable-operation"
        or not isinstance(event.get("operationId"), str)
        or not event["operationId"]
        for event in broadcasts
    ):
        raise DeploymentError("transaction broadcast bypasses the owner durable-operation boundary")
    nonces = [event["nonceId"] for event in broadcasts if "nonceId" in event]
    if len(nonces) != len(set(nonces)):
        raise DeploymentError("duplicate transaction nonce detected in journal")
    charges = [event["chargeId"] for event in body if event.get("kind") == "charge" and "chargeId" in event]
    if not charges or len(charges) != len(set(charges)):
        raise DeploymentError("missing or duplicate charge ID detected in journal")

    usage = sum(int(event.get("usageUnits", 0)) for event in body if event.get("kind") == "charge")
    charge_wei = sum(int(event.get("chargeWei", 0)) for event in body if event.get("kind") == "charge")
    if usage != manifest["expected"]["usageUnits"] or charge_wei != int(manifest["expected"]["chargesWei"]):
        raise DeploymentError("observed usage/charges do not match the manifest")

    seals: dict[str, str] = {}
    for event in body:
        output_id = event.get("outputId")
        digest = event.get("sealedOutputSha256")
        if output_id is None and digest is None:
            continue
        if not isinstance(output_id, str) or not SHA256.fullmatch(str(digest)):
            raise DeploymentError("sealed output events require outputId and SHA-256")
        previous = seals.setdefault(output_id, digest)
        if previous != digest:
            raise DeploymentError(f"sealed output changed after restart: {output_id}")
    if not seals or not any(event.get("kind") == "finalize" and event.get("outputId") in seals for event in body):
        raise DeploymentError("no finalized sealed output was re-verified")

    manifest_hash = sha256_file(manifest_path)
    prior_hash = hashlib.sha256(canonical_event_bytes(body)).hexdigest()
    required_closure = {
        "manifestSha256": manifest_hash,
        "priorEventsSha256": prior_hash,
        "sealedOutputsUnchanged": True,
        "resumeVerified": True,
        "unresolvedSafetyEvents": 0,
        "unresolvedClaims": 0,
        "duplicateNonces": 0,
        "duplicateCharges": 0,
        "fairnessBudgetMet": True,
        "deadlineBudgetMet": True,
    }
    for name, expected in required_closure.items():
        if closure.get(name) != expected:
            raise DeploymentError(f"closure assertion failed: {name}")
    if int(closure.get("durationSeconds", 0)) < int(manifest["durationSeconds"]):
        raise DeploymentError("closure duration is shorter than the release-soak duration")
    return {
        "passed": True,
        "manifestSha256": manifest_hash,
        "journalSha256": sha256_file(journal_path),
        "durationSeconds": closure["durationSeconds"],
        "events": len(events),
        "faults": sorted(fault_events),
        "sealedOutputs": len(seals),
        "usageUnits": usage,
        "chargesWei": str(charge_wei),
        "restartResumeVerified": True,
        "healthPollingOnly": False,
    }


def run_owner(manifest_path: Path, journal_path: Path, state_path: Path, command_path: Path, resume: bool) -> dict:
    manifest = load_json(manifest_path)
    validate_manifest(manifest)
    manifest_hash = sha256_file(manifest_path)
    if state_path.exists():
        state = load_json(state_path)
        if not resume:
            raise DeploymentError("state already exists; --resume is required")
        if state.get("manifestSha256") != manifest_hash:
            raise DeploymentError("resume manifest does not match persisted state")
    else:
        if resume:
            raise DeploymentError("--resume was requested but no durable state exists")
        state = {"schemaVersion": 1, "manifestSha256": manifest_hash, "attempts": 0, "status": "running"}
    command = json.loads(command_path.read_text(encoding="utf-8"))
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise DeploymentError("owner command file must contain a non-empty JSON argv array")
    if re.search(r"(^|[^a-z0-9_-])git(?:\.exe)?([^a-z0-9_-]|$)", " ".join(command), re.I):
        raise DeploymentError("repository-history commands are forbidden")
    state["attempts"] = int(state.get("attempts", 0)) + 1
    atomic_write_json(state_path, state)
    completed = subprocess.run(
        command,
        cwd=state_path.parent,
        env={
            **os.environ,
            "ZKDEAL_SOAK_MANIFEST": str(manifest_path),
            "ZKDEAL_SOAK_JOURNAL": str(journal_path),
            "ZKDEAL_SOAK_STATE": str(state_path),
            "ZKDEAL_SOAK_RESUME": "1" if resume else "0",
        },
        timeout=min(int(manifest["durationSeconds"]) + 7200, 93600),
        check=False,
    )
    if completed.returncode != 0:
        state["status"] = "owner-command-failed"
        state["ownerExitCode"] = completed.returncode
        atomic_write_json(state_path, state)
        raise DeploymentError(f"owner soak command exited {completed.returncode}")
    result = verify_closure(manifest_path, journal_path)
    state.update({"status": "passed", "closure": result})
    atomic_write_json(state_path, state)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--journal", required=True)
    run = commands.add_parser("run")
    run.add_argument("--manifest", required=True)
    run.add_argument("--journal", required=True)
    run.add_argument("--state", required=True)
    run.add_argument("--owner-command-file", required=True)
    run.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    try:
        require_container()
        if args.command == "verify":
            result = verify_closure(Path(args.manifest).resolve(), Path(args.journal).resolve())
        else:
            result = run_owner(
                Path(args.manifest).resolve(), Path(args.journal).resolve(),
                Path(args.state).resolve(), Path(args.owner_command_file).resolve(), args.resume,
            )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (DeploymentError, OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
