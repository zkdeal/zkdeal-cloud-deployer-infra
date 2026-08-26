#!/usr/bin/env python3
"""Exercise public and private health/security gates without mutating state."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass

from common import DeploymentError, load_profile, require_container


@dataclass(frozen=True)
class Probe:
    name: str
    url: str
    expected: int
    contains: str | None = None


def probes(base_url: str, queue_url: str) -> list[Probe]:
    return [
        Probe("public-health", f"{base_url}/health", 200, "status"),
        Probe("public-config", f"{base_url}/config", 200, "chainId"),
        Probe("front-door-hides-metrics", f"{base_url}/metrics", 404),
        Probe("front-door-hides-admission", f"{base_url}/admission/v1/pending", 404),
        Probe("operator-docs", f"{base_url}/docs/", 200, "operator reference"),
        Probe("queue-health", f"{queue_url}/health", 200, "status"),
        Probe("queue-metrics", f"{queue_url}/metrics", 200, "zkdeal_queue_jobs_waiting"),
    ]


def execute(probe: Probe, timeout: float) -> dict:
    request = urllib.request.Request(probe.url, headers={"User-Agent": "zkdeal-deployment-smoke/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            code = response.status
            body = response.read(1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        code = exc.code
        body = exc.read(64 * 1024).decode("utf-8", "replace")
    except OSError as exc:
        return {"name": probe.name, "url": probe.url, "ok": False, "error": str(exc)}
    ok = code == probe.expected and (probe.contains is None or probe.contains in body)
    return {
        "name": probe.name,
        "url": probe.url,
        "status": code,
        "expected": probe.expected,
        "contentGate": probe.contains,
        "ok": ok,
    }


def run_checks(base_url: str, queue_url: str, timeout: float) -> list[dict]:
    return [execute(item, timeout) for item in probes(base_url.rstrip("/"), queue_url.rstrip("/"))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--queue-url", default="http://127.0.0.1:3005")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        require_container()
        _, profile = load_profile(args.profile)
        if profile["environment"] != "local" and args.base_url == "http://127.0.0.1:8088":
            raise DeploymentError("non-local smoke requires an explicit --base-url")
        if args.dry_run:
            print(json.dumps([asdict(item) for item in probes(args.base_url, args.queue_url)], indent=2))
            return 0
        results = run_checks(args.base_url, args.queue_url, args.timeout)
        payload = {"profile": profile["name"], "passed": all(item["ok"] for item in results), "probes": results}
        print(json.dumps(payload, indent=2))
        return 0 if payload["passed"] else 1
    except DeploymentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
