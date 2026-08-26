#!/usr/bin/env python3
"""First-party failover-provider-v1 adapters for Docker Compose and Kubernetes.

The provider is the only deployment component allowed to hold narrowly scoped
platform authority.  The promotion controller remains credential-free with
respect to Docker, Kubernetes, PostgreSQL, routing and signer activation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


MAX_BODY_BYTES = 1_048_576
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{7,199}$")
SAFE_RESOURCE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,62}$")
SAFE_LABEL_KEY = re.compile(r"^(?:[a-z0-9.-]+/)?[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
LSN = re.compile(r"^[0-9A-Fa-f]+/[0-9A-Fa-f]+$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ProviderError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def require_container() -> None:
    if not Path("/.dockerenv").exists() and os.environ.get("ZKDEAL_VERIFIED_CONTAINER") != "1":
        raise ProviderError(500, "container-required", "failover provider execution is container-only")


def secret(name: str) -> str:
    direct = os.environ.get(name)
    file_name = os.environ.get(f"{name}_FILE")
    if direct and file_name:
        raise ProviderError(500, "invalid-config", f"{name} and {name}_FILE are mutually exclusive")
    if file_name:
        path = Path(file_name)
        # Kubernetes Secret volumes intentionally expose keys through a
        # read-only symlink into an atomically rotated ..data directory.
        # Resolve it, but require the final target to remain a regular file.
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ProviderError(500, "invalid-config", f"{name}_FILE is unavailable") from exc
        if not resolved.is_file():
            raise ProviderError(500, "invalid-config", f"{name}_FILE must resolve to a regular file")
        direct = path.read_text(encoding="utf-8").strip()
    if not direct or len(direct) < 16:
        raise ProviderError(500, "invalid-config", f"{name} must contain at least 16 characters")
    return direct


def integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ProviderError(500, "invalid-config", f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ProviderError(500, "invalid-config", f"{name} must be between {minimum} and {maximum}")
    return value


def safe_value(name: str, pattern: re.Pattern[str] = SAFE_RESOURCE) -> str:
    value = os.environ.get(name, "")
    if not pattern.fullmatch(value):
        raise ProviderError(500, "invalid-config", f"{name} is not a safe fixed resource name")
    return value


def checked_url(value: str, name: str, allow_http: bool, *, cluster_http: bool = False) -> str:
    parsed = urlparse(value)
    internal = bool(
        cluster_http
        and parsed.scheme == "http"
        and parsed.hostname
        and ("." not in parsed.hostname or parsed.hostname.endswith((".svc", ".svc.cluster.local")))
    )
    if parsed.scheme != "https" and not (allow_http or internal):
        raise ProviderError(500, "invalid-config", f"{name} must use HTTPS")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProviderError(500, "invalid-config", f"{name} is not a service URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ProviderError(500, "invalid-config", f"{name} contains forbidden URL fields")
    return value.rstrip("/")


def lsn_value(value: str) -> int:
    if not LSN.fullmatch(value):
        raise ProviderError(503, "invalid-lsn", "platform returned a malformed PostgreSQL LSN")
    high, low = value.split("/", 1)
    return (int(high, 16) << 32) + int(low, 16)


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schemaVersion": 1, "operations": {}}
    if path.is_symlink() or not path.is_file():
        raise ProviderError(500, "invalid-state", "provider state must be a regular file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schemaVersion") != 1 or not isinstance(value.get("operations"), dict):
        raise ProviderError(500, "invalid-state", "provider state schema is invalid")
    return value


def request_json(url: str, timeout: int, allow_http: bool) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    context = ssl.create_default_context()
    if allow_http and urlparse(url).scheme == "https":
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            payload = response.read(MAX_BODY_BYTES + 1)
            status = response.status
    except urllib.error.HTTPError as exc:
        payload = exc.read(MAX_BODY_BYTES + 1)
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, {}
    if len(payload) > MAX_BODY_BYTES:
        return 0, {}
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, {}
    return status, value if isinstance(value, dict) else {}


def witness_networks(urls: tuple[str, ...]) -> list[frozenset[str]]:
    identities: list[frozenset[str]] = []
    for url in urls:
        parsed = urlparse(url)
        try:
            rows = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ProviderError(503, "witness-dns-failed", f"witness DNS resolution failed: {parsed.hostname}") from exc
        addresses = frozenset(row[4][0] for row in rows)
        if not addresses:
            raise ProviderError(503, "witness-dns-failed", f"witness has no network address: {parsed.hostname}")
        identities.append(addresses)
    for index, left in enumerate(identities):
        for right in identities[index + 1:]:
            if left & right:
                raise ProviderError(503, "witnesses-not-independent", "active witnesses resolve to an overlapping network identity")
    return identities


def active_healthy(value: dict[str, Any], coordinator_id: str) -> bool:
    return (
        value.get("coordinatorId") == coordinator_id
        and value.get("effectiveRole") == "active"
        and value.get("acceptingWrites") is True
        and value.get("fenceFresh") is True
    )


def verify_witness_failures(config: "Config", request: dict[str, Any]) -> None:
    witness_networks(config.active_health_urls)
    failed = 0
    for url in config.active_health_urls:
        status, value = request_json(url, config.request_timeout_seconds, config.allow_http)
        if status == 200 and active_healthy(value, request["activeCoordinatorId"]):
            raise ProviderError(409, "active-witness-veto", "an independent witness still observes a fresh active writer")
        failed += 1
    if failed < 2 or request["failedWitnessCount"] != failed:
        raise ProviderError(409, "witness-count-mismatch", "provider witness observations do not match the request")


def verify_standby(config: "Config", coordinator_id: str) -> None:
    status, value = request_json(config.standby_health_url, config.request_timeout_seconds, config.allow_http)
    if status != 200 or not (
        value.get("coordinatorId") == coordinator_id
        and value.get("configuredRole") == "standby"
        and value.get("effectiveRole") == "standby"
        and value.get("acceptingWrites") is False
    ):
        raise ProviderError(503, "standby-not-safe", "standby is not healthy, signerless and fenced")


def verify_indexer(config: "Config") -> None:
    status, value = request_json(config.indexer_freshness_url, config.request_timeout_seconds, config.allow_http)
    if status != 200 or value.get("indexerHeadMatchesL1") is not True or value.get("unresolvedSafetyEvents") != 0:
        raise ProviderError(503, "indexer-not-fresh", "canonical indexer freshness/safety gate failed")


def verify_signer_authority(config: "Config") -> None:
    # Workload availability and Service endpoint publication are separate
    # Kubernetes control-plane observations.  The provider waits for both,
    # bounded by the same failover deadline, before it claims that signer
    # authority is usable.  Every configured role must be healthy in the same
    # observation pass; a transient success cannot mask a later failure.
    deadline = time.monotonic() + config.platform_timeout_seconds
    last_statuses: dict[str, int] = {}
    while time.monotonic() < deadline:
        healthy = True
        for url in config.signer_authority_health_urls:
            status, _ = request_json(url, config.request_timeout_seconds, config.allow_http)
            last_statuses[url] = status
            if status != 200:
                healthy = False
        if healthy:
            return
        time.sleep(1)
    failed = sum(status != 200 for status in last_statuses.values())
    raise ProviderError(
        503,
        "signer-activation-failed",
        f"{failed or len(config.signer_authority_health_urls)} post-fence signer role(s) did not become healthy",
    )


CommandRunner = Callable[[list[str], int], subprocess.CompletedProcess[str]]


def default_runner(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)


class Driver:
    def prepare(
        self,
        request: dict[str, Any],
        existing_target: str | None,
        checkpoint: Callable[[str], None],
    ) -> dict[str, Any]:
        raise NotImplementedError

    def commit(self, operation_id: str, owner_hash: str) -> dict[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class DockerSettings:
    project: str
    network: str
    active_service: str
    standby_service: str
    primary_db_service: str
    standby_db_service: str
    signer_service: str
    database_alias: str
    application_alias: str
    pg_user: str
    pg_database: str
    pg_data: str

    @classmethod
    def from_environment(cls) -> "DockerSettings":
        return cls(
            project=safe_value("DOCKER_COMPOSE_PROJECT", SAFE_ID),
            network=safe_value("DOCKER_FAILOVER_NETWORK", SAFE_ID),
            active_service=safe_value("DOCKER_ACTIVE_SERVICE"),
            standby_service=safe_value("DOCKER_STANDBY_SERVICE"),
            primary_db_service=safe_value("DOCKER_PRIMARY_DB_SERVICE"),
            standby_db_service=safe_value("DOCKER_STANDBY_DB_SERVICE"),
            signer_service=safe_value("DOCKER_SIGNER_SERVICE"),
            database_alias=safe_value("DOCKER_DATABASE_ALIAS"),
            application_alias=safe_value("DOCKER_APPLICATION_ALIAS"),
            pg_user=safe_value("FAILOVER_PGUSER"),
            pg_database=safe_value("FAILOVER_PGDATABASE"),
            pg_data=os.environ.get("FAILOVER_PGDATA", "/var/lib/postgresql/data"),
        )


class DockerDriver(Driver):
    def __init__(self, config: "Config", settings: DockerSettings, runner: CommandRunner = default_runner):
        self.config = config
        self.settings = settings
        self.runner = runner

    def run(self, *args: str, tolerate: bool = False, timeout: int = 30) -> str:
        completed = self.runner(["docker", *args], timeout)
        if completed.returncode != 0 and not tolerate:
            message = (completed.stderr or completed.stdout).strip()[:500]
            raise ProviderError(503, "docker-operation-failed", f"fixed Docker operation failed: {message}")
        return completed.stdout.strip()

    def container(self, service: str) -> str:
        value = self.run(
            "ps", "-aq",
            "--filter", f"label=com.docker.compose.project={self.settings.project}",
            "--filter", f"label=com.docker.compose.service={service}",
        ).splitlines()
        if len(value) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", value[0]):
            raise ProviderError(503, "docker-target-ambiguous", f"expected exactly one fixed Compose service: {service}")
        return value[0]

    def running(self, container: str) -> bool:
        return self.run("inspect", "--format", "{{.State.Running}}", container) == "true"

    def stop(self, container: str) -> None:
        if self.running(container):
            self.run("stop", "--time", "10", container, timeout=20)
        if self.running(container):
            raise ProviderError(503, "docker-fence-failed", "container remained running after fence")

    def exec(self, container: str, *command: str, user: str | None = None, timeout: int = 30) -> str:
        args = ["exec"]
        if user:
            args.extend(["--user", user])
        args.extend([container, *command])
        return self.run(*args, timeout=timeout)

    def psql(self, container: str, query: str) -> str:
        return self.exec(
            container, "psql", "-U", self.settings.pg_user, "-d", self.settings.pg_database,
            "--set", "ON_ERROR_STOP=1", "-tAc", query,
        ).strip()

    def route_alias(self, container: str, alias: str) -> None:
        self.run("network", "disconnect", "-f", self.settings.network, container, tolerate=True)
        self.run("network", "connect", "--alias", alias, self.settings.network, container)

    def prepare(
        self,
        request: dict[str, Any],
        existing_target: str | None,
        checkpoint: Callable[[str], None],
    ) -> dict[str, Any]:
        active = self.container(self.settings.active_service)
        standby = self.container(self.settings.standby_service)
        primary = self.container(self.settings.primary_db_service)
        standby_db = self.container(self.settings.standby_db_service)
        signer = self.container(self.settings.signer_service)
        self.stop(active)
        if self.running(signer):
            raise ProviderError(503, "signer-enabled-too-early", "standby signer authority is active before fencing")

        target = existing_target
        if not target:
            if not self.running(primary):
                raise ProviderError(503, "primary-target-unavailable", "primary stopped before its durable target was captured")
            synchronous = self.psql(
                primary,
                "select count(*) from pg_stat_replication where state='streaming' and sync_state in ('sync','quorum')",
            )
            if not synchronous.isdigit() or int(synchronous) < 1:
                raise ProviderError(503, "standby-not-synchronous", "standby was not synchronously streaming at the fence")
            target = self.psql(primary, "select pg_current_wal_flush_lsn()")
            lsn_value(target)
            checkpoint(target)

        deadline = time.monotonic() + self.config.platform_timeout_seconds
        replay = ""
        while time.monotonic() < deadline:
            recovery = self.psql(standby_db, "select pg_is_in_recovery()")
            replay = self.psql(standby_db, "select coalesce(pg_last_wal_replay_lsn()::text,'')")
            if recovery in {"t", "f"} and replay and lsn_value(replay) >= lsn_value(target):
                break
            time.sleep(1)
        else:
            raise ProviderError(503, "standby-replay-stale", "standby did not replay the fenced primary target")

        self.stop(primary)
        if self.psql(standby_db, "select pg_is_in_recovery()") == "t":
            self.exec(standby_db, "pg_ctl", "-D", self.settings.pg_data, "promote", "-w", user="postgres")
        if self.psql(standby_db, "select pg_is_in_recovery()") != "f":
            raise ProviderError(503, "database-promotion-failed", "standby remained in recovery")
        self.run("network", "disconnect", "-f", self.settings.network, primary, tolerate=True)
        self.route_alias(standby_db, self.settings.database_alias)
        verify_indexer(self.config)
        checkpoint_age = 0
        return {
            "operationId": request["candidateId"],
            "status": "READY_FOR_APPLICATION_PROMOTION",
            "activeFenced": True,
            "oldWriterTerminated": True,
            "targetCapturedByProvider": True,
            "databasePromoted": True,
            "standbyReplayAtOrAfterTarget": True,
            "indexerHeadMatchesL1": True,
            "stableDatabaseEndpointRouted": True,
            "standbySignerAuthorityActive": False,
            "primaryTargetSource": "durable-fenced-wal-checkpoint",
            "primaryTargetLsn": target,
            "standbyReplayLsn": replay,
            "checkpointAgeSeconds": checkpoint_age,
        }

    def commit(self, operation_id: str, owner_hash: str) -> dict[str, Any]:
        active = self.container(self.settings.active_service)
        standby = self.container(self.settings.standby_service)
        signer = self.container(self.settings.signer_service)
        self.stop(active)
        self.run("network", "disconnect", "-f", self.settings.network, active, tolerate=True)
        self.route_alias(standby, self.settings.application_alias)
        if not self.running(signer):
            self.run("start", signer)
        if not self.running(signer):
            raise ProviderError(503, "signer-activation-failed", "post-fence signer boundary did not activate")
        verify_signer_authority(self.config)
        return {
            "operationId": operation_id,
            "writerRouteCommitted": True,
            "writerCoordinatorId": self.config.standby_coordinator_id,
            "oldWriterRouteRemoved": True,
            "stableDatabaseEndpointRouted": True,
            "signerAuthorityActivatedAfterFence": True,
        }


@dataclass(frozen=True)
class KubernetesSettings:
    namespace: str
    active_deployment: str
    standby_deployment: str
    primary_db_pod: str
    standby_db_pod: str
    primary_db_statefulset: str
    postgres_container: str
    database_service: str
    application_service: str
    signer_deployment: str
    route_scope_label_key: str
    route_scope_label_value: str
    route_label_key: str
    standby_route_value: str
    pg_user: str
    pg_database: str
    pg_data: str

    @classmethod
    def from_environment(cls) -> "KubernetesSettings":
        return cls(
            namespace=safe_value("K8S_FAILOVER_NAMESPACE"),
            active_deployment=safe_value("K8S_ACTIVE_DEPLOYMENT"),
            standby_deployment=safe_value("K8S_STANDBY_DEPLOYMENT"),
            primary_db_pod=safe_value("K8S_PRIMARY_DB_POD"),
            standby_db_pod=safe_value("K8S_STANDBY_DB_POD"),
            primary_db_statefulset=safe_value("K8S_PRIMARY_DB_STATEFULSET"),
            postgres_container=safe_value("K8S_POSTGRES_CONTAINER"),
            database_service=safe_value("K8S_DATABASE_SERVICE"),
            application_service=safe_value("K8S_APPLICATION_SERVICE"),
            signer_deployment=safe_value("K8S_SIGNER_DEPLOYMENT"),
            route_scope_label_key=safe_value("K8S_ROUTE_SCOPE_LABEL_KEY", SAFE_LABEL_KEY),
            route_scope_label_value=safe_value("K8S_ROUTE_SCOPE_LABEL_VALUE"),
            route_label_key=safe_value("K8S_ROUTE_LABEL_KEY", SAFE_LABEL_KEY),
            standby_route_value=safe_value("K8S_STANDBY_ROUTE_VALUE"),
            pg_user=safe_value("FAILOVER_PGUSER"),
            pg_database=safe_value("FAILOVER_PGDATABASE"),
            pg_data=os.environ.get("FAILOVER_PGDATA", "/var/lib/postgresql/data"),
        )


class KubernetesDriver(Driver):
    def __init__(self, config: "Config", settings: KubernetesSettings, runner: CommandRunner = default_runner):
        self.config = config
        self.settings = settings
        self.runner = runner

    def run(self, *args: str, timeout: int = 30) -> str:
        command = ["kubectl", "--namespace", self.settings.namespace, *args]
        completed = self.runner(command, timeout)
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout).strip()[:500]
            raise ProviderError(503, "kubernetes-operation-failed", f"fixed Kubernetes operation failed: {message}")
        return completed.stdout.strip()

    def get_json(self, kind_name: str) -> dict[str, Any]:
        value = json.loads(self.run("get", kind_name, "-o", "json"))
        if not isinstance(value, dict):
            raise ProviderError(503, "kubernetes-response-invalid", "Kubernetes returned a non-object")
        return value

    def scale_workload(self, kind: str, name: str, replicas: int) -> None:
        if kind not in {"deployment", "statefulset"}:
            raise ProviderError(500, "invalid-config", "provider attempted to scale an unapproved workload kind")
        self.run("scale", f"{kind}/{name}", f"--replicas={replicas}")
        deadline = time.monotonic() + self.config.platform_timeout_seconds
        while time.monotonic() < deadline:
            value = self.get_json(f"{kind}/{name}")
            status = value.get("status", {})
            field = "availableReplicas" if kind == "deployment" else "readyReplicas"
            observed = int(status.get(field, 0) or 0)
            if observed == replicas:
                return
            time.sleep(1)
        raise ProviderError(503, "kubernetes-scale-timeout", f"{kind} did not reach {replicas} replicas")

    def scale(self, deployment: str, replicas: int) -> None:
        self.scale_workload("deployment", deployment, replicas)

    def exec_pg(self, pod: str, *command: str) -> str:
        return self.run("exec", f"pod/{pod}", "-c", self.settings.postgres_container, "--", *command)

    def psql(self, pod: str, query: str) -> str:
        return self.exec_pg(
            pod, "psql", "-U", self.settings.pg_user, "-d", self.settings.pg_database,
            "--set", "ON_ERROR_STOP=1", "-tAc", query,
        ).strip()

    def patch_route(self, service: str) -> None:
        selector = {
            self.settings.route_scope_label_key: self.settings.route_scope_label_value,
            self.settings.route_label_key: self.settings.standby_route_value,
        }
        patch = json.dumps({"spec": {"selector": selector}}, separators=(",", ":"))
        self.run("patch", f"service/{service}", "--type=merge", "-p", patch)
        value = self.get_json(f"service/{service}")
        if value.get("spec", {}).get("selector") != selector:
            raise ProviderError(503, "kubernetes-route-failed", "stable Service selector did not switch exactly")

    def prepare(
        self,
        request: dict[str, Any],
        existing_target: str | None,
        checkpoint: Callable[[str], None],
    ) -> dict[str, Any]:
        self.scale(self.settings.active_deployment, 0)
        signer = self.get_json(f"deployment/{self.settings.signer_deployment}")
        if int(signer.get("spec", {}).get("replicas", 0) or 0) != 0:
            raise ProviderError(503, "signer-enabled-too-early", "standby signer authority is active before fencing")
        target = existing_target
        if not target:
            synchronous = self.psql(
                self.settings.primary_db_pod,
                "select count(*) from pg_stat_replication where state='streaming' and sync_state in ('sync','quorum')",
            )
            if not synchronous.isdigit() or int(synchronous) < 1:
                raise ProviderError(503, "standby-not-synchronous", "standby was not synchronously streaming at the fence")
            target = self.psql(self.settings.primary_db_pod, "select pg_current_wal_flush_lsn()")
            lsn_value(target)
            checkpoint(target)
        deadline = time.monotonic() + self.config.platform_timeout_seconds
        replay = ""
        while time.monotonic() < deadline:
            recovery = self.psql(self.settings.standby_db_pod, "select pg_is_in_recovery()")
            replay = self.psql(self.settings.standby_db_pod, "select coalesce(pg_last_wal_replay_lsn()::text,'')")
            if recovery in {"t", "f"} and replay and lsn_value(replay) >= lsn_value(target):
                break
            time.sleep(1)
        else:
            raise ProviderError(503, "standby-replay-stale", "standby did not replay the fenced primary target")
        # Scale the former primary controller to zero before promotion. Deleting
        # only its Pod would let the StatefulSet recreate a stale primary.
        self.scale_workload("statefulset", self.settings.primary_db_statefulset, 0)
        if self.psql(self.settings.standby_db_pod, "select pg_is_in_recovery()") == "t":
            self.exec_pg(self.settings.standby_db_pod, "pg_ctl", "-D", self.settings.pg_data, "promote", "-w")
        if self.psql(self.settings.standby_db_pod, "select pg_is_in_recovery()") != "f":
            raise ProviderError(503, "database-promotion-failed", "standby remained in recovery")
        self.patch_route(self.settings.database_service)
        verify_indexer(self.config)
        return {
            "operationId": request["candidateId"],
            "status": "READY_FOR_APPLICATION_PROMOTION",
            "activeFenced": True,
            "oldWriterTerminated": True,
            "targetCapturedByProvider": True,
            "databasePromoted": True,
            "standbyReplayAtOrAfterTarget": True,
            "indexerHeadMatchesL1": True,
            "stableDatabaseEndpointRouted": True,
            "standbySignerAuthorityActive": False,
            "primaryTargetSource": "durable-fenced-wal-checkpoint",
            "primaryTargetLsn": target,
            "standbyReplayLsn": replay,
            "checkpointAgeSeconds": 0,
        }

    def commit(self, operation_id: str, owner_hash: str) -> dict[str, Any]:
        self.scale(self.settings.active_deployment, 0)
        self.patch_route(self.settings.application_service)
        self.scale(self.settings.signer_deployment, 1)
        verify_signer_authority(self.config)
        return {
            "operationId": operation_id,
            "writerRouteCommitted": True,
            "writerCoordinatorId": self.config.standby_coordinator_id,
            "oldWriterRouteRemoved": True,
            "stableDatabaseEndpointRouted": True,
            "signerAuthorityActivatedAfterFence": True,
        }


@dataclass(frozen=True)
class Config:
    platform: str
    active_health_urls: tuple[str, ...]
    standby_health_url: str
    indexer_freshness_url: str
    signer_authority_health_urls: tuple[str, ...]
    active_coordinator_id: str
    standby_coordinator_id: str
    provider_token: str
    approval_token: str
    request_timeout_seconds: int
    platform_timeout_seconds: int
    state_path: Path
    listen_host: str
    listen_port: int
    tls_cert_file: str | None
    tls_key_file: str | None
    allow_http: bool

    @classmethod
    def from_environment(cls) -> "Config":
        allow_http = os.environ.get("FAILOVER_PROVIDER_ALLOW_INSECURE_HTTP") == "acceptance-only"
        platform = os.environ.get("FAILOVER_PLATFORM", "")
        if platform not in {"docker", "kubernetes"}:
            raise ProviderError(500, "invalid-config", "FAILOVER_PLATFORM must be docker or kubernetes")
        urls = tuple(
            checked_url(item.strip(), "ACTIVE_HEALTH_URLS", allow_http)
            for item in os.environ.get("ACTIVE_HEALTH_URLS", "").split(",")
            if item.strip()
        )
        if len(urls) < 2:
            raise ProviderError(500, "invalid-config", "ACTIVE_HEALTH_URLS requires two independent witnesses")
        signer_urls = tuple(
            checked_url(item.strip(), "SIGNER_AUTHORITY_HEALTH_URLS", allow_http, cluster_http=True)
            for item in os.environ.get("SIGNER_AUTHORITY_HEALTH_URLS", "").split(",")
            if item.strip()
        )
        if not signer_urls:
            raise ProviderError(500, "invalid-config", "SIGNER_AUTHORITY_HEALTH_URLS requires at least one scoped signer role")
        active_id = os.environ.get("ACTIVE_COORDINATOR_ID", "")
        standby_id = os.environ.get("STANDBY_COORDINATOR_ID", "")
        if not SAFE_ID.fullmatch(active_id) or not SAFE_ID.fullmatch(standby_id) or active_id == standby_id:
            raise ProviderError(500, "invalid-config", "coordinator IDs must be distinct safe IDs")
        cert = os.environ.get("FAILOVER_PROVIDER_TLS_CERT_FILE")
        key = os.environ.get("FAILOVER_PROVIDER_TLS_KEY_FILE")
        if not allow_http and (not cert or not key):
            raise ProviderError(500, "invalid-config", "TLS certificate and key files are required")
        return cls(
            platform=platform,
            active_health_urls=urls,
            standby_health_url=checked_url(
                os.environ.get("STANDBY_HEALTH_URL", ""), "STANDBY_HEALTH_URL", allow_http,
                cluster_http=True,
            ),
            indexer_freshness_url=checked_url(
                os.environ.get("INDEXER_FRESHNESS_URL", ""), "INDEXER_FRESHNESS_URL", allow_http,
                cluster_http=True,
            ),
            signer_authority_health_urls=signer_urls,
            active_coordinator_id=active_id,
            standby_coordinator_id=standby_id,
            provider_token=secret("FAILOVER_PROVIDER_TOKEN"),
            approval_token=secret("FAILOVER_APPROVAL_TOKEN"),
            request_timeout_seconds=integer("REQUEST_TIMEOUT_SECONDS", 10, 1, 30),
            platform_timeout_seconds=integer("PLATFORM_TIMEOUT_SECONDS", 120, 10, 240),
            state_path=Path(os.environ.get("FAILOVER_PROVIDER_STATE_PATH", "/var/lib/zkdeal-failover-provider/state.json")),
            listen_host=os.environ.get("FAILOVER_PROVIDER_LISTEN_HOST", "0.0.0.0"),
            listen_port=integer("FAILOVER_PROVIDER_LISTEN_PORT", 8443, 1024, 65535),
            tls_cert_file=cert,
            tls_key_file=key,
            allow_http=allow_http,
        )


class Provider:
    def __init__(self, config: Config, driver: Driver):
        self.config = config
        self.driver = driver
        self.state = load_json(config.state_path)
        # ThreadingHTTPServer keeps probes responsive during a long promotion.
        # Platform mutations remain a single serialized state machine so two
        # duplicate or conflicting POSTs cannot both enter a driver boundary.
        self.operation_lock = threading.Lock()

    def save(self) -> None:
        atomic_json(self.config.state_path, self.state)

    def authenticate(self, authorization: str | None, approval: str | None) -> None:
        expected = f"Bearer {self.config.provider_token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise ProviderError(401, "unauthorized", "provider bearer is invalid")
        if not approval or not hmac.compare_digest(approval, self.config.approval_token):
            raise ProviderError(403, "approval-required", "incident approval token is invalid")

    def prepare(self, key: str, request: dict[str, Any]) -> dict[str, Any]:
        with self.operation_lock:
            return self._prepare(key, request)

    def _prepare(self, key: str, request: dict[str, Any]) -> dict[str, Any]:
        if not SAFE_ID.fullmatch(key):
            raise ProviderError(400, "invalid-idempotency-key", "Idempotency-Key must be a safe 8-200 character value")
        expected = {"candidateId", "activeCoordinatorId", "standbyCoordinatorId", "failedWitnessCount"}
        if set(request) != expected:
            raise ProviderError(400, "invalid-request", "prepare request fields differ from the provider contract")
        if not SAFE_ID.fullmatch(str(request.get("candidateId", ""))):
            raise ProviderError(400, "invalid-request", "candidateId is invalid")
        if request.get("activeCoordinatorId") != self.config.active_coordinator_id or request.get("standbyCoordinatorId") != self.config.standby_coordinator_id:
            raise ProviderError(409, "topology-mismatch", "request coordinator IDs differ from the fixed topology")
        if not isinstance(request.get("failedWitnessCount"), int):
            raise ProviderError(400, "invalid-request", "failedWitnessCount must be an integer")
        operation_id = request["candidateId"]
        operations = self.state["operations"]
        request_hash = canonical_hash(request)
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        operation = operations.get(operation_id)
        if operation:
            if operation.get("prepareKeySha256") != key_hash or operation.get("prepareRequestSha256") != request_hash:
                raise ProviderError(409, "idempotency-conflict", "candidate or key was reused with changed input")
            if operation.get("prepareResponse"):
                return operation["prepareResponse"]
        else:
            operation = {
                "candidateId": operation_id,
                "prepareKeySha256": key_hash,
                "prepareRequestSha256": request_hash,
                "phase": "PREPARING",
                "createdAtUnixMs": int(time.time() * 1000),
            }
            operations[operation_id] = operation
            self.save()

        verify_witness_failures(self.config, request)
        verify_standby(self.config, request["standbyCoordinatorId"])

        def checkpoint(target: str) -> None:
            operation["primaryTargetLsn"] = target
            operation["targetCapturedAtUnixMs"] = int(time.time() * 1000)
            self.save()

        response = self.driver.prepare(request, operation.get("primaryTargetLsn"), checkpoint)
        if response.get("operationId") != operation_id:
            raise ProviderError(503, "driver-contract-error", "platform driver returned the wrong operation ID")
        operation["prepareResponse"] = response
        operation["prepareResponseSha256"] = canonical_hash(response)
        operation["phase"] = "PREPARED"
        self.save()
        return response

    def commit(self, operation_id: str, key: str, request: dict[str, Any]) -> dict[str, Any]:
        with self.operation_lock:
            return self._commit(operation_id, key, request)

    def _commit(self, operation_id: str, key: str, request: dict[str, Any]) -> dict[str, Any]:
        if not SAFE_ID.fullmatch(operation_id) or not SAFE_ID.fullmatch(key):
            raise ProviderError(400, "invalid-idempotency-key", "operation and idempotency key must be safe IDs")
        if set(request) != {"ownerResponseSha256"} or not HEX64.fullmatch(str(request.get("ownerResponseSha256", ""))):
            raise ProviderError(400, "invalid-request", "commit requires the exact ownerResponseSha256")
        operation = self.state["operations"].get(operation_id)
        if not operation or operation.get("phase") not in {"PREPARED", "COMMITTED"}:
            raise ProviderError(409, "not-prepared", "operation has no durable prepared boundary")
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        request_hash = canonical_hash(request)
        if operation.get("commitKeySha256"):
            if operation["commitKeySha256"] != key_hash or operation.get("commitRequestSha256") != request_hash:
                raise ProviderError(409, "idempotency-conflict", "commit key was reused with changed input")
            if operation.get("commitResponse"):
                return operation["commitResponse"]
        else:
            operation["commitKeySha256"] = key_hash
            operation["commitRequestSha256"] = request_hash
            operation["ownerResponseSha256"] = request["ownerResponseSha256"]
            self.save()
        response = self.driver.commit(operation_id, request["ownerResponseSha256"])
        if response.get("operationId") != operation_id:
            raise ProviderError(503, "driver-contract-error", "platform driver returned the wrong operation ID")
        committed_at = int(time.time() * 1000)
        captured_at = operation.get("targetCapturedAtUnixMs")
        if not isinstance(captured_at, int):
            raise ProviderError(503, "checkpoint-missing", "prepared operation has no durable target capture time")
        # The measured promotion duration is part of the provider surface so an
        # acceptance plan can bind rtoSeconds to a real measurement instead of
        # a vacuous constant: fenced target capture through committed route.
        elapsed_ms = max(0, committed_at - captured_at)
        response["rtoSeconds"] = -(-elapsed_ms // 1000)
        response["targetCapturedAtUnixMs"] = captured_at
        response["committedAtUnixMs"] = committed_at
        operation["commitResponse"] = response
        operation["commitResponseSha256"] = canonical_hash(response)
        operation["phase"] = "COMMITTED"
        operation["committedAtUnixMs"] = committed_at
        self.save()
        return response

    def status(self, operation_id: str) -> dict[str, Any]:
        if not SAFE_ID.fullmatch(operation_id):
            raise ProviderError(400, "invalid-request", "operation ID must be a safe 8-200 character value")
        operation = self.state["operations"].get(operation_id)
        if not operation:
            raise ProviderError(404, "not-found", "failover operation is unknown")
        commit_response = operation.get("commitResponse") or {}
        return {
            "operationId": operation_id,
            "phase": operation.get("phase"),
            "createdAtUnixMs": operation.get("createdAtUnixMs"),
            "targetCapturedAtUnixMs": operation.get("targetCapturedAtUnixMs"),
            "committedAtUnixMs": operation.get("committedAtUnixMs"),
            "rtoSeconds": commit_response.get("rtoSeconds"),
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "zkdeal-failover-provider/1"

    @property
    def provider(self) -> Provider:
        return self.server.provider  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(json.dumps({"component": "failover-provider", "message": fmt % args}), flush=True)

    def send_json(self, status: int, value: dict[str, Any]) -> None:
        payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            # Health probes may close immediately after reading headers. This
            # is not an operation failure and must not emit a traceback.
            pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self.send_json(200, {"status": "ok", "platform": self.provider.config.platform})
        elif self.path == "/ready":
            self.send_json(200, {"ready": True, "stateDurable": True})
        elif self.path == "/capabilities":
            self.send_json(200, {
                "schemaVersion": 1,
                "contract": "failover-provider-v1",
                "platform": self.provider.config.platform,
                "witnessIndependence": "dns-address-disjoint-and-provider-rechecked",
                "durableIdempotency": True,
                "measuredRtoSeconds": True,
            })
        else:
            match = re.fullmatch(r"/v1/failovers/([a-z0-9][a-z0-9._:-]{7,199})", self.path)
            if match:
                try:
                    self.provider.authenticate(
                        self.headers.get("Authorization"),
                        self.headers.get("X-Zkdeal-Failover-Approval"),
                    )
                    self.send_json(200, self.provider.status(match.group(1)))
                except ProviderError as exc:
                    self.send_json(exc.status, {"code": exc.code, "message": str(exc)})
                return
            self.send_json(404, {"code": "not-found", "message": "route not found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > MAX_BODY_BYTES:
                raise ProviderError(400, "invalid-body", "JSON body size is invalid")
            if self.headers.get_content_type() != "application/json":
                raise ProviderError(400, "invalid-content-type", "Content-Type must be application/json")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ProviderError(400, "invalid-body", "JSON body must be an object")
            self.provider.authenticate(
                self.headers.get("Authorization"),
                self.headers.get("X-Zkdeal-Failover-Approval"),
            )
            key = self.headers.get("Idempotency-Key", "")
            if self.path == "/v1/failovers":
                response = self.provider.prepare(key, value)
            else:
                match = re.fullmatch(r"/v1/failovers/([a-z0-9][a-z0-9._:-]{7,199})/commit", self.path)
                if not match:
                    raise ProviderError(404, "not-found", "route not found")
                response = self.provider.commit(match.group(1), key, value)
            self.send_json(200, response)
        except ProviderError as exc:
            self.send_json(exc.status, {"code": exc.code, "message": str(exc)})
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self.send_json(400, {"code": "invalid-json", "message": "request JSON is invalid"})
        except Exception:
            self.send_json(500, {"code": "internal-error", "message": "provider operation failed closed"})


def build_driver(config: Config) -> Driver:
    if config.platform == "docker":
        return DockerDriver(config, DockerSettings.from_environment())
    return KubernetesDriver(config, KubernetesSettings.from_environment())


def main() -> int:
    require_container()
    config = Config.from_environment()
    provider = Provider(config, build_driver(config))
    # Promotion can include a bounded database catch-up and workload rollout.
    # Keep health/readiness responsive while one request performs that work so
    # an orchestrator does not kill a correct in-flight promotion.
    server = ThreadingHTTPServer((config.listen_host, config.listen_port), Handler)
    server.provider = provider  # type: ignore[attr-defined]
    if not config.allow_http:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(config.tls_cert_file, config.tls_key_file)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    print(json.dumps({"status": "listening", "platform": config.platform, "port": config.listen_port}), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProviderError as exc:
        print(json.dumps({"status": "blocked", "code": exc.code, "message": str(exc)}), flush=True)
        raise SystemExit(1)
