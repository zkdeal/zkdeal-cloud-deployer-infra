#!/usr/bin/env python3
"""Local-only Alertmanager webhook sink; stores bounded sanitized summaries."""

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Lock
import time


EVENTS = deque(maxlen=256)
EVENTS_LOCK = Lock()


def reply_json(handler, status, value):
    body = (json.dumps(value, sort_keys=True) + "\n").encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            reply_json(self, 200, {"status": "ok"})
            return
        if self.path == "/events":
            with EVENTS_LOCK:
                reply_json(self, 200, {"events": list(EVENTS)})
            return
        if self.path == "/reset":
            with EVENTS_LOCK:
                EVENTS.clear()
            reply_json(self, 200, {"reset": True})
            return
        self.send_error(404)

    def do_POST(self):
        if self.path != "/alerts":
            self.send_error(404)
            return
        length = min(int(self.headers.get("content-length", "0")), 1024 * 1024)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
            alerts = payload.get("alerts", []) if isinstance(payload, dict) else []
            summaries = []
            for alert in alerts if isinstance(alerts, list) else []:
                labels = alert.get("labels", {}) if isinstance(alert, dict) else {}
                summaries.append({
                    "receivedAt": int(time.time()),
                    "status": str(alert.get("status", payload.get("status", "unknown")))[:16],
                    "alertname": str(labels.get("alertname", "unknown"))[:128],
                    "service": str(labels.get("service", "unknown"))[:128],
                    "severity": str(labels.get("severity", "unknown"))[:32],
                })
            with EVENTS_LOCK:
                EVENTS.extend(summaries)
            count = len(summaries)
        except Exception:
            count = -1
        print(json.dumps({"time": time.time(), "alerts": count}), flush=True)
        self.send_response(204)
        self.end_headers()

    def log_message(self, format, *args):
        return


ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "9080"))), Handler).serve_forever()
