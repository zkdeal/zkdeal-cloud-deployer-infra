#!/usr/bin/env python3
"""Promotion protocol double for the infrastructure wrapper acceptance only."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os


PORT = int(os.environ.get("PORT", "3000"))
TOKEN = os.environ.get("PROMOTION_TEST_TOKEN", "promotion-acceptance-admin-token")
state = {"effectiveRole": "standby", "postCount": 0, "keys": []}


class Handler(BaseHTTPRequestHandler):
    def reply(self, status: int, payload: dict[str, object]) -> None:
        body = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/hosting/v1/health":
            self.reply(200, {"ok": True, "runtime": {
                "configuredRole": "standby", "effectiveRole": state["effectiveRole"],
            }})
        elif self.path == "/hosting/v1/capabilities":
            self.reply(200, {
                "authority": {
                    "fencing": "monotonic-transactional",
                    "promotion": {
                        "primaryTarget": "durable-fenced-wal-checkpoint",
                        "standbyReplay": "pg_last_wal_replay_lsn",
                        "atomicWithFenceTransfer": True,
                    },
                },
                "indexer": {"freshnessGateBlocks": 8},
            })
        elif self.path == "/test/stats":
            self.reply(200, {"conformanceOnly": True, **state})
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/test/reset-standby" and self.headers.get("X-Acceptance-Control") == "reset":
            state["effectiveRole"] = "standby"
            self.reply(200, {"reset": True, "conformanceOnly": True})
            return
        if self.path != "/hosting/v1/admin/promote":
            self.reply(404, {"error": "not found"})
            return
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.reply(403, {"error": "hosting administrator required"})
            return
        key = self.headers.get("Idempotency-Key", "")
        if not 8 <= len(key) <= 200:
            self.reply(400, {"error": "Idempotency-Key header is required"})
            return
        state["postCount"] += 1
        if key in state["keys"]:
            self.reply(409, {"error": "duplicate promotion key"})
            return
        state["keys"].append(key)
        if state["effectiveRole"] != "standby":
            self.reply(409, {"error": "coordinator is not standby"})
            return
        state["effectiveRole"] = "active"
        self.reply(200, {"promoted": True, "runtime": {
            "effectiveRole": "active", "indexerHeadMatchesL1": True,
            "promotionReplication": {"targetLsn": "0/16B6C50", "replayLsn": "0/16B6C50"},
        }})

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
