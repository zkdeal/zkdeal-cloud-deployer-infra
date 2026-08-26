#!/usr/bin/env python3
"""Container entrypoint for the durable owner-driven release soak.

This process owns resume/evidence mechanics and independent journal closure.
It deliberately refuses to invent lifecycle events: an exact hash-bound owner
driver must perform the live submit/prove/publish/fault work.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


sys.path.insert(0, "/opt/zkdeal")
from common import DeploymentError  # noqa: E402
from soak import load_json, run_owner, validate_manifest  # noqa: E402


SHA256 = __import__("re").compile(r"^[0-9a-f]{64}$")
REQUIRED_LIFECYCLE = (
    "room-create,submit,lease,live-prepare,prove,verify,blob-archive,"
    "aggregate-settle,sponsor,reorg,finalize,withdraw,reconcile"
)
REQUIRED_RESTARTS = (
    "headless-restart,prover-restart,coordinator-promotion,indexer-rollback,"
    "rpc-split,object-store-restart,database-restart,docker-host-restart-resume"
)
REQUIRED_DURABLE = (
    "source,image,trust-root,chain-seed,room,job,nonce,tx,event,cursor,usage,"
    "charge,sealed-output,safety,claim,fairness,deadline"
)


class SoakRunnerError(RuntimeError):
    pass


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="zkdeal-soak")
    value.add_argument("--submit-real-proof-jobs", action="store_true", required=True)
    value.add_argument("--restart", action="store_true", required=True)
    value.add_argument(
        "--assert-durable-results,cursors,nonces,charges,sealed-output,safety,claims,fairness,deadlines",
        dest="assert_durable", action="store_true", required=True,
    )
    value.add_argument("--bounded-backoff", action="store_true", required=True)
    value.add_argument("--emit-evidence-closure", action="store_true", required=True)
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bound_file(name: str, hash_name: str) -> Path:
    raw = os.environ.get(name, "")
    expected = os.environ.get(hash_name, "")
    path = Path(raw).resolve() if raw else Path()
    if not raw or path.is_symlink() or not path.is_file():
        raise SoakRunnerError(f"{name} is absent, not regular, or a symlink")
    if not SHA256.fullmatch(expected) or sha256_file(path) != expected:
        raise SoakRunnerError(f"{name} does not match {hash_name}")
    return path


def evidence_path(root: Path, name: str) -> Path:
    raw = os.environ.get(name, "")
    if not raw:
        raise SoakRunnerError(f"{name} is required")
    path = Path(raw).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SoakRunnerError(f"{name} must remain inside SOAK_EVIDENCE_DIR") from exc
    if path.is_symlink():
        raise SoakRunnerError(f"{name} must not be a symlink")
    return path


def require_runtime_contract() -> None:
    expected = {
        "REQUIRE_REAL_PROOF_JOBS": "1",
        "REQUIRE_FULL_LIFECYCLE": REQUIRED_LIFECYCLE,
        "REQUIRE_INDUCED_RESTARTS": REQUIRED_RESTARTS,
        "REQUIRE_DURABLE_ASSERTIONS": REQUIRED_DURABLE,
        "REQUIRE_APPEND_ONLY_JOURNAL": "1",
        "REQUIRE_RESTART_RESUME": "1",
    }
    for name, value in expected.items():
        if os.environ.get(name) != value:
            raise SoakRunnerError(f"{name} must equal the reviewed release-soak contract")


def validate_owner_command(path: Path) -> list[str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SoakRunnerError("owner soak command is not JSON") from exc
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise SoakRunnerError("owner soak command must be a nonempty JSON argv array")
    entrypoint = Path(value[0])
    if entrypoint != Path("/opt/zkdeal-owner-soak"):
        raise SoakRunnerError("owner soak command must use /opt/zkdeal-owner-soak")
    if entrypoint.is_symlink() or not entrypoint.is_file() or not os.access(entrypoint, os.X_OK):
        raise SoakRunnerError("the source-bound owner soak driver is absent or not executable")
    source_sha = os.environ.get("OWNER_SOAK_DRIVER_SOURCE_SHA256", "")
    observed = os.environ.get("OWNER_SOAK_DRIVER_IMAGE_LABEL_SHA256", "")
    if not SHA256.fullmatch(source_sha) or observed != source_sha:
        raise SoakRunnerError("owner soak driver source hash is absent or differs from its image label")
    return value


def write_exclusive(path: Path, value: dict[str, Any]) -> str:
    if path.exists() or path.is_symlink():
        raise SoakRunnerError(f"write-once evidence already exists: {path.name}")
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(payload).hexdigest()


def execute(_args: argparse.Namespace) -> dict[str, Any]:
    require_runtime_contract()
    root = Path(os.environ.get("SOAK_EVIDENCE_DIR", "/evidence")).resolve()
    if root.is_symlink() or not root.is_dir():
        raise SoakRunnerError("SOAK_EVIDENCE_DIR is absent, not a directory, or a symlink")
    manifest_path = bound_file("SOAK_MANIFEST_FILE", "SOAK_MANIFEST_SHA256")
    command_path = bound_file("SOAK_OWNER_COMMAND_FILE", "SOAK_OWNER_COMMAND_SHA256")
    validate_owner_command(command_path)
    manifest = load_json(manifest_path)
    validate_manifest(manifest)
    duration = int(os.environ.get("SOAK_DURATION_SECONDS", "0"))
    if duration != manifest.get("durationSeconds") or duration < 43_200:
        raise SoakRunnerError("SOAK_DURATION_SECONDS must equal the manifest and be at least 12 hours")

    journal_path = evidence_path(root, "SOAK_JOURNAL_FILE")
    state_path = evidence_path(root, "SOAK_STATE_FILE")
    closure_path = evidence_path(root, "SOAK_CLOSURE_FILE")
    resume = state_path.exists()
    try:
        result = run_owner(manifest_path, journal_path, state_path, command_path, resume)
    except DeploymentError as exc:
        raise SoakRunnerError(str(exc)) from exc
    closure = {
        "schemaVersion": 1,
        "passed": True,
        "classification": "real-owner-physical-soak",
        "manifestSha256": sha256_file(manifest_path),
        "ownerCommandSha256": sha256_file(command_path),
        "ownerDriverSourceSha256": os.environ["OWNER_SOAK_DRIVER_SOURCE_SHA256"],
        "journalSha256": sha256_file(journal_path),
        "stateSha256": sha256_file(state_path),
        "resumeUsed": resume,
        "verification": result,
    }
    digest = write_exclusive(closure_path, closure)
    return {**closure, "closure": str(closure_path), "closureSha256": digest}


def main(argv: list[str] | None = None) -> int:
    try:
        if not Path("/.dockerenv").exists() and os.environ.get("ZKDEAL_VERIFIED_CONTAINER") != "1":
            raise SoakRunnerError("soak runner execution is container-only")
        result = execute(parser().parse_args(argv))
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (SoakRunnerError, DeploymentError, OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
