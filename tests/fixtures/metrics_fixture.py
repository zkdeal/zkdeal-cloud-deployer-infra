#!/usr/bin/env python3
"""Bounded synthetic metric source for the live alert fire/recover gate."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from urllib.parse import parse_qs, urlparse


SIGNAL = 1


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global SIGNAL
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            body = b'{"ok":true}\n'
            content_type = "application/json"
        elif parsed.path == "/set":
            raw = parse_qs(parsed.query).get("value", [""])[0]
            if raw not in {"0", "1"}:
                self.send_error(400, "value must be 0 or 1")
                return
            SIGNAL = int(raw)
            body = f'{{"value":{SIGNAL}}}\n'.encode()
            content_type = "application/json"
        elif parsed.path == "/metrics":
            body = (
                "# HELP zkdeal_acceptance_signal Synthetic alert lifecycle signal.\n"
                "# TYPE zkdeal_acceptance_signal gauge\n"
                f"zkdeal_acceptance_signal {SIGNAL}\n"
            ).encode()
            content_type = "text/plain; version=0.0.4"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "9100"))), Handler).serve_forever()
