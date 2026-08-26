#!/usr/bin/env python3
"""Non-mutating service-shape stub for deployment conformance only."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import urllib.request


NAME = os.environ.get("SERVICE_NAME", "unnamed")
PORT = int(os.environ.get("PORT", "8080"))
DEPENDENCIES = [value for value in os.environ.get("DEPENDENCY_URLS", "").split(",") if value]


def dependencies_ready():
    for url in DEPENDENCIES:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status >= 400:
                    return False
        except OSError:
            return False
    return True


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, payload):
        body = (json.dumps(payload, sort_keys=True) + "\n").encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"status": "ok", "service": NAME, "conformanceOnly": True})
        elif self.path == "/ready":
            ready = dependencies_ready()
            self.reply(200 if ready else 503, {"ready": ready, "service": NAME, "conformanceOnly": True})
        elif self.path == "/capabilities":
            self.reply(200, {"schemaVersion": 1, "service": NAME, "businessLogic": False, "conformanceOnly": True})
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        self.reply(501, {"error": "CONFORMANCE_STUB_NO_BUSINESS_LOGIC", "service": NAME})

    do_PUT = do_POST
    do_DELETE = do_POST

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

