#!/usr/bin/env python3
"""Plan or apply deployment-metadata migrations.

Owner-service data migrations are intentionally not duplicated here. This
script records only deployment-layout revisions and reports when an owner
migration hook is required.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from common import DeploymentError, atomic_write_json, load_profile, profile_state_root, require_container, require_project_write_path


CURRENT_LAYOUT = 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        require_container()
        _, profile = load_profile(args.profile)
        state_root = require_project_write_path(profile_state_root(profile))
        marker = state_root / "deployment-schema.json"
        current = 0
        if marker.exists():
            current = int(json.loads(marker.read_text(encoding="utf-8"))["layoutVersion"])
        if current > CURRENT_LAYOUT:
            raise DeploymentError(f"deployment layout {current} is newer than supported {CURRENT_LAYOUT}")
        plan = {
            "profile": profile["name"],
            "currentLayout": current,
            "targetLayout": CURRENT_LAYOUT,
            "steps": [] if current == CURRENT_LAYOUT else ["record-layout-v1"],
            "ownerDataMigration": "none-advertised; owner hooks must be supplied by a capability manifest",
        }
        if not args.apply:
            print(json.dumps(plan, indent=2))
            return 0
        state_root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(marker, {
            "schemaVersion": 1,
            "layoutVersion": CURRENT_LAYOUT,
            "profile": profile["name"],
            "appliedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "ownerDataModified": False,
        })
        plan["applied"] = True
        print(json.dumps(plan, indent=2))
        return 0
    except (DeploymentError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
