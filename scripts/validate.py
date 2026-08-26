#!/usr/bin/env python3
"""Validate a deployment profile and fail closed on production policy."""

from __future__ import annotations

import argparse
import json
import sys

from common import DeploymentError, load_profile, require_container, validate_profile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--check-owner-artifacts", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        require_container()
        path, profile = load_profile(args.profile)
        result = validate_profile(profile, check_artifacts=args.check_owner_artifacts)
    except DeploymentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    payload = {
        "profile": str(path),
        "valid": result.ok,
        "errors": list(result.errors),
        "warnings": list(result.warnings),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"profile: {path}")
        for warning in result.warnings:
            print(f"WARNING: {warning}")
        for error in result.errors:
            print(f"ERROR: {error}")
        print("VALID" if result.ok else "INVALID")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
