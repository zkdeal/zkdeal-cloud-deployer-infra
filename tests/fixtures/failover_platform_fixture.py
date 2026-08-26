#!/usr/bin/env python3
"""Acceptance-only health/indexer/signer boundary for failover adapters."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


ROLE = os.environ.get("FIXTURE_ROLE", "")
COORDINATOR_ID = os.environ.get("FIXTURE_COORDINATOR_ID", "")
state = {"healthy": os.environ.get("FIXTURE_HEALTHY", "false") == "true"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def reply(self, status, value):
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        if self.path == "/identity":
            return self.reply(200, {"role": ROLE, "coordinatorId": COORDINATOR_ID})
        if self.path == "/freshness" and ROLE == "indexer":
            return self.reply(200, {"indexerHeadMatchesL1": True, "unresolvedSafetyEvents": 0})
        if self.path == "/health" and ROLE in {"witness", "active"}:
            if state["healthy"]:
                return self.reply(200, {
                    "coordinatorId": COORDINATOR_ID,
                    "effectiveRole": "active",
                    "acceptingWrites": True,
                    "fenceFresh": True,
                })
            return self.reply(503, {"status": "unavailable"})
        if self.path == "/health" and ROLE == "standby":
            return self.reply(200, {
                "coordinatorId": COORDINATOR_ID,
                "configuredRole": "standby",
                "effectiveRole": "standby",
                "acceptingWrites": False,
            })
        if self.path == "/health" and ROLE == "signer":
            return self.reply(200, {"signerAuthority": "post-fence-only"})
        self.reply(404, {"error": "not-found"})

    def do_POST(self):  # noqa: N802
        if self.path == "/test/unhealthy" and ROLE == "witness" and self.headers.get("X-Acceptance-Control") == "set-unhealthy":
            state["healthy"] = False
            return self.reply(200, {"healthy": False})
        self.reply(403, {"error": "forbidden"})


HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
