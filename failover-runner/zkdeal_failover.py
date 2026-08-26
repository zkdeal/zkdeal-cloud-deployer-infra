#!/usr/bin/env python3
"""Run and independently close a real provider-backed promotion rehearsal.

The deployment promotion controller owns ordering.  The acceptance runner owns
the adversarial stale-writer/LSN assertions.  This wrapper joins their exact
write-once records; it does not implement or simulate either service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


MAX_OUTPUT_BYTES = 4 * 1024 * 1024
CONTROLLER = Path("/opt/zkdeal/promotion_controller.py")
ACCEPTANCE = Path("/opt/zkdeal-acceptance")


class FailoverRunnerError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regular_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise FailoverRunnerError(f"{label} is absent, not regular, or a symlink")
    raw = path.read_bytes()
    if len(raw) > MAX_OUTPUT_BYTES:
        raise FailoverRunnerError(f"{label} exceeds {MAX_OUTPUT_BYTES} bytes")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FailoverRunnerError(f"{label} is not JSON") from exc
    if not isinstance(value, dict):
        raise FailoverRunnerError(f"{label} must contain a JSON object")
    return value


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="zkdeal-failover")
    value.add_argument("--terminate-active", action="store_true", required=True)
    value.add_argument("--persist-primary-target-lsn", action="store_true", required=True)
    value.add_argument("--assert-standby-replay", action="store_true", required=True)
    value.add_argument("--promote", action="store_true", required=True)
    value.add_argument("--assert-stale-writer-denied", action="store_true", required=True)
    value.add_argument("--assert-rto-seconds", type=int, required=True)
    return value


def require_container() -> None:
    if not Path("/.dockerenv").exists() and os.environ.get("ZKDEAL_VERIFIED_CONTAINER") != "1":
        raise FailoverRunnerError("failover runner execution is container-only")


def run_checked(
    command: list[str], timeout: int, label: str, *, environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout, check=False, env=environment,
    )
    if len(result.stdout) > MAX_OUTPUT_BYTES or len(result.stderr) > MAX_OUTPUT_BYTES:
        raise FailoverRunnerError(f"{label} output exceeded the evidence bound")
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace")[-2000:]
        raise FailoverRunnerError(f"{label} exited {result.returncode}: {detail}")
    return result


def validate_controller_state(value: dict[str, Any], rto_limit: int) -> None:
    required = {
        "status": "promotion-complete",
        "promoted": True,
        "oldWriterFenced": True,
        "databasePromoted": True,
        "ownerPromoted": True,
        "writerRouteCommitted": True,
        "signerActivatedOnlyAfterFence": True,
    }
    for name, expected in required.items():
        if value.get(name) != expected:
            raise FailoverRunnerError(f"promotion controller closure differs: {name}")
    rto = value.get("rtoSeconds")
    if not isinstance(rto, (int, float)) or isinstance(rto, bool) or rto < 0 or rto > rto_limit:
        raise FailoverRunnerError("promotion controller RTO is absent or over budget")
    if value.get("candidateId") != os.environ.get("PROMOTION_CANDIDATE_ID"):
        raise FailoverRunnerError("promotion controller state names another candidate")


def validate_acceptance_record(value: dict[str, Any], rto_limit: int) -> None:
    if (
        value.get("schemaVersion") != 1
        or value.get("passed") is not True
        or value.get("scenario") != "split-brain-promotion"
        or value.get("candidateId") != os.environ.get("PROMOTION_CANDIDATE_ID")
    ):
        raise FailoverRunnerError("split-brain acceptance record is absent or names another candidate")
    evidence = value.get("evidence")
    if not isinstance(evidence, dict):
        raise FailoverRunnerError("split-brain acceptance record lacks evidence")
    if evidence.get("fenced") is not True or evidence.get("promotedReady") is not True:
        raise FailoverRunnerError("split-brain acceptance did not prove fence and readiness")
    target = evidence.get("targetLsn")
    replay = evidence.get("replayLsn")
    if not isinstance(target, str) or not isinstance(replay, str):
        raise FailoverRunnerError("split-brain acceptance omitted target/replay LSN")
    rto = evidence.get("rtoSeconds")
    if not isinstance(rto, int) or isinstance(rto, bool) or rto < 0 or rto > rto_limit:
        raise FailoverRunnerError("split-brain acceptance RTO is absent or over budget")


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if not 30 <= args.assert_rto_seconds <= 300:
        raise FailoverRunnerError("--assert-rto-seconds must be between 30 and 300")
    for executable in (CONTROLLER, ACCEPTANCE):
        if executable.is_symlink() or not executable.is_file():
            raise FailoverRunnerError(f"required executable is absent: {executable}")
    evidence_dir = Path(os.environ.get("FAILOVER_EVIDENCE_DIR", "/evidence")).resolve()
    if evidence_dir.is_symlink() or not evidence_dir.is_dir():
        raise FailoverRunnerError("FAILOVER_EVIDENCE_DIR is absent, not a directory, or a symlink")
    state_path = Path(os.environ.get(
        "PROMOTION_CONTROLLER_STATE_PATH", str(evidence_dir / "promotion-state.json"),
    )).resolve()
    try:
        state_path.relative_to(evidence_dir)
    except ValueError as exc:
        raise FailoverRunnerError("promotion state must be persisted inside FAILOVER_EVIDENCE_DIR") from exc
    if state_path.exists():
        raise FailoverRunnerError("promotion state already exists; use a fresh evidence directory")

    provider_operation_url = os.environ.get("FAILOVER_PROVIDER_OPERATION_URL", "")
    if not provider_operation_url:
        raise FailoverRunnerError("FAILOVER_PROVIDER_OPERATION_URL is required")
    controller_environment = dict(os.environ)
    controller_environment["FAILOVER_PROVIDER_URL"] = provider_operation_url
    run_checked(
        [sys.executable, str(CONTROLLER)], args.assert_rto_seconds + 120,
        "promotion controller", environment=controller_environment,
    )
    controller = regular_json(state_path, "promotion controller state")
    validate_controller_state(controller, args.assert_rto_seconds)

    candidate = os.environ.get("PROMOTION_CANDIDATE_ID", "")
    acceptance_path = evidence_dir / f"{candidate}-split-brain-promotion.json"
    if acceptance_path.exists():
        raise FailoverRunnerError("split-brain acceptance output already exists")
    environment_evidence = os.environ.get("ACCEPTANCE_EVIDENCE_DIR")
    if environment_evidence and Path(environment_evidence).resolve() != evidence_dir:
        raise FailoverRunnerError("ACCEPTANCE_EVIDENCE_DIR must equal FAILOVER_EVIDENCE_DIR")
    os.environ["ACCEPTANCE_EVIDENCE_DIR"] = str(evidence_dir)
    run_checked([
        str(ACCEPTANCE), "split-brain-promotion", "--kill-active", "--assert-fence",
        "--assert-rto-seconds", str(args.assert_rto_seconds),
    ], args.assert_rto_seconds + 120, "split-brain acceptance")
    acceptance = regular_json(acceptance_path, "split-brain acceptance record")
    validate_acceptance_record(acceptance, args.assert_rto_seconds)

    output_path = evidence_dir / "failover-closure.json"
    if output_path.exists() or output_path.is_symlink():
        raise FailoverRunnerError("write-once failover closure already exists")
    closure = {
        "schemaVersion": 1,
        "passed": True,
        "classification": "real-provider-owner-platform",
        "candidateId": candidate,
        "promotionControllerStateSha256": sha256_file(state_path),
        "splitBrainAcceptanceSha256": sha256_file(acceptance_path),
        "activeTerminated": True,
        "primaryTargetPersisted": True,
        "standbyReplayAtOrAfterTarget": True,
        "staleWriterDenied": True,
        "promotedReady": True,
        "signerActivatedOnlyAfterFence": True,
        "maximumRtoSeconds": args.assert_rto_seconds,
    }
    payload = canonical_bytes(closure)
    with output_path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return {**closure, "closure": str(output_path), "closureSha256": hashlib.sha256(payload).hexdigest()}


def main(argv: list[str] | None = None) -> int:
    try:
        require_container()
        result = execute(parser().parse_args(argv))
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (FailoverRunnerError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
