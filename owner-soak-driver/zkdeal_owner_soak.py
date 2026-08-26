#!/usr/bin/env python3
"""Owner soak driver: the live workload half of the 12-hour zkdeal release soak.

The soak runner (soak-runner/zkdeal_soak.py plus scripts/soak.py) owns manifest
validation, restart supervision at the container boundary and the independent
journal closure verification.  This driver is the hash-bound owner command it
executes: one stdlib-only file that drives the real hosted lifecycle -- rooms,
admissions, live BatchInputV5 preparation, real CUDA Groth16 proving, owner
durable L1 publication, aggregation, sponsorship, withdrawal, reorg and
reconciliation -- injects every scheduled fault through the reviewed
fault-control and failover-provider surfaces, and appends the durable evidence
journal that soak.verify_closure replays.

Process shape: the entrypoint runs as a small supervisor that forks one
--worker subprocess.  The docker-host-restart-resume fault is executed by the
worker announcing the fault and then being SIGKILLed by the supervisor
mid-tree; the respawned worker must resume from the journal plus its own
driver state with contiguous sequence numbers, no duplicate nonces or charges
and byte-identical sealed outputs.

Everything the verifier demands is computed, never asserted blind: charge and
usage sums are recomputed from the journal and the live ledger and compared
against the manifest expectation before the closure event may be written.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping


SHA256 = re.compile(r"^[0-9a-f]{64}$")
HEX32 = re.compile(r"^0x[0-9a-f]{64}$")
HEX20 = re.compile(r"^0x[0-9a-f]{40}$")
EPHEMERAL_TOKEN = re.compile(r"^eph_[A-Za-z0-9_-]{28,120}$")
JOB_ID = re.compile(r"^pj-[0-9a-f]{20}$")
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024

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
AUTH_ALIASES = (
    "tenant_a", "tenant_b", "node_a", "node_b", "l1_liveness", "l1_room",
    "l1_aggregate", "l1_sponsor", "withdrawal", "fault_control",
    "backup_restore", "failover_control", "failover_approval",
)
TOKEN_FILE_ENV = {alias: "SOAK_AUTH_%s_TOKEN_FILE" % alias.upper() for alias in AUTH_ALIASES}

AGGREGATE_SELECTOR = "0x5e8b37ac"
WITHDRAWAL_SELECTOR = "0xb051a9f8"
SPONSOR_SELECTORS = {
    "reserve": "0x827ac259",
    "renew": "0xf180fe5d",
    "checkpoint": "0xe19bc67e",
    "dispose": "0xed97f11a",
}
REQUIRED_FAULTS = frozenset({
    "headless-restart", "prover-restart", "coordinator-promotion",
    "indexer-rollback", "rpc-split", "object-store-restart",
    "database-restart", "docker-host-restart-resume",
})
PULSE_COUNT = 36
AGGREGATE_MEMBERS = 8
AGGREGATE_BLOBS = 6
AGGREGATE_APPLIED = 7
AGGREGATE_FAILED = 1
# release-settlement-scenario.json: the last aggregate member's already-proved
# batch is submitted first as a single-room operation, so its unchanged
# aggregate member is stale at settlement and fails without being charged.
STALE_MEMBER_INDEX = AGGREGATE_MEMBERS - AGGREGATE_FAILED
# Calldata-availability aggregate members carry the zero hash in place of an
# equivalence program/statement (RoomManager submitAggregate convention).
ZERO_HASH = "0x" + "00" * 32
REORG_DEPTH = 3

# The exact reviewed release-soak argv markers; the supervisor respawns the
# worker with the same markers so the argparse contract is enforced end to end.
ARGV_MARKERS = (
    "--submit-real-proof-jobs",
    "--restart",
    "--assert-durable-results,cursors,nonces,charges,sealed-output,safety,claims,fairness,deadlines",
    "--bounded-backoff",
    "--emit-evidence-closure",
)


class DriverError(RuntimeError):
    """A fail-closed owner soak error; the closure is never written after it."""


class WorkerKilled(RuntimeError):
    """Raised by an injected kill hook to simulate the docker-host SIGKILL."""


class Clock:
    """Injectable monotonic time and sleep so tests can run virtual hours."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def canonical_event_bytes(event: Mapping[str, Any]) -> bytes:
    """Exact scripts/soak.py canonical journal form (ASCII-only bytes)."""
    return (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_json(raw: bytes, label: str) -> Any:
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise DriverError(f"{label} exceeds {MAX_DOCUMENT_BYTES} bytes")

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DriverError(f"{label} contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DriverError(f"{label} is not strict UTF-8 JSON: {exc}") from exc


def require_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise DriverError(f"{field} must be a nonempty bounded string")
    return value


def require_int(value: Any, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        if isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]*", value) and int(value) >= minimum:
            return int(value)
        raise DriverError(f"{field} must be an integer >= {minimum}")
    return value


def require_true(value: Any, field: str) -> bool:
    if value is not True:
        raise DriverError(f"{field} must be exactly true")
    return True


def fsync_directory(path: Path) -> None:
    """Best-effort durable flush of a directory entry (POSIX idiom)."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def load_manifest(path: Path) -> dict[str, Any]:
    """Minimal shape validation; scripts/soak.py owns full manifest policy."""
    value = parse_json(path.read_bytes(), "soak manifest")
    if not isinstance(value, dict) or value.get("kind") != "zkdeal-release-soak" or value.get("schemaVersion") != 1:
        raise DriverError("manifest must be a zkdeal-release-soak schemaVersion 1 object")
    duration = require_int(value.get("durationSeconds"), "durationSeconds", 1)
    faults = value.get("scheduledFaults")
    if not isinstance(faults, list):
        raise DriverError("manifest scheduledFaults must be a list")
    seen: dict[str, int] = {}
    for fault in faults:
        if not isinstance(fault, dict):
            raise DriverError("every scheduled fault must be an object")
        kind = require_str(fault.get("kind"), "scheduledFaults.kind")
        at_second = require_int(fault.get("atSecond"), "scheduledFaults.atSecond", 0)
        if kind in seen:
            raise DriverError(f"scheduled fault {kind} appears twice")
        if at_second >= duration:
            raise DriverError(f"scheduled fault {kind} is outside the soak duration")
        seen[kind] = at_second
    if set(seen) != set(REQUIRED_FAULTS):
        raise DriverError(f"manifest must schedule exactly the reviewed faults, got {sorted(seen)}")
    expected = value.get("expected")
    if not isinstance(expected, dict):
        raise DriverError("manifest expected must be an object")
    require_int(expected.get("usageUnits"), "expected.usageUnits", 0)
    require_int(expected.get("chargesWei"), "expected.chargesWei", 0)
    seed = value.get("chainSeed")
    if not isinstance(seed, dict) or not isinstance(seed.get("chainId"), int) or seed["chainId"] < 1:
        raise DriverError("manifest chainSeed.chainId must be a positive integer")
    return value


# ---------------------------------------------------------------------------
# Journal: append-only NDJSON evidence with fsync-per-line durability.
# ---------------------------------------------------------------------------


class Journal:
    """Append-only canonical NDJSON journal with a write-once closure.

    Every line is the exact soak.py canonical event form so the file bytes of
    the body equal canonical_event_bytes(events) and priorEventsSha256 can be
    recomputed identically by the independent verifier.
    """

    def __init__(self, path: Path, hook: Callable[[dict[str, Any]], None] | None = None):
        self.path = path
        self.hook = hook
        self.events: list[dict[str, Any]] = []
        self.event_ids: set[str] = set()
        self.closed = False
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise DriverError("soak journal must be a regular file")
            for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not raw.strip():
                    continue
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise DriverError(f"journal line {line_number} is not an object")
                if self.closed:
                    raise DriverError("journal contains events after its closure")
                self.events.append(value)
                if value.get("kind") == "closure":
                    self.closed = True
                event_id = value.get("eventId")
                if isinstance(event_id, str):
                    self.event_ids.add(event_id)
            for expected, event in enumerate(self.events, 1):
                if event.get("seq") != expected:
                    raise DriverError(f"journal sequence is not contiguous at event {expected}")

    def _write(self, event: dict[str, Any]) -> None:
        if self.hook is not None:
            self.hook(event)
        payload = canonical_event_bytes(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

    def append(self, kind: str, fields: dict[str, Any]) -> dict[str, Any]:
        if self.closed:
            raise DriverError("journal closure already written; the journal is append-only and closed")
        event = {"seq": len(self.events) + 1, "kind": kind, **fields}
        self._write(event)
        self.events.append(event)
        event_id = event.get("eventId")
        if isinstance(event_id, str):
            self.event_ids.add(event_id)
        return event

    def emit_once(self, event_id: str, kind: str, **fields: Any) -> dict[str, Any] | None:
        """Exactly-once emission keyed by a deterministic eventId."""
        if event_id in self.event_ids:
            return None
        return self.append(kind, {"eventId": event_id, **fields})

    def close(self, fields: dict[str, Any]) -> dict[str, Any]:
        if self.closed:
            raise DriverError("journal closure already written")
        prior = sha256_hex(b"".join(canonical_event_bytes(event) for event in self.events))
        event = {"seq": len(self.events) + 1, "kind": "closure", "priorEventsSha256": prior, **fields}
        self._write(event)
        self.events.append(event)
        self.closed = True
        return event


# ---------------------------------------------------------------------------
# DriverState: the driver's own durable exactly-once step ledger.
# ---------------------------------------------------------------------------


class DriverState:
    """Durable driver state, distinct from the runner's SOAK_STATE_FILE.

    Holds the run identity used to derive deterministic idempotency keys, the
    completed timeline step keys, per-purpose idempotency attempt counters and
    the checkpointed elapsed-seconds position for restart resume.
    """

    def __init__(self, path: Path, manifest_sha256: str):
        self.path = path
        self.existed = path.exists()
        if self.existed:
            if path.is_symlink() or not path.is_file():
                raise DriverError("owner driver state must be a regular file")
            value = parse_json(path.read_bytes(), "owner driver state")
            if not isinstance(value, dict) or value.get("schemaVersion") != 1:
                raise DriverError("owner driver state schema is invalid")
            if value.get("manifestSha256") != manifest_sha256:
                raise DriverError("resume manifest does not match the persisted owner driver state")
            self.value = value
        else:
            self.value = {
                "schemaVersion": 1,
                "manifestSha256": manifest_sha256,
                "runId": sha256_hex(os.urandom(32))[:16],
                "steps": {},
                "attempts": {},
                "elapsedSeconds": 0,
                "ledgerCursor": 0,
                "resumeVerified": False,
                "workerBoots": 0,
                "data": {},
            }
            self.save()

    def save(self) -> None:
        payload = (json.dumps(self.value, indent=2, sort_keys=True) + "\n").encode()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        fsync_directory(self.path.parent)

    @property
    def run_id(self) -> str:
        return str(self.value["runId"])

    def step_done(self, key: str) -> bool:
        return key in self.value["steps"]

    def mark_done(self, key: str, elapsed_seconds: int, result: dict[str, Any] | None = None) -> None:
        self.value["steps"][key] = {"completedAtSecond": elapsed_seconds, "result": result or {}}
        self.value["elapsedSeconds"] = max(int(self.value.get("elapsedSeconds", 0)), elapsed_seconds)
        self.save()

    def step_result(self, key: str) -> dict[str, Any]:
        step = self.value["steps"].get(key)
        return dict(step.get("result", {})) if isinstance(step, dict) else {}

    def attempt(self, purpose: str) -> int:
        return int(self.value["attempts"].get(purpose, 1))

    def bump_attempt(self, purpose: str) -> int:
        value = self.attempt(purpose) + 1
        self.value["attempts"][purpose] = value
        self.save()
        return value

    def get(self, key: str, default: Any = None) -> Any:
        return self.value["data"].get(key, default)

    def put(self, key: str, value: Any) -> None:
        self.value["data"][key] = value
        self.save()


# ---------------------------------------------------------------------------
# Http: bounded, redirect-free, hash-aware HTTP (adapted from HttpExecutor).
# ---------------------------------------------------------------------------


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def normalized_base(value: str, label: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise DriverError(f"{label} is malformed") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise DriverError(f"{label} must be an HTTP(S) URL without user info")
    if parsed.query or parsed.fragment:
        raise DriverError(f"{label} must not contain query or fragment data")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def safe_path(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value:
        raise DriverError("request path must be an absolute URL path")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment or any(part == ".." for part in parsed.path.split("/")):
        raise DriverError("request path escaped its endpoint or contains a fragment")
    return value


ERROR_DETAIL_LIMIT = 400


def describe_error_body(parsed: Any, raw: bytes) -> str:
    """Render a rejected response compactly enough to put in an error message.

    A refusal that reports only its status code is close to useless: `HTTP 400`
    tells an operator that the coordinator disliked the request but not which
    part of it, and the coordinator's own correlated-request log records an
    empty error object for these, so the response body is the only place the
    reason exists. Prefer the conventional fields, fall back to the whole
    document, and bound the length so a large body cannot flood the journal.
    """
    if isinstance(parsed, dict):
        for key in ("message", "error", "detail", "reason", "title"):
            value = parsed.get(key)
            if isinstance(value, str) and value:
                code = parsed.get("code")
                if isinstance(code, str) and code and code != value:
                    return f"{code}: {value}"[:ERROR_DETAIL_LIMIT]
                return value[:ERROR_DETAIL_LIMIT]
        try:
            return json.dumps(parsed, sort_keys=True)[:ERROR_DETAIL_LIMIT]
        except (TypeError, ValueError):
            pass
    if parsed is not None:
        return str(parsed)[:ERROR_DETAIL_LIMIT]
    if raw:
        return raw.decode("utf-8", "replace").strip()[:ERROR_DETAIL_LIMIT]
    return ""


class Http:
    """Bounded-attempt HTTP client with file-mounted scoped bearer tokens."""

    def __init__(self, environ: Mapping[str, str], clock: Clock):
        self.environ = environ
        self.clock = clock
        self.endpoints: dict[str, str] = {}
        for alias, env_name in sorted(ENDPOINT_ENV.items()):
            raw = environ.get(env_name, "")
            if not raw:
                raise DriverError(f"{env_name} is required by the release soak")
            self.endpoints[alias] = normalized_base(raw, env_name)
        if self.endpoints["rpc_a"] == self.endpoints["rpc_b"]:
            raise DriverError("L1_RPC_A and L1_RPC_B must be distinct provider URLs")
        self.tokens: dict[str, str] = {}
        self.opener = urllib.request.build_opener(NoRedirect())
        try:
            timeout = float(environ.get("SOAK_HTTP_TIMEOUT_SECONDS", "10"))
        except ValueError as exc:
            raise DriverError("SOAK_HTTP_TIMEOUT_SECONDS must be a number") from exc
        self.timeout = max(1.0, min(timeout, 30.0))
        self.max_wait_ms = 0

    def token(self, alias: str) -> str:
        if alias in self.tokens:
            return self.tokens[alias]
        env_name = TOKEN_FILE_ENV.get(alias)
        if not env_name:
            raise DriverError(f"unknown auth alias {alias}")
        path_value = self.environ.get(env_name, "")
        if not path_value:
            raise DriverError(f"{env_name} is required by the release soak")
        path = Path(path_value)
        if path.is_symlink() or not path.is_file():
            raise DriverError(f"{env_name} is absent, not regular, or a symlink")
        # POSIX permission bits are the enforced boundary in the Linux runtime;
        # platforms that do not honor st_mode never run the release soak.
        if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise DriverError(f"{env_name} must be private to its owning user")
        raw = path.read_bytes()
        if len(raw) > 4096:
            raise DriverError(f"{env_name} is unexpectedly large")
        try:
            value = raw.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise DriverError(f"{env_name} is not UTF-8") from exc
        if not EPHEMERAL_TOKEN.fullmatch(value):
            raise DriverError(f"token for auth alias {alias} is not an enclave-scoped eph_ token")
        self.tokens[alias] = value
        return value

    def request(
        self,
        endpoint: str,
        method: str,
        path: str,
        *,
        body: Any = None,
        auth: str | None = None,
        headers: Mapping[str, str] | None = None,
        expect: tuple[int, ...] = (200,),
        attempts: int = 1,
        interval: float = 1.0,
        label: str = "",
    ) -> tuple[int, Any, bytes]:
        if endpoint not in self.endpoints:
            raise DriverError(f"request uses an unknown endpoint alias {endpoint}")
        if method not in {"GET", "POST", "PUT", "DELETE"}:
            raise DriverError(f"request uses a forbidden method {method}")
        if method == "GET" and body is not None:
            raise DriverError("GET requests must not include a body")
        if not isinstance(attempts, int) or not 1 <= attempts <= 600:
            raise DriverError("request attempts are outside 1..600")
        if not 0.0 <= float(interval) <= 60.0:
            raise DriverError("request interval is outside 0..60 seconds")
        request_bytes = b"" if body is None else canonical_bytes(body)
        if len(request_bytes) > MAX_DOCUMENT_BYTES:
            raise DriverError("request body exceeds the byte limit")
        merged = {"accept": "application/json"}
        if body is not None:
            merged["content-type"] = "application/json"
        for name, value in (headers or {}).items():
            lowered = str(name).lower()
            text = str(value)
            if "\r" in text or "\n" in text or len(text) > 256:
                raise DriverError("request contains a malformed header value")
            merged[lowered] = text
        if auth is not None:
            merged["authorization"] = f"Bearer {self.token(auth)}"
        url = self.endpoints[endpoint] + safe_path(path)
        name = label or f"{method} {endpoint}{path}"
        last_error = "request was not attempted"
        for attempt in range(1, attempts + 1):
            try:
                request = urllib.request.Request(
                    url, data=request_bytes if body is not None else None, headers=merged, method=method,
                )
                try:
                    response = self.opener.open(request, timeout=self.timeout)
                    status = int(response.status)
                    raw = response.read(MAX_DOCUMENT_BYTES + 1)
                except urllib.error.HTTPError as exc:
                    status = int(exc.code)
                    raw = exc.read(MAX_DOCUMENT_BYTES + 1)
                if len(raw) > MAX_DOCUMENT_BYTES:
                    raise DriverError(f"{name} response exceeds the byte limit")
                parsed: Any = None
                if raw:
                    parsed = parse_json(raw, f"{name} response")
                if status in expect:
                    return status, parsed, raw
                last_error = f"HTTP {status} not in {sorted(expect)}"
                detail = describe_error_body(parsed, raw)
                if detail:
                    last_error = f"{last_error}: {detail}"
            except (OSError, urllib.error.URLError, DriverError) as exc:
                last_error = str(exc)
            if attempt < attempts and interval > 0:
                # Bounded backoff: the wait grows geometrically but is capped
                # so no single fairness wait can exceed the reviewed budget.
                wait = min(interval * (2 ** min(attempt - 1, 4)), 5.0)
                self.max_wait_ms = max(self.max_wait_ms, int(wait * 1000))
                self.clock.sleep(wait)
        raise DriverError(f"{name} failed after {attempts} attempts: {last_error}")


# ---------------------------------------------------------------------------
# L1 primitives: keccak-256, RLP, secp256k1 and EIP-712.
#
# The admission workload has to produce two genuinely signed transactions -- the
# L1 `queueDeposit` that creates the claimable inbox entry, and the L2 room
# transaction whose sender the coordinator recovers -- and has to re-derive the
# EIP-712 admission digest so a returned receipt can be checked against the
# room's on-chain admission signer instead of being trusted. The soak container
# is stdlib-only, so those four primitives are implemented here, exactly and
# only as far as that job needs them.
# ---------------------------------------------------------------------------


_MASK64 = (1 << 64) - 1
_KECCAK_ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
_KECCAK_ROTATIONS = (
    (0, 36, 3, 41, 18), (1, 44, 10, 45, 2), (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56), (27, 20, 39, 8, 14),
)
_KECCAK_RATE_BYTES = 136

SECP256K1_P = (1 << 256) - (1 << 32) - 977
SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
SECP256K1_HALF_N = SECP256K1_N >> 1
SECP256K1_GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
SECP256K1_GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
SECP256K1_G = (SECP256K1_GX, SECP256K1_GY, 1)

ZERO_ADDRESS = "0x" + "00" * 20
# The anvil genesis account the soak rig deploys with; overridden through
# SOAK_L1_DEPOSITOR_KEY / SOAK_L1_DEPOSITOR_KEY_FILE on any other L1.
ANVIL_GENESIS_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

# RoomManagerIntakeFacet.queueDeposit / RoomManagerBase.DepositQueued.
QUEUE_DEPOSIT_SIGNATURE = "queueDeposit(uint64,address,uint256,address)"
DEPOSIT_QUEUED_SIGNATURE = "DepositQueued(uint64,uint64,address,address,uint256)"
ROOM_STATE_SIGNATURE = "roomState(uint64)"
# RoomManager.sol: keccak256("ZkdealRoom") / keccak256("6") / block.chainid /
# address(this), and RoomManagerBase.ADMISSION_RECEIPT_TYPEHASH verbatim.
EIP712_DOMAIN_TYPE = (
    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)
ADMISSION_DOMAIN_NAME = "ZkdealRoom"
ADMISSION_DOMAIN_VERSION = "6"
ADMISSION_RECEIPT_TYPE = (
    "AdmissionReceipt(uint64 roomId,uint64 admissionId,bytes32 transactionHash,"
    "uint64 depositInboxId,bytes32 depositContentHash,uint64 deadlineBlock,"
    "uint64 maximumBatchIndex,uint64 bondEpoch,uint256 admissionFee)"
)
# IRoomManager.Room as RoomManagerObservationFacet.roomState returns it: a fully
# static tuple, so the eth_call return data is exactly these 44 words in order.
ROOM_STATE_WORD_COUNT = 44
ROOM_STATE_WORDS = {
    "state": 0,
    "authorizationMode": 1,
    "batchIndex": 18,
    "inboxCursor": 22,
    "nextInboxId": 23,
    "admissionCursor": 24,
    "minimumDepositConfirmations": 31,
    "maximumAdmissionWindow": 35,
    "bondEpoch": 36,
    "admissionSigner": 37,
    "minimumServiceBond": 38,
    "omissionPenalty": 39,
    "serviceBond": 40,
}
ROOM_STATE_ADDRESS_WORDS = frozenset({"admissionSigner"})
ROOM_STATE_OPEN = 1
ROOM_AUTHORIZATION_VALIDITY_ONLY = 1
# web2-api/server/src/admission.ts DEFAULT_MINIMUM_DEADLINE_LEAD_BLOCKS.
DEFAULT_MINIMUM_DEADLINE_LEAD_BLOCKS = 8
# Broadcast answers that mean "this exact transaction is already on the node",
# which is the normal outcome of replaying a durably recorded deposit.
BENIGN_BROADCAST_ERRORS = (
    "already known", "already imported", "known transaction",
    "nonce too low", "transaction already exists",
)


def _rotl64(value: int, shift: int) -> int:
    shift %= 64
    if shift == 0:
        return value & _MASK64
    return ((value << shift) | (value >> (64 - shift))) & _MASK64


def _keccak_f1600(lanes: list[int]) -> None:
    for round_constant in _KECCAK_ROUND_CONSTANTS:
        parity = [
            lanes[x] ^ lanes[x + 5] ^ lanes[x + 10] ^ lanes[x + 15] ^ lanes[x + 20]
            for x in range(5)
        ]
        theta = [parity[(x - 1) % 5] ^ _rotl64(parity[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(0, 25, 5):
                lanes[x + y] ^= theta[x]
        rotated = [0] * 25
        for x in range(5):
            for y in range(5):
                rotated[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl64(
                    lanes[x + 5 * y], _KECCAK_ROTATIONS[x][y]
                )
        for x in range(5):
            for y in range(5):
                lanes[x + 5 * y] = rotated[x + 5 * y] ^ (
                    (~rotated[(x + 1) % 5 + 5 * y] & _MASK64) & rotated[(x + 2) % 5 + 5 * y]
                )
        lanes[0] ^= round_constant


def keccak256(data: bytes) -> bytes:
    """Keccak-256 (the Ethereum pre-standard padding, not SHA3-256)."""
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % _KECCAK_RATE_BYTES:
        padded.append(0x00)
    padded[-1] ^= 0x80
    lanes = [0] * 25
    for offset in range(0, len(padded), _KECCAK_RATE_BYTES):
        block = padded[offset:offset + _KECCAK_RATE_BYTES]
        for index in range(_KECCAK_RATE_BYTES // 8):
            lanes[index] ^= int.from_bytes(block[index * 8:index * 8 + 8], "little")
        _keccak_f1600(lanes)
    return b"".join(lane.to_bytes(8, "little") for lane in lanes[:4])


def keccak_hex(data: bytes) -> str:
    return "0x" + keccak256(data).hex()


def function_selector(signature: str) -> bytes:
    return keccak256(signature.encode("ascii"))[:4]


def event_topic(signature: str) -> str:
    return keccak_hex(signature.encode("ascii"))


def strip_hex(value: Any, field: str) -> str:
    text = value if isinstance(value, str) else ""
    if not text.startswith("0x") or len(text) % 2 or not re.fullmatch(r"0x[0-9a-fA-F]*", text):
        raise DriverError(f"{field} is not even-length 0x hexadecimal")
    return text[2:].lower()


def hex_bytes(value: Any, field: str) -> bytes:
    return bytes.fromhex(strip_hex(value, field))


def hex_int(value: Any, field: str) -> int:
    text = value if isinstance(value, str) else ""
    if not re.fullmatch(r"0x[0-9a-fA-F]+", text):
        raise DriverError(f"{field} is not a 0x quantity")
    return int(text, 16)


def abi_uint(value: int) -> bytes:
    number = int(value)
    if number < 0 or number >= (1 << 256):
        raise DriverError("ABI uint256 word is out of range")
    return number.to_bytes(32, "big")


def abi_address(value: str) -> bytes:
    raw = hex_bytes(value, "ABI address")
    if len(raw) != 20:
        raise DriverError("ABI address word must be 20 bytes")
    return bytes(12) + raw


def abi_bytes32(value: str) -> bytes:
    raw = hex_bytes(value, "ABI bytes32")
    if len(raw) != 32:
        raise DriverError("ABI bytes32 word must be 32 bytes")
    return raw


def rlp_encode(item: Any) -> bytes:
    """Canonical RLP over integers, byte strings and (nested) lists."""
    if isinstance(item, list):
        payload = b"".join(rlp_encode(entry) for entry in item)
        return _rlp_prefix(len(payload), 0xC0) + payload
    if isinstance(item, bool):
        raise DriverError("RLP does not encode booleans")
    if isinstance(item, int):
        if item < 0:
            raise DriverError("RLP does not encode negative integers")
        payload = item.to_bytes((item.bit_length() + 7) // 8, "big") if item else b""
    elif isinstance(item, (bytes, bytearray)):
        payload = bytes(item)
    else:
        raise DriverError(f"RLP cannot encode {type(item).__name__}")
    if len(payload) == 1 and payload[0] < 0x80:
        return payload
    return _rlp_prefix(len(payload), 0x80) + payload


def _rlp_prefix(length: int, offset: int) -> bytes:
    if length < 56:
        return bytes([offset + length])
    encoded = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([offset + 55 + len(encoded)]) + encoded


def _jacobian_double(point: tuple[int, int, int]) -> tuple[int, int, int]:
    x, y, z = point
    if y == 0 or z == 0:
        return (0, 0, 0)
    ysq = (y * y) % SECP256K1_P
    s = (4 * x * ysq) % SECP256K1_P
    m = (3 * x * x) % SECP256K1_P
    nx = (m * m - 2 * s) % SECP256K1_P
    ny = (m * (s - nx) - 8 * ysq * ysq) % SECP256K1_P
    nz = (2 * y * z) % SECP256K1_P
    return (nx, ny, nz)


def _jacobian_add(left: tuple[int, int, int], right: tuple[int, int, int]) -> tuple[int, int, int]:
    if left[1] == 0 or left[2] == 0:
        return right
    if right[1] == 0 or right[2] == 0:
        return left
    x1, y1, z1 = left
    x2, y2, z2 = right
    z1sq = (z1 * z1) % SECP256K1_P
    z2sq = (z2 * z2) % SECP256K1_P
    u1 = (x1 * z2sq) % SECP256K1_P
    u2 = (x2 * z1sq) % SECP256K1_P
    s1 = (y1 * z2sq * z2) % SECP256K1_P
    s2 = (y2 * z1sq * z1) % SECP256K1_P
    if u1 == u2:
        if s1 != s2:
            return (0, 0, 0)
        return _jacobian_double(left)
    h = (u2 - u1) % SECP256K1_P
    r = (s2 - s1) % SECP256K1_P
    h2 = (h * h) % SECP256K1_P
    h3 = (h * h2) % SECP256K1_P
    u1h2 = (u1 * h2) % SECP256K1_P
    nx = (r * r - h3 - 2 * u1h2) % SECP256K1_P
    ny = (r * (u1h2 - nx) - s1 * h3) % SECP256K1_P
    nz = (h * z1 * z2) % SECP256K1_P
    return (nx, ny, nz)


def _jacobian_multiply(point: tuple[int, int, int], scalar: int) -> tuple[int, int, int]:
    factor = scalar % SECP256K1_N
    if factor == 0 or point[1] == 0 or point[2] == 0:
        return (0, 0, 0)
    result = (0, 0, 0)
    addend = point
    while factor:
        if factor & 1:
            result = _jacobian_add(result, addend)
        addend = _jacobian_double(addend)
        factor >>= 1
    return result


def _to_affine(point: tuple[int, int, int]) -> tuple[int, int]:
    x, y, z = point
    if y == 0 or z == 0:
        raise DriverError("secp256k1 produced the point at infinity")
    inverse = pow(z, SECP256K1_P - 2, SECP256K1_P)
    square = (inverse * inverse) % SECP256K1_P
    return ((x * square) % SECP256K1_P, (y * square * inverse) % SECP256K1_P)


def private_key_int(value: str, field: str = "L1 private key") -> int:
    raw = hex_bytes(value, field)
    if len(raw) != 32:
        raise DriverError(f"{field} must be 32 bytes")
    key = int.from_bytes(raw, "big")
    if not 0 < key < SECP256K1_N:
        raise DriverError(f"{field} is outside the secp256k1 scalar field")
    return key


def public_key_address(public_key: tuple[int, int]) -> str:
    encoded = public_key[0].to_bytes(32, "big") + public_key[1].to_bytes(32, "big")
    return "0x" + keccak256(encoded)[12:].hex()


def private_key_address(private_key: int) -> str:
    return public_key_address(_to_affine(_jacobian_multiply(SECP256K1_G, private_key)))


def _rfc6979_nonces(digest: bytes, private_key: int):
    """RFC 6979 deterministic k over HMAC-SHA256 (bits2octets, mod n)."""
    key_octets = private_key.to_bytes(32, "big")
    message_octets = (int.from_bytes(digest, "big") % SECP256K1_N).to_bytes(32, "big")
    v = b"\x01" * 32
    k = b"\x00" * 32
    k = hmac.new(k, v + b"\x00" + key_octets + message_octets, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\x01" + key_octets + message_octets, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    for _attempt in range(1024):
        v = hmac.new(k, v, hashlib.sha256).digest()
        candidate = int.from_bytes(v, "big")
        if 0 < candidate < SECP256K1_N:
            yield candidate
        k = hmac.new(k, v + b"\x00", hashlib.sha256).digest()
        v = hmac.new(k, v, hashlib.sha256).digest()


def sign_digest(digest: bytes, private_key: int) -> tuple[int, int, int]:
    """Deterministic low-s ECDSA over secp256k1; returns (r, s, recoveryId)."""
    if len(digest) != 32:
        raise DriverError("secp256k1 signs exactly one 32-byte digest")
    message = int.from_bytes(digest, "big") % SECP256K1_N
    for nonce in _rfc6979_nonces(digest, private_key):
        point = _to_affine(_jacobian_multiply(SECP256K1_G, nonce))
        r = point[0] % SECP256K1_N
        if r == 0:
            continue
        s = (pow(nonce, SECP256K1_N - 2, SECP256K1_N) * (message + r * private_key)) % SECP256K1_N
        if s == 0:
            continue
        recovery = (point[1] & 1) | (2 if point[0] >= SECP256K1_N else 0)
        if s > SECP256K1_HALF_N:
            # EIP-2 low-s normalization flips the parity of the recovered R.
            s = SECP256K1_N - s
            recovery ^= 1
        return r, s, recovery
    raise DriverError("secp256k1 signing exhausted its deterministic nonce sequence")


def recover_public_key(digest: bytes, r: int, s: int, recovery: int) -> tuple[int, int]:
    if len(digest) != 32 or not 0 < r < SECP256K1_N or not 0 < s < SECP256K1_N:
        raise DriverError("secp256k1 signature components are outside the scalar field")
    if recovery not in (0, 1, 2, 3):
        raise DriverError("secp256k1 recovery id is outside 0..3")
    x = r + SECP256K1_N if recovery >= 2 else r
    if x >= SECP256K1_P:
        raise DriverError("secp256k1 recovery x coordinate is outside the field")
    alpha = (pow(x, 3, SECP256K1_P) + 7) % SECP256K1_P
    beta = pow(alpha, (SECP256K1_P + 1) // 4, SECP256K1_P)
    if (beta * beta - alpha) % SECP256K1_P:
        raise DriverError("secp256k1 recovery point is not on the curve")
    y = beta if (beta & 1) == (recovery & 1) else SECP256K1_P - beta
    message = int.from_bytes(digest, "big") % SECP256K1_N
    combined = _jacobian_add(
        _jacobian_multiply((x, y, 1), s),
        _jacobian_multiply(SECP256K1_G, (SECP256K1_N - message) % SECP256K1_N),
    )
    inverse = pow(r, SECP256K1_N - 2, SECP256K1_N)
    return _to_affine(_jacobian_multiply(combined, inverse))


def recover_signature_address(digest: bytes, signature: Any, field: str) -> str:
    raw = hex_bytes(signature, field)
    if len(raw) != 65:
        raise DriverError(f"{field} must be 65 signature bytes")
    r = int.from_bytes(raw[0:32], "big")
    s = int.from_bytes(raw[32:64], "big")
    v = raw[64]
    recovery = v - 27 if v in (27, 28) else v
    if recovery not in (0, 1):
        raise DriverError(f"{field} carries an unusable recovery byte {v}")
    if s > SECP256K1_HALF_N:
        raise DriverError(f"{field} is not low-s normalized")
    return public_key_address(recover_public_key(digest, r, s, recovery))


def sign_legacy_transaction(
    *,
    nonce: int,
    gas_price: int,
    gas_limit: int,
    to: str,
    value: int,
    data: bytes,
    chain_id: int,
    private_key: int,
) -> tuple[str, str]:
    """EIP-155 legacy transaction; returns (rawSignedTransaction, hash)."""
    recipient = hex_bytes(to, "transaction recipient")
    if len(recipient) != 20:
        raise DriverError("transaction recipient must be a 20-byte address")
    unsigned = [nonce, gas_price, gas_limit, recipient, value, data, chain_id, 0, 0]
    r, s, recovery = sign_digest(keccak256(rlp_encode(unsigned)), private_key)
    signed = [
        nonce, gas_price, gas_limit, recipient, value, data,
        chain_id * 2 + 35 + recovery, r, s,
    ]
    raw = rlp_encode(signed)
    return "0x" + raw.hex(), keccak_hex(raw)


def eip712_domain_separator(name: str, version: str, chain_id: int, verifying_contract: str) -> bytes:
    return keccak256(
        keccak256(EIP712_DOMAIN_TYPE.encode("ascii"))
        + keccak256(name.encode("ascii"))
        + keccak256(version.encode("ascii"))
        + abi_uint(chain_id)
        + abi_address(verifying_contract)
    )


def admission_receipt_digest(
    chain_id: int, room_manager: str, receipt: Mapping[str, Any],
) -> bytes:
    """The exact digest RoomManagerBase._admissionReceiptHash is checked against."""
    struct_hash = keccak256(
        keccak256(ADMISSION_RECEIPT_TYPE.encode("ascii"))
        + abi_uint(require_int(receipt.get("roomId"), "receipt.roomId"))
        + abi_uint(require_int(receipt.get("admissionId"), "receipt.admissionId"))
        + abi_bytes32(receipt.get("transactionHash"))
        + abi_uint(require_int(receipt.get("depositInboxId"), "receipt.depositInboxId"))
        + abi_bytes32(receipt.get("depositContentHash"))
        + abi_uint(require_int(receipt.get("deadlineBlock"), "receipt.deadlineBlock"))
        + abi_uint(require_int(receipt.get("maximumBatchIndex"), "receipt.maximumBatchIndex"))
        + abi_uint(require_int(receipt.get("bondEpoch"), "receipt.bondEpoch"))
        + abi_uint(require_int(receipt.get("admissionFee"), "receipt.admissionFee"))
    )
    separator = eip712_domain_separator(
        ADMISSION_DOMAIN_NAME, ADMISSION_DOMAIN_VERSION, chain_id, room_manager,
    )
    return keccak256(b"\x19\x01" + separator + struct_hash)


def deposit_content_hash(depositor: str, beneficiary: str, asset: str, amount: int) -> str:
    """RoomManagerBase._hashDepositContent(depositor, beneficiary, asset, amount)."""
    return keccak_hex(
        abi_address(depositor) + abi_address(beneficiary) + abi_address(asset) + abi_uint(amount)
    )


# ---------------------------------------------------------------------------
# Stack: typed wrappers over the hosted owner, queue, prover and indexer.
# ---------------------------------------------------------------------------


class Stack:
    def __init__(self, http: Http, clock: Clock, environ: Mapping[str, str], manifest: Mapping[str, Any]):
        self.http = http
        self.clock = clock
        self.environ = environ
        self.chain_id = int(manifest["chainSeed"]["chainId"])
        self.addresses: dict[str, str] = {}
        # True only once capabilities() has published a complete address set.
        # It lets address() distinguish "nobody negotiated" from "negotiated,
        # but not under that name", which need different remedies.
        self.negotiated = False
        self.minimum_confirmations = int(environ.get("ZKDEAL_MINIMUM_CONFIRMATIONS", "1"))
        # --- admission workload configuration -----------------------------
        # The L2 room transaction is signed by the same key that pays for the
        # L1 deposit, because the coordinator refuses a receipt whose deposit
        # beneficiary is not the recovered L2 sender (admission.ts:
        # "deposit inbox id belongs to another beneficiary").
        self._depositor_key: int | None = None
        self._depositor_address: str | None = None
        self.deposit_asset = environ.get("SOAK_DEPOSIT_ASSET", ZERO_ADDRESS).lower()
        if not HEX20.fullmatch(self.deposit_asset):
            raise DriverError("SOAK_DEPOSIT_ASSET must be a 20-byte hexadecimal address")
        self.deposit_amount = require_int(
            environ.get("SOAK_DEPOSIT_AMOUNT_WEI", "1000000000000000"), "SOAK_DEPOSIT_AMOUNT_WEI", 1,
        )
        self.deposit_gas_limit = require_int(
            environ.get("SOAK_DEPOSIT_GAS_LIMIT", "600000"), "SOAK_DEPOSIT_GAS_LIMIT", 21_000,
        )
        self.l2_gas_limit = require_int(
            environ.get("SOAK_L2_GAS_LIMIT", "21000"), "SOAK_L2_GAS_LIMIT", 21_000,
        )
        self.l2_chain_id = require_int(
            environ.get("ZKDEAL_L2_CHAIN_ID", str(self.chain_id)), "ZKDEAL_L2_CHAIN_ID", 1,
        )
        self.admission_fee = require_int(
            environ.get("SOAK_ADMISSION_FEE_WEI", "0"), "SOAK_ADMISSION_FEE_WEI", 0,
        )
        self.minimum_deadline_lead = require_int(
            environ.get("SOAK_MINIMUM_DEADLINE_LEAD_BLOCKS", str(DEFAULT_MINIMUM_DEADLINE_LEAD_BLOCKS)),
            "SOAK_MINIMUM_DEADLINE_LEAD_BLOCKS", 1,
        )
        self.admission_deadline_lead = require_int(
            environ.get("SOAK_ADMISSION_DEADLINE_LEAD_BLOCKS", "64"),
            "SOAK_ADMISSION_DEADLINE_LEAD_BLOCKS", 1,
        )
        self.admission_batch_margin = require_int(
            environ.get("SOAK_ADMISSION_BATCH_MARGIN", "1024"), "SOAK_ADMISSION_BATCH_MARGIN", 1,
        )
        self.admission_auth = environ.get("SOAK_ADMISSION_AUTH_ALIAS", "node_a")
        if self.admission_auth not in TOKEN_FILE_ENV:
            raise DriverError("SOAK_ADMISSION_AUTH_ALIAS is not a reviewed auth alias")

    def capabilities(self) -> dict[str, Any]:
        _status, body, _raw = self.http.request(
            "coordinator", "GET", "/hosting/v1/capabilities", auth="tenant_a",
            headers={"accept-schema-version": "1"}, attempts=60, interval=1.0,
        )
        if not isinstance(body, dict):
            raise DriverError("owner capabilities response is not an object")
        managed = body.get("managedL1Operations")
        if not isinstance(managed, dict):
            raise DriverError("owner capabilities omit managedL1Operations")
        for surface in ("roomBatch", "roomAggregate", "poolSponsorMutation"):
            value = managed.get(surface)
            if not isinstance(value, dict) or value.get("enabled") is not True:
                raise DriverError(f"owner managedL1Operations.{surface} is not enabled")
        addresses = body.get("addresses")
        negotiated: dict[str, str] = {}
        for name in ("roomManager", "operationsAccount", "roomPool", "sponsorAccount"):
            variable = "ZKDEAL_" + re.sub(r"([A-Z])", r"_\1", name).upper()
            # The two sources are exclusive: a string from the coordinator is
            # taken as authoritative and the environment is never consulted.
            # The refusal has to say which one was actually read, or it sends
            # the operator to inspect a file that was never opened.
            if isinstance(addresses, dict) and isinstance(addresses.get(name), str):
                raw = addresses[name]
                source = f"the coordinator's capabilities.addresses.{name}"
            else:
                raw = self.environ.get(variable, "")
                source = f"{variable} (the coordinator sent no addresses.{name})"
            value = raw.lower()
            if not HEX20.fullmatch(value):
                raise DriverError(
                    f"deployment address {name} is not a 20-byte hexadecimal address; "
                    f"it was read from {source}",
                )
            negotiated[name] = value
        # Publish the set in one assignment. Writing per name left a partially
        # populated map behind when a later name failed, which makes some
        # lookups resolve and the rest report that nothing was negotiated - the
        # most misleading state available.
        self.addresses = negotiated
        self.negotiated = True
        return body

    def address(self, name: str) -> str:
        if name not in self.addresses:
            # Naming only the symptom made this cost a full stack bring-up to
            # locate. The two causes need different remedies: nothing was
            # negotiated at all (capabilities() never ran on this code path),
            # or negotiation happened but does not carry the requested name.
            if not self.negotiated:
                raise DriverError(
                    f"deployment address {name!r} was requested before stack capabilities "
                    "were negotiated; call capabilities() first on this code path",
                )
            known = ", ".join(sorted(self.addresses))
            raise DriverError(
                f"deployment address {name!r} is not one the coordinator negotiated; "
                f"capabilities supplied: {known}",
            )
        return self.addresses[name]

    def ready(self, attempts: int = 120) -> None:
        self.http.request("coordinator", "GET", "/hosting/v1/ready", attempts=attempts, interval=1.0)

    def health(self, endpoint: str, attempts: int = 120) -> None:
        self.http.request(endpoint, "GET", "/health", attempts=attempts, interval=1.0)

    def indexer_status(self, attempts: int = 1) -> dict[str, Any]:
        _status, body, _raw = self.http.request(
            "indexer", "GET", "/hosting/v1/indexer/status", auth="tenant_a",
            attempts=attempts, interval=1.0,
        )
        if not isinstance(body, dict):
            raise DriverError("indexer status response is not an object")
        return body

    def fresh_indexer(self, attempts: int = 120) -> dict[str, Any]:
        last: dict[str, Any] = {}
        for attempt in range(attempts):
            last = self.indexer_status(attempts=3)
            if last.get("indexerHeadMatchesL1") is True and last.get("unresolvedSafetyEvents") == 0:
                return last
            self.clock.sleep(1.0)
        raise DriverError(f"indexer did not recover freshness: {last}")

    # -- L1 JSON-RPC ------------------------------------------------------

    def l1_rpc(
        self, method: str, params: list[Any], *, endpoint: str = "rpc_a",
        attempts: int = 30, tolerate_error: bool = False,
    ) -> Any:
        """Unauthenticated L1 JSON-RPC (the anvil/provider endpoints)."""
        _status, body, _raw = self.http.request(
            endpoint, "POST", "/",
            body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            attempts=attempts, interval=1.0, label=f"L1 {method}",
        )
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
            raise DriverError(f"L1 JSON-RPC {method} response is not a JSON-RPC object")
        error = body.get("error")
        if error is not None:
            if tolerate_error:
                return {"error": error}
            raise DriverError(f"L1 JSON-RPC {method} failed: {error}")
        return body.get("result")

    def l1_block_number(self) -> int:
        return hex_int(self.l1_rpc("eth_blockNumber", []), "eth_blockNumber result")

    def l1_receipt(self, transaction_hash: str, attempts: int = 180) -> dict[str, Any]:
        """Wait for the canonical receipt of one L1 transaction."""
        for _attempt in range(attempts):
            receipt = self.l1_rpc("eth_getTransactionReceipt", [transaction_hash])
            if isinstance(receipt, dict) and receipt.get("blockNumber") is not None:
                return receipt
            if receipt is not None and not isinstance(receipt, dict):
                raise DriverError(f"L1 receipt for {transaction_hash} is not an object")
            self.clock.sleep(1.0)
        raise DriverError(f"L1 transaction {transaction_hash} was never mined")

    def depositor_key(self) -> int:
        if self._depositor_key is None:
            raw = self.environ.get("SOAK_L1_DEPOSITOR_KEY", "")
            path_value = self.environ.get("SOAK_L1_DEPOSITOR_KEY_FILE", "")
            if path_value:
                path = Path(path_value)
                if path.is_symlink() or not path.is_file():
                    raise DriverError("SOAK_L1_DEPOSITOR_KEY_FILE is absent, not regular, or a symlink")
                raw = path.read_text(encoding="utf-8").strip()
            self._depositor_key = private_key_int(raw or ANVIL_GENESIS_KEY, "L1 depositor key")
            self._depositor_address = private_key_address(self._depositor_key)
        return self._depositor_key

    def depositor_address(self) -> str:
        self.depositor_key()
        return str(self._depositor_address)

    def room_chain_state(self, room_id: str) -> dict[str, Any]:
        """Chain-first roomState(uint64) read: the same authority the
        coordinator itself signs against, never the observation archive."""
        call = function_selector(ROOM_STATE_SIGNATURE) + abi_uint(require_int(room_id, "roomId"))
        blob = hex_bytes(
            self.l1_rpc("eth_call", [
                {"to": self.address("roomManager"), "data": "0x" + call.hex()}, "latest",
            ]),
            f"roomState({room_id}) return data",
        )
        if len(blob) < ROOM_STATE_WORD_COUNT * 32:
            raise DriverError(
                f"roomState({room_id}) returned {len(blob)} bytes, expected at least "
                f"{ROOM_STATE_WORD_COUNT * 32} for the IRoomManager.Room tuple"
            )
        value: dict[str, Any] = {}
        for name, index in ROOM_STATE_WORDS.items():
            word = blob[index * 32:(index + 1) * 32]
            if name in ROOM_STATE_ADDRESS_WORDS:
                value[name] = "0x" + word[12:].hex()
            else:
                value[name] = int.from_bytes(word, "big")
        if value["state"] != ROOM_STATE_OPEN:
            raise DriverError(
                f"room {room_id} is not Open on the RoomManager (roomState.state={value['state']})"
            )
        if value["authorizationMode"] != ROOM_AUTHORIZATION_VALIDITY_ONLY:
            raise DriverError(
                f"room {room_id} is not VALIDITY_ONLY, so this coordinator cannot admit for it"
            )
        if not HEX20.fullmatch(str(value["admissionSigner"])) or int(value["admissionSigner"], 16) == 0:
            raise DriverError(f"room {room_id} has no on-chain admission signer")
        return value

    # -- admission workload -----------------------------------------------

    def fund_and_deposit(
        self,
        room_id: str,
        room_state: Mapping[str, Any] | None = None,
        prepared: Mapping[str, Any] | None = None,
        on_prepared: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Queue one real L1 deposit and return its claimable inbox entry.

        The signed bytes are handed to `on_prepared` before they are broadcast,
        so a killed worker replays the identical transaction (same nonce, same
        hash) instead of queueing a second deposit.
        """
        state = dict(room_state) if room_state is not None else self.room_chain_state(room_id)
        key = self.depositor_key()
        depositor = self.depositor_address()
        record = dict(prepared) if prepared else None
        if record is None or not record.get("rawTransaction"):
            nonce = hex_int(
                self.l1_rpc("eth_getTransactionCount", [depositor, "pending"]),
                "eth_getTransactionCount result",
            )
            gas_price = max(hex_int(self.l1_rpc("eth_gasPrice", []), "eth_gasPrice result") * 2, 10 ** 9)
            data = (
                function_selector(QUEUE_DEPOSIT_SIGNATURE)
                + abi_uint(require_int(room_id, "roomId"))
                + abi_address(self.deposit_asset)
                + abi_uint(self.deposit_amount)
                + abi_address(depositor)
            )
            raw, transaction_hash = sign_legacy_transaction(
                nonce=nonce, gas_price=gas_price, gas_limit=self.deposit_gas_limit,
                to=self.address("roomManager"),
                value=self.deposit_amount if self.deposit_asset == ZERO_ADDRESS else 0,
                data=data, chain_id=self.chain_id, private_key=key,
            )
            record = {
                "roomId": str(room_id), "nonce": nonce, "rawTransaction": raw,
                "transactionHash": transaction_hash, "depositor": depositor,
                "beneficiary": depositor, "asset": self.deposit_asset,
                "requestedAmount": str(self.deposit_amount),
            }
            if on_prepared is not None:
                on_prepared(dict(record))
        transaction_hash = str(record["transactionHash"]).lower()
        answer = self.l1_rpc(
            "eth_sendRawTransaction", [record["rawTransaction"]], tolerate_error=True,
        )
        if isinstance(answer, dict) and "error" in answer:
            message = str(answer["error"]).lower()
            if not any(benign in message for benign in BENIGN_BROADCAST_ERRORS):
                raise DriverError(
                    f"room {room_id} L1 deposit broadcast was rejected by eth_sendRawTransaction: "
                    f"{answer['error']}"
                )
        elif isinstance(answer, str) and answer.lower() != transaction_hash:
            raise DriverError(
                f"room {room_id} L1 deposit broadcast returned {answer}, not the signed hash "
                f"{transaction_hash}"
            )
        receipt = self.l1_receipt(transaction_hash)
        if str(receipt.get("status", "")).lower() != "0x1":
            raise DriverError(
                f"room {room_id} L1 deposit {transaction_hash} reverted (status "
                f"{receipt.get('status')}); the room cannot escrow this asset"
            )
        entry = self.decode_deposit_queued(room_id, receipt)
        if entry["beneficiary"] != str(record["beneficiary"]).lower():
            raise DriverError(
                f"room {room_id} DepositQueued names beneficiary {entry['beneficiary']}, not the "
                f"admission sender {record['beneficiary']}"
            )
        queued_at = hex_int(receipt.get("blockNumber"), "L1 receipt blockNumber")
        self.await_confirmations(
            queued_at, require_int(state["minimumDepositConfirmations"], "minimumDepositConfirmations"),
            f"room {room_id} deposit {entry['depositInboxId']}",
        )
        return {
            **record,
            "depositInboxId": str(entry["depositInboxId"]),
            "asset": entry["asset"],
            "amount": str(entry["amount"]),
            "beneficiary": entry["beneficiary"],
            "queuedAtBlock": str(queued_at),
            "depositContentHash": deposit_content_hash(
                str(record["depositor"]), entry["beneficiary"], entry["asset"], entry["amount"],
            ),
        }

    def decode_deposit_queued(self, room_id: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
        """Decode the exact RoomManagerBase.DepositQueued log of this receipt."""
        topic = event_topic(DEPOSIT_QUEUED_SIGNATURE)
        room_manager = self.address("roomManager")
        logs = receipt.get("logs")
        if not isinstance(logs, list):
            raise DriverError("L1 deposit receipt carries no log list")
        for entry in logs:
            if not isinstance(entry, dict) or str(entry.get("address", "")).lower() != room_manager:
                continue
            topics = entry.get("topics")
            if not isinstance(topics, list) or len(topics) != 4:
                continue
            if str(topics[0]).lower() != topic:
                continue
            if hex_int(topics[1], "DepositQueued roomId topic") != require_int(room_id, "roomId"):
                continue
            data = hex_bytes(entry.get("data"), "DepositQueued data")
            if len(data) != 64:
                raise DriverError("DepositQueued data is not exactly (address asset, uint256 amount)")
            return {
                "depositInboxId": hex_int(topics[2], "DepositQueued inboxId topic"),
                "beneficiary": "0x" + strip_hex(topics[3], "DepositQueued beneficiary topic")[-40:],
                "asset": "0x" + data[12:32].hex(),
                "amount": int.from_bytes(data[32:64], "big"),
            }
        raise DriverError(
            f"room {room_id} L1 deposit receipt carries no DepositQueued({topic}) log from "
            f"{room_manager}"
        )

    def await_confirmations(self, block_number: int, minimum: int, label: str, attempts: int = 600) -> int:
        """Block until the L1 head is `minimum` blocks past `block_number`."""
        head = self.l1_block_number()
        for _attempt in range(attempts):
            if head >= block_number and head - block_number >= minimum:
                return head
            self.clock.sleep(1.0)
            head = self.l1_block_number()
        raise DriverError(
            f"{label} never reached its {minimum}-block confirmation depth (head {head}, "
            f"queued at {block_number})"
        )

    def build_admission_request(
        self, room_id: str, deposit: Mapping[str, Any], room_state: Mapping[str, Any], l2_nonce: int,
    ) -> dict[str, Any]:
        """Sign the L2 room transaction and bound its admission request.

        The bounds are exactly the ones AdmissionService.submitSerial enforces:
        `deadlineBlock >= head + minimumDeadlineLeadBlocks`, `deadlineBlock <=
        head + roomState.maximumAdmissionWindow`, `maximumBatchIndex >
        latestBatchIndex` and `admissionFee >= the operator fee floor`.
        """
        window = require_int(room_state["maximumAdmissionWindow"], "roomState.maximumAdmissionWindow", 1)
        if window < self.minimum_deadline_lead:
            raise DriverError(
                f"room {room_id} maximumAdmissionWindow {window} is below the coordinator minimum "
                f"deadline lead {self.minimum_deadline_lead}; no deadline can satisfy both bounds"
            )
        lead = min(window, max(self.admission_deadline_lead, self.minimum_deadline_lead + 1))
        head = self.l1_block_number()
        raw, transaction_hash = sign_legacy_transaction(
            nonce=l2_nonce,
            gas_price=10 ** 9,
            gas_limit=self.l2_gas_limit,
            to=self.depositor_address(),
            value=0,
            data=b"",
            chain_id=self.l2_chain_id,
            private_key=self.depositor_key(),
        )
        return {
            "rawSignedTransaction": raw,
            "depositInboxId": str(deposit["depositInboxId"]),
            "deadlineBlock": str(head + lead),
            "maximumBatchIndex": str(
                require_int(room_state["batchIndex"], "roomState.batchIndex") + self.admission_batch_margin
            ),
            "admissionFee": str(self.admission_fee),
            # Driver-side bookkeeping; never part of the POSTed AdmissionRequest.
            "transactionHash": transaction_hash,
            "l2Nonce": l2_nonce,
            "headBlock": str(head),
        }

    def submit_admission(
        self,
        room_id: str,
        deposit_inbox_id: str,
        request: Mapping[str, Any],
        deposit: Mapping[str, Any],
        room_state: Mapping[str, Any],
        correlation: str,
        attempts: int = 30,
    ) -> dict[str, Any]:
        """POST the AdmissionRequest and verify the slashable receipt it returns."""
        if str(request.get("depositInboxId")) != str(deposit_inbox_id):
            raise DriverError("admission request does not name the deposit it was built for")
        body = {
            "rawSignedTransaction": request["rawSignedTransaction"],
            "depositInboxId": str(deposit_inbox_id),
            "deadlineBlock": str(request["deadlineBlock"]),
            "maximumBatchIndex": str(request["maximumBatchIndex"]),
            "admissionFee": str(request["admissionFee"]),
        }
        path = f"/rooms/{urllib.parse.quote(str(room_id), safe='')}/transactions"
        answer: Any = None
        for attempt in range(1, attempts + 1):
            # Every answer the route can author is expected here so a refusal
            # is reported with its decision and reason instead of collapsing
            # into an anonymous transport failure. 401 is the shape a
            # misrouted operator credential takes; it never becomes admitted.
            status, answer, _raw = self.http.request(
                "coordinator", "POST", path, body=body, auth=self.admission_auth,
                headers={"x-correlation-id": correlation},
                expect=(200, 400, 401, 404, 429, 503), attempts=1,
            )
            if status == 200:
                break
            decision = answer.get("decision") if isinstance(answer, dict) else None
            reason = answer.get("reason") if isinstance(answer, dict) else None
            if status in (429, 503) and attempt < attempts:
                # ADMISSION_UNAVAILABLE / RATE_LIMITED are the coordinator's
                # retryable answers; NOT_ADMITTED never becomes admitted.
                self.clock.sleep(1.0)
                continue
            raise DriverError(
                f"room {room_id} admission was refused at POST {path}: HTTP {status} "
                f"{decision} {reason}"
            )
        if not isinstance(answer, dict) or answer.get("decision") != "LOCALLY_ADMITTED":
            raise DriverError(f"room {room_id} admission response is not a LOCALLY_ADMITTED decision")
        receipt = answer.get("receipt")
        if not isinstance(receipt, dict):
            raise DriverError(f"room {room_id} admission response carries no receipt object")
        return self.validate_admission_receipt(room_id, request, deposit, room_state, receipt)

    def validate_admission_receipt(
        self,
        room_id: str,
        request: Mapping[str, Any],
        deposit: Mapping[str, Any],
        room_state: Mapping[str, Any],
        receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Fail closed unless the receipt is a slashable promise for this request.

        Every committed field is compared against what was asked for, the
        deposit content hash is recomputed from the canonical DepositQueued
        values, and the EIP-712 signature is recovered against the room's
        on-chain admissionSigner. An admission id is never taken on trust.
        """
        expected = {
            "roomId": str(room_id),
            "transactionHash": str(request["transactionHash"]).lower(),
            "depositInboxId": str(request["depositInboxId"]),
            "deadlineBlock": str(request["deadlineBlock"]),
            "maximumBatchIndex": str(request["maximumBatchIndex"]),
            "admissionFee": str(request["admissionFee"]),
            "depositContentHash": str(deposit["depositContentHash"]).lower(),
        }
        for name, wanted in expected.items():
            actual = str(receipt.get(name, "")).lower() if name.endswith("Hash") else str(receipt.get(name, ""))
            if actual != wanted:
                raise DriverError(
                    f"room {room_id} admission receipt field {name} is {actual!r}, expected {wanted!r}"
                )
        admission_id = str(receipt.get("admissionId", ""))
        if not re.fullmatch(r"[1-9][0-9]*", admission_id):
            raise DriverError(f"room {room_id} admission receipt carries no canonical admissionId")
        require_int(receipt.get("bondEpoch"), "receipt.bondEpoch")
        digest = admission_receipt_digest(self.chain_id, self.address("roomManager"), receipt)
        signer = recover_signature_address(digest, receipt.get("signature"), "admission receipt signature")
        room_signer = str(room_state["admissionSigner"]).lower()
        if signer != room_signer:
            raise DriverError(
                f"room {room_id} admission receipt {admission_id} recovers to {signer}, not the "
                f"room's on-chain admissionSigner {room_signer}; it is not slashable"
            )
        return {
            "roomId": str(room_id),
            "admissionId": admission_id,
            "transactionHash": expected["transactionHash"],
            "depositInboxId": expected["depositInboxId"],
            "depositContentHash": expected["depositContentHash"],
            "deadlineBlock": expected["deadlineBlock"],
            "maximumBatchIndex": expected["maximumBatchIndex"],
            "admissionFee": expected["admissionFee"],
            "bondEpoch": str(receipt.get("bondEpoch")),
            "admissionSigner": signer,
        }

    def create_room(self, room_id: str, idempotency: str) -> dict[str, Any]:
        _status, body, _raw = self.http.request(
            "coordinator", "POST", "/hosting/v1/rooms/deployments",
            body={"allocationId": f"soak-alloc-{room_id}", "roomId": room_id, "metadata": {}},
            auth="tenant_a", headers={"idempotency-key": idempotency},
            expect=(200, 201, 202), attempts=30, interval=1.0,
        )
        if not isinstance(body, dict) or not require_str(body.get("operationId", body.get("roomId", room_id)), "roomId"):
            raise DriverError("room deployment intent was not accepted")
        return body

    def lease_admissions(self, room_id: str, correlation: str) -> list[dict[str, Any]]:
        _status, body, _raw = self.http.request(
            "coordinator", "POST", f"/hosting/v1/admissions/{room_id}/lease",
            body={"limit": 4, "leaseMs": 300_000}, auth="node_a",
            headers={"x-correlation-id": correlation}, attempts=30, interval=1.0,
        )
        if not isinstance(body, dict) or not isinstance(body.get("entries"), list) or not body["entries"]:
            raise DriverError("owner admission lease returned no entries")
        return body["entries"]

    def ack_admissions(self, room_id: str, admission_ids: list[str], idempotency: str, correlation: str) -> None:
        _status, body, _raw = self.http.request(
            "coordinator", "POST", f"/hosting/v1/admissions/{room_id}/ack",
            body={"admissionIds": admission_ids}, auth="node_a",
            headers={"idempotency-key": idempotency, "x-correlation-id": correlation},
            attempts=30, interval=1.0,
        )
        if not isinstance(body, dict) or require_int(body.get("acknowledged"), "acknowledged") != len(admission_ids):
            raise DriverError("owner did not acknowledge every admission")

    def queue_job(self, endpoint_path: str, request: Any, idempotency: str, correlation: str, room_id: str) -> str:
        _status, body, _raw = self.http.request(
            "queue", "POST", "/queue/v1/jobs",
            body={
                "endpoint": endpoint_path,
                "proofClass": self.environ.get("SOAK_PROOF_CLASS", "groth16-production"),
                "request": request,
                "roomId": room_id,
                "serviceClass": self.environ.get("SOAK_SERVICE_CLASS", "soak"),
                "partition": "shared",
                "correlationId": correlation,
                "billingMode": "quoted",
                "maximumChargeAmount": self.environ.get("SOAK_MAX_CHARGE_AMOUNT", "1000000000000000000"),
                "maximumChargeCurrency": self.environ.get("SOAK_MAX_CHARGE_CURRENCY", "WEI"),
            },
            auth="node_a",
            headers={"idempotency-key": idempotency, "x-correlation-id": correlation},
            expect=(200, 201, 202), attempts=30, interval=1.0,
        )
        job_id = str(body.get("jobId", "")) if isinstance(body, dict) else ""
        if not JOB_ID.fullmatch(job_id):
            raise DriverError("queue returned a malformed jobId")
        return job_id

    def queue_wait_done(self, job_id: str, correlation: str, attempts: int = 600) -> str:
        for attempt in range(attempts):
            _status, body, _raw = self.http.request(
                "queue", "GET", f"/queue/v1/jobs/{job_id}", auth="node_a",
                headers={"x-correlation-id": correlation}, attempts=5, interval=1.0,
            )
            if not isinstance(body, dict) or body.get("jobId") != job_id:
                raise DriverError("queue status is not bound to the requested job")
            status = str(body.get("status", ""))
            if status == "FAILED":
                raise DriverError(f"queue job {job_id} failed: {body.get('errorCode')}")
            if status == "DONE":
                digest = str(body.get("resultDigest", "")).lower()
                if not SHA256.fullmatch(digest):
                    raise DriverError(f"queue DONE job {job_id} omitted resultDigest")
                return digest
            self.clock.sleep(1.0)
        raise DriverError(f"queue job {job_id} did not complete")

    def queue_result(self, job_id: str, expected_digest: str) -> tuple[dict[str, Any], bytes]:
        _status, body, raw = self.http.request(
            "queue", "GET", f"/queue/v1/jobs/{job_id}/result", auth="node_a",
            attempts=10, interval=1.0,
        )
        if sha256_hex(raw) != expected_digest:
            raise DriverError(f"queue result bytes for {job_id} do not match durable resultDigest")
        if not isinstance(body, dict):
            raise DriverError("queue result is not an object")
        return body, raw

    def publish_operation(self, path: str, body: dict[str, Any], auth: str, idempotency: str, correlation: str) -> dict[str, Any]:
        _status, value, _raw = self.http.request(
            "coordinator", "POST", path, body=body, auth=auth,
            headers={"idempotency-key": idempotency, "x-correlation-id": correlation},
            expect=(200, 202), attempts=30, interval=1.0,
        )
        if not isinstance(value, dict):
            raise DriverError(f"{path} response is not an object")
        return self.wait_finalized(str(value.get("operationId", "")), auth, correlation)

    def wait_finalized(self, operation_id: str, auth: str, correlation: str, attempts: int = 600) -> dict[str, Any]:
        require_str(operation_id, "operationId")
        for attempt in range(attempts):
            _status, value, _raw = self.http.request(
                "coordinator", "GET", f"/hosting/v1/l1-transactions/{urllib.parse.quote(operation_id, safe='')}",
                auth=auth, headers={"x-correlation-id": correlation}, attempts=5, interval=1.0,
            )
            if not isinstance(value, dict) or value.get("operationId") != operation_id:
                raise DriverError("owner L1 operation identity differs from the requested operation")
            status = str(value.get("status", ""))
            if status in {"FAILED", "RECOVERY_REQUIRED", "SUPERSEDED"}:
                raise DriverError(f"owner L1 operation {operation_id} entered {status}")
            if status == "FINALIZED":
                return self.validate_finalized(value)
            self.clock.sleep(1.0)
        raise DriverError(f"owner L1 operation {operation_id} did not finalize")

    def validate_finalized(self, value: dict[str, Any]) -> dict[str, Any]:
        require_true(value.get("finalized"), "operation.finalized")
        if require_int(value.get("confirmations"), "operation.confirmations") < self.minimum_confirmations:
            raise DriverError("finalized operation does not satisfy its confirmation policy")
        source = value.get("receiptSource")
        if not isinstance(source, dict) or source.get("canonical") is not True or not source.get("providerIds"):
            raise DriverError("finalized operation lacks canonical provider evidence")
        tx_hash = str(value.get("transactionHash", "")).lower()
        if not HEX32.fullmatch(tx_hash):
            raise DriverError("finalized operation lacks a transaction hash")
        require_str(str(value.get("nonce", "")), "operation.nonce")
        require_str(str(value.get("from", "")), "operation.from")
        if value.get("castBroadcast") is True:
            raise DriverError("operation was cast-broadcast outside the owner durable boundary")
        return value

    def operation_selector(self, value: Mapping[str, Any]) -> str:
        selector = value.get("selector")
        if not isinstance(selector, str):
            binding = value.get("binding")
            selector = binding.get("selector") if isinstance(binding, dict) else None
        if not isinstance(selector, str) or not re.fullmatch(r"0x[0-9a-f]{8}", selector):
            raise DriverError("owner operation does not journal its pinned selector")
        return selector

    def nonce_id(self, value: Mapping[str, Any]) -> str:
        return f"{str(value['from']).lower()}:{value['nonce']}"

    def indexer_rpc(self, method: str, params: dict[str, Any], attempts: int = 30) -> Any:
        """Authenticated indexer JSON-RPC catalog (POST /hosting/v1/json-rpc)."""
        _status, body, _raw = self.http.request(
            "indexer", "POST", "/hosting/v1/json-rpc",
            body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            auth="tenant_a", attempts=attempts, interval=1.0,
        )
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
            raise DriverError(f"indexer JSON-RPC {method} response is not a JSON-RPC object")
        if "error" in body:
            raise DriverError(f"indexer JSON-RPC {method} failed: {body['error']}")
        return body.get("result")

    def aggregate_member_outcomes(
        self, aggregate_hash: str, expected: int, attempts: int = 600,
    ) -> list[dict[str, Any]]:
        """Post-finality honest source for member application results.

        The indexer surfaces canonical AggregateMemberOutcome facts through
        zkdeal_getBatches (fact kinds batch/aggregate/data-availability); the
        driver filters them by the on-chain aggregate statement hash and never
        trusts prover- or driver-side expectations for applied/failed counts.
        """
        outcomes: list[dict[str, Any]] = []
        for attempt in range(attempts):
            facts = self.indexer_rpc("zkdeal_getBatches", {"limit": 1000})
            if not isinstance(facts, list):
                raise DriverError("zkdeal_getBatches result is not a fact list")
            outcomes = []
            for fact in facts:
                if not isinstance(fact, dict):
                    continue
                payload = fact.get("payload")
                if not isinstance(payload, dict):
                    continue
                provenance = payload.get("provenance")
                args = payload.get("args")
                if not isinstance(provenance, dict) or not isinstance(args, dict):
                    continue
                if provenance.get("eventName") != "AggregateMemberOutcome":
                    continue
                if str(args.get("aggregateHash", "")).lower() != aggregate_hash:
                    continue
                outcomes.append(args)
            if len(outcomes) >= expected:
                return outcomes
            self.clock.sleep(1.0)
        raise DriverError(
            f"indexer surfaced {len(outcomes)} AggregateMemberOutcome facts, expected {expected}"
        )

    def create_sponsorship(self, body: dict[str, Any], idempotency: str) -> dict[str, Any]:
        _status, value, _raw = self.http.request(
            "coordinator", "POST", "/hosting/v1/sponsorships", body=body, auth="tenant_a",
            headers={"idempotency-key": idempotency}, expect=(200, 201), attempts=30, interval=1.0,
        )
        if not isinstance(value, dict):
            raise DriverError("sponsorship creation response is not an object")
        return value

    def ledger_entries(self, after: int) -> list[dict[str, Any]]:
        _status, body, _raw = self.http.request(
            "coordinator", "GET", f"/hosting/v1/billing/ledger?after={int(after)}&limit=500",
            auth="tenant_a", attempts=30, interval=1.0,
        )
        if not isinstance(body, dict) or not isinstance(body.get("entries"), list):
            raise DriverError("billing ledger response omitted entries")
        entries = []
        for entry in body["entries"]:
            if not isinstance(entry, dict):
                raise DriverError("billing ledger entry is not an object")
            entries.append({
                "entryId": require_int(entry.get("entryId"), "ledger entryId"),
                "chargeId": require_str(entry.get("chargeId"), "ledger chargeId"),
                "usageUnits": require_int(entry.get("usageUnits"), "ledger usageUnits"),
                "chargeWei": str(require_int(entry.get("chargeWei"), "ledger chargeWei")),
            })
        return sorted(entries, key=lambda item: item["entryId"])

    def usage_entries(self) -> list[dict[str, Any]]:
        _status, body, _raw = self.http.request(
            "coordinator", "GET", "/hosting/v1/usage?after=0&limit=500", auth="tenant_a",
            attempts=30, interval=1.0,
        )
        if not isinstance(body, dict) or not isinstance(body.get("entries"), list):
            raise DriverError("usage response omitted entries")
        return body["entries"]

    def withdrawal_proof(self, room_id: str, epoch: str, index: str) -> dict[str, Any]:
        _status, body, _raw = self.http.request(
            "coordinator", "GET", f"/hosting/v1/withdrawals/{room_id}/{epoch}/{index}/proof",
            auth="withdrawal", attempts=60, interval=1.0,
        )
        if not isinstance(body, dict) or body.get("realProof") is not True or not isinstance(body.get("proof"), dict):
            raise DriverError("withdrawal proof is not a real finalized positional proof")
        return body

    def request_withdrawal_claim(
        self, room_id: str, epoch: str, index: str, idempotency: str,
        expect: tuple[int, ...] = (200, 202),
    ) -> tuple[int, dict[str, Any]]:
        status, body, _raw = self.http.request(
            "coordinator", "POST", f"/hosting/v1/withdrawals/{room_id}/{epoch}/{index}/claims",
            body={}, auth="withdrawal", headers={"idempotency-key": idempotency},
            expect=expect, attempts=30, interval=1.0,
        )
        if not isinstance(body, dict):
            raise DriverError("withdrawal claim response is not an object")
        return status, body

    def withdrawal_claim(self, claim_id: str, attempts: int = 600) -> dict[str, Any]:
        for attempt in range(attempts):
            _status, body, _raw = self.http.request(
                "coordinator", "GET", f"/hosting/v1/withdrawal-claims/{urllib.parse.quote(claim_id, safe='')}",
                auth="withdrawal", attempts=5, interval=1.0,
            )
            if not isinstance(body, dict) or body.get("claimId") != claim_id:
                raise DriverError("withdrawal claim identity differs from the request")
            status = str(body.get("status", ""))
            if status in {"FAILED", "REJECTED"}:
                raise DriverError(f"withdrawal claim {claim_id} entered {status}")
            if status == "FINALIZED":
                return body
            self.clock.sleep(1.0)
        raise DriverError(f"withdrawal claim {claim_id} did not finalize")


# ---------------------------------------------------------------------------
# Workload: the owner lifecycle cycles that produce the journal evidence.
# ---------------------------------------------------------------------------


class Workload:
    PULSE_ROOM = "101"
    AGGREGATE_ROOMS = tuple(str(201 + index) for index in range(AGGREGATE_MEMBERS))

    def __init__(
        self,
        stack: Stack,
        journal: Journal,
        state: DriverState,
        environ: Mapping[str, str],
        manifest: Mapping[str, Any],
    ):
        self.stack = stack
        self.journal = journal
        self.state = state
        self.environ = environ
        self.manifest = manifest
        # Nothing else in the soak creates admissions, so the workload has to
        # generate its own: a room with an empty admission queue has nothing to
        # lease, nothing live to prepare and nothing to settle.
        self.pulse_admissions = self.admission_count("SOAK_ADMISSIONS_PER_PULSE", 1)
        self.member_admissions = self.admission_count("SOAK_ADMISSIONS_PER_AGGREGATE_MEMBER", 1)

    def admission_count(self, name: str, default: int) -> int:
        value = require_int(self.environ.get(name, str(default)), name, 1)
        if value > 8:
            raise DriverError(f"{name} must stay within 1..8 admissions per cycle")
        return value

    def key(self, *parts: str) -> str:
        return "soak-" + self.state.run_id + "-" + "-".join(parts)

    def confirmation_policy(self) -> dict[str, Any]:
        return {"minimumConfirmations": self.stack.minimum_confirmations, "requireFinalized": True}

    def emit_charges(self, step: str, expected_count: int | None = None) -> dict[str, Any]:
        """Journal the new live-ledger entries as unique charge events.

        The expected-count assertion is evaluated over the journaled charges
        labeled with this step, not over the fetch delta, so a killed worker
        that already journaled the step's charges resumes without divergence.
        """
        cursor = int(self.state.value.get("ledgerCursor", 0))
        entries = self.stack.ledger_entries(cursor)
        for entry in entries:
            self.journal.emit_once(
                f"charge:{entry['chargeId']}", "charge",
                chargeId=entry["chargeId"], usageUnits=entry["usageUnits"],
                chargeWei=entry["chargeWei"], step=step,
            )
        if entries:
            self.state.value["ledgerCursor"] = max(entry["entryId"] for entry in entries)
            self.state.save()
        step_events = [
            event for event in self.journal.events
            if event.get("kind") == "charge" and event.get("step") == step
        ]
        if expected_count is not None and len(step_events) != expected_count:
            raise DriverError(
                f"step {step} journaled {len(step_events)} ledger charges, expected {expected_count}"
            )
        usage = sum(int(event.get("usageUnits", 0)) for event in step_events)
        wei = sum(int(event.get("chargeWei", 0)) for event in step_events)
        return {"charges": len(step_events), "usageUnits": usage, "chargesWei": str(wei)}

    def next_l2_nonce(self) -> int:
        """Durable strictly-increasing L2 account nonce for the workload sender.

        Saved before the transaction it numbers is signed, so a killed worker
        never re-signs a different payload under a nonce it already used.
        """
        value = require_int(self.state.get("l2TransactionNonce", 0), "l2TransactionNonce")
        self.state.put("l2TransactionNonce", value + 1)
        return value

    def ensure_admissions(
        self, step: str, room_id: str, count: int, correlation: str,
    ) -> list[dict[str, Any]]:
        """Guarantee `count` fresh committed admissions exist for this room.

        Each one is a real L1 `queueDeposit` plus a real coordinator admission
        over a real signed L2 transaction. Both halves are recorded in the
        durable driver state before they are broadcast, so a killed worker
        replays them rather than minting a second deposit or a second receipt.
        """
        return [
            self.ensure_admission(step, room_id, index, correlation)
            for index in range(count)
        ]

    def ensure_admission(
        self, step: str, room_id: str, index: int, correlation: str,
    ) -> dict[str, Any]:
        state_key = f"admission:{step}:{room_id}:{index}"
        stored = self.state.get(state_key)
        record = dict(stored) if isinstance(stored, dict) else {}
        if record.get("admissionId"):
            self.emit_admission_submit(step, record)
            return record
        for attempt in range(2):
            room_state = self.stack.room_chain_state(room_id)
            deposit = self.ensure_deposit(state_key, record, room_id, room_state)
            stored_request = record.get("request")
            request = dict(stored_request) if isinstance(stored_request, dict) else None
            if request is None:
                request = self.stack.build_admission_request(
                    room_id, deposit, room_state, self.next_l2_nonce(),
                )
                record["request"] = request
                self.state.put(state_key, record)
            try:
                receipt = self.stack.submit_admission(
                    room_id, str(deposit["depositInboxId"]), request, deposit, room_state, correlation,
                )
            except DriverError as exc:
                # A resumed worker replays its recorded request first, because
                # the coordinator's admission WAL is keyed by the transaction
                # hash and returns the receipt it already committed. Only an
                # outright refusal -- a deadline the restart outlived -- earns
                # exactly one fresh deposit and request, mirroring the fault
                # controller's fresh-idempotency-key recovery.
                if attempt or "admission was refused" not in str(exc):
                    raise
                record.pop("request", None)
                record.pop("deposit", None)
                self.state.put(state_key, record)
                continue
            record.update(receipt)
            record["step"] = step
            self.state.put(state_key, record)
            self.emit_admission_submit(step, record)
            return record
        raise DriverError(f"room {room_id} admission {state_key} was never issued")

    def ensure_deposit(
        self, state_key: str, record: dict[str, Any], room_id: str, room_state: Mapping[str, Any],
    ) -> dict[str, Any]:
        stored = record.get("deposit")
        deposit = dict(stored) if isinstance(stored, dict) else {}
        if deposit.get("depositInboxId"):
            return deposit

        def remember(prepared: dict[str, Any]) -> None:
            record["deposit"] = prepared
            self.state.put(state_key, record)

        deposit = self.stack.fund_and_deposit(
            room_id, room_state=room_state,
            prepared=deposit if deposit.get("rawTransaction") else None,
            on_prepared=remember,
        )
        record["deposit"] = deposit
        self.state.put(state_key, record)
        return deposit

    def emit_admission_submit(self, step: str, record: Mapping[str, Any]) -> None:
        """Journal one 'submit' lifecycle event per issued admission.

        soak.verify_closure requires the 'submit' lifecycle kind and that the
        journal as a whole persists roomId/jobId/nonceId/txHash/eventId/cursor;
        an admission has no proof job, so this event carries the ids it really
        owns (room, admission, L2 transaction hash, deposit inbox) plus the
        live indexer cursor, and the prove lineage keeps journaling its jobId.
        """
        event_id = f"submit-admission:{record['roomId']}:{record['admissionId']}"
        if event_id in self.journal.event_ids:
            return
        cursor = self.stack.indexer_status(attempts=10)
        self.journal.emit_once(
            event_id, "submit",
            roomId=str(record["roomId"]), admissionId=str(record["admissionId"]),
            txHash=str(record["transactionHash"]), depositInboxId=str(record["depositInboxId"]),
            depositContentHash=str(record["depositContentHash"]),
            deadlineBlock=str(record["deadlineBlock"]),
            maximumBatchIndex=str(record["maximumBatchIndex"]),
            admissionFee=str(record["admissionFee"]),
            admissionSigner=str(record["admissionSigner"]),
            cursor=require_int(cursor.get("cursor"), "indexer cursor"), step=step,
        )

    def run_job(
        self, purpose: tuple[str, ...], endpoint: str, request: Any, room_id: str, correlation: str,
    ) -> tuple[str, str, dict[str, Any]]:
        """Queue one durable proof job, wait for DONE and return its bound result."""
        job_id = self.stack.queue_job(endpoint, request, self.key(*purpose), correlation, room_id)
        digest = self.stack.queue_wait_done(job_id, correlation)
        result, _raw = self.stack.queue_result(job_id, digest)
        return job_id, digest, result

    def prove_and_verify(self, step: str, room_id: str, correlation: str, request_seed: dict[str, Any]) -> dict[str, Any]:
        """Run the live prepare -> real CUDA Groth16 prove -> verify job chain."""
        prepare_job = self.stack.queue_job(
            "/hosting/v1/rooms/prepare-batch",
            {"schemaVersion": 1, "roomId": room_id, "batchInput": "BatchInputV5", **request_seed},
            self.key(step, "prepare"), correlation, room_id,
        )
        cursor = self.stack.indexer_status(attempts=10)
        self.journal.emit_once(
            f"submit:{step}", "submit", jobId=prepare_job, roomId=room_id,
            cursor=require_int(cursor.get("cursor"), "indexer cursor"),
        )
        prepare_digest = self.stack.queue_wait_done(prepare_job, correlation)
        prepared, _raw = self.stack.queue_result(prepare_job, prepare_digest)
        # The real live-prepare response (prover live_prepare.rs) carries
        # schemaVersion/preparedFrom/fixture but does not echo the request's
        # batchInput label, so the check binds only response-owned fields.
        if (
            prepared.get("schemaVersion") != 1
            or prepared.get("fixture") is not False
            or prepared.get("preparedFrom") != "live-room-engine-state"
        ):
            raise DriverError(f"step {step} prepare result is not a live non-fixture BatchInputV5 artifact")
        artifact_digest = str(prepared.get("prepareArtifactDigest", "")).lower()
        if not SHA256.fullmatch(artifact_digest) or prepared.get("contentAddress") != artifact_digest:
            raise DriverError(f"step {step} prepare result has an inconsistent content address")
        self.journal.emit_once(
            f"live-prepare:{step}", "live-prepare",
            preparedFrom="live-room-engine-state", batchInput="BatchInputV5", fixture=False,
            jobId=prepare_job, roomId=room_id, prepareArtifactDigest=artifact_digest,
        )
        proof_request = prepared.get("proofRequest")
        if not isinstance(proof_request, dict) or proof_request.get("production") is not True \
                or proof_request.get("proofMode") != "groth16":
            raise DriverError(f"step {step} proofRequest is not the production Groth16 witness")
        prove_job = self.stack.queue_job("/v5/rooms/prove", proof_request, self.key(step, "prove"), correlation, room_id)
        prove_digest = self.stack.queue_wait_done(prove_job, correlation)
        proved, _raw = self.stack.queue_result(prove_job, prove_digest)
        # The production prover (commands_v5.rs) attests real CUDA through the
        # enforce_production_gpu-derived gpuUuid; an explicit realCuda flag is
        # also accepted so a stub stack can declare itself.
        real_cuda = proved.get("realCuda") is True or (
            isinstance(proved.get("gpuUuid"), str) and bool(proved.get("gpuUuid"))
        )
        if proved.get("proofMode") != "groth16" or not real_cuda:
            raise DriverError(f"step {step} prove result is not a real CUDA Groth16 receipt")
        self.journal.emit_once(
            f"prove:{step}", "prove", outputId=prove_job, sealedOutputSha256=prove_digest,
            realCuda=True, proofMode="groth16", jobId=prove_job, roomId=room_id,
        )
        # The verify request must be exactly {journal, journalHash, receiptB64}:
        # the prover (cmd_verify_room_v5) requires the journal, and the owner's
        # room-aggregates operation canonically compares the recorded verify
        # request against exactly these three prove-result fields.
        verify_job = self.stack.queue_job(
            "/v5/rooms/verify",
            {
                "journal": proved.get("journal"),
                "journalHash": proved.get("journalHash"),
                "receiptB64": proved.get("receiptB64"),
            },
            self.key(step, "verify"), correlation, room_id,
        )
        verify_digest = self.stack.queue_wait_done(verify_job, correlation)
        verified, _raw = self.stack.queue_result(verify_job, verify_digest)
        # cmd_verify_room_v5 cryptographically re-verifies the receipt and
        # reports ok/proofMode; realReceipt is the stub stack's declaration.
        real_receipt = verified.get("realReceipt") is True or verified.get("proofMode") == "groth16"
        if verified.get("ok") is not True or not real_receipt:
            raise DriverError(f"step {step} verify result does not cover the real receipt")
        self.journal.emit_once(f"verify:{step}", "verify", realReceipt=True, jobId=verify_job, roomId=room_id)
        # Aggregate identity extraction: the room-aggregates operation binds
        # every member witness through journalHash/roomProgramId/receiptB64 and
        # the DA lineage through the provisional submission's deploymentDomain
        # and canonical batch data, so all of them fail closed here.
        journal_hash = str(proved.get("journalHash", "")).lower()
        if not HEX32.fullmatch(journal_hash) or str(prepared.get("journalHash", "")).lower() != journal_hash:
            raise DriverError(f"step {step} prove journalHash differs from its live prepare artifact")
        program_id = str(proved.get("programId", "")).lower()
        if not HEX32.fullmatch(program_id):
            raise DriverError(f"step {step} prove result omits its zkVM programId")
        receipt_b64 = proved.get("receiptB64")
        if not isinstance(receipt_b64, str) or not receipt_b64:
            raise DriverError(f"step {step} prove result omits its receipt")
        provisional = prepared.get("provisionalSubmission")
        contract_journal = provisional.get("journal") if isinstance(provisional, dict) else None
        if not isinstance(contract_journal, dict):
            raise DriverError(f"step {step} prepare result omits its provisional submission journal")
        deployment_domain = str(contract_journal.get("deploymentDomain", "")).lower()
        if not HEX32.fullmatch(deployment_domain):
            raise DriverError(f"step {step} provisional journal omits its deploymentDomain")
        canonical_data = provisional.get("canonicalBatchData")
        if not isinstance(canonical_data, str) or not canonical_data.startswith("0x"):
            raise DriverError(f"step {step} prepare result omits its canonical batch data")
        return {
            "artifacts": {
                "prepare": {"jobId": prepare_job, "resultDigest": prepare_digest, "prepareArtifactDigest": artifact_digest},
                "prove": {"jobId": prove_job, "resultDigest": prove_digest},
                "verify": {"jobId": verify_job, "resultDigest": verify_digest},
            },
            "proof": {
                "journalHash": journal_hash, "programId": program_id, "receiptB64": receipt_b64,
                "deploymentDomain": deployment_domain, "canonicalData": canonical_data,
            },
        }

    def reverify_sealed_output(self, output_id: str, expected_digest: str) -> None:
        """Re-read the sealed proof bytes and fail closed on any drift."""
        _body, raw = self.stack.queue_result(output_id, expected_digest)
        if sha256_hex(raw) != expected_digest:
            raise DriverError(f"sealed output changed after publication: {output_id}")

    def setup(self) -> dict[str, Any]:
        for room_id in (self.PULSE_ROOM, *self.AGGREGATE_ROOMS):
            accepted = self.stack.create_room(room_id, self.key("room", room_id))
            self.journal.emit_once(
                f"room-create:{room_id}", "room-create", roomId=room_id,
                deploymentOperationId=str(accepted.get("operationId", "")),
            )
        return {"rooms": 1 + len(self.AGGREGATE_ROOMS)}

    def pulse(self, index: int) -> dict[str, Any]:
        step = f"pulse-{index:02d}"
        room_id = self.PULSE_ROOM
        correlation = self.key(step)
        # Own the work this cycle proves: without a fresh admission the lease
        # below has nothing to hand out and the pulse would prove an empty room.
        self.ensure_admissions(step, room_id, self.pulse_admissions, correlation)
        entries = self.stack.lease_admissions(room_id, correlation)
        admission_ids = [require_str(entry.get("admissionId"), "admissionId") for entry in entries]
        self.journal.emit_once(f"lease:{step}", "lease", roomId=room_id, admissionIds=admission_ids)
        jobs = self.prove_and_verify(step, room_id, correlation, {"cycle": step})
        artifacts = jobs["artifacts"]
        self.stack.ack_admissions(room_id, admission_ids, self.key(step, "ack"), correlation)
        operation = self.stack.publish_operation(
            "/hosting/v1/l1-operations/room-batches",
            {
                "schemaVersion": 1,
                "chainId": self.stack.chain_id,
                "roomManager": self.stack.address("roomManager"),
                "expectedOperationsAccount": self.stack.address("operationsAccount"),
                "roomId": room_id,
                "artifacts": artifacts,
                "admissionIds": admission_ids,
                "confirmationPolicy": self.confirmation_policy(),
            },
            "l1_room", self.key(step, "publish"), correlation,
        )
        operation_id = require_str(operation.get("operationId"), "operationId")
        self.journal.emit_once(
            f"txb:{operation_id}", "tx-broadcast",
            nonceId=self.stack.nonce_id(operation), txHash=str(operation["transactionHash"]).lower(),
            publisher="owner-durable-operation", operationId=operation_id, roomId=room_id,
        )
        self.reverify_sealed_output(artifacts["prove"]["jobId"], artifacts["prove"]["resultDigest"])
        self.journal.emit_once(
            f"finalize:{step}", "finalize", outputId=artifacts["prove"]["jobId"],
            sealedOutputSha256=artifacts["prove"]["resultDigest"], roomId=room_id,
        )
        charges = self.emit_charges(step, expected_count=1)
        # The reorg fault re-verifies the most recent durable pulse operation.
        self.state.put("reorgReference", {
            "operationId": operation_id,
            "nonceId": self.stack.nonce_id(operation),
            "txHash": str(operation["transactionHash"]).lower(),
            "outputId": artifacts["prove"]["jobId"],
            "sealedOutputSha256": artifacts["prove"]["resultDigest"],
            "ledgerCursor": int(self.state.value.get("ledgerCursor", 0)),
        })
        return charges

    def aggregate(self, index: int) -> dict[str, Any]:
        """One release aggregate cycle against the real coordinator contract.

        Mirrors the hosted-api-catalog lineage end to end: eight room
        prepare/prove/verify lineages, the stale pre-aggregate single-room
        batch for the last member, one /v5/data-availability/* lineage per
        blob member, one /v5/aggregates/prove + /v5/aggregates/verify pair
        over the ordered witness, and the room-aggregates operation binding
        exactly those durable jobs. Applied/failed/charge numbers come only
        from post-finality sources: canonical AggregateMemberOutcome facts
        and the live billing ledger.
        """
        step = f"aggregate-{index}"
        correlation = self.key(step)
        members: list[dict[str, Any]] = []
        for member_index, room_id in enumerate(self.AGGREGATE_ROOMS):
            member_step = f"{step}-m{member_index}"
            # Every member batch must carry real admitted work before it is
            # prepared, proved and settled inside the aggregate.
            self.ensure_admissions(member_step, room_id, self.member_admissions, correlation)
            jobs = self.prove_and_verify(
                member_step, room_id, correlation,
                {"cycle": member_step, "memberIndex": member_index},
            )
            members.append({"roomId": room_id, **jobs})
        deployment_domain = members[0]["proof"]["deploymentDomain"]
        if any(member["proof"]["deploymentDomain"] != deployment_domain for member in members):
            raise DriverError(f"{step} members do not share one deploymentDomain")

        # Pre-aggregate stale mutation (release-settlement-scenario.json): the
        # last member's already-proved batch is submitted first as a valid
        # single-room operation, so its unchanged aggregate member is stale at
        # settlement and must fail application without being charged.
        stale = members[STALE_MEMBER_INDEX]
        stale_operation = self.stack.publish_operation(
            "/hosting/v1/l1-operations/room-batches",
            {
                "schemaVersion": 1,
                "chainId": self.stack.chain_id,
                "roomManager": self.stack.address("roomManager"),
                "expectedOperationsAccount": self.stack.address("operationsAccount"),
                "roomId": stale["roomId"],
                "artifacts": stale["artifacts"],
                "admissionIds": [],
                "confirmationPolicy": self.confirmation_policy(),
            },
            "l1_room", self.key(step, "stale-batch"), correlation,
        )
        stale_operation_id = require_str(stale_operation.get("operationId"), "operationId")
        self.journal.emit_once(
            f"txb:{stale_operation_id}", "tx-broadcast",
            nonceId=self.stack.nonce_id(stale_operation),
            txHash=str(stale_operation["transactionHash"]).lower(),
            publisher="owner-durable-operation", operationId=stale_operation_id,
            roomId=stale["roomId"],
        )
        stale_charges = self.emit_charges(f"{step}-stale", expected_count=1)

        # Blob data-availability lineage for the first six members; the last
        # two members stay calldata-backed exactly like the release scenario.
        da_entries: list[dict[str, Any]] = []
        da_proofs: dict[int, dict[str, str]] = {}
        blob_total = 0
        for member_index in range(AGGREGATE_BLOBS):
            member = members[member_index]
            proof = member["proof"]
            seed_witness = {
                "deploymentDomain": deployment_domain,
                "roomId": member["roomId"],
                "journalHash": proof["journalHash"],
                "canonicalData": proof["canonicalData"],
                "blobStartIndex": blob_total,
            }
            # The prover derives the KZG blob vectors from the canonical data;
            # the operation compares the bound prepare job's request witness
            # against its result witness, so the complete witness is derived
            # first and the derivation job is reused when already complete.
            derive_job, derive_digest, derived = self.run_job(
                (step, f"da-derive-{member_index}"), "/v5/data-availability/prepare",
                {"equivalenceWitness": seed_witness}, member["roomId"], correlation,
            )
            full_witness = derived.get("equivalenceWitness")
            if not isinstance(full_witness, dict):
                raise DriverError(f"{step} DA member {member_index} prepare omitted its witness")
            if canonical_bytes(full_witness) == canonical_bytes(seed_witness):
                da_prepare, da_prepare_digest, prepared = derive_job, derive_digest, derived
            else:
                da_prepare, da_prepare_digest, prepared = self.run_job(
                    (step, f"da-prepare-{member_index}"), "/v5/data-availability/prepare",
                    {"equivalenceWitness": full_witness}, member["roomId"], correlation,
                )
                if canonical_bytes(prepared.get("equivalenceWitness")) != canonical_bytes(full_witness):
                    raise DriverError(f"{step} DA member {member_index} prepare does not echo its complete witness")
            statement = str(prepared.get("statement", "")).lower()
            if not HEX32.fullmatch(statement):
                raise DriverError(f"{step} DA member {member_index} prepare omitted its statement")
            da_prove, da_prove_digest, da_proved = self.run_job(
                (step, f"da-prove-{member_index}"), "/v5/data-availability/prove",
                {"equivalenceWitness": full_witness, "proofMode": "groth16", "production": True},
                member["roomId"], correlation,
            )
            real_cuda = da_proved.get("realCuda") is True or (
                isinstance(da_proved.get("gpuUuid"), str) and bool(da_proved.get("gpuUuid"))
            )
            da_program = str(da_proved.get("programId", "")).lower()
            da_receipt = da_proved.get("receiptB64")
            blobs_b64 = da_proved.get("blobsB64")
            if (
                da_proved.get("kind") != "data-availability-equivalence"
                or da_proved.get("proofMode") != "groth16" or not real_cuda
                or str(da_proved.get("statement", "")).lower() != statement
                or not HEX32.fullmatch(da_program)
                or not isinstance(da_receipt, str) or not da_receipt
            ):
                raise DriverError(f"{step} DA member {member_index} prove is not a production equivalence receipt")
            if not isinstance(blobs_b64, list) or len(blobs_b64) != 1 \
                    or not all(isinstance(blob, str) and blob for blob in blobs_b64):
                raise DriverError(f"{step} DA member {member_index} must prove exactly one blob")
            da_verify, da_verify_digest, da_verified = self.run_job(
                (step, f"da-verify-{member_index}"), "/v5/data-availability/verify",
                {"equivalenceWitness": full_witness, "receiptB64": da_receipt},
                member["roomId"], correlation,
            )
            if (
                da_verified.get("ok") is not True
                or str(da_verified.get("statement", "")).lower() != statement
                or str(da_verified.get("programId", "")).lower() != da_program
            ):
                raise DriverError(f"{step} DA member {member_index} verify does not cover the equivalence receipt")
            try:
                blob_digest = sha256_hex(base64.b64decode(blobs_b64[0]))
            except (ValueError, TypeError) as exc:
                raise DriverError(f"{step} DA member {member_index} blob is not base64") from exc
            self.journal.emit_once(
                f"blob:{step}:{member_index}", "blob-archive",
                roomId=member["roomId"], blobIndex=blob_total, blobDigest=blob_digest,
                daStatement=statement, jobId=da_prove,
            )
            da_entries.append({
                "memberIndex": member_index,
                "roomId": member["roomId"],
                "prepare": {"jobId": da_prepare, "resultDigest": da_prepare_digest},
                "prove": {"jobId": da_prove, "resultDigest": da_prove_digest},
                "verify": {"jobId": da_verify, "resultDigest": da_verify_digest},
            })
            da_proofs[member_index] = {"statement": statement, "programId": da_program, "receiptB64": da_receipt}
            blob_total += len(blobs_b64)
        if blob_total != AGGREGATE_BLOBS:
            raise DriverError(f"{step} proved {blob_total} blobs, expected {AGGREGATE_BLOBS}")

        # The recursive aggregate proof over the ordered member witnesses
        # (prover cmd_prove_aggregate_v1 / cmd_verify_aggregate_v1 contract).
        witness_members: list[dict[str, Any]] = []
        member_receipts: list[dict[str, str]] = []
        for member_index, member in enumerate(members):
            proof = member["proof"]
            da = da_proofs.get(member_index)
            witness_members.append({
                "roomId": member["roomId"],
                "roomProgramId": proof["programId"],
                "journalHash": proof["journalHash"],
                "equivalenceProgramId": da["programId"] if da else ZERO_HASH,
                "equivalenceStatement": da["statement"] if da else ZERO_HASH,
            })
            receipts = {"roomReceiptB64": proof["receiptB64"]}
            if da:
                receipts["equivalenceReceiptB64"] = da["receiptB64"]
            member_receipts.append(receipts)
        aggregate_witness = {"deploymentDomain": deployment_domain, "members": witness_members}
        aggregate_prove, aggregate_prove_digest, proved = self.run_job(
            (step, "agg-prove"), "/v5/aggregates/prove",
            {
                "aggregateWitness": aggregate_witness,
                "memberReceipts": member_receipts,
                "proofMode": "groth16",
                "production": True,
            },
            self.AGGREGATE_ROOMS[0], correlation,
        )
        real_cuda = proved.get("realCuda") is True or (
            isinstance(proved.get("gpuUuid"), str) and bool(proved.get("gpuUuid"))
        )
        aggregate_statement = str(proved.get("statement", "")).lower()
        aggregate_program = str(proved.get("programId", "")).lower()
        aggregate_receipt = proved.get("receiptB64")
        if (
            proved.get("kind") != "recursive-room-aggregate"
            or proved.get("proofMode") != "groth16" or not real_cuda
            or not HEX32.fullmatch(aggregate_statement) or not HEX32.fullmatch(aggregate_program)
            or not isinstance(aggregate_receipt, str) or not aggregate_receipt
            or require_int(proved.get("memberCount"), "aggregate memberCount") != AGGREGATE_MEMBERS
        ):
            raise DriverError(f"{step} aggregate receipt is not one production recursive CUDA Groth16 proof")
        self.journal.emit_once(
            f"prove:{step}", "prove", outputId=aggregate_prove, sealedOutputSha256=aggregate_prove_digest,
            realCuda=True, proofMode="groth16", jobId=aggregate_prove,
        )
        aggregate_verify, aggregate_verify_digest, verified = self.run_job(
            (step, "agg-verify"), "/v5/aggregates/verify",
            {"aggregateWitness": aggregate_witness, "receiptB64": aggregate_receipt},
            self.AGGREGATE_ROOMS[0], correlation,
        )
        if (
            verified.get("ok") is not True
            or str(verified.get("statement", "")).lower() != aggregate_statement
            or str(verified.get("programId", "")).lower() != aggregate_program
            or require_int(verified.get("memberCount"), "aggregate verify memberCount") != AGGREGATE_MEMBERS
        ):
            raise DriverError(f"{step} aggregate verification does not cover the recursive receipt")
        self.journal.emit_once(f"verify:{step}", "verify", realReceipt=True, jobId=aggregate_verify)

        operation = self.stack.publish_operation(
            "/hosting/v1/l1-operations/room-aggregates",
            {
                "schemaVersion": 1,
                "chainId": self.stack.chain_id,
                "roomManager": self.stack.address("roomManager"),
                "expectedOperationsAccount": self.stack.address("operationsAccount"),
                "artifacts": {
                    "members": [
                        {"roomId": member["roomId"], **member["artifacts"]} for member in members
                    ],
                    "aggregate": {
                        "prove": {"jobId": aggregate_prove, "resultDigest": aggregate_prove_digest},
                        "verify": {"jobId": aggregate_verify, "resultDigest": aggregate_verify_digest},
                    },
                    "dataAvailability": da_entries,
                },
                "confirmationPolicy": self.confirmation_policy(),
            },
            "l1_aggregate", self.key(step, "publish"), correlation,
        )
        operation_id = require_str(operation.get("operationId"), "operationId")
        # Selector, transactionType and the lineage identity live under the
        # operation's immutable operationBinding, never at the top level.
        binding = operation.get("binding")
        if not isinstance(binding, dict) or binding.get("kind") != "ROOM_AGGREGATE":
            raise DriverError("aggregate operation lacks its ROOM_AGGREGATE operationBinding")
        selector = self.stack.operation_selector(operation)
        if selector != AGGREGATE_SELECTOR:
            raise DriverError(f"aggregate operation selector {selector} differs from {AGGREGATE_SELECTOR}")
        if require_int(binding.get("transactionType"), "operationBinding.transactionType") != 3:
            raise DriverError("aggregate operationBinding is not an EIP-4844 type-3 transaction")
        if (
            str(binding.get("aggregateStatement", "")).lower() != aggregate_statement
            or str(binding.get("aggregateProgramId", "")).lower() != aggregate_program
        ):
            raise DriverError("aggregate operationBinding is not bound to the proved recursive statement")
        binding_members = binding.get("members")
        if not isinstance(binding_members, list) or len(binding_members) != AGGREGATE_MEMBERS:
            raise DriverError("aggregate operationBinding does not carry the eight ordered members")
        blob_count = require_int(binding.get("zkdealBlobCount"), "operationBinding.zkdealBlobCount")
        if blob_count != blob_total:
            raise DriverError(f"aggregate operationBinding carries {blob_count} blobs, proved {blob_total}")
        self.journal.emit_once(
            f"txb:{operation_id}", "tx-broadcast",
            nonceId=self.stack.nonce_id(operation), txHash=str(operation["transactionHash"]).lower(),
            publisher="owner-durable-operation", operationId=operation_id,
        )

        # Honest post-finality member outcomes: canonical AggregateMemberOutcome
        # facts for this exact aggregate statement, surfaced by the indexer.
        outcomes = self.stack.aggregate_member_outcomes(aggregate_statement, AGGREGATE_MEMBERS)
        if len(outcomes) != AGGREGATE_MEMBERS:
            raise DriverError(f"{step} settled {len(outcomes)} member outcomes, expected {AGGREGATE_MEMBERS}")
        failed_rooms = [
            str(outcome.get("roomId")) for outcome in outcomes
            if outcome.get("applied") not in (True, "true")
        ]
        applied = len(outcomes) - len(failed_rooms)
        failed = len(failed_rooms)
        if applied != AGGREGATE_APPLIED or failed != AGGREGATE_FAILED:
            raise DriverError(f"{step} settled applied={applied} failed={failed}, expected "
                              f"{AGGREGATE_APPLIED}+{AGGREGATE_FAILED}")
        if failed_rooms != [stale["roomId"]]:
            raise DriverError(f"{step} failed member rooms {failed_rooms} differ from the stale room")
        # Success-only charging: the live ledger must carry exactly one charge
        # per applied member and none for the stale failed member.
        charges = self.emit_charges(step, expected_count=AGGREGATE_APPLIED)
        successful_charges = charges["charges"]
        failed_charges = successful_charges - applied
        if failed_charges != 0:
            raise DriverError(f"{step} charged {failed_charges} failed members")
        self.journal.emit_once(
            f"aggregate-settle:{step}", "aggregate-settle",
            members=len(binding_members), transactionBlobs=blob_count,
            applied=applied, failed=failed,
            successfulCharges=successful_charges, failedCharges=failed_charges,
            publisher="owner-durable-operation", ownerOperationId=operation_id,
            selector=AGGREGATE_SELECTOR, transactionType=3,
            finalizedCanonical=True, castBroadcast=False,
            aggregateStatement=aggregate_statement,
            staleRoomId=stale["roomId"], staleOperationId=stale_operation_id,
        )
        self.reverify_sealed_output(aggregate_prove, aggregate_prove_digest)
        self.journal.emit_once(
            f"finalize:{step}", "finalize", outputId=aggregate_prove,
            sealedOutputSha256=aggregate_prove_digest,
        )
        return {
            "charges": stale_charges["charges"] + charges["charges"],
            "usageUnits": stale_charges["usageUnits"] + charges["usageUnits"],
            "chargesWei": str(int(stale_charges["chargesWei"]) + int(charges["chargesWei"])),
        }

    def sponsor(self) -> dict[str, Any]:
        step = "sponsor"
        correlation = self.key(step)
        run_id = self.state.run_id
        sponsorship_id = f"soak-sponsor-{run_id[:8]}"
        beneficiary_tenant = self.environ.get("SOAK_SPONSOR_BENEFICIARY_TENANT", "tenant_b").replace("_", "-")
        self.stack.create_sponsorship(
            {
                "sponsorshipId": sponsorship_id,
                "beneficiaryTenantId": beneficiary_tenant,
                "maximumQuantity": "100",
                "unit": "proof",
                "metadata": {},
            },
            self.key(step, "terms"),
        )
        profile = self.sponsor_profile(run_id)
        pool = self.stack.address("roomPool")
        sponsor_account = self.stack.address("sponsorAccount")
        base = {
            "schemaVersion": 1,
            "chainId": self.stack.chain_id,
            "roomPool": pool,
            "confirmationPolicy": self.confirmation_policy(),
        }
        reserve = self.stack.publish_operation(
            "/hosting/v1/l1-operations/pool-sponsor-mutations",
            {
                **base,
                "expectedSponsorAccount": sponsor_account,
                "sponsorshipId": sponsorship_id,
                "beneficiaryTenantId": beneficiary_tenant,
                "beneficiary": profile["beneficiary"],
                "mutation": {
                    "kind": "reserveAndStartForWithDataAvailabilityWithPermit",
                    "reservation": profile["reservation"],
                    "permit": profile["reservePermit"],
                },
            },
            "l1_sponsor", self.key(step, "reserve"), correlation,
        )
        allocation_id = str(reserve.get("allocationId", "")).lower()
        if not HEX32.fullmatch(allocation_id):
            raise DriverError("sponsored reservation did not return its allocation id")
        renew = self.stack.publish_operation(
            "/hosting/v1/l1-operations/pool-sponsor-mutations",
            {
                **base,
                "expectedSponsorAccount": sponsor_account,
                "sponsorshipId": sponsorship_id,
                "beneficiaryTenantId": beneficiary_tenant,
                "beneficiary": profile["beneficiary"],
                "mutation": {
                    "kind": "renewRoomForWithPermit",
                    "previousAllocationId": allocation_id,
                    "reservation": profile["reservation"],
                    "permit": profile["renewPermit"],
                },
            },
            "l1_sponsor", self.key(step, "renew"), correlation,
        )
        checkpoint = self.stack.publish_operation(
            "/hosting/v1/l1-operations/pool-finalized-checkpoints",
            {**base, "roomId": self.PULSE_ROOM},
            "l1_sponsor", self.key(step, "checkpoint"), correlation,
        )
        dispose = self.stack.publish_operation(
            "/hosting/v1/l1-operations/pool-beneficiary-disposals",
            {**base, "sponsorshipId": sponsorship_id, "beneficiary": profile["beneficiary"]},
            "l1_sponsor", self.key(step, "dispose"), correlation,
        )
        operations = {"reserve": reserve, "renew": renew, "checkpoint": checkpoint, "dispose": dispose}
        operation_ids: dict[str, str] = {}
        for name, operation in operations.items():
            selector = self.stack.operation_selector(operation)
            if selector != SPONSOR_SELECTORS[name]:
                raise DriverError(f"sponsor {name} selector {selector} differs from {SPONSOR_SELECTORS[name]}")
            operation_id = require_str(operation.get("operationId"), "operationId")
            operation_ids[name] = operation_id
            self.journal.emit_once(
                f"txb:{operation_id}", "tx-broadcast",
                nonceId=self.stack.nonce_id(operation), txHash=str(operation["transactionHash"]).lower(),
                publisher="owner-durable-operation", operationId=operation_id,
            )
        payer = require_str(reserve.get("payer"), "sponsor payer").lower()
        beneficiary = require_str(reserve.get("beneficiary"), "sponsor beneficiary").lower()
        refund_recipient = require_str(reserve.get("refundRecipient"), "sponsor refundRecipient").lower()
        if payer == beneficiary:
            raise DriverError("sponsorship payer and beneficiary must differ")
        if refund_recipient != payer:
            raise DriverError("sponsorship refund recipient must be the immutable payer")
        # Sender authority split: the finality checkpoint is signed by an
        # account distinct from the sponsor escrow signer.
        if str(checkpoint.get("from", "")).lower() == str(reserve.get("from", "")).lower():
            raise DriverError("sponsor operations do not split sender authority")
        charges = self.emit_charges(step, expected_count=1)
        self.journal.emit_once(
            "sponsor:once", "sponsor",
            payer=payer, beneficiary=beneficiary, refundRecipient=refund_recipient,
            doubleBilled=False, publisher="owner-durable-operation", senderAuthoritySplit=True,
            ownerOperations=operation_ids, selectors=dict(SPONSOR_SELECTORS),
            finalizedCanonical=True, sponsorshipId=sponsorship_id,
        )
        return charges

    def sponsor_profile(self, run_id: str) -> dict[str, Any]:
        """Sponsor reservation/permit material: mounted profile or derived."""
        raw_path = self.environ.get("SOAK_SPONSOR_PROFILE_FILE", "")
        if raw_path:
            value = parse_json(Path(raw_path).read_bytes(), "sponsor profile")
            if not isinstance(value, dict):
                raise DriverError("sponsor profile must be a JSON object")
            for field in ("beneficiary", "reservation", "reservePermit", "renewPermit"):
                if field not in value:
                    raise DriverError(f"sponsor profile omits {field}")
            return value

        def word(label: str) -> str:
            return "0x" + sha256_hex(f"soak-sponsor:{run_id}:{label}".encode())

        permit = {
            "value": "1000000000000000000",
            "deadline": "4102444800",
            "v": "27",
            "r": word("permit-r"),
            "s": word("permit-s"),
        }
        return {
            "beneficiary": "0x" + sha256_hex(f"soak-sponsor:{run_id}:beneficiary".encode())[:40],
            "reservation": {
                "nodeId": word("node"),
                "slotId": word("slot"),
                "presetId": word("preset"),
                "deadlineBlocksFromStart": "7200",
                "priceEpoch": "1",
                "maxTokenCharge": "1000000000000000000",
            },
            "reservePermit": permit,
            "renewPermit": {**permit, "r": word("renew-r"), "s": word("renew-s")},
        }

    def withdraw(self) -> dict[str, Any]:
        step = "withdraw"
        room_id = self.environ.get("SOAK_WITHDRAWAL_ROOM", self.PULSE_ROOM)
        epoch = self.environ.get("SOAK_WITHDRAWAL_EPOCH", "1")
        index = self.environ.get("SOAK_WITHDRAWAL_INDEX", "0")
        proof = self.stack.withdrawal_proof(room_id, epoch, index)
        _status, claim = self.stack.request_withdrawal_claim(room_id, epoch, index, self.key(step, "claim"))
        claim_id = require_str(claim.get("claimId"), "claimId")
        finalized = self.stack.withdrawal_claim(claim_id)
        operation_id = require_str(finalized.get("operationId"), "withdrawal operationId")
        operation = self.stack.wait_finalized(operation_id, "withdrawal", self.key(step))
        selector = self.stack.operation_selector(operation)
        if selector != WITHDRAWAL_SELECTOR:
            raise DriverError(f"withdrawal claim selector {selector} differs from {WITHDRAWAL_SELECTOR}")
        self.journal.emit_once(
            f"txb:{operation_id}", "tx-broadcast",
            nonceId=self.stack.nonce_id(operation), txHash=str(operation["transactionHash"]).lower(),
            publisher="owner-durable-operation", operationId=operation_id, roomId=room_id,
        )
        # Replay the already-claimed withdrawal under a fresh idempotency key;
        # the owner must reject it instead of double-claiming.
        replay_status, _body = self.stack.request_withdrawal_claim(
            room_id, epoch, index, self.key(step, "claim-replay"), expect=(409,),
        )
        if replay_status != 409:
            raise DriverError("withdrawal claim replay was not rejected")
        charges = self.emit_charges(step, expected_count=1)
        self.journal.emit_once(
            "withdraw:once", "withdraw",
            realProof=True, claimed=True, replayRejected=True,
            publisher="owner-durable-operation", ownerOperationId=operation_id,
            selector=WITHDRAWAL_SELECTOR, finalizedCanonical=True,
            roomId=room_id, claimId=claim_id, proofFinalized=bool(proof.get("finalized") is True),
        )
        return charges

    def reconcile(self) -> dict[str, Any]:
        status = self.stack.fresh_indexer()
        self.emit_charges("reconcile")
        usage = 0
        wei = 0
        charge_ids: set[str] = set()
        for event in self.journal.events:
            if event.get("kind") != "charge":
                continue
            charge_id = str(event.get("chargeId", ""))
            if charge_id in charge_ids:
                raise DriverError(f"duplicate charge {charge_id} detected during reconciliation")
            charge_ids.add(charge_id)
            usage += int(event.get("usageUnits", 0))
            wei += int(event.get("chargeWei", 0))
        ledger = self.stack.ledger_entries(0)
        ledger_usage = sum(entry["usageUnits"] for entry in ledger)
        ledger_wei = sum(int(entry["chargeWei"]) for entry in ledger)
        if {entry["chargeId"] for entry in ledger} != charge_ids or ledger_usage != usage or ledger_wei != wei:
            raise DriverError(
                f"journal charges (usage={usage}, wei={wei}) diverge from the live ledger "
                f"(usage={ledger_usage}, wei={ledger_wei})"
            )
        self.journal.emit_once(
            "reconcile:once", "reconcile",
            cursor=require_int(status.get("cursor"), "indexer cursor"),
            usageUnits=usage, chargesWei=str(wei), charges=len(charge_ids),
        )
        return {"usageUnits": usage, "chargesWei": str(wei)}


# ---------------------------------------------------------------------------
# FaultInjector: the eight reviewed faults and their recovery assertions.
# ---------------------------------------------------------------------------


class FaultInjector:
    def __init__(
        self,
        http: Http,
        stack: Stack,
        journal: Journal,
        state: DriverState,
        clock: Clock,
        environ: Mapping[str, str],
        kill_hook: Callable[[], None] | None = None,
    ):
        self.http = http
        self.stack = stack
        self.journal = journal
        self.state = state
        self.clock = clock
        self.environ = environ
        self.kill_hook = kill_hook
        self.binding: dict[str, Any] | None = None

    def key(self, *parts: str) -> str:
        return "soak-" + self.state.run_id + "-" + "-".join(parts)

    def fault_binding(self) -> dict[str, Any]:
        if self.binding is None:
            _status, body, _raw = self.http.request(
                "fault", "GET", "/capabilities", attempts=60, interval=1.0,
            )
            if not isinstance(body, dict):
                raise DriverError("fault-control capabilities response is not an object")
            binding = {
                "candidateId": require_str(body.get("candidateId"), "fault candidateId"),
                "planSha256": require_str(body.get("planSha256"), "fault planSha256"),
                "hostedIntegrationToken": require_str(body.get("hostedIntegrationToken"), "fault hostedIntegrationToken"),
            }
            expected_candidate = self.environ.get("SOAK_CANDIDATE_ID", "")
            if expected_candidate and binding["candidateId"] != expected_candidate:
                raise DriverError("fault-control candidate differs from SOAK_CANDIDATE_ID")
            expected_token = self.environ.get("HOSTED_INTEGRATION_TOKEN", "")
            if expected_token and binding["hostedIntegrationToken"] != expected_token:
                raise DriverError("fault-control hostedIntegrationToken differs from the candidate plan")
            self.binding = binding
        return dict(self.binding)

    def control(self, purpose: str, action: str, parameters: dict[str, Any]) -> dict[str, Any]:
        correlation = self.key(purpose)[:128]
        for _round in range(2):
            attempt = self.state.attempt(purpose)
            idempotency = f"{self.key(purpose)}-a{attempt}"
            status, body, _raw = self.http.request(
                "fault", "POST", "/v1/faults",
                body={
                    "schemaVersion": 1,
                    "binding": self.fault_binding(),
                    "action": action,
                    "parameters": parameters,
                },
                auth="fault_control",
                headers={"idempotency-key": idempotency, "x-correlation-id": correlation},
                expect=(200, 409), attempts=30, interval=1.0,
            )
            if status == 200:
                if not isinstance(body, dict) or not isinstance(body.get("operationId"), str):
                    raise DriverError(f"fault-control {action} returned no durable operation")
                return body
            code = ""
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                code = str(body["error"].get("code", ""))
            if code in {"INCOMPLETE_OPERATION", "JOURNAL_CONFLICT", "IDEMPOTENCY_CONFLICT"}:
                # A crash landed between the controller's durable intent and
                # its closure; the reviewed recovery is a fresh idempotency key.
                self.state.bump_attempt(purpose)
                continue
            raise DriverError(f"fault-control {action} conflicted: {code}")
        raise DriverError(f"fault-control {action} kept conflicting after a fresh idempotency key")

    def emit_fault(self, name: str, at_second: int) -> None:
        self.journal.emit_once(f"fault:{name}", "fault", fault=name, scheduledAtSecond=at_second)

    def emit_recovered(self, name: str, **fields: Any) -> None:
        self.journal.emit_once(f"recovered:{name}", "recovered", fault=name, **fields)

    def run(self, name: str, at_second: int) -> dict[str, Any]:
        handlers: dict[str, Callable[[str, int], dict[str, Any]]] = {
            "headless-restart": self.simple_restart,
            "prover-restart": self.simple_restart,
            "object-store-restart": self.simple_restart,
            "database-restart": self.simple_restart,
            "rpc-split": self.rpc_split,
            "indexer-rollback": self.indexer_rollback,
            "coordinator-promotion": self.coordinator_promotion,
            "docker-host-restart-resume": self.docker_host_restart,
        }
        if name not in handlers:
            raise DriverError(f"scheduled fault {name} is not a reviewed fault kind")
        return handlers[name](name, at_second)

    def simple_restart(self, name: str, at_second: int) -> dict[str, Any]:
        self.emit_fault(name, at_second)
        result = self.control(f"fault-{name}", name, {})
        if result.get("applied") is not True and result.get("restarted") is not True:
            raise DriverError(f"fault {name} was not applied by the controller")
        probes = {
            "headless-restart": ("headless", "health"),
            "prover-restart": ("prover", "health"),
            "object-store-restart": ("coordinator", "ready"),
            "database-restart": ("coordinator", "ready"),
        }
        endpoint, probe = probes[name]
        if probe == "health":
            self.stack.health(endpoint)
        else:
            self.stack.ready()
        if name == "database-restart":
            self.stack.fresh_indexer()
        self.emit_recovered(name, operationId=str(result["operationId"]))
        return result

    def rpc_split(self, name: str, at_second: int) -> dict[str, Any]:
        self.emit_fault(name, at_second)
        started = self.control(
            "fault-rpc-split-start", "rpc-disagreement",
            {"phase": "start", "preparedOperationId": None},
        )
        if started.get("phase") != "DISAGREEING" or started.get("disagreeA") == started.get("disagreeB"):
            raise DriverError("rpc-split fault did not create provider disagreement")
        restored = self.control(
            "fault-rpc-split-restore", "rpc-disagreement",
            {"phase": "restore", "preparedOperationId": str(started["operationId"])},
        )
        if restored.get("phase") != "RESTORED" or restored.get("restoredA") != restored.get("restoredB"):
            raise DriverError("rpc-split providers did not converge after restore")
        self.stack.ready()
        self.emit_recovered(name, operationId=str(restored["operationId"]))
        return restored

    def indexer_rollback(self, name: str, at_second: int) -> dict[str, Any]:
        reference = self.state.get("reorgReference")
        if not isinstance(reference, dict):
            raise DriverError("indexer-rollback requires a prior durable pulse operation")
        prepared = self.control(
            "fault-reorg-prepare", "l1-reorg",
            {"phase": "prepare", "depth": REORG_DEPTH, "preparedOperationId": None},
        )
        if prepared.get("phase") != "PREPARED":
            raise DriverError("l1-reorg branch was not prepared")
        self.emit_fault(name, at_second)
        replaced = self.control(
            "fault-reorg-replace", "l1-reorg",
            {"phase": "replace", "depth": REORG_DEPTH, "preparedOperationId": str(prepared["operationId"])},
        )
        previous = str(replaced.get("previousBlockHash", "")).lower()
        canonical = str(replaced.get("canonicalBlockHash", "")).lower()
        if replaced.get("phase") != "REPLACED" or not HEX32.fullmatch(previous) \
                or not HEX32.fullmatch(canonical) or previous == canonical:
            raise DriverError("l1-reorg replacement did not change the canonical hash")
        ledger_before = self.stack.ledger_entries(0)
        before_ids = [entry["chargeId"] for entry in ledger_before]
        if len(before_ids) != len(set(before_ids)):
            raise DriverError("ledger already carried duplicate charges before the reorg")
        high_water = max((entry["entryId"] for entry in ledger_before), default=0)
        rollback = self.control(
            "fault-indexer-rollback", "indexer-rollback",
            {"preparedOperationId": str(prepared["operationId"])},
        )
        if rollback.get("rollbackApplied") is not True:
            raise DriverError("indexer rollback was not applied")
        self.stack.fresh_indexer()
        # Canonical recovery must not mint a new nonce or a duplicate charge:
        # the referenced pulse operation must still be the same finalized
        # durable operation, and the rollback window must not grow the ledger.
        operation = self.stack.wait_finalized(
            str(reference["operationId"]), "l1_room", self.key("fault-reorg-recheck"),
        )
        if self.stack.nonce_id(operation) != reference["nonceId"]:
            raise DriverError("reorg recovery changed the durable operation nonce")
        if self.stack.ledger_entries(high_water):
            raise DriverError("reorg recovery minted a new charge during the rollback window")
        self.journal.emit_once(
            "reorg:once", "reorg",
            preFinalityOrphaned=True, canonicalRecovery=True,
            duplicateNonce=False, duplicateCharge=False,
            previousBlockHash=previous, canonicalBlockHash=canonical,
            rollbackDepth=REORG_DEPTH, operationId=str(reference["operationId"]),
        )
        self.emit_recovered(name, operationId=str(rollback["operationId"]))
        return rollback

    def coordinator_promotion(self, name: str, at_second: int) -> dict[str, Any]:
        self.emit_fault(name, at_second)
        terminated = self.control("fault-coordinator-terminate", "coordinator-terminate", {})
        if terminated.get("applied") is not True:
            raise DriverError("coordinator-terminate was not applied")
        active = self.environ.get("ACTIVE_COORDINATOR_ID", "")
        standby = self.environ.get("STANDBY_COORDINATOR_ID", "")
        if not active or not standby:
            raise DriverError("ACTIVE_COORDINATOR_ID and STANDBY_COORDINATOR_ID are required for coordinator-promotion")
        witnesses = int(self.environ.get("SOAK_FAILOVER_WITNESS_COUNT", "2"))
        operation_id = f"{self.key('promotion')[:64]}".lower()
        approval = {"x-zkdeal-failover-approval": self.http.token("failover_approval")}
        _status, prepared, _raw = self.http.request(
            "failover", "POST", "/v1/failovers",
            body={
                "candidateId": operation_id,
                "activeCoordinatorId": active,
                "standbyCoordinatorId": standby,
                "failedWitnessCount": witnesses,
            },
            auth="failover_control",
            headers={"idempotency-key": self.key("promotion-prepare"), **approval},
            attempts=30, interval=1.0,
        )
        if not isinstance(prepared, dict) or prepared.get("status") != "READY_FOR_APPLICATION_PROMOTION":
            raise DriverError("failover provider did not reach READY_FOR_APPLICATION_PROMOTION")
        for field in ("activeFenced", "oldWriterTerminated", "databasePromoted", "standbyReplayAtOrAfterTarget"):
            require_true(prepared.get(field), f"failover prepare {field}")
        if prepared.get("standbySignerAuthorityActive") is not False:
            raise DriverError("standby signer authority was active before the fence")
        _status, committed, _raw = self.http.request(
            "failover", "POST", f"/v1/failovers/{operation_id}/commit",
            body={"ownerResponseSha256": sha256_hex(canonical_bytes(prepared))},
            auth="failover_control",
            headers={"idempotency-key": self.key("promotion-commit"), **approval},
            attempts=30, interval=1.0,
        )
        if not isinstance(committed, dict):
            raise DriverError("failover commit response is not an object")
        for field in ("writerRouteCommitted", "oldWriterRouteRemoved", "signerAuthorityActivatedAfterFence"):
            require_true(committed.get(field), f"failover commit {field}")
        self.stack.ready()
        self.emit_recovered(name, operationId=operation_id, rtoSeconds=int(committed.get("rtoSeconds", 0)))
        return committed

    def docker_host_restart(self, name: str, at_second: int) -> dict[str, Any]:
        """Announce the fault, then die by SIGKILL; the respawned worker
        verifies the durable resume and emits the recovery assertion."""
        recovered_id = f"recovered:{name}"
        if recovered_id in self.journal.event_ids:
            return {"resumed": True}
        if f"fault:{name}" not in self.journal.event_ids:
            self.emit_fault(name, at_second)
            marker = Path(self.environ["ZKDEAL_SOAK_JOURNAL"] + ".kill-request")
            marker.write_text(json.dumps({"fault": name}) + "\n", encoding="utf-8")
            if self.kill_hook is not None:
                self.kill_hook()
                raise DriverError("injected kill hook returned instead of terminating the worker")
            while True:  # pragma: no cover - terminated by the supervisor SIGKILL
                self.clock.sleep(5.0)
        # Resume path: the fault event is durable, the process tree was killed
        # and this is the respawned worker. Verify the resume invariants.
        if int(self.state.value.get("workerBoots", 0)) < 2:
            raise DriverError("docker-host-restart-resume recovery is running in the killed worker generation")
        finalized = [event for event in self.journal.events if event.get("kind") == "finalize"]
        if not finalized:
            raise DriverError("no finalized sealed output existed before the docker-host restart")
        last = finalized[-1]
        # Byte-identical sealed output across the host restart.
        _body, raw = self.stack.queue_result(str(last["outputId"]), str(last["sealedOutputSha256"]))
        if sha256_hex(raw) != last["sealedOutputSha256"]:
            raise DriverError("sealed output changed across the docker-host restart")
        self.stack.ready()
        self.stack.fresh_indexer()
        self.state.value["resumeVerified"] = True
        self.state.save()
        self.emit_recovered(
            name, resumedSeq=len(self.journal.events),
            reverifiedOutputId=str(last["outputId"]), workerBoots=int(self.state.value["workerBoots"]),
        )
        return {"resumed": True}


# ---------------------------------------------------------------------------
# Pacer: the merged workload/fault timeline with deadline catch-up.
# ---------------------------------------------------------------------------


class Slot:
    __slots__ = ("second", "order", "key", "run")

    def __init__(self, second: int, order: int, key: str, run: Callable[[], dict[str, Any] | None]):
        self.second = second
        self.order = order
        self.key = key
        self.run = run


def build_plan(manifest: Mapping[str, Any], workload: Workload, injector: FaultInjector) -> list[Slot]:
    duration = int(manifest["durationSeconds"])
    faults = {str(item["kind"]): int(item["atSecond"]) for item in manifest["scheduledFaults"]}
    docker_at = faults["docker-host-restart-resume"]
    slots: list[Slot] = [Slot(0, 0, "setup", workload.setup)]
    pulse_interval = max(1, duration // PULSE_COUNT)
    for index in range(PULSE_COUNT):
        slots.append(Slot(index * pulse_interval, 1, f"pulse-{index:02d}",
                          lambda index=index: workload.pulse(index)))
    # Aggregate cycles: one must fully finalize before the docker-host fault
    # and one must follow it, so restart resume is bracketed by durable
    # aggregate evidence.
    aggregate_seconds = [duration // 12, duration // 2, (duration * 7) // 8]
    if not any(second < docker_at for second in aggregate_seconds):
        aggregate_seconds[0] = max(0, docker_at - 60)
    if not any(second > docker_at for second in aggregate_seconds):
        aggregate_seconds[-1] = min(duration - 60, docker_at + 60)
    for index, second in enumerate(sorted(aggregate_seconds)):
        slots.append(Slot(second, 2, f"aggregate-{index}",
                          lambda index=index: workload.aggregate(index)))
    slots.append(Slot(duration // 3, 3, "sponsor", workload.sponsor))
    slots.append(Slot((duration * 2) // 3, 4, "withdraw", workload.withdraw))
    for name in sorted(faults):
        at_second = faults[name]
        slots.append(Slot(at_second, 5, f"fault-{name}",
                          lambda name=name, at_second=at_second: injector.run(name, at_second)))
    slots.append(Slot(max(0, duration - 60), 6, "reconcile", workload.reconcile))
    return sorted(slots, key=lambda slot: (slot.second, slot.order, slot.key))


class Pacer:
    """Runs the timeline against elapsed soak seconds with <=60s sleep slices.

    Elapsed position is checkpointed into DriverState on every completed slot
    so a killed worker resumes at its durable timeline offset instead of
    restarting the twelve hours.
    """

    def __init__(self, clock: Clock, state: DriverState):
        self.clock = clock
        self.state = state
        self.base = int(state.value.get("elapsedSeconds", 0))
        self.started = clock.monotonic()
        self.deadline_misses = 0

    def elapsed(self) -> float:
        return self.base + (self.clock.monotonic() - self.started)

    def wait_until(self, second: int) -> None:
        while self.elapsed() < second:
            remaining = second - self.elapsed()
            self.clock.sleep(min(60.0, max(remaining, 0.05)))

    def run(self, slots: list[Slot], duration: int, grace_seconds: int = 300) -> None:
        for slot in slots:
            if self.state.step_done(slot.key):
                continue
            self.wait_until(slot.second)
            if self.elapsed() > slot.second + grace_seconds:
                # Deadline catch-up: the slot still runs, but the miss is
                # counted against the manifest deadline budget.
                self.deadline_misses += 1
            result = slot.run()
            self.state.mark_done(slot.key, int(self.elapsed()), result if isinstance(result, dict) else None)
        self.wait_until(duration)


# ---------------------------------------------------------------------------
# Expected-results assertion and evidence closure.
# ---------------------------------------------------------------------------


def journal_accounting(journal: Journal) -> dict[str, Any]:
    nonces: list[str] = []
    charge_ids: list[str] = []
    usage = 0
    wei = 0
    for event in journal.events:
        kind = event.get("kind")
        if kind == "tx-broadcast" and "nonceId" in event:
            nonces.append(str(event["nonceId"]))
        if kind == "charge":
            charge_ids.append(str(event.get("chargeId", "")))
            usage += int(event.get("usageUnits", 0))
            wei += int(event.get("chargeWei", 0))
    return {
        "usageUnits": usage,
        "chargesWei": wei,
        "chargeIds": charge_ids,
        "nonces": nonces,
        "duplicateNonces": len(nonces) - len(set(nonces)),
        "duplicateCharges": len(charge_ids) - len(set(charge_ids)),
    }


def assert_expected(
    manifest: Mapping[str, Any],
    journal: Journal,
    stack: Stack,
    state: DriverState,
) -> dict[str, Any]:
    """Recompute usage/charge sums from journal and live ledger and hard-fail
    on any divergence from the manifest expectation, before any closure."""
    accounting = journal_accounting(journal)
    if accounting["duplicateNonces"]:
        raise DriverError("duplicate transaction nonce detected before closure")
    if accounting["duplicateCharges"]:
        raise DriverError("duplicate charge detected before closure")
    ledger = stack.ledger_entries(0)
    ledger_usage = sum(entry["usageUnits"] for entry in ledger)
    ledger_wei = sum(int(entry["chargeWei"]) for entry in ledger)
    if {entry["chargeId"] for entry in ledger} != set(accounting["chargeIds"]):
        raise DriverError("journaled charge IDs diverge from the live ledger")
    if ledger_usage != accounting["usageUnits"] or ledger_wei != accounting["chargesWei"]:
        raise DriverError(
            f"journal charges (usage={accounting['usageUnits']}, wei={accounting['chargesWei']}) "
            f"diverge from the live ledger (usage={ledger_usage}, wei={ledger_wei})"
        )
    expected_usage = int(manifest["expected"]["usageUnits"])
    expected_wei = int(manifest["expected"]["chargesWei"])
    if accounting["usageUnits"] != expected_usage or accounting["chargesWei"] != expected_wei:
        raise DriverError(
            f"observed usage/charges (usage={accounting['usageUnits']}, wei={accounting['chargesWei']}) "
            f"do not match the manifest expected (usage={expected_usage}, wei={expected_wei})"
        )
    status = stack.fresh_indexer()
    if status.get("unresolvedSafetyEvents") != 0:
        raise DriverError("unresolved safety events remain before closure")
    if state.value.get("resumeVerified") is not True:
        raise DriverError("restart resume was never verified; the release soak is not closable")
    return accounting


def write_closure(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    journal: Journal,
    pacer: Pacer,
    http: Http,
    accounting: dict[str, Any],
) -> dict[str, Any]:
    budgets = manifest.get("budgets") if isinstance(manifest.get("budgets"), dict) else {}
    max_fairness = int(budgets.get("maxFairnessWaitMs", 5000))
    max_misses = int(budgets.get("maxDeadlineMisses", 0))
    fairness_met = http.max_wait_ms <= max_fairness
    deadline_met = pacer.deadline_misses <= max_misses
    if not fairness_met:
        raise DriverError(f"bounded backoff exceeded the fairness budget: {http.max_wait_ms}ms > {max_fairness}ms")
    if not deadline_met:
        raise DriverError(f"{pacer.deadline_misses} timeline deadline misses exceed the budget {max_misses}")
    duration = max(int(pacer.elapsed()), int(manifest["durationSeconds"]))
    return journal.close({
        "manifestSha256": sha256_file(manifest_path),
        "durationSeconds": duration,
        "sealedOutputsUnchanged": True,
        "resumeVerified": True,
        "unresolvedSafetyEvents": 0,
        "unresolvedClaims": 0,
        "duplicateNonces": accounting["duplicateNonces"],
        "duplicateCharges": accounting["duplicateCharges"],
        "fairnessBudgetMet": fairness_met,
        "deadlineBudgetMet": deadline_met,
    })


# ---------------------------------------------------------------------------
# Worker, supervisor, argv contract.
# ---------------------------------------------------------------------------


def state_path_for(environ: Mapping[str, str], journal_path: Path) -> Path:
    raw = environ.get("ZKDEAL_OWNER_SOAK_STATE", "")
    path = Path(raw) if raw else journal_path.with_name(journal_path.name + ".owner-state.json")
    runner_state = environ.get("ZKDEAL_SOAK_STATE", "")
    if runner_state and Path(runner_state).resolve() == path.resolve():
        raise DriverError("owner driver state must be distinct from the runner ZKDEAL_SOAK_STATE file")
    return path


def run_worker(
    environ: Mapping[str, str] | None = None,
    clock: Clock | None = None,
    kill_hook: Callable[[], None] | None = None,
    journal_hook: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    env = dict(os.environ if environ is None else environ)
    clock = clock or Clock()
    manifest_path = Path(env.get("ZKDEAL_SOAK_MANIFEST", ""))
    journal_raw = env.get("ZKDEAL_SOAK_JOURNAL", "")
    if not env.get("ZKDEAL_SOAK_MANIFEST") or not journal_raw:
        raise DriverError("ZKDEAL_SOAK_MANIFEST and ZKDEAL_SOAK_JOURNAL are required")
    journal_path = Path(journal_raw)
    manifest = load_manifest(manifest_path)
    manifest_sha = sha256_file(manifest_path)
    journal = Journal(journal_path, hook=journal_hook)
    if journal.closed:
        # Write-once closure: a completed journal is terminal evidence.
        print(json.dumps({"component": "owner-soak-driver", "event": "already-closed"}, sort_keys=True), flush=True)
        return 0
    state = DriverState(state_path_for(env, journal_path), manifest_sha)
    resume = env.get("ZKDEAL_SOAK_RESUME") == "1" or bool(journal.events) or state.existed
    state.value["workerBoots"] = int(state.value.get("workerBoots", 0)) + 1
    state.save()
    http = Http(env, clock)
    stack = Stack(http, clock, env, manifest)
    workload = Workload(stack, journal, state, env, manifest)
    injector = FaultInjector(http, stack, journal, state, clock, env, kill_hook=kill_hook)
    print(json.dumps({
        "component": "owner-soak-driver",
        "event": "worker-started",
        "resume": resume,
        "workerBoots": int(state.value["workerBoots"]),
        "journalEvents": len(journal.events),
    }, sort_keys=True), flush=True)
    # Capability negotiation happens on every worker boot (not only inside the
    # setup slot) so a resumed worker re-learns the deployment addresses.
    stack.capabilities()
    pacer = Pacer(clock, state)
    pacer.run(build_plan(manifest, workload, injector), int(manifest["durationSeconds"]))
    accounting = assert_expected(manifest, journal, stack, state)
    closure = write_closure(manifest_path, manifest, journal, pacer, http, accounting)
    print(json.dumps({
        "component": "owner-soak-driver",
        "event": "closure-written",
        "seq": closure["seq"],
        "durationSeconds": closure["durationSeconds"],
        "usageUnits": accounting["usageUnits"],
        "chargesWei": str(accounting["chargesWei"]),
    }, sort_keys=True), flush=True)
    return 0


def run_supervisor(environ: Mapping[str, str] | None = None, max_respawns: int = 4) -> int:
    """Fork the worker and deliver the docker-host SIGKILL when requested.

    The worker announces the docker-host-restart-resume fault by writing a
    kill-request marker next to the journal and then blocking; the supervisor
    SIGKILLs it mid-tree and respawns it with resume enabled. The journal has
    exactly one writer at any time: the supervisor never appends events.
    """
    env = dict(os.environ if environ is None else environ)
    journal_raw = env.get("ZKDEAL_SOAK_JOURNAL", "")
    if not journal_raw:
        raise DriverError("ZKDEAL_SOAK_JOURNAL is required")
    marker = Path(journal_raw + ".kill-request")
    marker.unlink(missing_ok=True)
    resume = env.get("ZKDEAL_SOAK_RESUME") == "1"
    respawns = 0
    while True:
        child_env = {**env, "ZKDEAL_SOAK_RESUME": "1" if resume else "0"}
        process = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), *ARGV_MARKERS, "--worker"],
            env=child_env,
        )
        while True:
            returncode = process.poll()
            if marker.exists():
                process.kill()
                process.wait()
                marker.unlink(missing_ok=True)
                resume = True
                respawns += 1
                if respawns > max_respawns:
                    raise DriverError("worker kill/respawn budget exhausted")
                print(json.dumps({
                    "component": "owner-soak-driver",
                    "event": "worker-killed-for-docker-host-restart",
                    "respawns": respawns,
                }, sort_keys=True), flush=True)
                break
            if returncode is not None:
                return returncode
            time.sleep(1)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="zkdeal-owner-soak")
    value.add_argument("--submit-real-proof-jobs", action="store_true", required=True)
    value.add_argument("--restart", action="store_true", required=True)
    value.add_argument(
        "--assert-durable-results,cursors,nonces,charges,sealed-output,safety,claims,fairness,deadlines",
        dest="assert_durable", action="store_true", required=True,
    )
    value.add_argument("--bounded-backoff", action="store_true", required=True)
    value.add_argument("--emit-evidence-closure", action="store_true", required=True)
    value.add_argument("--worker", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        if args.worker:
            return run_worker()
        return run_supervisor()
    except WorkerKilled:
        raise
    except (DriverError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


