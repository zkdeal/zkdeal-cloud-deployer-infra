#!/usr/bin/env python3
"""Run one container-only gate and persist a tamper-evident evidence record."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

from common import (
    DeploymentError,
    ROOT,
    atomic_write,
    atomic_write_json,
    require_container,
    run_command,
    sha256_file,
)
from verify_artifacts import inventory


RUNTIME_PARTS = {
    ".git", ".state", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "__pycache__", "evidence", "node_modules",
}
SECRET_NAMES = {".env", ".env.local", ".env.production", "secrets.json"}
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
MAX_EXPLICIT_INPUT_BYTES = 16 * 1024 * 1024


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def source_manifest() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    # Prune runtime trees before descending into them.  A post-rglob filter
    # still walks multi-gigabyte acceptance state/evidence trees and can make
    # the evidence preflight slower than the gate it records.
    for directory, dirnames, filenames in os.walk(ROOT, topdown=True, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if name not in RUNTIME_PARTS)
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if filename in SECRET_NAMES or path.suffix == ".pyc":
                continue
            relative = path.relative_to(ROOT)
            rows.append({
                "path": relative.as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    return rows


def explicit_input_manifest(values: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[Path] = set()
    for value in values:
        path = Path(value)
        candidate = path if path.is_absolute() else ROOT / path
        if candidate.is_symlink():
            raise DeploymentError(f"explicit evidence input is a symlink: {value}")
        resolved = candidate.resolve()
        try:
            relative = resolved.relative_to(ROOT.resolve())
        except ValueError as exc:
            raise DeploymentError(f"explicit evidence input escaped the deployment project: {value}") from exc
        if resolved in seen:
            raise DeploymentError(f"duplicate explicit evidence input: {relative.as_posix()}")
        seen.add(resolved)
        if resolved.is_symlink() or not resolved.is_file():
            raise DeploymentError(f"explicit evidence input is absent, not regular, or a symlink: {relative.as_posix()}")
        size = resolved.stat().st_size
        if size > MAX_EXPLICIT_INPUT_BYTES:
            raise DeploymentError(
                f"explicit evidence input exceeds {MAX_EXPLICIT_INPUT_BYTES} bytes: {relative.as_posix()}"
            )
        rows.append({
            "path": relative.as_posix(),
            "bytes": size,
            "sha256": sha256_file(resolved),
        })
    return sorted(rows, key=lambda row: str(row["path"]))


def runner_identity(reference: str | None, supplied_id: str | None) -> dict[str, object]:
    identity: dict[str, object] = {
        "reference": reference,
        "id": supplied_id,
        "containerId": socket.gethostname(),
    }
    if supplied_id:
        return identity
    # Available only in the explicitly profiled socket-bearing orchestrator.
    inspected = run_command(
        ["docker", "container", "inspect", socket.gethostname(), "--format", "{{.Image}}"],
        timeout=15,
    )
    if inspected.returncode == 0 and inspected.stdout.strip():
        identity["id"] = inspected.stdout.strip()
    else:
        identity["inspectionError"] = inspected.stderr.strip()[:500]
    return identity


def reject_forbidden_command(command: list[str]) -> None:
    if not command:
        raise DeploymentError("a gate command is required after --")
    rendered = " ".join(command)
    if re.search(r"(^|[^a-z0-9_-])git(?:\.exe)?([^a-z0-9_-]|$)", rendered, re.I):
        raise DeploymentError("repository-history commands are forbidden")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--runner-image", required=True)
    parser.add_argument("--runner-image-id")
    parser.add_argument("--classification", choices=("static", "live", "release"), required=True)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--input-file", action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    try:
        require_container()
        if not SAFE_NAME.fullmatch(args.name):
            raise DeploymentError("gate name must be a safe lowercase identifier")
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        reject_forbidden_command(command)
        if args.timeout < 1 or args.timeout > 43_200:
            raise DeploymentError("timeout must be between 1 and 43200 seconds")

        started = utc_now()
        run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{args.name}"
        evidence_dir = ROOT / "evidence" / run_id
        evidence_dir.mkdir(parents=True, exist_ok=False)
        # Snapshot inputs before the command; generators may intentionally
        # change deployment outputs and those must not be mislabeled as input.
        sources = source_manifest()
        owners = inventory()
        explicit_inputs = explicit_input_manifest(args.input_file)
        timed_out = False
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=args.timeout,
                check=False,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except subprocess.TimeoutExpired as exc:
            timed_out = True

            def timeout_text(value: str | bytes | None) -> str:
                if value is None:
                    return ""
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="replace")
                return value

            stdout = timeout_text(exc.stdout)
            stderr = timeout_text(exc.stderr)
            stderr += f"\nERROR: gate exceeded explicit {args.timeout}s timeout\n"
            completed = subprocess.CompletedProcess(command, 124, stdout, stderr)
        explicit_inputs_stable = True
        explicit_inputs_after: list[dict[str, object]] = []
        try:
            explicit_inputs_after = explicit_input_manifest(args.input_file)
            if explicit_inputs_after != explicit_inputs:
                explicit_inputs_stable = False
                completed = subprocess.CompletedProcess(
                    command,
                    completed.returncode if completed.returncode != 0 else 70,
                    completed.stdout,
                    completed.stderr + "\nERROR: an explicit candidate input changed while the gate was running\n",
                )
        except DeploymentError as exc:
            explicit_inputs_stable = False
            completed = subprocess.CompletedProcess(
                command,
                completed.returncode if completed.returncode != 0 else 70,
                completed.stdout,
                completed.stderr + f"\nERROR: explicit candidate input post-run check failed: {exc}\n",
            )
        finished = utc_now()

        stdout_path = evidence_dir / "stdout.log"
        stderr_path = evidence_dir / "stderr.log"
        atomic_write(stdout_path, completed.stdout.encode("utf-8", errors="replace"))
        atomic_write(stderr_path, completed.stderr.encode("utf-8", errors="replace"))
        record = {
            "schemaVersion": 1,
            "gate": args.name,
            "classification": args.classification,
            "startedAt": started.isoformat(),
            "finishedAt": finished.isoformat(),
            "durationSeconds": round((finished - started).total_seconds(), 3),
            "command": command,
            "workingDirectory": "cloud-deployer-infra",
            "runner": runner_identity(args.runner_image, args.runner_image_id),
            "exitCode": completed.returncode,
            "passed": completed.returncode == 0,
            "timedOut": timed_out,
            "outputs": {
                "stdout": {
                    "path": stdout_path.relative_to(ROOT).as_posix(),
                    "bytes": stdout_path.stat().st_size,
                    "sha256": sha256_file(stdout_path),
                },
                "stderr": {
                    "path": stderr_path.relative_to(ROOT).as_posix(),
                    "bytes": stderr_path.stat().st_size,
                    "sha256": sha256_file(stderr_path),
                },
            },
            "inputs": {
                "deploymentSource": sources,
                "deploymentSourceManifestSha256": __import__("hashlib").sha256(
                    json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "explicitFiles": explicit_inputs,
                "explicitFilesPostRun": explicit_inputs_after,
                "explicitFilesStable": explicit_inputs_stable,
                "ownerArtifacts": owners,
            },
            "secretsRecorded": False,
            "repositoryHistoryRecorded": False,
        }
        record_path = evidence_dir / "record.json"
        atomic_write_json(record_path, record)
        record_hash = sha256_file(record_path)
        atomic_write(evidence_dir / "record.sha256", f"{record_hash}  record.json\n".encode())

        # Echo the captured output so interactive runs remain useful.
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        print(json.dumps({
            "evidence": evidence_dir.relative_to(ROOT).as_posix(),
            "recordSha256": record_hash,
            "exitCode": completed.returncode,
        }))
        return completed.returncode
    except (DeploymentError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
