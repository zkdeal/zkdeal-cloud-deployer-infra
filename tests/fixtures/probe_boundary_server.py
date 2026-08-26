#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("health", "signer", "coordinator", "agent-owner"), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--path", default="/health")
    parser.add_argument("--account", default="0x1111111111111111111111111111111111111111")
    parser.add_argument("--token", default="")
    parser.add_argument("--heartbeat-enabled", choices=("true", "false"), default="true")
    parser.add_argument("--queue-token", default="queue-token")
    parser.add_argument("--prover-token", default="prover-token")
    args = parser.parse_args()
    lock = threading.Lock()
    state: dict[str, object] = {
        "leases": 0,
        "completions": 0,
        "failures": 0,
        "proves": 0,
        "heartbeats": 0,
        "queueAuth": False,
        "proverAuth": False,
        "heartbeatAuth": False,
        "schemaNegotiated": False,
        "idempotencyBound": False,
        "correlationBound": False,
        "completedJobId": None,
    }
    operation: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_values: object) -> None:
            return

        def send_json(self, status: int, value: object) -> None:
            body = json.dumps(value, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if args.mode == "health" and self.path == args.path:
                self.send_json(200, {"ok": True})
            elif args.mode == "agent-owner" and self.path == "/healthz":
                self.send_json(200, {"ok": True})
            elif args.mode == "agent-owner" and self.path == "/acceptance/state":
                with lock:
                    self.send_json(200, dict(state))
            elif args.mode == "agent-owner" and self.path == "/hosting/v1/capabilities":
                self.send_json(200, {
                    "schemaVersion": 1,
                    "negotiation": {"header": "Accept-Schema-Version", "supported": [1]},
                    "managedL1Operations": {"nodeHeartbeat": {"enabledWhenConfigured": True}},
                })
            elif args.mode == "agent-owner" and self.path.startswith("/hosting/v1/l1-transactions/"):
                with lock:
                    if not operation:
                        self.send_json(404, {"error": "operation not found"})
                    else:
                        self.send_json(200, dict(operation))
            elif args.mode == "coordinator" and self.path == "/hosting/v1/ready":
                self.send_json(200, {"status": "ready"})
            elif args.mode == "coordinator" and self.path == "/hosting/v1/capabilities":
                if args.token and self.headers.get("authorization") != f"Bearer {args.token}":
                    self.send_json(401, {"error": "unauthorized"})
                    return
                self.send_json(200, {
                    "managedL1Operations": {
                        "nodeHeartbeat": {
                            "enabledWhenConfigured": args.heartbeat_enabled == "true",
                        },
                    },
                })
            else:
                self.send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            request = json.loads(self.rfile.read(length) or b"{}")
            if args.mode == "agent-owner":
                if self.path == "/queue/v1/lease":
                    authorized = self.headers.get("authorization") == f"Bearer {args.queue_token}"
                    with lock:
                        state["queueAuth"] = authorized
                        leased = int(state["leases"])
                        if authorized and leased == 0:
                            state["leases"] = 1
                    if not authorized:
                        self.send_json(401, {"error": "unauthorized"})
                    elif leased == 0:
                        self.send_json(200, {
                            "jobId": "job-live-0001",
                            "correlationId": "corr-job-live-0001",
                            "tenantId": "tenant-live-a",
                            "roomId": "7",
                            "endpoint": "/v5/rooms/prove",
                            "needsGpu": True,
                            "attempts": 1,
                            "request": {"witness": "owner-queue-payload"},
                        })
                    else:
                        self.send_response(204)
                        self.end_headers()
                    return
                if self.path == "/v5/rooms/prove":
                    authorized = self.headers.get("authorization") == f"Bearer {args.prover_token}"
                    with lock:
                        state["proverAuth"] = authorized
                        if authorized:
                            state["proves"] = int(state["proves"]) + 1
                    if not authorized:
                        self.send_json(401, {"reason": "unauthorized"})
                    else:
                        self.send_json(200, {"proof": "hash-bound-result", "input": request})
                    return
                if self.path.startswith("/queue/v1/jobs/job-live-0001/"):
                    authorized = self.headers.get("authorization") == f"Bearer {args.queue_token}"
                    if not authorized:
                        self.send_json(401, {"error": "unauthorized"})
                        return
                    with lock:
                        if self.path.endswith("/complete"):
                            state["completions"] = int(state["completions"]) + 1
                            state["completedJobId"] = "job-live-0001"
                        elif self.path.endswith("/fail"):
                            state["failures"] = int(state["failures"]) + 1
                    self.send_json(200, {"ok": True})
                    return
                if self.path == "/hosting/v1/l1-operations/node-heartbeats":
                    correlation = self.headers.get("x-correlation-id", "")
                    idempotency = self.headers.get("idempotency-key", "")
                    authorized = self.headers.get("authorization") == f"Bearer {args.token}"
                    schema = self.headers.get("accept-schema-version") == "1"
                    if not authorized:
                        self.send_json(401, {"error": "unauthorized"})
                        return
                    value = {
                        "operationId": "heartbeat-operation-0001",
                        "idempotencyKey": idempotency,
                        "correlationId": correlation,
                        "status": "FINALIZED",
                        "chainId": request.get("chainId"),
                        "from": request.get("expectedLivenessAccount"),
                        "to": request.get("poolAddress"),
                        "nonce": "1",
                        "transactionHash": "0x" + "ab" * 32,
                        "blockNumber": "100",
                        "blockHash": "0x" + "cd" * 32,
                        "confirmations": 8,
                        "receiptSource": {
                            "providerIds": ["independent-a", "independent-b"],
                            "observedAt": "2026-08-21T00:00:00Z",
                            "canonical": True,
                        },
                        "finalized": True,
                    }
                    with lock:
                        operation.clear()
                        operation.update(value)
                        state["heartbeats"] = int(state["heartbeats"]) + 1
                        state["heartbeatAuth"] = authorized
                        state["schemaNegotiated"] = schema
                        state["idempotencyBound"] = idempotency.startswith("node-heartbeat:")
                        state["correlationBound"] = correlation == idempotency
                    self.send_json(200, value)
                    return
                self.send_json(404, {"error": "not found"})
                return
            if args.mode == "signer" and args.token and self.headers.get("authorization") != f"Bearer {args.token}":
                self.send_json(401, {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32001}})
                return
            if args.mode != "signer" or request.get("method") != "eth_accounts":
                self.send_json(400, {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32601}})
                return
            self.send_json(200, {"jsonrpc": "2.0", "id": request.get("id"), "result": [args.account]})

    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
