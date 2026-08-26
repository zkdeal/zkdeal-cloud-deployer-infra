#!/usr/bin/env python3
"""Candidate-bound, allowlisted acceptance fault and load controller.

The HTTP caller selects only reviewed actions and logical target names.  RPC
URLs, Docker container identities, load paths, methods and request bodies come
from one hash-bound topology file and are never accepted from a request.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import http.client
import json
import os
import re
import socket
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_TARGET_RESPONSE_BYTES = 4 * 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")
TOKEN = re.compile(r"^sha256:[0-9a-f]{64}$")
CANDIDATE = re.compile(r"^[a-z0-9][a-z0-9._-]{7,79}$")
SAFE_ID = re.compile(r"^[a-z][a-z0-9._-]{2,79}$")
IDEMPOTENCY = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")
CORRELATION = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
OCI_DIGEST = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
EPHEMERAL_TOKEN = re.compile(r"^eph_[A-Za-z0-9_-]{28,120}$")
ALLOWED_RESTART_TARGETS = {
    "coordinator-active",
    "coordinator-standby",
    "indexer",
    "reconciler",
    "publisher",
    "headless-node",
    "prover",
    "prover-agent",
    "postgres-primary",
    "postgres-standby",
    "minio",
    "front-door",
    "rpc-a",
    "rpc-b",
}
# Logical services whose containers may be paused/unpaused to inject a stall.
ALLOWED_PAUSE_TARGETS = {
    "coordinator-active",
    "indexer",
    "reconciler",
    "publisher",
    "headless-node",
    "prover",
    "prover-agent",
}
ALLOWED_LOAD_PROFILES = {
    "rpc",
    "sse",
    "indexer",
    "admission",
    "scheduler",
    "projection",
}
ALLOWED_LOAD_METHODS = {"GET", "POST"}


class ControlError(RuntimeError):
    def __init__(self, message: str, status: int = 400, code: str = "INVALID_REQUEST"):
        super().__init__(message)
        self.status = status
        self.code = code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def strict_object(raw: bytes, label: str, maximum: int = MAX_REQUEST_BYTES) -> dict[str, Any]:
    if len(raw) > maximum:
        raise ControlError(f"{label} exceeds {maximum} bytes", 413, "DOCUMENT_TOO_LARGE")

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ControlError(f"{label} contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ControlError(f"{label} must contain an object")
    return value


def exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ControlError(f"{label} keys differ; missing={missing}, extra={extra}")


def bounded_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ControlError(f"{label} must be an integer from {minimum} through {maximum}")
    return value


def read_secret(
    path_value: str,
    expected_hash: str,
    label: str,
    *,
    require_ephemeral: bool = False,
) -> str:
    path = Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise ControlError(f"{label} is absent, not regular, or a symlink", 503, "CONFIGURATION_ERROR")
    # POSIX permission bits are the enforced boundary in the Linux runtime;
    # they are not meaningful on platforms that do not honor st_mode, where the
    # controller never runs, so the check applies only where it is enforceable.
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ControlError(f"{label} must have mode 0600 or stricter", 503, "CONFIGURATION_ERROR")
    raw = path.read_bytes()
    if len(raw) > 4096:
        raise ControlError(f"{label} exceeds 4096 bytes", 503, "CONFIGURATION_ERROR")
    try:
        token = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ControlError(f"{label} is not UTF-8", 503, "CONFIGURATION_ERROR") from exc
    if len(token) < 24 or any(character.isspace() for character in token):
        raise ControlError(f"{label} is too short or contains whitespace", 503, "CONFIGURATION_ERROR")
    if require_ephemeral and not EPHEMERAL_TOKEN.fullmatch(token):
        raise ControlError(f"{label} must contain an enclave-scoped eph_ token", 503, "CONFIGURATION_ERROR")
    if not SHA256.fullmatch(expected_hash) or sha256_bytes(token.encode()) != expected_hash:
        raise ControlError(f"{label} differs from the topology hash", 503, "CONFIGURATION_ERROR")
    return token


def normalized_url(value: Any, allowed_hosts: set[str], label: str) -> str:
    if not isinstance(value, str) or len(value) > 512:
        raise ControlError(f"{label} must be a bounded URL", 503, "CONFIGURATION_ERROR")
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise ControlError(f"{label} is malformed", 503, "CONFIGURATION_ERROR") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed.hostname not in allowed_hosts
    ):
        raise ControlError(f"{label} is outside the fixed target topology", 503, "CONFIGURATION_ERROR")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def safe_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) > 256 or not value.startswith("/") or "\\" in value:
        raise ControlError(f"{label} must be a bounded absolute path", 503, "CONFIGURATION_ERROR")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or ".." in parsed.path.split("/"):
        raise ControlError(f"{label} escapes its fixed endpoint", 503, "CONFIGURATION_ERROR")
    return parsed.path


def fsync_dir(path: Path) -> None:
    """Best-effort durable flush of a directory entry.

    Directory fsync is a POSIX durability idiom; platforms that refuse to open a
    directory for fsync (where this controller never runs) skip it rather than
    failing the write, which is already complete.
    """
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


def durable_create(path: Path, value: dict[str, Any]) -> str:
    payload = canonical_bytes(value)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ControlError(f"write-once journal record already exists: {path.name}", 409, "JOURNAL_CONFLICT") from exc
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_dir(path.parent)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return sha256_bytes(payload)


def read_record(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ControlError("journal record is absent or unsafe", 409, "INCOMPLETE_OPERATION")
    return strict_object(path.read_bytes(), "journal record", MAX_RESPONSE_BYTES)


@dataclass(frozen=True)
class LoadEndpoint:
    base_url: str
    path: str
    method: str
    body: dict[str, Any] | None
    token: str | None


@dataclass(frozen=True)
class Topology:
    classification: str
    candidate_id: str
    plan_sha256: str
    hosted_token: str
    auth_token_sha256: str
    allowed_hosts: frozenset[str]
    rpc_a: str
    rpc_b: str
    docker_socket: str | None
    restart_targets: dict[str, str]
    partition_network: str | None
    partition_targets: tuple[str, ...]
    load_profiles: dict[str, tuple[LoadEndpoint, ...]]
    request_timeout_seconds: int
    topology_sha256: str

    @property
    def binding(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidate_id,
            "planSha256": self.plan_sha256,
            "hostedIntegrationToken": self.hosted_token,
        }


def load_topology(path_value: str, expected_sha256: str) -> Topology:
    path = Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise ControlError("FAULT_CONTROL_TOPOLOGY_FILE is absent or unsafe", 503, "CONFIGURATION_ERROR")
    raw = path.read_bytes()
    observed = sha256_bytes(raw)
    if not SHA256.fullmatch(expected_sha256) or observed != expected_sha256:
        raise ControlError("fault topology differs from FAULT_CONTROL_TOPOLOGY_SHA256", 503, "CONFIGURATION_ERROR")
    value = strict_object(raw, "fault topology", MAX_RESPONSE_BYTES)
    exact_keys(value, {
        "schemaVersion", "classification", "candidateId", "planSha256",
        "hostedIntegrationToken", "faultControlTokenSha256", "allowedHosts",
        "rpcProviders", "docker", "loadProfiles", "requestTimeoutSeconds",
    }, "fault topology")
    if value["schemaVersion"] != 1 or value["classification"] not in {
        "release-candidate", "non-release-fixture",
    }:
        raise ControlError("fault topology schema/classification is invalid", 503, "CONFIGURATION_ERROR")
    candidate_id = value["candidateId"]
    plan_sha256 = value["planSha256"]
    hosted_token = value["hostedIntegrationToken"]
    auth_hash = value["faultControlTokenSha256"]
    if not isinstance(candidate_id, str) or not CANDIDATE.fullmatch(candidate_id):
        raise ControlError("fault topology candidateId is malformed", 503, "CONFIGURATION_ERROR")
    if not isinstance(plan_sha256, str) or not SHA256.fullmatch(plan_sha256):
        raise ControlError("fault topology planSha256 is malformed", 503, "CONFIGURATION_ERROR")
    if not isinstance(hosted_token, str) or not TOKEN.fullmatch(hosted_token):
        raise ControlError("fault topology hostedIntegrationToken is malformed", 503, "CONFIGURATION_ERROR")
    if not isinstance(auth_hash, str) or not SHA256.fullmatch(auth_hash):
        raise ControlError("fault topology scoped token hash is malformed", 503, "CONFIGURATION_ERROR")
    hosts = value["allowedHosts"]
    if (
        not isinstance(hosts, list)
        or not 2 <= len(hosts) <= 32
        or any(not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host) for host in hosts)
        or len(set(hosts)) != len(hosts)
    ):
        raise ControlError("fault topology allowedHosts is invalid", 503, "CONFIGURATION_ERROR")
    allowed_hosts = set(hosts)
    providers = value["rpcProviders"]
    if not isinstance(providers, dict) or set(providers) != {"rpc-a", "rpc-b"}:
        raise ControlError("fault topology requires exactly rpc-a and rpc-b", 503, "CONFIGURATION_ERROR")
    rpc_a = normalized_url(providers["rpc-a"], allowed_hosts, "rpc-a")
    rpc_b = normalized_url(providers["rpc-b"], allowed_hosts, "rpc-b")
    if rpc_a == rpc_b:
        raise ControlError("rpc-a and rpc-b must be distinct", 503, "CONFIGURATION_ERROR")
    docker = value["docker"]
    if not isinstance(docker, dict):
        raise ControlError("fault topology docker must be an object", 503, "CONFIGURATION_ERROR")
    exact_keys(
        docker,
        {"socket", "containerPrefix", "restartTargets", "partitionNetwork", "partitionTargets"},
        "fault topology docker",
    )
    docker_socket = docker["socket"]
    if docker_socket not in {None, "/var/run/docker.sock"}:
        raise ControlError("only the fixed Docker socket is supported", 503, "CONFIGURATION_ERROR")
    prefix = docker["containerPrefix"]
    if not isinstance(prefix, str) or not CONTAINER.fullmatch(prefix):
        raise ControlError("docker containerPrefix is invalid", 503, "CONFIGURATION_ERROR")
    restart = docker["restartTargets"]
    if not isinstance(restart, dict) or not set(restart).issubset(ALLOWED_RESTART_TARGETS):
        raise ControlError("restartTargets contains an unreviewed logical target", 503, "CONFIGURATION_ERROR")
    restart_targets: dict[str, str] = {}
    for alias, container in restart.items():
        if not isinstance(container, str) or not CONTAINER.fullmatch(container) or not container.startswith(prefix):
            raise ControlError(f"restart target {alias} escapes the candidate prefix", 503, "CONFIGURATION_ERROR")
        restart_targets[alias] = container
    partition_network = docker["partitionNetwork"]
    partition_aliases = docker["partitionTargets"]
    if partition_network is not None and (
        not isinstance(partition_network, str) or not CONTAINER.fullmatch(partition_network)
        or not partition_network.startswith(prefix)
    ):
        raise ControlError("partitionNetwork escapes the candidate prefix", 503, "CONFIGURATION_ERROR")
    if (
        not isinstance(partition_aliases, list)
        or len(partition_aliases) > 8
        or len(set(partition_aliases)) != len(partition_aliases)
        or any(alias not in restart_targets for alias in partition_aliases)
    ):
        raise ControlError("partitionTargets must reference fixed restart targets", 503, "CONFIGURATION_ERROR")
    if bool(partition_aliases) != bool(partition_network):
        raise ControlError("partition network and targets must be configured together", 503, "CONFIGURATION_ERROR")
    raw_profiles = value["loadProfiles"]
    if not isinstance(raw_profiles, dict) or not set(raw_profiles).issubset(ALLOWED_LOAD_PROFILES):
        raise ControlError("loadProfiles contains an unreviewed profile", 503, "CONFIGURATION_ERROR")
    if value["classification"] == "release-candidate" and set(raw_profiles) != ALLOWED_LOAD_PROFILES:
        raise ControlError(
            "release-candidate loadProfiles must configure all six reviewed profiles",
            503,
            "CONFIGURATION_ERROR",
        )
    profiles: dict[str, tuple[LoadEndpoint, ...]] = {}
    for name, raw_profile in raw_profiles.items():
        if not isinstance(raw_profile, dict):
            raise ControlError(f"load profile {name} must be an object", 503, "CONFIGURATION_ERROR")
        exact_keys(raw_profile, {"endpoints"}, f"load profile {name}")
        endpoints = raw_profile["endpoints"]
        if not isinstance(endpoints, list) or not 1 <= len(endpoints) <= 2:
            raise ControlError(f"load profile {name} requires one or two endpoints", 503, "CONFIGURATION_ERROR")
        parsed_endpoints: list[LoadEndpoint] = []
        for index, endpoint in enumerate(endpoints):
            if not isinstance(endpoint, dict):
                raise ControlError("load endpoint must be an object", 503, "CONFIGURATION_ERROR")
            exact_keys(endpoint, {"baseUrl", "path", "method", "body", "tokenFile", "tokenSha256"}, "load endpoint")
            method = endpoint["method"]
            body = endpoint["body"]
            if method not in ALLOWED_LOAD_METHODS or (method == "GET" and body is not None) or (
                body is not None and not isinstance(body, dict)
            ):
                raise ControlError(f"load endpoint {name}[{index}] method/body is invalid", 503, "CONFIGURATION_ERROR")
            if body is not None and len(canonical_bytes(body)) > MAX_REQUEST_BYTES:
                raise ControlError("fixed load body is too large", 503, "CONFIGURATION_ERROR")
            token_file = endpoint["tokenFile"]
            token_hash = endpoint["tokenSha256"]
            token_value = None
            if bool(token_file) != bool(token_hash):
                raise ControlError("load endpoint token file/hash must be paired", 503, "CONFIGURATION_ERROR")
            if token_file:
                if not isinstance(token_file, str) or not isinstance(token_hash, str):
                    raise ControlError("load endpoint token binding is malformed", 503, "CONFIGURATION_ERROR")
                token_value = read_secret(
                    token_file,
                    token_hash,
                    f"load profile {name} token",
                    require_ephemeral=True,
                )
            parsed_endpoints.append(LoadEndpoint(
                normalized_url(endpoint["baseUrl"], allowed_hosts, f"load profile {name} baseUrl"),
                safe_path(endpoint["path"], f"load profile {name} path"),
                method,
                body,
                token_value,
            ))
        profiles[name] = tuple(parsed_endpoints)
    timeout = bounded_int(value["requestTimeoutSeconds"], "requestTimeoutSeconds", 1, 30)
    if value["classification"] == "release-candidate":
        required_restart_targets = {
            "coordinator-active", "headless-node", "prover", "indexer",
            "minio", "postgres-primary", "front-door",
        }
        if docker_socket is None or not required_restart_targets.issubset(restart_targets):
            raise ControlError(
                "release-candidate topology does not enable every reviewed container fault",
                503,
                "CONFIGURATION_ERROR",
            )
        if not partition_network or not partition_aliases:
            raise ControlError(
                "release-candidate topology does not enable the reviewed network partition",
                503,
                "CONFIGURATION_ERROR",
            )
    return Topology(
        value["classification"], candidate_id, plan_sha256, hosted_token, auth_hash,
        frozenset(allowed_hosts), rpc_a, rpc_b, docker_socket, restart_targets,
        partition_network, tuple(partition_aliases),
        profiles, timeout, observed,
    )


class Journal:
    """Write-once intent and closure journal.

    A crash after the intent is durable but before closure fails closed instead
    of repeating an external fault.  Operators must inspect the target and use
    a new reviewed idempotency key; the incomplete record remains evidence.
    """

    def __init__(self, root: Path, binding_digest: str):
        if root.is_symlink() or not root.is_dir():
            raise ControlError("FAULT_CONTROL_JOURNAL_DIR is absent or unsafe", 503, "CONFIGURATION_ERROR")
        probe = root / ".write-probe"
        try:
            descriptor = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            probe.unlink()
        except OSError as exc:
            raise ControlError("fault journal is not writable", 503, "CONFIGURATION_ERROR") from exc
        self.root = root
        self.binding_digest = binding_digest
        self.lock = threading.Lock()

    def paths(self, idempotency_key: str, operation_id: str) -> tuple[Path, Path]:
        key_hash = sha256_bytes(idempotency_key.encode())
        return self.root / f"{key_hash}.intent.json", self.root / f"{operation_id}.closure.json"

    def execute(
        self,
        action: str,
        body: dict[str, Any],
        idempotency_key: str,
        correlation_id: str,
        callback: Callable[[str], tuple[dict[str, Any], dict[str, Any]]],
    ) -> tuple[dict[str, Any], bool]:
        if not IDEMPOTENCY.fullmatch(idempotency_key):
            raise ControlError("Idempotency-Key is malformed")
        request_digest = sha256_bytes(canonical_bytes({
            "action": action,
            "bindingDigest": self.binding_digest,
            "body": body,
            "correlationId": correlation_id,
        }))
        operation_id = f"fc-{request_digest[:40]}"
        intent_path, closure_path = self.paths(idempotency_key, operation_id)
        with self.lock:
            if intent_path.exists():
                intent = read_record(intent_path)
                if intent.get("requestDigest") != request_digest or intent.get("operationId") != operation_id:
                    raise ControlError("idempotency key was already used with changed input", 409, "IDEMPOTENCY_CONFLICT")
                if not closure_path.exists():
                    raise ControlError("prior operation stopped after durable intent", 409, "INCOMPLETE_OPERATION")
                closure = read_record(closure_path)
                replay = dict(closure["publicResult"])
                replay.update({
                    "operationId": operation_id,
                    "closureSha256": sha256_bytes(canonical_bytes(closure)),
                    "correlationId": closure["correlationId"],
                })
                return replay, True
            intent = {
                "schemaVersion": 1,
                "service": "zkdeal-fault-control",
                "operationId": operation_id,
                "action": action,
                "bindingDigest": self.binding_digest,
                "idempotencyKeySha256": sha256_bytes(idempotency_key.encode()),
                "requestDigest": request_digest,
                "correlationId": correlation_id,
                "startedAt": utc_now(),
            }
            durable_create(intent_path, intent)
            public_result, private_result = callback(operation_id)
            closure = {
                "schemaVersion": 1,
                "service": "zkdeal-fault-control",
                "operationId": operation_id,
                "action": action,
                "bindingDigest": self.binding_digest,
                "requestDigest": request_digest,
                "correlationId": correlation_id,
                "intentSha256": sha256_bytes(intent_path.read_bytes()),
                "completedAt": utc_now(),
                "publicResult": public_result,
                "privateResult": private_result,
            }
            closure_digest = durable_create(closure_path, closure)
            response = dict(public_result)
            response.update({
                "operationId": operation_id,
                "closureSha256": closure_digest,
                "correlationId": correlation_id,
            })
            return response, False

    def closure(self, operation_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"fc-[0-9a-f]{40}", operation_id):
            raise ControlError("operationId is malformed")
        closure = read_record(self.root / f"{operation_id}.closure.json")
        result = dict(closure["publicResult"])
        result.update({
            "operationId": operation_id,
            "closureSha256": sha256_bytes(canonical_bytes(closure)),
            "correlationId": closure["correlationId"],
            "action": closure["action"],
        })
        return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def http_json(
    base_url: str,
    path: str,
    method: str,
    body: dict[str, Any] | None,
    timeout: int,
    token: str | None = None,
) -> tuple[int, Any, bytes, float]:
    payload = b"" if body is None else canonical_bytes(body)
    headers = {"accept": "application/json"}
    if body is not None:
        headers["content-type"] = "application/json"
    if token:
        headers["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(base_url + path, data=payload if body is not None else None, headers=headers, method=method)
    opener = urllib.request.build_opener(NoRedirect())
    started = time.monotonic()
    try:
        response = opener.open(request, timeout=timeout)
        status = int(response.status)
        raw = response.read(MAX_TARGET_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read(MAX_TARGET_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise ControlError(f"fixed target request failed: {exc}", 502, "TARGET_UNAVAILABLE") from exc
    elapsed = (time.monotonic() - started) * 1000
    if len(raw) > MAX_TARGET_RESPONSE_BYTES:
        raise ControlError("fixed target response exceeded 4 MiB", 502, "TARGET_RESPONSE_TOO_LARGE")
    parsed: Any = None
    if raw:
        parsed = strict_object(raw, "fixed target response", MAX_TARGET_RESPONSE_BYTES)
    return status, parsed, raw, elapsed


def rpc_json(base_url: str, method: str, params: list[Any], timeout: int, request_id: int) -> Any:
    status, response, _raw, _elapsed = http_json(base_url, "/", "POST", {
        "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
    }, timeout)
    if status != 200 or not isinstance(response, dict) or response.get("error") is not None or "result" not in response:
        raise ControlError(f"fixed RPC method {method} failed", 502, "RPC_CONTROL_FAILED")
    return response["result"]


class UnixDockerConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: int):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        connection.connect(self.socket_path)
        self.sock = connection


# Docker API version prefix.
#
# This was `/v1.43/` for every call, and every call against a modern daemon
# failed before it began:
#
#     {"message":"client version 1.43 is too old.
#       Minimum supported API version is 1.44, please upgrade your client"}
#
# The daemon answers that with HTTP 400, the controller maps it to
# CONTAINER_ACTION_FAILED, and the operator sees a fault drill that "failed"
# for no visible reason.  Engine 29.1.4 - the version on the 5090 acceptance
# node - raised MinAPIVersion to 1.44, which silently retired every container
# and network action this controller has.
#
# Unversioned paths are served at the daemon's own current version and cannot
# go stale the same way.  The five container verbs and the two network endpoints
# used here have been stable across every version in that range, so pinning
# buys nothing and costs exactly this.
DOCKER_API_PREFIX = ""


def docker_restart(socket_path: str, container: str, timeout: int) -> None:
    connection = UnixDockerConnection(socket_path, timeout)
    try:
        encoded = urllib.parse.quote(container, safe="")
        # Same grace-versus-client-timeout constraint as docker_container_action.
        connection.request(
            "POST", f"{DOCKER_API_PREFIX}/containers/{encoded}/restart?t={stop_grace_seconds(timeout)}",
            body=b"", headers={"Content-Length": "0"},
        )
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if response.status != 204 or len(raw) > MAX_RESPONSE_BYTES:
            raise ControlError(f"fixed Docker restart returned HTTP {response.status}", 502, "RESTART_FAILED")
    except OSError as exc:
        raise ControlError(f"fixed Docker restart failed: {exc}", 502, "RESTART_FAILED") from exc
    finally:
        connection.close()


# The only Docker container verbs this controller ever issues.  Every request
# maps to one of these narrow lifecycle actions on an allowlisted container; the
# raw Docker socket is never otherwise exposed.
#
# `True` marks the actions that carry a stop grace period.  It is NOT hardcoded:
# `requestTimeoutSeconds` is bounded at 30 and is 10 in the acceptance rig, so a
# fixed `?t=30` guaranteed that Docker would still be waiting out the grace when
# the client socket gave up.  Every drill against a service that does not exit
# promptly then reported CONTAINER_ACTION_FAILED while the restart it asked for
# was in fact proceeding normally.  That is exactly what the prover-restart
# drill did the first time it was ever executed, against a CUDA process.
DOCKER_CONTAINER_ACTIONS = {
    "restart": ({204}, True),
    "stop": ({204, 304}, True),
    "start": ({204, 304}, False),
    "pause": ({204}, False),
    "unpause": ({204}, False),
}


def stop_grace_seconds(timeout: int) -> int:
    """Leave room for the kill and the restart inside the client's own budget.

    A fault drill wants the service gone, not shut down politely, so erring
    towards a shorter grace is also the more faithful fault.
    """
    return max(1, min(timeout - 5, timeout // 2))


def docker_container_action(socket_path: str, container: str, action: str, timeout: int) -> None:
    spec = DOCKER_CONTAINER_ACTIONS.get(action)
    if spec is None:
        raise ControlError("unreviewed Docker container action", 500, "INTERNAL_ERROR")
    accepted, has_grace = spec
    query = f"?t={stop_grace_seconds(timeout)}" if has_grace else ""
    connection = UnixDockerConnection(socket_path, timeout)
    try:
        encoded = urllib.parse.quote(container, safe="")
        connection.request(
            "POST", f"{DOCKER_API_PREFIX}/containers/{encoded}/{action}{query}",
            body=b"", headers={"Content-Length": "0"},
        )
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if response.status not in accepted or len(raw) > MAX_RESPONSE_BYTES:
            raise ControlError(
                f"fixed Docker {action} returned HTTP {response.status}", 502, "CONTAINER_ACTION_FAILED",
            )
    except OSError as exc:
        raise ControlError(f"fixed Docker {action} failed: {exc}", 502, "CONTAINER_ACTION_FAILED") from exc
    finally:
        connection.close()


def docker_network_action(
    socket_path: str,
    network: str,
    containers: list[str],
    phase: str,
    timeout: int,
) -> None:
    if phase not in {"start", "restore"}:
        raise ControlError("network partition phase is invalid")
    endpoint = "disconnect" if phase == "start" else "connect"
    connection = UnixDockerConnection(socket_path, timeout)
    try:
        encoded_network = urllib.parse.quote(network, safe="")
        for container in containers:
            body = canonical_bytes({"Container": container, "Force": False})
            connection.request(
                "POST", f"{DOCKER_API_PREFIX}/networks/{encoded_network}/{endpoint}", body=body,
                headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
            )
            response = connection.getresponse()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if response.status not in {200, 201} or len(raw) > MAX_RESPONSE_BYTES:
                raise ControlError(
                    f"fixed network {endpoint} returned HTTP {response.status}",
                    502, "NETWORK_ACTION_FAILED",
                )
    except OSError as exc:
        raise ControlError(f"fixed network {endpoint} failed: {exc}", 502, "NETWORK_ACTION_FAILED") from exc
    finally:
        connection.close()


class Controller:
    def __init__(
        self,
        topology: Topology,
        scoped_token: str,
        journal: Journal,
        receipt_binding: dict[str, Any],
    ):
        self.topology = topology
        self.scoped_token = scoped_token
        self.journal = journal
        self.receipt_binding = receipt_binding

    def authenticate(self, authorization: str | None) -> None:
        supplied = ""
        if authorization and authorization.startswith("Bearer "):
            supplied = authorization[7:]
        expected = hashlib.sha256(self.scoped_token.encode()).digest()
        observed = hashlib.sha256(supplied.encode()).digest()
        if not supplied or not __import__("hmac").compare_digest(expected, observed):
            raise ControlError("fault-control scoped bearer token required", 401, "UNAUTHORIZED")

    def validate_binding(self, body: dict[str, Any]) -> None:
        binding = body.get("binding")
        if not isinstance(binding, dict) or binding != self.topology.binding:
            raise ControlError("request binding differs from the exact candidate plan", 409, "CANDIDATE_BINDING_MISMATCH")

    def capabilities(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "service": "zkdeal-fault-control",
            "classification": self.topology.classification,
            "candidateId": self.topology.candidate_id,
            "planSha256": self.topology.plan_sha256,
            "hostedIntegrationToken": self.topology.hosted_token,
            "topologySha256": self.topology.topology_sha256,
            "scopedBearer": "fault_control",
            "candidateDescriptorSha256": self.receipt_binding["candidateDescriptorSha256"],
            "topologyReceiptSha256": self.receipt_binding["topologyReceiptSha256"],
            "platform": self.receipt_binding["platform"],
            "adapterImage": self.receipt_binding["adapterImage"],
            "adapterSourceSha256": self.receipt_binding["adapterSourceSha256"],
            "faultActions": [
                "l1-reorg", "rpc-disagreement", "rpc-provider-control", "coordinator-terminate",
                "service-pause", "headless-restart", "prover-restart", "indexer-rollback",
                "object-store-restart", "database-restart", "network-partition", "sse-disconnect",
            ],
            "loadProfiles": ["rpc", "sse", "indexer", "admission", "scheduler", "projection"],
            "restartTargets": sorted(self.topology.restart_targets),
            "requestControlledUrls": False,
            "requestControlledCommands": False,
            "requestControlledTargetBodies": False,
            "durableWriteOnceJournal": True,
        }

    def receipt(self, result: dict[str, Any]) -> dict[str, Any]:
        return {**result, **self.receipt_binding}

    def execute(
        self,
        route: str,
        body: dict[str, Any],
        idempotency_key: str,
        correlation_id: str,
    ) -> tuple[dict[str, Any], bool]:
        if not CORRELATION.fullmatch(correlation_id):
            raise ControlError("X-Correlation-Id is malformed")
        self.validate_binding(body)
        if route == "fault":
            exact_keys(body, {"schemaVersion", "binding", "action", "parameters"}, "fault request")
            if body["schemaVersion"] != 1 or body["action"] not in self.capabilities()["faultActions"]:
                raise ControlError("fault action is not in the reviewed enum")
            if not isinstance(body["parameters"], dict):
                raise ControlError("fault parameters must be an object")
            callback = self._fault_callback(body["action"], body["parameters"])
            result, replay = self.journal.execute(
                f"fault:{body['action']}", body, idempotency_key, correlation_id, callback,
            )
            return self.receipt(result), replay
        if route == "load":
            exact_keys(body, {"schemaVersion", "binding", "profile", "requests", "concurrency", "durationSeconds"}, "load request")
            profile = body["profile"]
            if body["schemaVersion"] != 1 or profile not in self.capabilities()["loadProfiles"] or profile not in self.topology.load_profiles:
                raise ControlError("load profile is not in the candidate allowlist")
            requests = bounded_int(body["requests"], "requests", 1, 10_000)
            concurrency = bounded_int(body["concurrency"], "concurrency", 1, 64)
            duration = bounded_int(body["durationSeconds"], "durationSeconds", 1, 900)
            result, replay = self.journal.execute(
                "load", body, idempotency_key, correlation_id,
                lambda operation_id: self._load(profile, requests, concurrency, duration, operation_id),
            )
            result["profile"] = body["profile"]
            return self.receipt(result), replay
        raise ControlError("unknown control route", 404, "NOT_FOUND")

    def _fault_callback(
        self,
        action: str,
        parameters: dict[str, Any],
    ) -> Callable[[str], tuple[dict[str, Any], dict[str, Any]]]:
        if action == "l1-reorg":
            exact_keys(parameters, {"phase", "depth", "preparedOperationId"}, "l1-reorg parameters")
            if parameters["phase"] not in {"prepare", "replace"}:
                raise ControlError("l1-reorg phase is invalid")
            depth = bounded_int(parameters["depth"], "reorg depth", 1, 64)
            prepared = parameters["preparedOperationId"]
            if (parameters["phase"] == "prepare" and prepared is not None) or (
                parameters["phase"] == "replace" and not isinstance(prepared, str)
            ):
                raise ControlError("preparedOperationId does not match the l1-reorg phase")
            return lambda operation_id: self._reorg(parameters["phase"], depth, prepared, operation_id)
        if action == "rpc-disagreement":
            exact_keys(parameters, {"phase", "preparedOperationId"}, "rpc-disagreement parameters")
            if parameters["phase"] not in {"start", "restore"}:
                raise ControlError("rpc-disagreement phase is invalid")
            prepared = parameters["preparedOperationId"]
            if (parameters["phase"] == "start" and prepared is not None) or (
                parameters["phase"] == "restore" and not isinstance(prepared, str)
            ):
                raise ControlError("preparedOperationId does not match the disagreement phase")
            return lambda operation_id: self._rpc_disagreement(parameters["phase"], prepared, operation_id)
        if action == "rpc-provider-control":
            exact_keys(parameters, {"provider", "phase"}, "rpc-provider-control parameters")
            provider = parameters["provider"]
            phase = parameters["phase"]
            if provider not in {"rpc-a", "rpc-b"}:
                raise ControlError("rpc-provider-control provider must be rpc-a or rpc-b")
            if phase not in {"stop", "start"}:
                raise ControlError("rpc-provider-control phase must be stop or start")
            return lambda operation_id: self._container(provider, phase, "rpc-provider-control", operation_id)
        if action == "service-pause":
            exact_keys(parameters, {"target", "phase"}, "service-pause parameters")
            target = parameters["target"]
            phase = parameters["phase"]
            if target not in ALLOWED_PAUSE_TARGETS:
                raise ControlError("service-pause target is not a reviewed pausable service")
            if phase not in {"pause", "unpause"}:
                raise ControlError("service-pause phase must be pause or unpause")
            return lambda operation_id: self._container(target, phase, "service-pause", operation_id)
        exact_keys(parameters, set() if action not in {"indexer-rollback", "network-partition"} else (
            {"preparedOperationId"} if action == "indexer-rollback" else {"phase"}
        ), f"{action} parameters")
        if action == "coordinator-terminate":
            return lambda operation_id: self._container("coordinator-active", "stop", action, operation_id)
        restart_map = {
            "headless-restart": "headless-node",
            "prover-restart": "prover",
            "object-store-restart": "minio",
            "database-restart": "postgres-primary",
            "sse-disconnect": "front-door",
        }
        if action in restart_map:
            return lambda operation_id: self._container(restart_map[action], "restart", action, operation_id)
        if action == "indexer-rollback":
            prepared = parameters["preparedOperationId"]
            return lambda operation_id: self._indexer_rollback(prepared, operation_id)
        if action == "network-partition":
            phase = parameters["phase"]
            if phase not in {"start", "restore"}:
                raise ControlError("network-partition phase is invalid")
            return lambda operation_id: self._network_partition(phase, operation_id)
        raise ControlError("fault action is not implemented", 503, "CONTROL_DISABLED")

    def _block(self, base_url: str, request_id: int) -> dict[str, Any]:
        block = rpc_json(base_url, "eth_getBlockByNumber", ["latest", False], self.topology.request_timeout_seconds, request_id)
        if (
            not isinstance(block, dict)
            or not isinstance(block.get("hash"), str)
            or not re.fullmatch(r"0x[0-9a-fA-F]{64}", block["hash"])
            or not isinstance(block.get("timestamp"), str)
            or not re.fullmatch(r"0x[0-9a-fA-F]{1,16}", block["timestamp"])
        ):
            raise ControlError("fixed RPC omitted the latest block hash or timestamp", 502, "RPC_CONTROL_FAILED")
        return block

    def _block_hash(self, base_url: str, request_id: int) -> str:
        return self._block(base_url, request_id)["hash"].lower()

    def _mine(
        self,
        base_url: str,
        blocks: int,
        base_fee: int,
        request_id: int,
        start_timestamp: int | None = None,
    ) -> None:
        rpc_json(base_url, "anvil_setNextBlockBaseFeePerGas", [hex(base_fee)], self.topology.request_timeout_seconds, request_id)
        for offset in range(blocks):
            if start_timestamp is not None:
                # Pinned per-block timestamps keep independently mined branches
                # byte-identical across both providers.
                rpc_json(
                    base_url, "evm_setNextBlockTimestamp", [start_timestamp + offset],
                    self.topology.request_timeout_seconds, request_id + 500 + offset,
                )
            rpc_json(base_url, "evm_mine", [], self.topology.request_timeout_seconds, request_id + offset + 1)

    def _prior_closure(self, operation_id: str, expected_action: str) -> dict[str, Any]:
        if not isinstance(operation_id, str) or not re.fullmatch(r"fc-[0-9a-f]{40}", operation_id):
            raise ControlError("preparedOperationId is malformed")
        closure = read_record(self.journal.root / f"{operation_id}.closure.json")
        if closure.get("action") != expected_action or closure.get("bindingDigest") != self.journal.binding_digest:
            raise ControlError("prepared operation belongs to another action or candidate", 409, "CANDIDATE_BINDING_MISMATCH")
        return closure

    def _reorg(self, phase: str, depth: int, prepared: str | None, operation_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Symmetric two-provider reorg.

        Both independent providers snapshot together, mine the identical
        to-be-replaced branch, and later revert and mine the identical
        replacement branch with one shared base fee and pinned timestamps, so
        rpc-a and rpc-b converge on the same replacement canonical hash and the
        acceptance runner's dual-provider agreement check is honestly closable.
        """
        if phase == "prepare":
            snapshot_a = rpc_json(self.topology.rpc_a, "evm_snapshot", [], self.topology.request_timeout_seconds, 10)
            snapshot_b = rpc_json(self.topology.rpc_b, "evm_snapshot", [], self.topology.request_timeout_seconds, 11)
            for label, snapshot in (("rpc-a", snapshot_a), ("rpc-b", snapshot_b)):
                if not isinstance(snapshot, str) or len(snapshot) > 128:
                    raise ControlError(f"fixed RPC {label} returned an invalid snapshot id", 502, "RPC_CONTROL_FAILED")
            before_a = self._block(self.topology.rpc_a, 12)
            before_b = self._block(self.topology.rpc_b, 13)
            if before_a["hash"].lower() != before_b["hash"].lower():
                raise ControlError("RPC providers did not agree before the reorg branch", 409, "PRECONDITION_FAILED")
            branch_fee = 1000 + int(operation_id[-6:], 16)
            branch_timestamp = int(before_a["timestamp"], 16) + 1
            self._mine(self.topology.rpc_a, depth, branch_fee, 20, branch_timestamp)
            self._mine(self.topology.rpc_b, depth, branch_fee, 60, branch_timestamp)
            previous = self._block_hash(self.topology.rpc_a, 100)
            if previous != self._block_hash(self.topology.rpc_b, 101):
                raise ControlError("RPC providers diverged while mining the reorg branch", 502, "RPC_CONTROL_FAILED")
            return {
                "phase": "PREPARED", "depth": depth, "previousBlockHash": previous,
                "candidateId": self.topology.candidate_id,
            }, {
                "snapshotA": snapshot_a, "snapshotB": snapshot_b,
                "previousBlockHash": previous, "depth": depth,
            }
        closure = self._prior_closure(str(prepared), "fault:l1-reorg")
        private = closure.get("privateResult")
        if not isinstance(private, dict) or private.get("depth") != depth:
            raise ControlError("prepared reorg depth differs", 409, "IDEMPOTENCY_CONFLICT")
        for label, base, snapshot, request_id in (
            ("rpc-a", self.topology.rpc_a, private.get("snapshotA"), 110),
            ("rpc-b", self.topology.rpc_b, private.get("snapshotB"), 111),
        ):
            if rpc_json(base, "evm_revert", [snapshot], self.topology.request_timeout_seconds, request_id) is not True:
                raise ControlError(f"fixed RPC {label} did not revert the prepared branch", 502, "RPC_CONTROL_FAILED")
        replacement_fee = 2_000_000 + int(operation_id[-6:], 16)
        replacement_timestamp = int(self._block(self.topology.rpc_a, 112)["timestamp"], 16) + 1
        self._mine(self.topology.rpc_a, depth, replacement_fee, 120, replacement_timestamp)
        self._mine(self.topology.rpc_b, depth, replacement_fee, 160, replacement_timestamp)
        canonical = self._block_hash(self.topology.rpc_a, 200)
        previous = str(private["previousBlockHash"])
        if canonical != self._block_hash(self.topology.rpc_b, 201):
            raise ControlError("RPC providers did not converge on the replacement branch", 502, "RPC_CONTROL_FAILED")
        if canonical == previous:
            raise ControlError("reorg replacement did not change the canonical hash", 502, "RPC_CONTROL_FAILED")
        return {
            "phase": "REPLACED", "rollbackDepth": depth,
            "previousBlockHash": previous, "canonicalBlockHash": canonical,
            "candidateId": self.topology.candidate_id,
        }, {}

    def _rpc_disagreement(self, phase: str, prepared: str | None, operation_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if phase == "start":
            snapshot_a = rpc_json(self.topology.rpc_a, "evm_snapshot", [], self.topology.request_timeout_seconds, 300)
            snapshot_b = rpc_json(self.topology.rpc_b, "evm_snapshot", [], self.topology.request_timeout_seconds, 301)
            before_a = self._block_hash(self.topology.rpc_a, 302)
            before_b = self._block_hash(self.topology.rpc_b, 303)
            if before_a != before_b:
                raise ControlError("RPC providers did not agree before fault injection", 409, "PRECONDITION_FAILED")
            self._mine(self.topology.rpc_a, 1, 3000 + int(operation_id[-6:], 16), 310)
            self._mine(self.topology.rpc_b, 1, 9000 + int(operation_id[-6:], 16), 320)
            disagree_a = self._block_hash(self.topology.rpc_a, 330)
            disagree_b = self._block_hash(self.topology.rpc_b, 331)
            if disagree_a == disagree_b:
                raise ControlError("RPC fault failed to create disagreement", 502, "RPC_CONTROL_FAILED")
            return {
                "phase": "DISAGREEING", "agreedBeforeA": before_a, "agreedBeforeB": before_b,
                "disagreeA": disagree_a, "disagreeB": disagree_b,
                "candidateId": self.topology.candidate_id,
            }, {"snapshotA": snapshot_a, "snapshotB": snapshot_b}
        closure = self._prior_closure(str(prepared), "fault:rpc-disagreement")
        private = closure.get("privateResult")
        if not isinstance(private, dict):
            raise ControlError("prepared RPC fault is incomplete", 409, "INCOMPLETE_OPERATION")
        for base, snapshot, request_id in (
            (self.topology.rpc_a, private.get("snapshotA"), 340),
            (self.topology.rpc_b, private.get("snapshotB"), 341),
        ):
            if rpc_json(base, "evm_revert", [snapshot], self.topology.request_timeout_seconds, request_id) is not True:
                raise ControlError("RPC provider did not restore its snapshot", 502, "RPC_CONTROL_FAILED")
        shared_fee = 15_000 + int(operation_id[-6:], 16)
        self._mine(self.topology.rpc_a, 1, shared_fee, 350)
        self._mine(self.topology.rpc_b, 1, shared_fee, 360)
        restored_a = self._block_hash(self.topology.rpc_a, 370)
        restored_b = self._block_hash(self.topology.rpc_b, 371)
        if restored_a != restored_b:
            raise ControlError("RPC providers did not converge after restore", 502, "RPC_CONTROL_FAILED")
        return {
            "phase": "RESTORED", "restoredA": restored_a, "restoredB": restored_b,
            "candidateId": self.topology.candidate_id,
        }, {}

    def _restart(self, target: str, operation_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.topology.docker_socket:
            raise ControlError("Docker restart control is disabled", 503, "CONTROL_DISABLED")
        docker_restart(self.topology.docker_socket, self.topology.restart_targets[target], self.topology.request_timeout_seconds)
        return {
            "restarted": True, "target": target, "candidateId": self.topology.candidate_id,
            "restartReceipt": sha256_bytes(canonical_bytes({"operationId": operation_id, "target": target})),
        }, {}

    def _container(
        self,
        target: str,
        docker_action: str,
        fault_action: str,
        operation_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.topology.docker_socket or target not in self.topology.restart_targets:
            raise ControlError(f"{fault_action} is disabled by the fixed topology", 503, "CONTROL_DISABLED")
        docker_container_action(
            self.topology.docker_socket,
            self.topology.restart_targets[target],
            docker_action,
            self.topology.request_timeout_seconds,
        )
        return {
            "action": fault_action,
            "applied": True,
            "logicalTarget": target,
            "dockerAction": docker_action,
            "containerId": self.topology.restart_targets[target],
            "candidateId": self.topology.candidate_id,
            "platformReceipt": sha256_bytes(canonical_bytes({
                "operationId": operation_id,
                "action": fault_action,
                "dockerAction": docker_action,
                "logicalTarget": target,
                "containerId": self.topology.restart_targets[target],
            })),
        }, {}

    def _indexer_rollback(self, prepared: Any, operation_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        closure = self._prior_closure(str(prepared), "fault:l1-reorg")
        public = closure.get("publicResult")
        if not isinstance(public, dict) or public.get("phase") != "REPLACED":
            raise ControlError("indexer rollback requires a completed replacement reorg", 409, "PRECONDITION_FAILED")
        result, private = self._container("indexer", "restart", "indexer-rollback", operation_id)
        result.update({"rollbackApplied": True, "rollbackDepth": public.get("rollbackDepth")})
        return result, private

    def _network_partition(self, phase: str, operation_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.topology.docker_socket or not self.topology.partition_network or not self.topology.partition_targets:
            raise ControlError("network partition is disabled by the fixed topology", 503, "CONTROL_DISABLED")
        containers = [self.topology.restart_targets[alias] for alias in self.topology.partition_targets]
        docker_network_action(
            self.topology.docker_socket,
            self.topology.partition_network,
            containers,
            phase,
            self.topology.request_timeout_seconds,
        )
        return {
            "action": "network-partition",
            "phase": "PARTITIONED" if phase == "start" else "RESTORED",
            "logicalTargets": list(self.topology.partition_targets),
            "candidateId": self.topology.candidate_id,
            "platformReceipt": sha256_bytes(canonical_bytes({
                "operationId": operation_id,
                "phase": phase,
                "logicalTargets": self.topology.partition_targets,
            })),
        }, {}

    def _load(
        self,
        profile: str,
        request_limit: int,
        concurrency: int,
        duration_seconds: int,
        _operation_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        endpoints = self.topology.load_profiles[profile]
        started = time.monotonic()
        latencies: list[float] = []
        status_counts: dict[str, int] = {}
        mismatches = 0
        completed = 0
        lock = threading.Lock()

        def one(_sequence: int) -> None:
            nonlocal mismatches, completed
            observations: list[tuple[int, str]] = []
            local_latencies: list[float] = []
            for endpoint in endpoints:
                status, _parsed, raw, elapsed = http_json(
                    endpoint.base_url, endpoint.path, endpoint.method, endpoint.body,
                    self.topology.request_timeout_seconds, endpoint.token,
                )
                observations.append((status, sha256_bytes(raw)))
                local_latencies.append(elapsed)
            with lock:
                completed += 1
                latencies.extend(local_latencies)
                for status, _digest in observations:
                    key = str(status)
                    status_counts[key] = status_counts.get(key, 0) + 1
                if len(set(observations)) > 1:
                    mismatches += 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures: set[concurrent.futures.Future[None]] = set()
            sequence = 0
            while sequence < request_limit and time.monotonic() - started < duration_seconds:
                while len(futures) < concurrency and sequence < request_limit:
                    futures.add(pool.submit(one, sequence))
                    sequence += 1
                done, futures = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    future.result()
            for future in concurrent.futures.as_completed(futures):
                future.result()
        elapsed_seconds = max(0.001, time.monotonic() - started)
        ordered = sorted(latencies)
        p99_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.99))) if ordered else 0
        p99 = round(ordered[p99_index], 3) if ordered else 0.0
        result = {
            "profile": profile,
            "requests": completed,
            "durationSeconds": round(elapsed_seconds, 3),
            "configuredDurationSeconds": duration_seconds,
            "concurrency": concurrency,
            "p99LatencyMs": p99,
            "mismatches": mismatches,
            "statusCounts": dict(sorted(status_counts.items())),
            "backpressureObserved": any(int(status) in {429, 503} for status in status_counts),
            "candidateId": self.topology.candidate_id,
        }
        return result, {}


class Handler(BaseHTTPRequestHandler):
    server_version = "zkdeal-fault-control/1"
    controller: Controller

    def log_message(self, format_string: str, *args: Any) -> None:
        print(json.dumps({
            "component": "fault-control-http",
            "event": "access",
            "message": format_string % args,
        }, sort_keys=True), flush=True)

    def send_json(self, status: int, value: dict[str, Any], replay: bool = False) -> None:
        payload = canonical_bytes(value)
        if len(payload) > MAX_RESPONSE_BYTES:
            status = 500
            payload = canonical_bytes({"error": {"code": "RESPONSE_TOO_LARGE", "message": "response exceeded 1 MiB"}})
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        if replay:
            self.send_header("idempotent-replay", "true")
        self.end_headers()
        self.wfile.write(payload)

    def handle_error(self, error: Exception) -> None:
        if isinstance(error, ControlError):
            self.send_json(error.status, {"error": {"code": error.code, "message": str(error)}})
        else:
            self.send_json(500, {"error": {"code": "INTERNAL_ERROR", "message": "control operation failed"}})

    def body(self) -> dict[str, Any]:
        if self.headers.get("transfer-encoding"):
            raise ControlError("chunked request bodies are forbidden", 411, "CONTENT_LENGTH_REQUIRED")
        try:
            length = int(self.headers.get("content-length", "-1"))
        except ValueError as exc:
            raise ControlError("Content-Length is malformed", 411, "CONTENT_LENGTH_REQUIRED") from exc
        if not 0 <= length <= MAX_REQUEST_BYTES:
            raise ControlError("Content-Length is absent or too large", 413, "DOCUMENT_TOO_LARGE")
        if self.headers.get_content_type() != "application/json":
            raise ControlError("content-type must be application/json", 415, "UNSUPPORTED_MEDIA_TYPE")
        return strict_object(self.rfile.read(length), "request")

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.query or parsed.fragment:
                raise ControlError("query and fragment data are forbidden")
            if parsed.path == "/health":
                self.send_json(200, {"status": "healthy", "service": "zkdeal-fault-control"})
                return
            if parsed.path == "/ready":
                self.send_json(200, {
                    "status": "ready", "candidateId": self.controller.topology.candidate_id,
                    "topologySha256": self.controller.topology.topology_sha256,
                })
                return
            if parsed.path == "/capabilities":
                self.send_json(200, self.controller.capabilities())
                return
            match = re.fullmatch(r"/v1/(?:faults|load-runs)/(fc-[0-9a-f]{40})", parsed.path)
            if match:
                self.controller.authenticate(self.headers.get("authorization"))
                self.send_json(200, self.controller.receipt(self.controller.journal.closure(match.group(1))))
                return
            raise ControlError("route not found", 404, "NOT_FOUND")
        except Exception as exc:
            self.handle_error(exc)

    def do_POST(self) -> None:  # noqa: N802
        routes = {"/v1/faults": "fault", "/v1/load-runs": "load"}
        try:
            route = routes.get(self.path)
            if not route:
                raise ControlError("route not found", 404, "NOT_FOUND")
            self.controller.authenticate(self.headers.get("authorization"))
            key = self.headers.get("idempotency-key", "")
            correlation_id = self.headers.get("x-correlation-id", "")
            result, replay = self.controller.execute(route, self.body(), key, correlation_id)
            self.send_json(200, result, replay)
        except Exception as exc:
            self.handle_error(exc)


def build_controller(environ: dict[str, str] | None = None) -> Controller:
    env = os.environ if environ is None else environ
    topology = load_topology(env.get("FAULT_CONTROL_TOPOLOGY_FILE", ""), env.get("FAULT_CONTROL_TOPOLOGY_SHA256", ""))
    for name, expected in (
        ("FAULT_CONTROL_CANDIDATE_ID", topology.candidate_id),
        ("FAULT_CONTROL_PLAN_SHA256", topology.plan_sha256),
        ("FAULT_CONTROL_HOSTED_INTEGRATION_TOKEN", topology.hosted_token),
    ):
        if env.get(name) != expected:
            raise ControlError(f"{name} differs from the hash-bound topology", 503, "CONFIGURATION_ERROR")
    token = read_secret(
        env.get("FAULT_CONTROL_TOKEN_FILE", ""),
        topology.auth_token_sha256,
        "FAULT_CONTROL_TOKEN_FILE",
        require_ephemeral=True,
    )
    descriptor_sha = env.get("FAULT_CONTROL_CANDIDATE_DESCRIPTOR_SHA256", "")
    adapter_source = env.get("FAULT_CONTROL_ADAPTER_SOURCE_SHA256", "")
    adapter_image = env.get("FAULT_CONTROL_ADAPTER_IMAGE", "")
    platform = env.get("FAULT_CONTROL_PLATFORM", "")
    if not SHA256.fullmatch(descriptor_sha):
        raise ControlError("FAULT_CONTROL_CANDIDATE_DESCRIPTOR_SHA256 is malformed", 503, "CONFIGURATION_ERROR")
    if not SHA256.fullmatch(adapter_source):
        raise ControlError("FAULT_CONTROL_ADAPTER_SOURCE_SHA256 is malformed", 503, "CONFIGURATION_ERROR")
    if not OCI_DIGEST.fullmatch(adapter_image):
        raise ControlError("FAULT_CONTROL_ADAPTER_IMAGE must be repository@sha256", 503, "CONFIGURATION_ERROR")
    if platform != "docker":
        raise ControlError("FAULT_CONTROL_PLATFORM must name the reviewed docker adapter", 503, "CONFIGURATION_ERROR")
    receipt_binding = {
        "candidateDescriptorSha256": descriptor_sha,
        "topologyReceiptSha256": topology.topology_sha256,
        "platform": platform,
        "adapterImage": adapter_image,
        "adapterSourceSha256": adapter_source,
    }
    binding_digest = sha256_bytes(canonical_bytes({**topology.binding, **receipt_binding}))
    journal = Journal(Path(env.get("FAULT_CONTROL_JOURNAL_DIR", "/journal")), binding_digest)
    return Controller(topology, token, journal, receipt_binding)


def main() -> int:
    try:
        controller = build_controller()
        Handler.controller = controller
        host = os.environ.get("FAULT_CONTROL_HOST", "0.0.0.0")
        port = bounded_int(int(os.environ.get("FAULT_CONTROL_PORT", "8080")), "FAULT_CONTROL_PORT", 1, 65535)
        server = ThreadingHTTPServer((host, port), Handler)
        print(json.dumps({
            "component": "fault-control",
            "event": "started",
            "candidateId": controller.topology.candidate_id,
            "classification": controller.topology.classification,
            "port": port,
        }, sort_keys=True), flush=True)
        server.serve_forever()
        return 0
    except (ControlError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
