#!/usr/bin/env python3
"""Inventory owner artifacts without modifying their projects."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import (
    DeploymentError,
    atomic_write_json,
    resolve_artifacts,
    require_container,
    sha256_file,
    sha256_tree,
)


def inventory() -> dict:
    umbrella, artifacts = resolve_artifacts()
    rows = []
    valid = True
    for artifact in artifacts:
        path = (umbrella / artifact["path"]).resolve()
        row = {**artifact, "resolvedPath": str(path), "exists": path.exists()}
        if path.is_file():
            row.update({"sha256": sha256_file(path), "files": 1, "bytes": path.stat().st_size})
        elif path.is_dir():
            digest, count, total = sha256_tree(path)
            row.update({"sha256": digest, "files": count, "bytes": total})
        elif artifact.get("required"):
            valid = False
        rows.append(row)
    return {"schemaVersion": 1, "umbrellaRoot": str(umbrella), "valid": valid, "artifacts": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", help="optional JSON output inside this deployment project")
    args = parser.parse_args()
    try:
        require_container()
        result = inventory()
        if args.output:
            atomic_write_json(Path(args.output), result)
        print(json.dumps(result, indent=2))
        return 0 if result["valid"] else 1
    except DeploymentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
