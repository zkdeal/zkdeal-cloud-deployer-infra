#!/usr/bin/env python3
"""Execute and independently verify zkdeal hosted acceptance scenarios.

The runner deliberately owns assertions, not service behavior. Candidate plans
choose real API calls and JSON pointers into their responses; this module fixes
the required evidence fields, source boundaries and invariants for every
scenario. Bearer values are read only from mounted files and never serialized.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
CANDIDATE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{7,79}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
TOKEN = re.compile(r"^sha256:[0-9a-f]{64}$")
EPHEMERAL_TOKEN = re.compile(r"^eph_[A-Za-z0-9_-]{28,120}$")
HEX32 = re.compile(r"^0x[0-9a-f]{64}$")
ENDPOINT_ENV = {
    "coordinator": "COORDINATOR_URL",
    "indexer": "INDEXER_URL",
    "queue": "QUEUE_URL",
    "headless": "HEADLESS_URL",
    "rpc_a": "L1_RPC_A",
    "rpc_b": "L1_RPC_B",
    "fault": "ACCEPTANCE_FAULT_URL",
    "backup": "ACCEPTANCE_BACKUP_URL",
    "failover": "FAILOVER_PROVIDER_URL",
    "prover": "PROVER_URL",
    "logs": "LOG_QUERY_URL",
}
TOKEN_FILE_ENV = {
    "admin": "ACCEPTANCE_ADMIN_TOKEN_FILE",
    "tenant_a": "ACCEPTANCE_TENANT_A_TOKEN_FILE",
    "tenant_b": "ACCEPTANCE_TENANT_B_TOKEN_FILE",
    "node_a": "ACCEPTANCE_NODE_A_TOKEN_FILE",
    "node_b": "ACCEPTANCE_NODE_B_TOKEN_FILE",
    "l1_liveness": "ACCEPTANCE_L1_LIVENESS_TOKEN_FILE",
    "l1_room": "ACCEPTANCE_L1_ROOM_TOKEN_FILE",
    "l1_aggregate": "ACCEPTANCE_L1_AGGREGATE_TOKEN_FILE",
    "l1_sponsor": "ACCEPTANCE_L1_SPONSOR_TOKEN_FILE",
    "withdrawal": "ACCEPTANCE_WITHDRAWAL_TOKEN_FILE",
    "fault_control": "ACCEPTANCE_FAULT_CONTROL_TOKEN_FILE",
    "backup_restore": "ACCEPTANCE_BACKUP_RESTORE_TOKEN_FILE",
    "failover_control": "ACCEPTANCE_FAILOVER_CONTROL_TOKEN_FILE",
    "failover_approval": "ACCEPTANCE_FAILOVER_APPROVAL_TOKEN_FILE",
}
CONTROL_ENDPOINT_AUTH = {
    "fault": "fault_control",
    "backup": "backup_restore",
    "failover": "failover_control",
}
CONTROL_AUTH_ENDPOINT = {authority: endpoint for endpoint, authority in CONTROL_ENDPOINT_AUTH.items()}
ALLOWED_HEADERS = {
    "accept-schema-version", "idempotency-key", "x-correlation-id", "last-event-id",
}


class AcceptanceError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AcceptanceError(f"JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def parse_json(raw: bytes, label: str) -> Any:
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise AcceptanceError(f"{label} exceeds {MAX_DOCUMENT_BYTES} bytes")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError(f"{label} is not strict UTF-8 JSON: {exc}") from exc


def regular_secret(path_value: str, label: str) -> str:
    path = Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise AcceptanceError(f"{label} is absent, not regular, or a symlink")
    # POSIX permission bits are the enforced boundary in the Linux runner; on
    # platforms that do not honor st_mode (and where the runner never executes)
    # the check is skipped rather than rejecting every mounted credential.
    mode = stat.S_IMODE(path.stat().st_mode)
    if os.name == "posix" and mode & 0o077:
        raise AcceptanceError(f"{label} must be private to its owning user")
    raw = path.read_bytes()
    if len(raw) > 4096:
        raise AcceptanceError(f"{label} is unexpectedly large")
    try:
        value = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise AcceptanceError(f"{label} is not UTF-8") from exc
    if len(value) < 16 or any(character.isspace() for character in value):
        raise AcceptanceError(f"{label} is too short or contains whitespace")
    return value


def load_plan() -> tuple[dict[str, Any], bytes]:
    path_value = os.environ.get("ACCEPTANCE_PLAN_FILE")
    inline = os.environ.get("ACCEPTANCE_PLAN_JSON")
    if bool(path_value) == bool(inline):
        raise AcceptanceError("set exactly one of ACCEPTANCE_PLAN_FILE or ACCEPTANCE_PLAN_JSON")
    if path_value:
        path = Path(path_value)
        if path.is_symlink() or not path.is_file():
            raise AcceptanceError("ACCEPTANCE_PLAN_FILE is absent, not regular, or a symlink")
        raw = path.read_bytes()
    else:
        raw = inline.encode("utf-8")  # type: ignore[union-attr]
    expected = os.environ.get("ACCEPTANCE_PLAN_SHA256", "")
    if not SHA256.fullmatch(expected) or sha256(raw) != expected:
        raise AcceptanceError("acceptance plan SHA-256 is absent or differs")
    value = parse_json(raw, "acceptance plan")
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schemaVersion", "candidateId", "hostedIntegrationToken", "ownerDatabaseSchema",
            "authTokenSha256", "scenarios",
        }
        or value.get("schemaVersion") != 1
    ):
        raise AcceptanceError("acceptance plan must be a schemaVersion 1 object")
    candidate = str(value.get("candidateId", ""))
    token = str(value.get("hostedIntegrationToken", ""))
    if not CANDIDATE_ID.fullmatch(candidate):
        raise AcceptanceError("acceptance plan candidateId is malformed")
    if not TOKEN.fullmatch(token):
        raise AcceptanceError("acceptance plan hostedIntegrationToken is malformed")
    if os.environ.get("ACCEPTANCE_CANDIDATE_ID") != candidate:
        raise AcceptanceError("runtime candidate ID differs from the plan")
    if os.environ.get("HOSTED_INTEGRATION_TOKEN") != token:
        raise AcceptanceError("runtime hostedIntegration token differs from the plan")
    if not isinstance(value.get("ownerDatabaseSchema"), int) or value["ownerDatabaseSchema"] < 1:
        raise AcceptanceError("acceptance plan ownerDatabaseSchema must be a positive integer")
    auth_hashes = value.get("authTokenSha256")
    if not isinstance(auth_hashes, dict):
        raise AcceptanceError("acceptance plan authTokenSha256 must be an object")
    for alias, expected_hash in auth_hashes.items():
        if alias not in TOKEN_FILE_ENV or not isinstance(expected_hash, str) or not SHA256.fullmatch(expected_hash):
            raise AcceptanceError(f"acceptance plan credential binding {alias!r} is malformed")
    scenarios = value.get("scenarios")
    if not isinstance(scenarios, dict):
        raise AcceptanceError("acceptance plan scenarios must be an object")
    return value, raw


def normalized_base(value: str, label: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise AcceptanceError(f"{label} is malformed") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise AcceptanceError(f"{label} must be an HTTP(S) URL without user info")
    if parsed.query or parsed.fragment:
        raise AcceptanceError(f"{label} must not contain query or fragment data")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def safe_path(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value:
        raise AcceptanceError("step path must be an absolute URL path")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment or any(part == ".." for part in parsed.path.split("/")):
        raise AcceptanceError("step path escaped its endpoint or contains a fragment")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass(frozen=True)
class StepResult:
    step_id: str
    endpoint: str
    method: str
    path: str
    status: int
    body: Any
    request_sha256: str
    response_sha256: str
    attempts: int

    def pointer_document(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "body": self.body,
            "requestSha256": self.request_sha256,
            "responseSha256": self.response_sha256,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.step_id,
            "endpoint": self.endpoint,
            "method": self.method,
            "path": self.path,
            "status": self.status,
            "attempts": self.attempts,
            "requestSha256": self.request_sha256,
            "responseSha256": self.response_sha256,
        }


def pointer(value: Any, path: str) -> Any:
    if path == "":
        return value
    if not isinstance(path, str) or not path.startswith("/"):
        raise AcceptanceError(f"invalid JSON pointer {path!r}")
    current = value
    for raw_part in path[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise AcceptanceError(f"JSON pointer {path!r} is absent")
    return current


class HttpExecutor:
    def __init__(self, endpoint_names: set[str], auth_hashes: dict[str, str]):
        self.endpoints: dict[str, str] = {}
        for name in sorted(endpoint_names):
            env_name = ENDPOINT_ENV.get(name)
            if not env_name:
                raise AcceptanceError(f"unknown endpoint alias {name}")
            raw = os.environ.get(env_name, "")
            if not raw:
                raise AcceptanceError(f"{env_name} is required by this scenario")
            self.endpoints[name] = normalized_base(raw, env_name)
        if "rpc_a" in self.endpoints and "rpc_b" in self.endpoints:
            if self.endpoints["rpc_a"] == self.endpoints["rpc_b"]:
                raise AcceptanceError("L1_RPC_A and L1_RPC_B must be distinct provider URLs")
        self.tokens: dict[str, str] = {}
        self.auth_hashes = auth_hashes
        self.opener = urllib.request.build_opener(NoRedirect())

    def token(self, alias: str) -> str:
        if alias not in self.tokens:
            env_name = TOKEN_FILE_ENV.get(alias)
            expected = self.auth_hashes.get(alias)
            if not env_name or not expected or not os.environ.get(env_name):
                raise AcceptanceError(f"token file for auth alias {alias} is not configured")
            value = regular_secret(os.environ[env_name], env_name)
            if not EPHEMERAL_TOKEN.fullmatch(value):
                raise AcceptanceError(f"token for auth alias {alias} is not enclave-scoped")
            if sha256(value.encode("utf-8")) != expected:
                raise AcceptanceError(f"token file for auth alias {alias} differs from the candidate plan")
            self.tokens[alias] = value
        return self.tokens[alias]

    def verify_candidate(self, expected_schema: int, expected_token: str) -> dict[str, Any]:
        result = self.run({
            "id": "candidate-capability",
            "endpoint": "coordinator",
            "method": "GET",
            "path": "/hosting/v1/capabilities",
            "headers": {"accept-schema-version": "1"},
            "expectStatus": [200],
            "attempts": 20,
            "intervalMs": 1000,
        }, [
            "/body/databaseSchema",
            "/body/managedL1Operations/roomBatch/enabled",
            "/body/managedL1Operations/roomBatch/hostedIntegration/enabled",
            "/body/managedL1Operations/roomBatch/hostedIntegration/acceptanceToken",
            "/body/managedL1Operations/roomAggregate/enabled",
            "/body/managedL1Operations/poolSponsorMutation/enabled",
        ])
        body = result.body
        if not isinstance(body, dict) or integer(body.get("databaseSchema"), "databaseSchema", 1) != expected_schema:
            raise AcceptanceError("live owner database schema differs from the candidate plan")
        managed = body.get("managedL1Operations")
        room_batch = managed.get("roomBatch") if isinstance(managed, dict) else None
        room_aggregate = managed.get("roomAggregate") if isinstance(managed, dict) else None
        pool_sponsor = managed.get("poolSponsorMutation") if isinstance(managed, dict) else None
        hosted = room_batch.get("hostedIntegration") if isinstance(room_batch, dict) else None
        if not isinstance(room_batch, dict) or not boolean(room_batch.get("enabled"), "roomBatch.enabled"):
            raise AcceptanceError("live owner roomBatch capability is disabled")
        if not isinstance(hosted, dict) or not boolean(hosted.get("enabled"), "hostedIntegration.enabled"):
            raise AcceptanceError("live owner hostedIntegration capability is disabled")
        if hosted.get("acceptanceToken") != expected_token:
            raise AcceptanceError("live owner hostedIntegration token differs from the candidate plan")
        if not isinstance(room_aggregate, dict) or not boolean(
            room_aggregate.get("enabled"), "roomAggregate.enabled",
        ):
            raise AcceptanceError("live owner roomAggregate capability is disabled")
        if not isinstance(pool_sponsor, dict) or not boolean(
            pool_sponsor.get("enabled"), "poolSponsorMutation.enabled",
        ):
            raise AcceptanceError("live owner poolSponsorMutation capability is disabled")
        return {
            "databaseSchema": expected_schema,
            "hostedIntegrationToken": expected_token,
            "roomAggregateEnabled": True,
            "poolSponsorMutationEnabled": True,
            "responseSha256": result.response_sha256,
        }

    def run(self, step: dict[str, Any], required_pointers: list[str]) -> StepResult:
        step_id = str(step.get("id", ""))
        endpoint = str(step.get("endpoint", ""))
        method = str(step.get("method", ""))
        if not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", step_id):
            raise AcceptanceError("step id is malformed")
        if endpoint not in self.endpoints:
            raise AcceptanceError(f"step {step_id} uses an unavailable endpoint")
        if method not in {"GET", "POST", "PUT", "DELETE"}:
            raise AcceptanceError(f"step {step_id} uses a forbidden method")
        path = safe_path(step.get("path"))
        body = step.get("body")
        if method == "GET" and body is not None:
            raise AcceptanceError(f"GET step {step_id} must not include a body")
        request_bytes = b"" if body is None else canonical_bytes(body)
        if len(request_bytes) > MAX_DOCUMENT_BYTES:
            raise AcceptanceError(f"step {step_id} request exceeds the byte limit")
        expected = step.get("expectStatus")
        if (
            not isinstance(expected, list) or not expected
            or any(not isinstance(value, int) or value < 100 or value > 599 for value in expected)
        ):
            raise AcceptanceError(f"step {step_id} has invalid expected statuses")
        headers = {"accept": "application/json"}
        if body is not None:
            headers["content-type"] = "application/json"
        supplied = step.get("headers", {})
        if not isinstance(supplied, dict):
            raise AcceptanceError(f"step {step_id} headers must be an object")
        for raw_name, raw_value in supplied.items():
            name = str(raw_name).lower()
            value = str(raw_value)
            if name not in ALLOWED_HEADERS or "\r" in value or "\n" in value or len(value) > 256:
                raise AcceptanceError(f"step {step_id} contains a forbidden header")
            headers[name] = value
        auth = step.get("auth")
        if auth is not None:
            headers["authorization"] = f"Bearer {self.token(str(auth))}"
        approval_auth = step.get("approvalAuth")
        if approval_auth is not None:
            if endpoint != "failover" or approval_auth != "failover_approval":
                raise AcceptanceError(f"step {step_id} contains a forbidden approval authority")
            headers["x-zkdeal-failover-approval"] = self.token("failover_approval")
        attempts = step.get("attempts", 1)
        interval_ms = step.get("intervalMs", 0)
        if not isinstance(attempts, int) or not 1 <= attempts <= 600:
            raise AcceptanceError(f"step {step_id} attempts are outside 1..600")
        if not isinstance(interval_ms, int) or not 0 <= interval_ms <= 10_000:
            raise AcceptanceError(f"step {step_id} intervalMs is outside 0..10000")
        timeout = float(os.environ.get("ACCEPTANCE_HTTP_TIMEOUT_SECONDS", "10"))
        timeout = max(1.0, min(timeout, 30.0))
        url = self.endpoints[endpoint] + path
        last_error = "request was not attempted"
        for attempt in range(1, attempts + 1):
            raw = b""
            status = 0
            try:
                request = urllib.request.Request(
                    url, data=request_bytes if body is not None else None, headers=headers, method=method,
                )
                try:
                    response = self.opener.open(request, timeout=timeout)
                    status = int(response.status)
                    raw = response.read(MAX_DOCUMENT_BYTES + 1)
                except urllib.error.HTTPError as exc:
                    status = int(exc.code)
                    raw = exc.read(MAX_DOCUMENT_BYTES + 1)
                if len(raw) > MAX_DOCUMENT_BYTES:
                    raise AcceptanceError(f"step {step_id} response exceeds the byte limit")
                parsed: Any = None
                if raw:
                    parsed = parse_json(raw, f"step {step_id} response")
                result = StepResult(
                    step_id, endpoint, method, path, status, parsed, sha256(request_bytes), sha256(raw), attempt,
                )
                if status not in expected:
                    last_error = f"HTTP {status} not in {expected}"
                else:
                    try:
                        for required in required_pointers:
                            pointer(result.pointer_document(), required)
                        return result
                    except AcceptanceError as exc:
                        last_error = str(exc)
            except (OSError, urllib.error.URLError, AcceptanceError) as exc:
                last_error = str(exc)
            if attempt < attempts and interval_ms:
                time.sleep(interval_ms / 1000)
        raise AcceptanceError(f"step {step_id} failed after {attempts} attempts: {last_error}")


def boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise AcceptanceError(f"{field} must be a boolean")
    return value


def integer(value: Any, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise AcceptanceError(f"{field} must be an integer >= {minimum}")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]*", value):
        result = int(value)
    else:
        raise AcceptanceError(f"{field} must be an integer >= {minimum}")
    if result < minimum:
        raise AcceptanceError(f"{field} must be an integer >= {minimum}")
    return result


def pg_lsn(value: Any, field: str) -> tuple[str, int]:
    raw = text(value, field).upper()
    if not re.fullmatch(r"[0-9A-F]{1,8}/[0-9A-F]{1,8}", raw):
        raise AcceptanceError(f"{field} must be a PostgreSQL LSN")
    high, low = raw.split("/", 1)
    return raw, (int(high, 16) << 32) + int(low, 16)


def text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or any(ord(ch) < 32 for ch in value):
        raise AcceptanceError(f"{field} must be a nonempty bounded string")
    return value


def hash32(value: Any, field: str) -> str:
    result = text(value, field).lower()
    if not HEX32.fullmatch(result):
        raise AcceptanceError(f"{field} must be a 32-byte lowercase hex value")
    return result


def digest(value: Any, field: str) -> str:
    result = text(value, field).lower()
    if result.startswith("sha256:"):
        result = result[7:]
    if not SHA256.fullmatch(result):
        raise AcceptanceError(f"{field} must be a SHA-256 digest")
    return result


def equal_nonempty(left: Any, right: Any, label: str) -> str:
    left_value = text(left, f"{label}.left")
    right_value = text(right, f"{label}.right")
    if left_value != right_value:
        raise AcceptanceError(f"{label} differs")
    return left_value


def validate_reorg(e: dict[str, Any]) -> dict[str, Any]:
    previous = hash32(e["previousBlockHash"], "previousBlockHash")
    current = hash32(e["canonicalBlockHash"], "canonicalBlockHash")
    if previous == current:
        raise AcceptanceError("reorg did not change the canonical hash")
    if hash32(e["rpcABlockHash"], "rpcABlockHash") != current or hash32(e["rpcBBlockHash"], "rpcBBlockHash") != current:
        raise AcceptanceError("independent RPCs do not agree with the reingested canonical hash")
    if not boolean(e["reconciliationReady"], "reconciliationReady"):
        raise AcceptanceError("reconciliation did not recover readiness")
    return {
        "previousBlockHash": previous, "canonicalBlockHash": current,
        "rollbackDepth": integer(e["rollbackDepth"], "rollbackDepth", 1),
        "retractionPreviousState": text(e["retractionPreviousState"], "retractionPreviousState"),
        "retractionReason": text(e["retractionReason"], "retractionReason"),
        "reconciliationReady": True,
    }


def validate_rpc_disagreement(e: dict[str, Any]) -> dict[str, Any]:
    before = equal_nonempty(e["agreedBeforeA"], e["agreedBeforeB"], "agreedBefore")
    disagree_a = text(e["disagreeA"], "disagreeA")
    disagree_b = text(e["disagreeB"], "disagreeB")
    if disagree_a == disagree_b:
        raise AcceptanceError("RPC disagreement fault did not create a disagreement")
    if integer(e["criticalStatus"], "criticalStatus", 100) not in {409, 503}:
        raise AcceptanceError("critical read did not fail closed with 409/503")
    restored = equal_nonempty(e["restoredA"], e["restoredB"], "restored")
    if not boolean(e["readyAfterRestore"], "readyAfterRestore"):
        raise AcceptanceError("service did not recover after RPC agreement")
    return {"agreedBefore": before, "disagreementObserved": True, "restored": restored, "readyAfterRestore": True}


def validate_split_brain(e: dict[str, Any], rto_limit: int) -> dict[str, Any]:
    for field in ("activeTerminated", "staleWriterRejected", "promotedReady", "signerAfterFence"):
        if not boolean(e[field], field):
            raise AcceptanceError(f"split-brain invariant failed: {field}")
    target, target_value = pg_lsn(e["targetLsn"], "targetLsn")
    replay, replay_value = pg_lsn(e["replayLsn"], "replayLsn")
    if replay_value < target_value:
        raise AcceptanceError("standby replay LSN is behind the durable target")
    rto = integer(e["rtoSeconds"], "rtoSeconds")
    if rto > rto_limit:
        raise AcceptanceError(f"promotion RTO {rto}s exceeds {rto_limit}s")
    return {"targetLsn": target, "replayLsn": replay, "rtoSeconds": rto, "fenced": True, "promotedReady": True}


def validate_restore(e: dict[str, Any]) -> dict[str, Any]:
    db = digest(e["sourceDatabaseDigest"], "sourceDatabaseDigest")
    objects = digest(e["sourceObjectDigest"], "sourceObjectDigest")
    if digest(e["restoredDatabaseDigest"], "restoredDatabaseDigest") != db:
        raise AcceptanceError("restored database digest differs")
    if digest(e["restoredObjectDigest"], "restoredObjectDigest") != objects:
        raise AcceptanceError("restored object digest differs")
    for field in ("freshDatabase", "freshObjectStore", "serviceReady"):
        if not boolean(e[field], field):
            raise AcceptanceError(f"restore invariant failed: {field}")
    return {"databaseDigest": db, "objectDigest": objects, "freshTargets": True, "serviceReady": True}


def validate_sponsorship(e: dict[str, Any]) -> dict[str, Any]:
    for field in ("tenantIsolationDenied", "conflictingReplayDenied"):
        if not boolean(e[field], field):
            raise AcceptanceError(f"sponsorship invariant failed: {field}")
    operation = text(e["firstOperationId"], "firstOperationId")
    if text(e["replayOperationId"], "replayOperationId") != operation:
        raise AcceptanceError("sponsorship idempotent replay changed operation identity")
    reserved = integer(e["reservedQuantity"], "reservedQuantity")
    charged = integer(e["chargedQuantity"], "chargedQuantity")
    refunded = integer(e["refundedQuantity"], "refundedQuantity")
    if charged + refunded > reserved or boolean(e["doubleBilled"], "doubleBilled"):
        raise AcceptanceError("sponsorship quantities overbook or double bill")
    return {"operationId": operation, "reserved": reserved, "charged": charged, "refunded": refunded,
            "tenantIsolationDenied": True, "idempotent": True, "doubleBilled": False}


def validate_queue(e: dict[str, Any]) -> dict[str, Any]:
    a = integer(e["tenantACompleted"], "tenantACompleted", 1)
    b = integer(e["tenantBCompleted"], "tenantBCompleted", 1)
    if integer(e["starvedJobs"], "starvedJobs") != 0 or integer(e["edfViolations"], "edfViolations") != 0:
        raise AcceptanceError("queue starved work or violated tenant EDF")
    for field in ("leaseRecovered", "backoffObserved", "withinCaps"):
        if not boolean(e[field], field):
            raise AcceptanceError(f"queue invariant failed: {field}")
    return {"tenantACompleted": a, "tenantBCompleted": b, "starvedJobs": 0, "edfViolations": 0,
            "leaseRecovered": True, "backoffObserved": True, "withinCaps": True}


def validate_headless_restart(e: dict[str, Any]) -> dict[str, Any]:
    state_digest = digest(e["stateDigestBefore"], "stateDigestBefore")
    if digest(e["stateDigestAfter"], "stateDigestAfter") != state_digest:
        raise AcceptanceError("headless restart changed the durable state digest")
    admission_digest = digest(e["admissionJournalBefore"], "admissionJournalBefore")
    if digest(e["admissionJournalAfter"], "admissionJournalAfter") != admission_digest:
        raise AcceptanceError("headless restart changed the durable admission journal")
    restart_count = integer(e["restartCount"], "restartCount", 1)
    for field in ("lockRecovered", "roomReady", "queueJobPreserved"):
        if not boolean(e[field], field):
            raise AcceptanceError(f"headless restart invariant failed: {field}")
    if integer(e["duplicateSequences"], "duplicateSequences") != 0:
        raise AcceptanceError("headless restart produced duplicate sequencing")
    return {
        "stateDigest": state_digest,
        "admissionJournalDigest": admission_digest,
        "restartCount": restart_count,
        "lockRecovered": True,
        "roomReady": True,
        "queueJobPreserved": True,
        "duplicateSequences": 0,
    }


def validate_blob(e: dict[str, Any]) -> dict[str, Any]:
    manifest = digest(e["manifestDigest"], "manifestDigest")
    if digest(e["archiveDigest"], "archiveDigest") != manifest:
        raise AcceptanceError("blob archive digest differs from its manifest")
    raw = hash32(e["rawTransactionHash"], "rawTransactionHash")
    if hash32(e["rebroadcastRawTransactionHash"], "rebroadcastRawTransactionHash") != raw:
        raise AcceptanceError("blob restart changed the signed transaction bytes")
    if integer(e["signerCalls"], "signerCalls") != 1:
        raise AcceptanceError("blob restart invoked the signer more than once")
    for field in ("finalized", "sidecarVerified"):
        if not boolean(e[field], field):
            raise AcceptanceError(f"blob invariant failed: {field}")
    return {"manifestDigest": manifest, "rawTransactionHash": raw, "signerCalls": 1,
            "finalized": True, "sidecarVerified": True}


def validate_aggregate(e: dict[str, Any]) -> dict[str, Any]:
    expected = {"memberCount": 8, "uniqueRooms": 8, "appliedMembers": 7, "failedMembers": 1,
                "chargedMembers": 7, "failedMemberCharges": 0}
    for field, value in expected.items():
        if integer(e[field], field) != value:
            raise AcceptanceError(f"aggregate {field} must equal {value}")
    for field in ("retryApplied", "finalized"):
        if not boolean(e[field], field):
            raise AcceptanceError(f"aggregate invariant failed: {field}")
    return {**expected, "operationId": text(e["operationId"], "operationId"), "retryApplied": True, "finalized": True}


def validate_renewal(e: dict[str, Any]) -> dict[str, Any]:
    for field in ("checkpointFinalized", "freshPriceBound", "maximumChargeEnforced", "handoffNoOverlap"):
        if not boolean(e[field], field):
            raise AcceptanceError(f"renewal invariant failed: {field}")
    if integer(e["duplicateCharge"], "duplicateCharge") != 0:
        raise AcceptanceError("renewal charged an exact checkpoint more than once")
    return {"checkpointFinalized": True, "freshPriceBound": True, "maximumChargeEnforced": True,
            "refundQuantity": integer(e["refundQuantity"], "refundQuantity"), "duplicateCharge": 0,
            "handoffNoOverlap": True}


def validate_withdrawal(e: dict[str, Any]) -> dict[str, Any]:
    for field in ("proofRootValid", "finalized", "replayDenied", "externalRaceCanonical"):
        if not boolean(e[field], field):
            raise AcceptanceError(f"withdrawal invariant failed: {field}")
    return {"operationId": text(e["operationId"], "operationId"), "proofRootValid": True,
            "finalized": True, "replayDenied": True, "externalRaceCanonical": True,
            "sponsoredGas": boolean(e["sponsoredGas"], "sponsoredGas")}


def validate_load_shadow(e: dict[str, Any]) -> dict[str, Any]:
    requests = integer(e["requests"], "requests", 1000)
    duration = integer(e["durationSeconds"], "durationSeconds", 60)
    zero_fields = (
        "rpcMismatches", "sseLostEvents", "indexerMismatches", "admissionFailOpen",
        "schedulerStarvation", "projectionMismatches",
    )
    for field in zero_fields:
        if integer(e[field], field) != 0:
            raise AcceptanceError(f"load/shadow invariant failed: {field} is nonzero")
    if not boolean(e["capsEnforced"], "capsEnforced") or not boolean(e["backpressureObserved"], "backpressureObserved"):
        raise AcceptanceError("load/shadow run did not prove caps and backpressure")
    return {"requests": requests, "durationSeconds": duration, **{field: 0 for field in zero_fields},
            "capsEnforced": True, "backpressureObserved": True}


def validate_rpc_load_shadow(e: dict[str, Any], latency_limit_ms: int, mismatch_limit: int) -> dict[str, Any]:
    if not 1 <= latency_limit_ms <= 500:
        raise AcceptanceError("RPC p99 latency assertion must be within 1..500ms")
    if mismatch_limit != 0:
        raise AcceptanceError("RPC shadow mismatch limit must remain zero")
    requests = integer(e["requests"], "requests", 1000)
    duration = integer(e["durationSeconds"], "durationSeconds", 60)
    p99 = integer(e["p99LatencyMs"], "p99LatencyMs")
    if p99 > latency_limit_ms:
        raise AcceptanceError(f"RPC p99 latency {p99}ms exceeds {latency_limit_ms}ms")
    matched_a = integer(e["rpcAMatchedResponses"], "rpcAMatchedResponses", requests)
    matched_b = integer(e["rpcBMatchedResponses"], "rpcBMatchedResponses", requests)
    mismatches = integer(e["mismatches"], "mismatches")
    fail_open = integer(e["criticalFailOpen"], "criticalFailOpen")
    if matched_a < requests or matched_b < requests or mismatches != 0 or fail_open != 0:
        raise AcceptanceError("RPC load/shadow run lost comparisons, mismatched, or failed open")
    return {
        "requests": requests, "durationSeconds": duration, "p99LatencyMs": p99,
        "latencyBudgetMs": latency_limit_ms, "rpcAMatchedResponses": matched_a,
        "rpcBMatchedResponses": matched_b, "mismatches": 0, "criticalFailOpen": 0,
    }


def validate_sse_load_shadow(e: dict[str, Any]) -> dict[str, Any]:
    published = integer(e["eventsPublished"], "eventsPublished", 1000)
    observed = integer(e["eventsObserved"], "eventsObserved", published)
    if observed < published:
        raise AcceptanceError("SSE shadow consumer did not observe every published event")
    for field in ("lostEvents", "duplicateEventIds"):
        if integer(e[field], field) != 0:
            raise AcceptanceError(f"SSE load/shadow invariant failed: {field} is nonzero")
    for field in ("reconnectResumed", "backpressureObserved", "subscriberCapEnforced", "eventIdsMonotonic"):
        if not boolean(e[field], field):
            raise AcceptanceError(f"SSE load/shadow invariant failed: {field}")
    return {
        "eventsPublished": published, "eventsObserved": observed, "lostEvents": 0,
        "duplicateEventIds": 0, "reconnectResumed": True, "backpressureObserved": True,
        "subscriberCapEnforced": True, "eventIdsMonotonic": True,
    }


def validate_indexer_load_shadow(e: dict[str, Any], lag_limit_blocks: int) -> dict[str, Any]:
    if not 1 <= lag_limit_blocks <= 8:
        raise AcceptanceError("indexer lag assertion must be within 1..8 blocks")
    head = hash32(e["indexerHeadHash"], "indexerHeadHash")
    if hash32(e["rpcAHeadHash"], "rpcAHeadHash") != head or hash32(e["rpcBHeadHash"], "rpcBHeadHash") != head:
        raise AcceptanceError("indexer canonical head differs from the two independent RPCs")
    lag = integer(e["lagBlocks"], "lagBlocks")
    if lag > lag_limit_blocks:
        raise AcceptanceError(f"indexer lag {lag} exceeds {lag_limit_blocks} blocks")
    compared = integer(e["comparedBlocks"], "comparedBlocks", 1000)
    retractions = integer(e["retractionsObserved"], "retractionsObserved", 1)
    for field in ("rollbackApplied", "reconciliationReady"):
        if not boolean(e[field], field):
            raise AcceptanceError(f"indexer load/shadow invariant failed: {field}")
    if integer(e["canonicalMismatches"], "canonicalMismatches") != 0:
        raise AcceptanceError("indexer shadow comparison found canonical mismatches")
    return {
        "indexerHeadHash": head, "lagBlocks": lag, "lagBudgetBlocks": lag_limit_blocks,
        "comparedBlocks": compared, "retractionsObserved": retractions, "canonicalMismatches": 0,
        "rollbackApplied": True, "reconciliationReady": True,
    }


def validate_admission_load_shadow(e: dict[str, Any]) -> dict[str, Any]:
    submitted = integer(e["submittedAdmissions"], "submittedAdmissions", 1000)
    committed = integer(e["walCommittedAdmissions"], "walCommittedAdmissions")
    if committed != submitted:
        raise AcceptanceError("admission WAL count differs from accepted submissions")
    for field in ("lostAdmissions", "failOpenAdmissions"):
        if integer(e[field], field) != 0:
            raise AcceptanceError(f"admission load/shadow invariant failed: {field} is nonzero")
    for field in ("backpressureObserved", "tenantCapEnforced"):
        if not boolean(e[field], field):
            raise AcceptanceError(f"admission load/shadow invariant failed: {field}")
    recovered = integer(e["recoveredAdmissions"], "recoveredAdmissions", 1)
    return {
        "submittedAdmissions": submitted, "walCommittedAdmissions": committed,
        "recoveredAdmissions": recovered, "lostAdmissions": 0, "failOpenAdmissions": 0,
        "backpressureObserved": True, "tenantCapEnforced": True,
    }


def validate_scheduler_load_shadow(e: dict[str, Any]) -> dict[str, Any]:
    completed_a = integer(e["tenantACompleted"], "tenantACompleted", 1)
    completed_b = integer(e["tenantBCompleted"], "tenantBCompleted", 1)
    min_share = integer(e["minimumTenantServiceShareBps"], "minimumTenantServiceShareBps")
    if min_share < 2500 or min_share > 10000:
        raise AcceptanceError("scheduler minimum tenant service share is outside 2500..10000 bps")
    for field in ("starvedJobs", "deadlineMisses", "edfViolations"):
        if integer(e[field], field) != 0:
            raise AcceptanceError(f"scheduler load/shadow invariant failed: {field} is nonzero")
    urgent = integer(e["canonicalUrgentPreemptions"], "canonicalUrgentPreemptions", 1)
    aging = integer(e["agingRecoveries"], "agingRecoveries", 1)
    if not boolean(e["capacityCapsEnforced"], "capacityCapsEnforced"):
        raise AcceptanceError("scheduler load did not enforce capacity caps")
    return {
        "tenantACompleted": completed_a, "tenantBCompleted": completed_b,
        "minimumTenantServiceShareBps": min_share, "starvedJobs": 0, "deadlineMisses": 0,
        "edfViolations": 0, "canonicalUrgentPreemptions": urgent, "agingRecoveries": aging,
        "capacityCapsEnforced": True,
    }


def validate_projection_parity_shadow(e: dict[str, Any], mismatch_limit: int) -> dict[str, Any]:
    if mismatch_limit != 0:
        raise AcceptanceError("projection shadow mismatch limit must remain zero")
    primary = digest(e["primaryProjectionDigest"], "primaryProjectionDigest")
    if digest(e["shadowProjectionDigest"], "shadowProjectionDigest") != primary:
        raise AcceptanceError("primary and shadow projection digests differ")
    rooms = integer(e["comparedRooms"], "comparedRooms", 100)
    events = integer(e["comparedEvents"], "comparedEvents", 1000)
    if integer(e["mismatchCount"], "mismatchCount") != 0:
        raise AcceptanceError("projection shadow comparison found mismatches")
    if not boolean(e["reorgCompared"], "reorgCompared") or not boolean(e["finalityFloorMatched"], "finalityFloorMatched"):
        raise AcceptanceError("projection parity did not cover reorg and finality-floor state")
    return {
        "projectionDigest": primary, "comparedRooms": rooms, "comparedEvents": events,
        "mismatchCount": 0, "reorgCompared": True, "finalityFloorMatched": True,
    }


def validate_trace_join(e: dict[str, Any]) -> dict[str, Any]:
    correlation = text(e["queueCorrelationId"], "queueCorrelationId")
    job_id = text(e["queueJobId"], "queueJobId")
    tenant_id = text(e["queueTenantId"], "queueTenantId")
    room_id = text(e["queueRoomId"], "queueRoomId")
    for field in ("agentCorrelationId", "proverCorrelationId", "heartbeatCorrelationId", "completionCorrelationId"):
        if text(e[field], field) != correlation:
            raise AcceptanceError(f"trace hop {field} split the correlation identity")
    for field in ("agentJobId", "proverJobId", "finalJobId"):
        if text(e[field], field) != job_id:
            raise AcceptanceError(f"trace hop {field} changed the job identity")
    if text(e["agentTenantId"], "agentTenantId") != tenant_id:
        raise AcceptanceError("agent trace changed the tenant identity")
    if text(e["agentRoomId"], "agentRoomId") != room_id:
        raise AcceptanceError("agent trace changed the room identity")
    if text(e["finalStatus"], "finalStatus") != "DONE":
        raise AcceptanceError("trace-join job did not reach DONE")
    if not boolean(e["heartbeatFinalized"], "heartbeatFinalized"):
        raise AcceptanceError("trace-join heartbeat is not finalized")
    if boolean(e["directSignerConfigured"], "directSignerConfigured"):
        raise AcceptanceError("packaged agent retained direct signer authority")
    if boolean(e["directRpcConfigured"], "directRpcConfigured"):
        raise AcceptanceError("packaged agent retained direct L1 RPC authority")
    records = integer(e["traceRecordCount"], "traceRecordCount", 3)
    return {
        "correlationId": correlation, "tenantId": tenant_id, "roomId": room_id, "jobId": job_id,
        "traceRecordCount": records, "finalStatus": "DONE", "heartbeatFinalized": True,
        "directSignerConfigured": False, "directRpcConfigured": False,
    }


VALIDATORS: dict[str, Callable[..., dict[str, Any]]] = {
    "reorg": validate_reorg,
    "rpc-disagreement": validate_rpc_disagreement,
    "split-brain-promotion": validate_split_brain,
    "database-object-restore": validate_restore,
    "sponsorship": validate_sponsorship,
    "queue-congestion": validate_queue,
    "headless-restart": validate_headless_restart,
    "blob": validate_blob,
    "partial-aggregate": validate_aggregate,
    "renewal": validate_renewal,
    "withdrawal": validate_withdrawal,
    "load-shadow": validate_load_shadow,
    "rpc-load-shadow": validate_rpc_load_shadow,
    "sse-load-shadow": validate_sse_load_shadow,
    "indexer-load-shadow": validate_indexer_load_shadow,
    "admission-load-shadow": validate_admission_load_shadow,
    "scheduler-load-shadow": validate_scheduler_load_shadow,
    "projection-parity-shadow": validate_projection_parity_shadow,
    "prover-agent-trace-join": validate_trace_join,
}


SOURCE_RULES: dict[str, dict[str, set[str]]] = {
    "reorg": {
        "previousBlockHash": {"indexer"}, "canonicalBlockHash": {"indexer"},
        "rpcABlockHash": {"rpc_a"}, "rpcBBlockHash": {"rpc_b"},
        "rollbackDepth": {"indexer", "fault"}, "retractionPreviousState": {"indexer"},
        "retractionReason": {"indexer"}, "reconciliationReady": {"indexer", "coordinator"},
    },
    "rpc-disagreement": {
        "agreedBeforeA": {"rpc_a"}, "agreedBeforeB": {"rpc_b"}, "disagreeA": {"rpc_a"},
        "disagreeB": {"rpc_b"}, "criticalStatus": {"coordinator"}, "restoredA": {"rpc_a"},
        "restoredB": {"rpc_b"}, "readyAfterRestore": {"coordinator"},
    },
    "split-brain-promotion": {
        "activeTerminated": {"failover"}, "staleWriterRejected": {"coordinator", "failover"},
        "promotedReady": {"coordinator"}, "signerAfterFence": {"failover"},
        "targetLsn": {"failover"}, "replayLsn": {"failover"}, "rtoSeconds": {"failover"},
    },
    "database-object-restore": {
        "sourceDatabaseDigest": {"backup"}, "restoredDatabaseDigest": {"backup"},
        "sourceObjectDigest": {"backup"}, "restoredObjectDigest": {"backup"},
        "freshDatabase": {"backup"}, "freshObjectStore": {"backup"}, "serviceReady": {"coordinator"},
    },
    "sponsorship": {
        "tenantIsolationDenied": {"coordinator"}, "firstOperationId": {"coordinator"},
        "replayOperationId": {"coordinator"}, "conflictingReplayDenied": {"coordinator"},
        "reservedQuantity": {"coordinator"}, "chargedQuantity": {"indexer", "coordinator"},
        "refundedQuantity": {"indexer", "coordinator"}, "doubleBilled": {"indexer", "coordinator"},
    },
    "queue-congestion": {
        "tenantACompleted": {"queue"}, "tenantBCompleted": {"queue"}, "starvedJobs": {"queue"},
        "leaseRecovered": {"queue"}, "backoffObserved": {"queue"}, "withinCaps": {"queue"},
        "edfViolations": {"queue", "indexer"},
    },
    "headless-restart": {
        "stateDigestBefore": {"headless"}, "stateDigestAfter": {"headless"},
        "admissionJournalBefore": {"headless"}, "admissionJournalAfter": {"headless"},
        "restartCount": {"fault"}, "lockRecovered": {"headless"},
        "roomReady": {"headless"}, "queueJobPreserved": {"queue"},
        "duplicateSequences": {"headless", "indexer"},
    },
    "blob": {
        "manifestDigest": {"coordinator"}, "archiveDigest": {"indexer"},
        "rawTransactionHash": {"coordinator"}, "rebroadcastRawTransactionHash": {"coordinator"},
        "signerCalls": {"coordinator"}, "finalized": {"indexer", "coordinator"},
        "sidecarVerified": {"indexer"},
    },
    "partial-aggregate": {
        "memberCount": {"coordinator"}, "uniqueRooms": {"coordinator"},
        "appliedMembers": {"indexer"}, "failedMembers": {"indexer"},
        "chargedMembers": {"indexer", "coordinator"}, "failedMemberCharges": {"indexer", "coordinator"},
        "retryApplied": {"indexer"}, "operationId": {"coordinator"}, "finalized": {"indexer"},
    },
    "renewal": {
        "checkpointFinalized": {"indexer"}, "freshPriceBound": {"coordinator", "indexer"},
        "maximumChargeEnforced": {"coordinator", "indexer"}, "refundQuantity": {"coordinator", "indexer"},
        "duplicateCharge": {"coordinator", "indexer"}, "handoffNoOverlap": {"indexer"},
    },
    "withdrawal": {
        "proofRootValid": {"coordinator"}, "operationId": {"coordinator"}, "finalized": {"indexer"},
        "replayDenied": {"coordinator", "indexer"}, "externalRaceCanonical": {"indexer"},
        "sponsoredGas": {"coordinator"},
    },
    "load-shadow": {
        "requests": {"coordinator", "fault"}, "durationSeconds": {"fault"},
        "rpcMismatches": {"rpc_a", "rpc_b", "fault"}, "sseLostEvents": {"coordinator", "fault"},
        "indexerMismatches": {"indexer", "fault"}, "admissionFailOpen": {"coordinator", "fault"},
        "schedulerStarvation": {"queue", "fault"}, "projectionMismatches": {"indexer", "fault"},
        "capsEnforced": {"coordinator", "queue", "fault"}, "backpressureObserved": {"coordinator", "queue", "fault"},
    },
    "rpc-load-shadow": {
        "requests": {"fault"}, "durationSeconds": {"fault"}, "p99LatencyMs": {"fault"},
        "rpcAMatchedResponses": {"rpc_a"}, "rpcBMatchedResponses": {"rpc_b"},
        "mismatches": {"fault"}, "criticalFailOpen": {"coordinator", "fault"},
    },
    "sse-load-shadow": {
        "eventsPublished": {"coordinator"}, "eventsObserved": {"fault"}, "lostEvents": {"fault"},
        "duplicateEventIds": {"fault"}, "reconnectResumed": {"fault"},
        "backpressureObserved": {"fault"}, "subscriberCapEnforced": {"coordinator", "fault"},
        "eventIdsMonotonic": {"fault"},
    },
    "indexer-load-shadow": {
        "indexerHeadHash": {"indexer"}, "rpcAHeadHash": {"rpc_a"}, "rpcBHeadHash": {"rpc_b"},
        "lagBlocks": {"indexer"}, "comparedBlocks": {"fault"}, "retractionsObserved": {"indexer"},
        "canonicalMismatches": {"fault"}, "rollbackApplied": {"indexer"},
        "reconciliationReady": {"indexer"},
    },
    "admission-load-shadow": {
        "submittedAdmissions": {"fault"}, "walCommittedAdmissions": {"coordinator"},
        "recoveredAdmissions": {"coordinator"}, "lostAdmissions": {"fault"},
        "failOpenAdmissions": {"fault"}, "backpressureObserved": {"fault"},
        "tenantCapEnforced": {"coordinator", "fault"},
    },
    "scheduler-load-shadow": {
        "tenantACompleted": {"queue"}, "tenantBCompleted": {"queue"},
        "minimumTenantServiceShareBps": {"fault"}, "starvedJobs": {"queue", "fault"},
        "deadlineMisses": {"queue", "indexer", "fault"}, "edfViolations": {"queue", "fault"},
        "canonicalUrgentPreemptions": {"queue", "indexer"}, "agingRecoveries": {"queue"},
        "capacityCapsEnforced": {"queue", "coordinator"},
    },
    "projection-parity-shadow": {
        "primaryProjectionDigest": {"indexer"}, "shadowProjectionDigest": {"fault"},
        "comparedRooms": {"fault"}, "comparedEvents": {"fault"}, "mismatchCount": {"fault"},
        "reorgCompared": {"fault"}, "finalityFloorMatched": {"indexer", "fault"},
    },
    "prover-agent-trace-join": {
        "queueCorrelationId": {"queue"}, "queueTenantId": {"queue"}, "queueRoomId": {"queue"},
        "queueJobId": {"queue"}, "agentCorrelationId": {"logs"}, "agentTenantId": {"logs"},
        "agentRoomId": {"logs"}, "agentJobId": {"logs"}, "proverCorrelationId": {"prover"},
        "proverJobId": {"prover"}, "heartbeatCorrelationId": {"coordinator"},
        "heartbeatFinalized": {"coordinator"}, "completionCorrelationId": {"logs"},
        "finalJobId": {"queue", "coordinator"}, "finalStatus": {"queue", "coordinator"},
        "directSignerConfigured": {"coordinator", "logs"}, "directRpcConfigured": {"coordinator", "logs"},
        "traceRecordCount": {"logs"},
    },
}


def scenario_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zkdeal-acceptance")
    sub = parser.add_subparsers(dest="scenario", required=True)
    item = sub.add_parser("reorg"); item.add_argument("--assert-rollback-and-retraction", action="store_true", required=True)
    item = sub.add_parser("rpc-disagreement"); item.add_argument("--assert-fail-closed", action="store_true", required=True)
    item = sub.add_parser("split-brain-promotion")
    item.add_argument("--kill-active", action="store_true", required=True)
    item.add_argument("--assert-fence", action="store_true", required=True)
    item.add_argument("--assert-rto-seconds", type=int, required=True)
    item = sub.add_parser("database-object-restore")
    item.add_argument("--fresh-database", action="store_true", required=True)
    item.add_argument("--fresh-object-store", action="store_true", required=True)
    item.add_argument("--assert-hashes", action="store_true", required=True)
    item = sub.add_parser("sponsorship")
    item.add_argument("--assert-tenant-isolation", action="store_true", required=True)
    item.add_argument("--assert-idempotency", action="store_true", required=True)
    item = sub.add_parser("queue-congestion")
    item.add_argument("--assert-fairness", action="store_true", required=True)
    item.add_argument("--assert-backoff", action="store_true", required=True)
    item = sub.add_parser("headless-restart")
    item.add_argument("--assert-state-continuity", action="store_true", required=True)
    item.add_argument("--assert-single-writer", action="store_true", required=True)
    item = sub.add_parser("blob")
    item.add_argument("--assert-hash-binding", action="store_true", required=True)
    item.add_argument("--assert-restart-resume", action="store_true", required=True)
    item = sub.add_parser("partial-aggregate"); item.add_argument("--assert-member-outcomes", action="store_true", required=True)
    item = sub.add_parser("renewal"); item.add_argument("--assert-deadline-and-metering", action="store_true", required=True)
    item = sub.add_parser("withdrawal")
    item.add_argument("--assert-finality", action="store_true", required=True)
    item.add_argument("--assert-replay-denial", action="store_true", required=True)
    item = sub.add_parser("load-shadow")
    item.add_argument("--assert-rpc-sse-indexer-admission-scheduler-projection", action="store_true", required=True)
    item = sub.add_parser("rpc-load-shadow")
    item.add_argument("--assert-latency-budget-ms", type=int, required=True)
    item.add_argument("--assert-mismatch-rate", type=int, required=True)
    item = sub.add_parser("sse-load-shadow")
    item.add_argument("--assert-reconnect", action="store_true", required=True)
    item.add_argument("--assert-backpressure", action="store_true", required=True)
    item.add_argument("--assert-subscriber-cap", action="store_true", required=True)
    item = sub.add_parser("indexer-load-shadow")
    item.add_argument("--assert-rollback", action="store_true", required=True)
    item.add_argument("--assert-lag-budget-blocks", type=int, required=True)
    item = sub.add_parser("admission-load-shadow")
    item.add_argument("--assert-wal-durable", action="store_true", required=True)
    item.add_argument("--assert-backpressure", action="store_true", required=True)
    item.add_argument("--assert-cap", action="store_true", required=True)
    item = sub.add_parser("scheduler-load-shadow")
    item.add_argument("--assert-mixed-tenant-fairness", action="store_true", required=True)
    item.add_argument("--assert-deadline-budget", action="store_true", required=True)
    item = sub.add_parser("projection-parity-shadow")
    item.add_argument("--assert-projection-parity", action="store_true", required=True)
    item.add_argument("--assert-mismatch-rate", type=int, required=True)
    item = sub.add_parser("prover-agent-trace-join")
    item.add_argument("--assert-coordinator-agent-prover-heartbeat", action="store_true", required=True)
    return parser


def validate_scenario_shape(name: str, value: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    if not isinstance(value, dict) or set(value) != {"steps", "evidence"}:
        raise AcceptanceError(f"scenario {name} must contain exactly steps and evidence")
    steps = value["steps"]
    refs = value["evidence"]
    if not isinstance(steps, list) or not steps or not isinstance(refs, dict):
        raise AcceptanceError(f"scenario {name} steps/evidence are malformed")
    rules = SOURCE_RULES[name]
    if set(refs) != set(rules):
        raise AcceptanceError(
            f"scenario {name} evidence keys differ: missing={sorted(set(rules)-set(refs))} "
            f"extra={sorted(set(refs)-set(rules))}"
        )
    ids: set[str] = set()
    by_id: dict[str, dict[str, Any]] = {}
    for raw in steps:
        if not isinstance(raw, dict):
            raise AcceptanceError(f"scenario {name} contains a non-object step")
        step_id = str(raw.get("id", ""))
        if step_id in ids:
            raise AcceptanceError(f"scenario {name} repeats step id {step_id}")
        endpoint = str(raw.get("endpoint", ""))
        supplied_auth = raw.get("auth")
        required_auth = CONTROL_ENDPOINT_AUTH.get(endpoint)
        if required_auth is not None and supplied_auth != required_auth:
            raise AcceptanceError(
                f"scenario {name} step {step_id} must use a scoped {endpoint} authority"
            )
        scoped_endpoint = CONTROL_AUTH_ENDPOINT.get(str(supplied_auth))
        if scoped_endpoint is not None and endpoint != scoped_endpoint:
            raise AcceptanceError(
                f"scenario {name} step {step_id} cannot send {supplied_auth} to {endpoint}"
            )
        if supplied_auth == "failover_approval":
            raise AcceptanceError(
                f"scenario {name} step {step_id} cannot use failover approval as a bearer"
            )
        approval_auth = raw.get("approvalAuth")
        if endpoint == "failover" and raw.get("method") != "GET":
            if approval_auth != "failover_approval":
                raise AcceptanceError(
                    f"scenario {name} step {step_id} must use a separate failover approval authority"
                )
        elif approval_auth is not None:
            raise AcceptanceError(
                f"scenario {name} step {step_id} contains an inapplicable approval authority"
            )
        ids.add(step_id); by_id[step_id] = raw
    checked: dict[str, dict[str, str]] = {}
    for field, raw in refs.items():
        if not isinstance(raw, dict) or set(raw) != {"step", "pointer"}:
            raise AcceptanceError(f"scenario {name} evidence ref {field} is malformed")
        step_id = str(raw["step"]); path = str(raw["pointer"])
        if step_id not in by_id:
            raise AcceptanceError(f"scenario {name} evidence ref {field} names an absent step")
        endpoint = str(by_id[step_id].get("endpoint", ""))
        if endpoint not in rules[field]:
            raise AcceptanceError(f"scenario {name} field {field} cannot trust endpoint {endpoint}")
        if not path.startswith("/"):
            raise AcceptanceError(f"scenario {name} evidence ref {field} must point into a response wrapper")
        checked[field] = {"step": step_id, "pointer": path}
    return steps, checked


def write_record(record: dict[str, Any]) -> tuple[Path, str]:
    root = Path(os.environ.get("ACCEPTANCE_EVIDENCE_DIR", "/evidence"))
    if root.is_symlink() or not root.is_dir():
        raise AcceptanceError("ACCEPTANCE_EVIDENCE_DIR is absent, not a directory, or a symlink")
    filename = f"{record['candidateId']}-{record['scenario']}.json"
    target = root / filename
    if target.exists() or target.is_symlink():
        raise AcceptanceError(f"write-once evidence already exists: {filename}")
    payload = canonical_bytes(record)
    temporary = root / f".{filename}.{os.getpid()}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target, sha256(payload)


def execute(args: argparse.Namespace) -> dict[str, Any]:
    plan, raw_plan = load_plan()
    name = args.scenario
    value = plan["scenarios"].get(name)
    if value is None:
        raise AcceptanceError(f"acceptance plan omits scenario {name}")
    steps, refs = validate_scenario_shape(name, value)
    pointers_by_step: dict[str, list[str]] = {}
    for ref in refs.values():
        pointers_by_step.setdefault(ref["step"], []).append(ref["pointer"])
    endpoint_names = {str(step.get("endpoint")) for step in steps} | {"coordinator"}
    executor = HttpExecutor(endpoint_names, plan["authTokenSha256"])
    started = utc_now()
    candidate_capability = executor.verify_candidate(
        int(plan["ownerDatabaseSchema"]), str(plan["hostedIntegrationToken"]),
    )
    results: dict[str, StepResult] = {}
    for step in steps:
        result = executor.run(step, pointers_by_step.get(str(step.get("id")), []))
        results[result.step_id] = result
    captured = {
        field: pointer(results[ref["step"]].pointer_document(), ref["pointer"])
        for field, ref in refs.items()
    }
    if name == "split-brain-promotion":
        limit = int(args.assert_rto_seconds)
        if limit < 1 or limit > 300:
            raise AcceptanceError("split-brain RTO assertion must be within 1..300 seconds")
        normalized = validate_split_brain(captured, limit)
    elif name == "rpc-load-shadow":
        normalized = validate_rpc_load_shadow(
            captured, int(args.assert_latency_budget_ms), int(args.assert_mismatch_rate),
        )
    elif name == "indexer-load-shadow":
        normalized = validate_indexer_load_shadow(captured, int(args.assert_lag_budget_blocks))
    elif name == "projection-parity-shadow":
        normalized = validate_projection_parity_shadow(captured, int(args.assert_mismatch_rate))
    else:
        normalized = VALIDATORS[name](captured)
    finished = utc_now()
    record = {
        "schemaVersion": 1,
        "passed": True,
        "candidateId": plan["candidateId"],
        "hostedIntegrationToken": plan["hostedIntegrationToken"],
        "scenario": name,
        "planSha256": sha256(raw_plan),
        "candidateCapability": candidate_capability,
        "startedAt": started,
        "finishedAt": finished,
        "steps": [results[str(step["id"])].summary() for step in steps],
        "endpointBindingsSha256": {
            alias: sha256(base.encode("utf-8"))
            for alias, base in sorted(executor.endpoints.items())
        },
        "credentialBindings": {
            alias: plan["authTokenSha256"][alias] for alias in sorted(executor.tokens)
        },
        "evidence": normalized,
    }
    target, record_hash = write_record(record)
    return {
        "passed": True, "scenario": name, "candidateId": plan["candidateId"],
        "record": str(target), "recordSha256": record_hash,
    }


def main(argv: list[str] | None = None) -> int:
    try:
        args = scenario_parser().parse_args(argv)
        print(json.dumps(execute(args), sort_keys=True, separators=(",", ":")))
        return 0
    except (AcceptanceError, OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
