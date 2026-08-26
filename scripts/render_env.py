#!/usr/bin/env python3
"""Render non-secret owner-service environment from a validated profile."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import DeploymentError, atomic_write, load_profile, require_container, validate_profile


def quote(value: object) -> str:
    text = str(value)
    if any(character.isspace() or character in "#'\"" for character in text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        require_container()
        _, profile = load_profile(args.profile)
        result = validate_profile(profile)
        if not result.ok:
            raise DeploymentError("; ".join(result.errors))
        chain = profile["chain"]
        values = {
            "CHAIN_ID": chain["id"],
            "L1_RPC_URL": chain["rpcUrls"][0],
            "L1_RPC_URLS": ",".join(chain["rpcUrls"]),
            "ROOM_MANAGER": chain["contracts"]["roomManager"],
            "ROOM_POOL": chain["contracts"]["roomPool"],
            "ACCESS_TOKEN": chain["contracts"]["accessToken"],
            "MAX_ARCHIVE_LAG_BLOCKS": chain["maximumArchiveLagBlocks"],
            "COORDINATOR_ROLE": "standalone" if profile["services"]["coordinator"]["replicas"] == 1 else "active",
            "CORS_ORIGINS": ",".join(profile["security"]["corsOrigins"]),
        }
        content = "\n".join(f"{key}={quote(value)}" for key, value in values.items()) + "\n"
        if args.output:
            atomic_write(Path(args.output), content.encode())
        else:
            sys.stdout.write(content)
        return 0
    except DeploymentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
