#!/usr/bin/env python3
"""Create the local deployment state layout after validating all gates."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from common import (
    DeploymentError,
    ROOT,
    atomic_write_json,
    load_profile,
    profile_state_root,
    require_container,
    require_project_write_path,
    validate_profile,
)


DIRECTORIES = (
    "coordinator",
    "coordinator-standby",
    "queue",
    "postgres",
    "minio",
    "openbao-audit",
    "web3signer",
    "prometheus",
    "alertmanager",
    "loki",
    "promtail",
    "backups",
    "evidence",
    "restore",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        require_container()
        profile_path, profile = load_profile(args.profile)
        result = validate_profile(profile, check_artifacts=True)
        if not result.ok:
            raise DeploymentError("; ".join(result.errors))
        state_root = require_project_write_path(profile_state_root(profile))
        plan = [str(state_root / name) for name in DIRECTORIES]
        if args.dry_run:
            print(json.dumps({"profile": str(profile_path), "wouldCreate": plan}, indent=2))
            return 0
        for name in DIRECTORIES:
            (state_root / name).mkdir(parents=True, exist_ok=True)
        metadata = {
            "schemaVersion": 1,
            "profile": profile["name"],
            "environment": profile["environment"],
            "bootstrappedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "profilePath": str(profile_path.relative_to(ROOT)),
            "secretsRecorded": False,
        }
        atomic_write_json(state_root / "deployment-state.json", metadata)
        print(json.dumps(metadata, indent=2))
        return 0
    except DeploymentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
