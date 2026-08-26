#!/usr/bin/env python3
"""Print the rendered prover-agent readiness JavaScript for live replay."""

from __future__ import annotations

import sys

import yaml

from common import DeploymentError, require_container


def main() -> int:
    try:
        require_container()
        for document in yaml.safe_load_all(sys.stdin.read()):
            if not document or document.get("kind") != "Deployment":
                continue
            if not document.get("metadata", {}).get("name", "").endswith("-prover-agent"):
                continue
            container = document["spec"]["template"]["spec"]["containers"][0]
            command = container["readinessProbe"]["exec"]["command"]
            if len(command) != 3 or command[:2] != ["node", "-e"]:
                raise DeploymentError("rendered prover-agent readiness is not node -e")
            print(command[2])
            return 0
        raise DeploymentError("render has no prover-agent Deployment")
    except (DeploymentError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
