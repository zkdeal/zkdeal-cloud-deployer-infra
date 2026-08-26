#!/usr/bin/env python3
"""Collect reproducible deployment evidence without repository-history data."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import sys
from pathlib import Path

from common import (
    DeploymentError,
    ROOT,
    atomic_write_json,
    load_json,
    load_profile,
    profile_state_root,
    require_container,
    run_command,
    sha256_file,
    validate_profile,
)
from smoke import run_checks
from verify_artifacts import inventory


def image_references(profile: dict) -> dict[str, str]:
    override_path = profile["images"].get("overrides")
    if override_path:
        return dict(load_json(ROOT / override_path)["overrides"])
    manifest = load_json(ROOT / profile["images"]["manifest"])
    refs = {}
    for name, value in manifest["images"].items():
        if value.get("digest"):
            refs[name] = f"{value['repository']}@{value['digest']}"
    return refs


def inspect_image(reference: str) -> dict:
    completed = run_command(["docker", "image", "inspect", reference], timeout=30)
    if completed.returncode != 0:
        return {"reference": reference, "found": False, "error": completed.stderr.strip()[:500]}
    raw = json.loads(completed.stdout)[0]
    return {
        "reference": reference,
        "found": True,
        "id": raw.get("Id"),
        "repoDigests": raw.get("RepoDigests") or [],
        "repoTags": raw.get("RepoTags") or [],
        "created": raw.get("Created"),
        "os": raw.get("Os"),
        "architecture": raw.get("Architecture"),
    }


def file_evidence() -> list[dict]:
    roots = [ROOT / "compose", ROOT / "helm", ROOT / "kurtosis", ROOT / "observability", ROOT / "front-door"]
    rows = []
    for root in roots:
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            rows.append({
                "path": path.relative_to(ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    return rows


def runtime_versions() -> dict:
    docker = run_command(["docker", "version", "--format", "{{json .}}"], timeout=30)
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "docker": json.loads(docker.stdout) if docker.returncode == 0 and docker.stdout.strip() else None,
        "dockerError": None if docker.returncode == 0 else docker.stderr.strip()[:500],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output")
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--queue-url", default="http://127.0.0.1:3005")
    parser.add_argument("--skip-http", action="store_true")
    parser.add_argument("--require-images", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        require_container()
        profile_path, profile = load_profile(args.profile)
        checked = validate_profile(profile, check_artifacts=True)
        references = image_references(profile)
        if args.dry_run:
            print(json.dumps({
                "profile": str(profile_path),
                "wouldInspectImages": references,
                "wouldProbeHttp": not args.skip_http,
                "wouldHashDeploymentFiles": True,
                "wouldHashOwnerArtifacts": True,
            }, indent=2))
            return 0
        images = {name: inspect_image(reference) for name, reference in references.items()}
        if args.require_images and (not images or any(not value["found"] for value in images.values())):
            raise DeploymentError("one or more required local images are absent")
        smoke = None if args.skip_http else run_checks(args.base_url, args.queue_url, 5.0)
        evidence = {
            "schemaVersion": 1,
            "capturedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "profile": profile,
            "profilePath": str(profile_path),
            "profileValidation": {"valid": checked.ok, "errors": checked.errors, "warnings": checked.warnings},
            "runtime": runtime_versions(),
            "images": images,
            "ownerArtifacts": inventory(),
            "deploymentFiles": file_evidence(),
            "smoke": smoke,
            "secretsRecorded": False,
            "repositoryHistoryRecorded": False,
        }
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = Path(args.output).resolve() if args.output else ROOT / "evidence" / f"deployment-{timestamp}.json"
        atomic_write_json(output, evidence)
        print(json.dumps({"output": str(output), "images": images, "smokePassed": smoke is None or all(item["ok"] for item in smoke)}, indent=2))
        return 0 if checked.ok and (smoke is None or all(item["ok"] for item in smoke)) else 1
    except (DeploymentError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
