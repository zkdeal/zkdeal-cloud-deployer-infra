#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--openapi", required=True)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    openapi = json.loads(Path(args.openapi).read_text(encoding="utf-8"))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_values: object) -> None:
            return

        def reply(self, status: int, value: object) -> None:
            body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-schema-version", "1")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self) -> bool:
            return self.headers.get("authorization") == f"Bearer {args.token}"

        def read_json(self) -> dict:
            length = int(self.headers.get("content-length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self) -> None:
            if self.path == "/hosting/v1/openapi.json":
                self.reply(200, openapi)
            elif self.path == "/hosting/v1/health":
                self.reply(200, {"ok": True})
            elif self.path == "/hosting/v1/protected":
                self.reply(200, {"tenantId": "fixture-tenant"}) if self.authorized() else self.reply(401, {"error": "unauthorized"})
            else:
                self.reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            request = self.read_json()
            if self.path == "/hosting/v1/json-rpc" and request.get("method") == "hosting_capabilities":
                self.reply(200, {"jsonrpc": "2.0", "id": request.get("id"), "result": {"service": "fixture", "schemaVersion": 1}})
                return
            if self.path == "/hosting/v1/admin/reconcile":
                if not self.authorized():
                    self.reply(401, {"error": "unauthorized"})
                    return
                key = self.headers.get("idempotency-key", "")
                if len(key) < 8:
                    self.reply(400, {"error": "idempotency key required"})
                    return
                operation_id = "op-" + hashlib.sha256(key.encode()).hexdigest()[:12]
                self.reply(202, {"operationId": operation_id, "accepted": True})
                return
            self.reply(404, {"error": "not found"})

    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
