#!/usr/bin/env python3
"""Small, explicitly non-business backend for live edge-policy acceptance."""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PORT = int(os.environ.get("PORT", "3000"))
MODE = os.environ.get("MODE", "writer")
WRITER_ID = os.environ.get("WRITER_ID", "acceptance-writer")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _log(self) -> None:
        print(json.dumps({
            "path": self.path,
            "writer": WRITER_ID,
            "requestId": self.headers.get("X-Request-ID"),
            "forwardedFor": self.headers.get("X-Forwarded-For"),
            "lastEventId": self.headers.get("Last-Event-ID"),
        }, sort_keys=True), flush=True)

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802
        self._log()
        if MODE == "docs":
            body = b"edge acceptance docs\n"
            self._send(200, body, "text/plain", {"Cache-Control": "public, max-age=600"})
            return
        if self.path == "/hosting/v1/events":
            resumed = self.headers.get("Last-Event-ID", "")
            body = f"id: 42\nevent: acceptance\ndata: writer={WRITER_ID};resumed={resumed}\n\n".encode()
            self._send(200, body, "text/event-stream", {"Cache-Control": "no-store"})
            return
        body = json.dumps({
            "ok": True,
            "writer": WRITER_ID,
            "requestId": self.headers.get("X-Request-ID"),
            "forwardedFor": self.headers.get("X-Forwarded-For"),
        }, sort_keys=True).encode() + b"\n"
        self._send(200, body, "application/json")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        self.do_GET()


if __name__ == "__main__":
    try:
        ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
