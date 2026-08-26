#!/usr/bin/env python3
"""Run deployment conformance tests without invoking repository-history tools."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from common import DeploymentError, require_container

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    try:
        require_container()
    except DeploymentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"]
    completed = subprocess.run(command, cwd=ROOT, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
