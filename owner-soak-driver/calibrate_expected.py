#!/usr/bin/env python3
"""Calibrate the release-soak manifest `expected` block against a fresh stack.

Runs exactly one pulse cycle, one aggregate cycle, one sponsorship and one
withdrawal through the same Stack/Workload code the owner soak driver uses,
reads the exact per-cycle usageUnits/chargesWei deltas from the live billing
ledger, and prints the `expected` block for a full soak:

    36 * pulse + 3 * aggregate + 1 * sponsor + 1 * withdrawal

Requires the same endpoint/token environment the driver itself uses
(COORDINATOR_URL and friends plus SOAK_AUTH_*_TOKEN_FILE mounts). Scratch
journal/state files are written into --work-dir and are not release evidence.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path


def load_driver():
    path = Path(__file__).resolve().parent / "zkdeal_owner_soak.py"
    spec = importlib.util.spec_from_file_location("zkdeal_owner_soak", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    arguments = argparse.ArgumentParser(prog="calibrate-expected")
    arguments.add_argument("--manifest", required=True, help="release-soak manifest to calibrate against")
    arguments.add_argument("--work-dir", default="", help="scratch directory (default: a fresh temp dir)")
    args = arguments.parse_args(argv)
    driver = load_driver()
    try:
        import os

        environ = dict(os.environ)
        manifest_path = Path(args.manifest).resolve()
        manifest = driver.load_manifest(manifest_path)
        work_dir = Path(args.work_dir).resolve() if args.work_dir else Path(tempfile.mkdtemp(prefix="zkdeal-calibrate-"))
        work_dir.mkdir(parents=True, exist_ok=True)
        environ["ZKDEAL_SOAK_MANIFEST"] = str(manifest_path)
        environ["ZKDEAL_SOAK_JOURNAL"] = str(work_dir / "calibrate-journal.ndjson")
        clock = driver.Clock()
        journal = driver.Journal(work_dir / "calibrate-journal.ndjson")
        state = driver.DriverState(work_dir / "calibrate-state.json", driver.sha256_file(manifest_path))
        http = driver.Http(environ, clock)
        stack = driver.Stack(http, clock, environ, manifest)
        workload = driver.Workload(stack, journal, state, environ, manifest)

        # Mirror run_worker(): negotiating capabilities is the first thing a
        # driver boot does, and it is the only writer of Stack.addresses. Every
        # workload cycle except setup() performs a chain-first read that needs
        # one of those addresses, so a calibration that skips negotiation dies
        # on the first such read with a message about the address rather than
        # about the missing negotiation.
        stack.capabilities()

        workload.setup()
        pulse = workload.pulse(0)
        aggregate = workload.aggregate(0)
        sponsor = workload.sponsor()
        withdrawal = workload.withdraw()

        cycles = {"pulse": pulse, "aggregate": aggregate, "sponsor": sponsor, "withdrawal": withdrawal}
        multipliers = {"pulse": driver.PULSE_COUNT, "aggregate": 3, "sponsor": 1, "withdrawal": 1}
        usage = sum(multipliers[name] * int(cycle["usageUnits"]) for name, cycle in cycles.items())
        wei = sum(multipliers[name] * int(cycle["chargesWei"]) for name, cycle in cycles.items())
        print(json.dumps({
            "perCycle": {
                name: {"usageUnits": int(cycle["usageUnits"]), "chargesWei": str(cycle["chargesWei"])}
                for name, cycle in cycles.items()
            },
            "multipliers": multipliers,
            "expected": {"usageUnits": usage, "chargesWei": str(wei)},
        }, indent=2, sort_keys=True))
        return 0
    except driver.DriverError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
