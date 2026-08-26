#!/usr/bin/env python3
"""Multi-endpoint protocol double for promotion-controller acceptance only."""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


LOCK = threading.Lock()
STATE: dict[str, object] = {}
PROVIDER_TOKEN = "provider-token-acceptance"
APPROVAL_TOKEN = "approval-token-acceptance"
PRINCIPAL_TOKEN = "principal-token-acceptance"


def reset(update: dict[str, object] | None = None) -> None:
    with LOCK:
        STATE.clear()
        STATE.update({
            "witnessAHealthy": False,
            "witnessBHealthy": False,
            "unsafeProvider": False,
            "providerCalls": 0,
            "ownerCalls": 0,
            "commitCalls": 0,
            "unauthorizedCalls": 0,
            "events": [],
        })
        if update:
            STATE.update(update)


def reply(handler: BaseHTTPRequestHandler, status: int, value: dict[str, object]) -> None:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


class Handler(BaseHTTPRequestHandler):
    server_version = "zkdeal-promotion-controller-fixture/1"

    @property
    def role(self) -> str:
        return str(getattr(self.server, "role"))

    def body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def authorized(self, token: str, approval: bool = False) -> bool:
        valid = self.headers.get("Authorization") == f"Bearer {token}"
        if approval:
            valid = valid and self.headers.get("X-Zkdeal-Failover-Approval") == APPROVAL_TOKEN
        if not valid:
            with LOCK:
                STATE["unauthorizedCalls"] = int(STATE["unauthorizedCalls"]) + 1
            reply(self, 403, {"error": "forbidden"})
        return valid

    def do_GET(self) -> None:  # noqa: N802
        if self.role in {"witness-a", "witness-b"}:
            field = "witnessAHealthy" if self.role == "witness-a" else "witnessBHealthy"
            with LOCK:
                healthy = bool(STATE[field])
            reply(self, 200, {
                "coordinatorId": "primary-region-a",
                "effectiveRole": "active" if healthy else "unknown",
                "acceptingWrites": healthy,
                "fenceFresh": healthy,
            })
            return
        if self.role == "standby" and self.path == "/hosting/v1/health":
            reply(self, 200, {
                "coordinatorId": "standby-region-b",
                "configuredRole": "standby",
                "effectiveRole": "standby",
                "acceptingWrites": False,
            })
            return
        if self.role == "control" and self.path == "/stats":
            with LOCK:
                reply(self, 200, dict(STATE))
            return
        reply(self, 404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.role == "control" and self.path == "/control/reset":
            reset(self.body())
            reply(self, 200, {"reset": True})
            return
        if self.role == "provider" and self.path == "/v1/failovers":
            if not self.authorized(PROVIDER_TOKEN, approval=True):
                return
            body = self.body()
            with LOCK:
                STATE["providerCalls"] = int(STATE["providerCalls"]) + 1
                STATE["events"].append("provider-prepare")  # type: ignore[union-attr]
                unsafe = bool(STATE["unsafeProvider"])
            candidate = str(body.get("candidateId", ""))
            reply(self, 200, {
                "operationId": candidate,
                "status": "READY_FOR_APPLICATION_PROMOTION",
                "activeFenced": not unsafe,
                "oldWriterTerminated": not unsafe,
                "targetCapturedByProvider": True,
                "databasePromoted": True,
                "standbyReplayAtOrAfterTarget": True,
                "indexerHeadMatchesL1": True,
                "stableDatabaseEndpointRouted": True,
                "standbySignerAuthorityActive": False,
                "primaryTargetSource": "durable-fenced-wal-checkpoint",
                "primaryTargetLsn": "0/16B6C50",
                "standbyReplayLsn": "0/16B6D00",
                "checkpointAgeSeconds": 1,
            })
            return
        if self.role == "standby" and self.path == "/hosting/v1/admin/promote":
            if not self.authorized(PRINCIPAL_TOKEN):
                return
            if not self.headers.get("Idempotency-Key"):
                reply(self, 400, {"error": "missing idempotency key"})
                return
            with LOCK:
                STATE["ownerCalls"] = int(STATE["ownerCalls"]) + 1
                STATE["events"].append("owner-promote")  # type: ignore[union-attr]
            reply(self, 200, {
                "promoted": True,
                "effectiveRole": "active",
                "indexerHeadMatchesL1": True,
                "promotionReplication": {
                    "targetLsn": "0/16B6C50", "replayLsn": "0/16B6D00",
                },
            })
            return
        if self.role == "provider" and self.path.endswith("/commit"):
            if not self.authorized(PROVIDER_TOKEN, approval=True):
                return
            operation_id = self.path.removeprefix("/v1/failovers/").removesuffix("/commit")
            with LOCK:
                STATE["commitCalls"] = int(STATE["commitCalls"]) + 1
                STATE["events"].append("provider-commit")  # type: ignore[union-attr]
            reply(self, 200, {
                "operationId": operation_id,
                "writerRouteCommitted": True,
                "writerCoordinatorId": "standby-region-b",
                "oldWriterRouteRemoved": True,
                "stableDatabaseEndpointRouted": True,
                "signerAuthorityActivatedAfterFence": True,
            })
            return
        reply(self, 404, {"error": "not found"})

    def log_message(self, _format: str, *_values: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoints-file", required=True)
    args = parser.parse_args()
    reset()
    servers: dict[str, ThreadingHTTPServer] = {}
    for role in ("control", "witness-a", "witness-b", "provider", "standby"):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.role = role  # type: ignore[attr-defined]
        servers[role] = server
        threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoints = {
        role: f"http://127.0.0.1:{server.server_port}"
        for role, server in servers.items()
    }
    path = Path(args.endpoints_file)
    path.write_text(json.dumps(endpoints, sort_keys=True), encoding="utf-8")
    try:
        threading.Event().wait()
    finally:
        for server in servers.values():
            server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
