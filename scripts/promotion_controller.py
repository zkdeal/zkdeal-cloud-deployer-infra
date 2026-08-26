#!/usr/bin/env python3
"""Conservative provider-neutral warm-standby promotion controller.

The controller never promotes PostgreSQL itself. A separately authenticated
failover provider must fence the former writer, capture the durable primary
target, promote/repoint PostgreSQL, and prove replay/freshness. Only then does
this process call the owner promotion endpoint and ask the provider to commit
the stable writer route and post-fence signer boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse


MAX_RESPONSE_BYTES = 1_048_576
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{7,199}$")
LSN = re.compile(r"^[0-9A-Fa-f]+/[0-9A-Fa-f]+$")


class ControllerError(RuntimeError):
    pass


def require_container() -> None:
    if not Path("/.dockerenv").exists() and os.environ.get("ZKDEAL_VERIFIED_CONTAINER") != "1":
        raise ControllerError("promotion controller execution is container-only")


def secret(name: str) -> str:
    direct = os.environ.get(name)
    file_name = os.environ.get(f"{name}_FILE")
    if direct and file_name:
        raise ControllerError(f"{name} and {name}_FILE are mutually exclusive")
    if file_name:
        path = Path(file_name)
        if not path.is_file() or path.is_symlink():
            raise ControllerError(f"{name}_FILE must be a regular file")
        direct = path.read_text(encoding="utf-8").strip()
    if not direct or len(direct) < 16:
        raise ControllerError(f"{name} must be supplied as a scoped secret of at least 16 characters")
    return direct


def integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ControllerError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ControllerError(f"{name} must be between {minimum} and {maximum}")
    return value


def checked_url(
    value: str, name: str, allow_http: bool, *, allow_cluster_http: bool = False,
) -> str:
    parsed = urlparse(value)
    cluster_http = bool(
        allow_cluster_http
        and parsed.scheme == "http"
        and parsed.hostname
        and ("." not in parsed.hostname or parsed.hostname.endswith((".svc", ".svc.cluster.local")))
    )
    if parsed.scheme not in ({"http", "https"} if allow_http else {"https"}) and not cluster_http:
        raise ControllerError(f"{name} must use {'HTTP(S)' if allow_http else 'HTTPS'}")
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ControllerError(f"{name} is not an acceptable service URL")
    return value.rstrip("/")


def lsn_value(value: str) -> int:
    if not LSN.fullmatch(value):
        raise ControllerError(f"invalid PostgreSQL LSN: {value!r}")
    high, low = value.split("/", 1)
    return (int(high, 16) << 32) + int(low, 16)


@dataclass(frozen=True)
class Config:
    active_health_urls: tuple[str, ...]
    standby_health_url: str
    failover_provider_url: str
    promotion_endpoint: str
    active_coordinator_id: str
    standby_coordinator_id: str
    candidate_id: str
    provider_token: str
    approval_token: str
    principal_token: str
    failure_threshold: int
    poll_seconds: int
    max_monitor_cycles: int
    max_rto_seconds: int
    max_checkpoint_age_seconds: int
    request_timeout_seconds: int
    state_path: Path
    armed: bool
    allow_http: bool

    @classmethod
    def from_environment(cls) -> "Config":
        allow_http = os.environ.get("PROMOTION_ALLOW_INSECURE_HTTP") == "acceptance-only"
        urls = tuple(
            checked_url(item.strip(), "ACTIVE_HEALTH_URLS", allow_http)
            for item in os.environ.get("ACTIVE_HEALTH_URLS", "").split(",")
            if item.strip()
        )
        if len(urls) < 2:
            raise ControllerError("ACTIVE_HEALTH_URLS requires at least two independent witnesses")
        identities = {(urlparse(item).hostname, urlparse(item).port) for item in urls}
        if len(identities) != len(urls):
            raise ControllerError("active health witnesses must use distinct network identities")
        active_id = os.environ.get("ACTIVE_COORDINATOR_ID", "")
        standby_id = os.environ.get("STANDBY_COORDINATOR_ID", "")
        candidate = os.environ.get("PROMOTION_CANDIDATE_ID", "")
        for name, value in (
            ("ACTIVE_COORDINATOR_ID", active_id),
            ("STANDBY_COORDINATOR_ID", standby_id),
            ("PROMOTION_CANDIDATE_ID", candidate),
        ):
            if not SAFE_ID.fullmatch(value):
                raise ControllerError(f"{name} must be 8-200 lowercase safe characters")
        if active_id == standby_id:
            raise ControllerError("active and standby coordinator identities must differ")
        return cls(
            active_health_urls=urls,
            standby_health_url=checked_url(
                os.environ.get("STANDBY_HEALTH_URL", ""), "STANDBY_HEALTH_URL", allow_http,
                allow_cluster_http=True,
            ),
            failover_provider_url=checked_url(
                os.environ.get("FAILOVER_PROVIDER_URL", ""), "FAILOVER_PROVIDER_URL", allow_http,
            ),
            promotion_endpoint=checked_url(
                os.environ.get("PROMOTION_ENDPOINT", ""), "PROMOTION_ENDPOINT", allow_http,
                allow_cluster_http=True,
            ),
            active_coordinator_id=active_id,
            standby_coordinator_id=standby_id,
            candidate_id=candidate,
            provider_token=secret("FAILOVER_PROVIDER_TOKEN"),
            approval_token=secret("FAILOVER_APPROVAL_TOKEN"),
            principal_token=secret("PROMOTION_PRINCIPAL_TOKEN"),
            failure_threshold=integer("FAILURE_THRESHOLD", 3, 3, 20),
            poll_seconds=integer("POLL_SECONDS", 10, 1, 60),
            max_monitor_cycles=integer("MAX_MONITOR_CYCLES", 0, 0, 1_000_000),
            max_rto_seconds=integer("MAX_RTO_SECONDS", 300, 30, 300),
            max_checkpoint_age_seconds=integer("MAX_CHECKPOINT_AGE_SECONDS", 30, 1, 300),
            request_timeout_seconds=integer("REQUEST_TIMEOUT_SECONDS", 10, 1, 30),
            state_path=Path(os.environ.get(
                "PROMOTION_CONTROLLER_STATE_PATH", "/tmp/promotion-controller-state.json",
            )),
            armed=os.environ.get("PROMOTION_CONTROLLER_ARMED") == "true",
            allow_http=allow_http,
        )


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)


def emit(config: Config, status: str, **fields: Any) -> None:
    value = {
        "schemaVersion": 1,
        "status": status,
        "candidateId": config.candidate_id,
        "activeCoordinatorId": config.active_coordinator_id,
        "standbyCoordinatorId": config.standby_coordinator_id,
        "observedAtUnixMs": int(time.time() * 1000),
        **fields,
    }
    atomic_json(config.state_path, value)
    print(json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


def request_json(
    url: str,
    timeout: int,
    *,
    method: str = "GET",
    token: str | None = None,
    idempotency_key: str | None = None,
    approval_token: str | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if approval_token:
        headers["X-Zkdeal-Failover-Approval"] = approval_token
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ControllerError(f"response exceeded {MAX_RESPONSE_BYTES} bytes: {url}")
            if not 200 <= response.status < 300:
                raise ControllerError(f"HTTP {response.status} from {url}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ControllerError(f"request failed for {url}: {type(exc).__name__}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ControllerError(f"non-JSON response from {url}") from exc
    if not isinstance(value, dict):
        raise ControllerError(f"response from {url} is not an object")
    return value


def active_witness_healthy(value: dict[str, Any], active_id: str) -> bool:
    return all((
        value.get("coordinatorId") == active_id,
        value.get("effectiveRole") == "active",
        value.get("acceptingWrites") is True,
        value.get("fenceFresh") is True,
    ))


def standby_ready(value: dict[str, Any], standby_id: str) -> bool:
    return all((
        value.get("coordinatorId") == standby_id,
        value.get("configuredRole") == "standby",
        value.get("effectiveRole") == "standby",
        value.get("acceptingWrites") is False,
    ))


def validate_prepare(config: Config, value: dict[str, Any]) -> str:
    operation_id = value.get("operationId")
    if operation_id != config.candidate_id:
        raise ControllerError("failover provider operation ID differs from the candidate ID")
    required = (
        "activeFenced", "oldWriterTerminated", "targetCapturedByProvider",
        "databasePromoted", "standbyReplayAtOrAfterTarget", "indexerHeadMatchesL1",
        "stableDatabaseEndpointRouted",
    )
    for field in required:
        if value.get(field) is not True:
            raise ControllerError(f"failover provider did not prove {field}")
    if value.get("status") != "READY_FOR_APPLICATION_PROMOTION":
        raise ControllerError("failover provider is not ready for application promotion")
    if value.get("primaryTargetSource") != "durable-fenced-wal-checkpoint":
        raise ControllerError("failover provider did not use the durable fenced primary target")
    if value.get("standbySignerAuthorityActive") is not False:
        raise ControllerError("standby signer authority became active before fence promotion")
    target = str(value.get("primaryTargetLsn", ""))
    replay = str(value.get("standbyReplayLsn", ""))
    if lsn_value(replay) < lsn_value(target):
        raise ControllerError("standby replay LSN is behind the fenced primary target")
    age = value.get("checkpointAgeSeconds")
    if not isinstance(age, int) or age < 0 or age > config.max_checkpoint_age_seconds:
        raise ControllerError("failover provider checkpoint is stale")
    return operation_id


def validate_owner_promotion(value: dict[str, Any]) -> None:
    for field in ("promoted", "indexerHeadMatchesL1"):
        if value.get(field) is not True:
            raise ControllerError(f"owner promotion did not prove {field}")
    if value.get("effectiveRole") != "active":
        raise ControllerError("owner promotion did not activate the standby coordinator")
    replication = value.get("promotionReplication")
    if not isinstance(replication, dict):
        raise ControllerError("owner promotion omitted replication evidence")


def validate_commit(config: Config, operation_id: str, value: dict[str, Any]) -> None:
    expected = {
        "operationId": operation_id,
        "writerRouteCommitted": True,
        "writerCoordinatorId": config.standby_coordinator_id,
        "oldWriterRouteRemoved": True,
        "stableDatabaseEndpointRouted": True,
        "signerAuthorityActivatedAfterFence": True,
    }
    for field, wanted in expected.items():
        if value.get(field) != wanted:
            raise ControllerError(f"failover provider commit differs: {field}")


def run(config: Config) -> dict[str, Any] | None:
    emit(config, "monitoring", armed=config.armed, witnessCount=len(config.active_health_urls))
    consecutive = 0
    first_failure = 0.0
    cycle = 0
    while True:
        cycle += 1
        healthy: list[str] = []
        failed: list[str] = []
        for url in config.active_health_urls:
            try:
                value = request_json(url, config.request_timeout_seconds)
                if active_witness_healthy(value, config.active_coordinator_id):
                    healthy.append(url)
                else:
                    failed.append(url)
            except ControllerError:
                failed.append(url)
        if healthy:
            consecutive = 0
            first_failure = 0.0
            emit(config, "active-healthy", cycle=cycle, healthyWitnesses=len(healthy))
        else:
            if consecutive == 0:
                first_failure = time.monotonic()
            consecutive += 1
            emit(
                config, "active-unhealthy", cycle=cycle,
                consecutiveFailures=consecutive, failedWitnesses=len(failed),
            )
            if consecutive >= config.failure_threshold:
                break
        if config.max_monitor_cycles and cycle >= config.max_monitor_cycles:
            emit(config, "monitor-window-complete", promoted=False)
            return None
        time.sleep(config.poll_seconds)

    if not config.armed:
        raise ControllerError("failure threshold reached but promotion controller is not armed")
    standby = request_json(config.standby_health_url, config.request_timeout_seconds)
    if not standby_ready(standby, config.standby_coordinator_id):
        raise ControllerError("standby coordinator is not signerless standby-ready")

    prepare = request_json(
        config.failover_provider_url,
        config.request_timeout_seconds,
        method="POST",
        token=config.provider_token,
        approval_token=config.approval_token,
        idempotency_key=config.candidate_id,
        body={
            "candidateId": config.candidate_id,
            "activeCoordinatorId": config.active_coordinator_id,
            "standbyCoordinatorId": config.standby_coordinator_id,
            "failedWitnessCount": len(config.active_health_urls),
        },
    )
    operation_id = validate_prepare(config, prepare)
    emit(config, "provider-fenced-and-promoted", operationId=operation_id)

    owner_key = f"{config.candidate_id}:owner"
    owner = request_json(
        config.promotion_endpoint,
        config.request_timeout_seconds,
        method="POST",
        token=config.principal_token,
        idempotency_key=owner_key,
        body={},
    )
    validate_owner_promotion(owner)
    owner_hash = hashlib.sha256(
        json.dumps(owner, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    emit(config, "owner-promoted", operationId=operation_id, ownerResponseSha256=owner_hash)

    commit = request_json(
        f"{config.failover_provider_url}/{quote(operation_id, safe='')}/commit",
        config.request_timeout_seconds,
        method="POST",
        token=config.provider_token,
        approval_token=config.approval_token,
        idempotency_key=f"{config.candidate_id}:commit",
        body={"ownerResponseSha256": owner_hash},
    )
    validate_commit(config, operation_id, commit)
    rto = round(time.monotonic() - first_failure, 3)
    if rto > config.max_rto_seconds:
        raise ControllerError(f"promotion exceeded configured RTO: {rto}s")
    result = {
        "promoted": True,
        "operationId": operation_id,
        "rtoSeconds": rto,
        "oldWriterFenced": True,
        "databasePromoted": True,
        "ownerPromoted": True,
        "writerRouteCommitted": True,
        "signerActivatedOnlyAfterFence": True,
    }
    emit(config, "promotion-complete", **result)
    return result


def main() -> int:
    try:
        require_container()
        result = run(Config.from_environment())
        return 0 if result is not None else 3
    except (ControllerError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
