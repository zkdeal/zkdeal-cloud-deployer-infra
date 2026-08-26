#!/usr/bin/env python3
"""Verify the private, exact-image topology shared with Kurtosis acceptance."""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Callable

import jsonschema

from common import DeploymentError, ROOT, atomic_write_json, load_json, require_container, sha256_file
from production_compose import validate_reference


SCHEMA = ROOT / "config/schemas/candidate-private-topology.schema.json"
ENDPOINTS = {
    "coordinator", "indexer", "queue", "headless", "l1_rpc_a", "l1_rpc_b",
    "fault", "backup", "failover", "prover", "logs",
}
EXPECTED_IMAGE_KEYS = {
    "coordinator": "coordinator",
    "indexer": "coordinator",
    "queue": "coordinator",
    "headless": "headlessNode",
    "fault": "faultController",
    "backup": "backupRestoreController",
    "failover": "failoverProvider",
    "prover": "prover",
    "logs": "loki",
}
REQUIRED_WORKLOADS = {
    "coordinator-active", "coordinator-standby", "indexer", "headless-node",
    "active-witness-a", "active-witness-b", "l1-rpc-a", "l1-rpc-b",
    "fault-control", "backup-restore-control", "failover-provider",
    "prover", "logs", "postgres-primary", "postgres-standby",
}
CAPABILITIES = {
    "faultControl": {
        "service": "zkdeal-fault-control-adapter",
        "protocol": "fault-control-v1",
        "authRole": "fault_control",
        "imageKey": "faultController",
        "label": "org.zkdeal.fault-control.source.sha256",
    },
    "backupRestore": {
        "service": "zkdeal-backup-restore-control-adapter",
        "protocol": "backup-restore-control-v1",
        "authRole": "backup_restore",
        "imageKey": "backupRestoreController",
        "label": "org.zkdeal.backup-restore-control.source.sha256",
    },
}
RUNTIME_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:-]{1,299}$")
LSN = re.compile(r"^[0-9A-F]+/[0-9A-F]+$")
RECOVERY_SQL = "SELECT pg_is_in_recovery()"
PRIMARY_REPLICATION_SQL = "SELECT state, sync_state, replay_lsn FROM pg_stat_replication"
STANDBY_WAL_RECEIVER_SQL = (
    "SELECT status, written_lsn, flushed_lsn, pg_last_wal_replay_lsn() FROM pg_stat_wal_receiver"
)
STREAMING_STATES = {"streaming", "active"}
CommandRunner = Callable[[list[str], str], str]


def strict_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DeploymentError(f"{label} is absent, not regular, or a symlink")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DeploymentError(f"{label} is not JSON") from exc
    if not isinstance(value, dict):
        raise DeploymentError(f"{label} must contain a JSON object")
    return value


def project_file(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise DeploymentError(f"{label} path is absent")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise DeploymentError(f"{label} path must be deployment-relative")
    path = (ROOT / relative).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise DeploymentError(f"{label} escaped the deployment project") from exc
    if path.is_symlink() or not path.is_file():
        raise DeploymentError(f"{label} is absent, not regular, or a symlink")
    return path


def private_url(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise DeploymentError(f"{label} must be an HTTP(S) URL")
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise DeploymentError(f"{label} is malformed") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise DeploymentError(f"{label} must be an HTTP(S) URL without user info/query/fragment")
    host = parsed.hostname.lower()
    allowed = host == "host.docker.internal" or host.endswith((".internal", ".svc", ".local"))
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        allowed = address.is_private and not address.is_loopback and not address.is_unspecified
    if not allowed:
        raise DeploymentError(f"{label} is not a private candidate bridge URL")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def lsn_value(value: str) -> int:
    high, low = value.split("/", 1)
    return (int(high, 16) << 32) + int(low, 16)


def run_json(command: list[str], label: str) -> dict[str, Any]:
    completed = subprocess.run(command, text=True, capture_output=True, check=False, timeout=30)
    if completed.returncode != 0:
        raise DeploymentError(f"{label} failed: {completed.stderr.strip()[-1000:]}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DeploymentError(f"{label} returned non-JSON") from exc
    if not isinstance(value, dict):
        raise DeploymentError(f"{label} returned a non-object")
    return value


def run_workload_command(command: list[str], label: str) -> str:
    """Default command runner for exec-into-workload queries; injectable in tests."""
    completed = subprocess.run(command, text=True, capture_output=True, check=False, timeout=30)
    if completed.returncode != 0:
        raise DeploymentError(f"{label} failed: {(completed.stderr or completed.stdout).strip()[-1000:]}")
    return completed.stdout


def replication_exec_command(platform: str, identity: str, sql: str) -> list[str]:
    """Build the exact exec-into-workload psql command for one platform."""
    shell = (
        "psql --no-psqlrc --tuples-only --no-align "
        '--username "$POSTGRES_USER" --dbname "$POSTGRES_DB" '
        f'--command "{sql}"'
    )
    if platform == "compose":
        if not SAFE_IDENTITY.fullmatch(identity) or "/" in identity or "@" in identity:
            raise DeploymentError("Docker workload identity must be one exact safe container name")
        return ["docker", "exec", identity, "sh", "-c", shell]
    if platform == "kubernetes":
        parts = identity.split("/")
        if len(parts) != 3 or any(not SAFE_IDENTITY.fullmatch(part) for part in parts):
            raise DeploymentError("Kubernetes workload identity must be namespace/pod/container")
        namespace, pod_name, container_name = parts
        return [
            "kubectl", "exec", "-n", namespace, pod_name, "-c", container_name,
            "--", "sh", "-c", shell,
        ]
    raise DeploymentError(f"replication inspection does not support platform: {platform}")


def parse_recovery_flag(output: str, label: str) -> bool:
    value = output.strip()
    if value not in {"t", "f", "true", "false"}:
        raise DeploymentError(f"{label} returned an unexpected pg_is_in_recovery value: {value[:100]}")
    return value in {"t", "true"}


def parse_query_rows(output: str, columns: int, label: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != columns:
            raise DeploymentError(f"{label} returned a malformed row: {line[:200]}")
        rows.append(parts)
    return rows


def observed_lsn(value: str, label: str) -> str:
    if not LSN.fullmatch(value):
        raise DeploymentError(f"{label} observed a malformed LSN: {value[:100]}")
    return value


def inspect_replication(
    database: dict[str, Any],
    primary: dict[str, Any],
    standby: dict[str, Any],
    platform: str,
    runner: CommandRunner | None = None,
) -> dict[str, object]:
    """Live-inspect the primary/standby PostgreSQL pair via exec-into-workload psql.

    Fails closed when either instance reports the wrong pg_is_in_recovery()
    role, when the primary shows no streaming standby in pg_stat_replication,
    or when the standby WAL receiver is not streaming.
    """
    execute = runner if runner is not None else run_workload_command
    primary_identity = str(primary["identity"])
    standby_identity = str(standby["identity"])
    commands = {
        "primaryRecovery": replication_exec_command(platform, primary_identity, RECOVERY_SQL),
        "primaryReplication": replication_exec_command(platform, primary_identity, PRIMARY_REPLICATION_SQL),
        "standbyRecovery": replication_exec_command(platform, standby_identity, RECOVERY_SQL),
        "standbyWalReceiver": replication_exec_command(platform, standby_identity, STANDBY_WAL_RECEIVER_SQL),
    }

    primary_captured = dt.datetime.now(dt.timezone.utc).isoformat()
    primary_in_recovery = parse_recovery_flag(
        execute(commands["primaryRecovery"], f"pg_is_in_recovery on {primary_identity}"),
        f"candidate PostgreSQL primary {primary_identity}",
    )
    if primary_in_recovery:
        raise DeploymentError(
            f"candidate PostgreSQL primary reports pg_is_in_recovery()=true: {primary_identity}"
        )
    replication_rows = parse_query_rows(
        execute(commands["primaryReplication"], f"pg_stat_replication on {primary_identity}"),
        3,
        f"candidate PostgreSQL primary {primary_identity}",
    )
    streaming_rows = [row for row in replication_rows if row[0] in STREAMING_STATES]
    if not streaming_rows:
        raise DeploymentError(
            "candidate PostgreSQL primary shows no streaming standby in pg_stat_replication: "
            f"{primary_identity}"
        )

    standby_captured = dt.datetime.now(dt.timezone.utc).isoformat()
    standby_in_recovery = parse_recovery_flag(
        execute(commands["standbyRecovery"], f"pg_is_in_recovery on {standby_identity}"),
        f"candidate PostgreSQL standby {standby_identity}",
    )
    if not standby_in_recovery:
        raise DeploymentError(
            f"candidate PostgreSQL standby is not in recovery (pg_is_in_recovery()=false): {standby_identity}"
        )
    receiver_rows = parse_query_rows(
        execute(commands["standbyWalReceiver"], f"pg_stat_wal_receiver on {standby_identity}"),
        4,
        f"candidate PostgreSQL standby {standby_identity}",
    )
    if len(receiver_rows) != 1:
        raise DeploymentError(
            f"candidate PostgreSQL standby WAL receiver is absent or ambiguous: {standby_identity}"
        )
    receiver_status, written_lsn, flushed_lsn, replay_lsn = receiver_rows[0]
    if receiver_status not in STREAMING_STATES:
        raise DeploymentError(
            f"candidate PostgreSQL standby WAL receiver is not streaming ({receiver_status}): {standby_identity}"
        )
    replay = observed_lsn(replay_lsn, f"candidate PostgreSQL standby {standby_identity}")
    if lsn_value(replay) < lsn_value(database["targetLsn"]):
        raise DeploymentError("candidate PostgreSQL standby observed replay is behind the persisted target")
    return {
        "schema": "zkdeal/replication-inspection/v1",
        "platform": platform,
        "primaryWorkload": database["primaryWorkload"],
        "standbyWorkload": database["standbyWorkload"],
        "primaryIdentity": primary_identity,
        "standbyIdentity": standby_identity,
        "primaryInRecovery": False,
        "standbyInRecovery": True,
        "streamingState": streaming_rows[0][0],
        "syncState": streaming_rows[0][1],
        "replayLsn": replay,
        "primaryStandbys": [
            {"state": row[0], "syncState": row[1], "replayLsn": row[2]} for row in replication_rows
        ],
        "standbyWalReceiver": {
            "status": receiver_status,
            "writtenLsn": observed_lsn(written_lsn, f"candidate PostgreSQL standby {standby_identity}"),
            "flushedLsn": observed_lsn(flushed_lsn, f"candidate PostgreSQL standby {standby_identity}"),
            "replayLsn": replay,
        },
        "commands": {name: list(command) for name, command in commands.items()},
        "queries": {
            "recovery": RECOVERY_SQL,
            "primaryReplication": PRIMARY_REPLICATION_SQL,
            "standbyWalReceiver": STANDBY_WAL_RECEIVER_SQL,
        },
        "capturedAt": {"primary": primary_captured, "standby": standby_captured},
        "staticClaimsAccepted": False,
    }


def inspect_docker(workload: dict[str, Any], network: str) -> dict[str, object]:
    identity = str(workload["identity"])
    if not SAFE_IDENTITY.fullmatch(identity) or "/" in identity or "@" in identity:
        raise DeploymentError("Docker workload identity must be one exact safe container name")
    container = run_json(["docker", "container", "inspect", identity, "--format", "{{json .}}"], f"inspect {identity}")
    state = container.get("State")
    network_settings = container.get("NetworkSettings")
    config = container.get("Config")
    if not isinstance(state, dict) or state.get("Running") is not True:
        raise DeploymentError(f"Docker workload is not running: {identity}")
    health = state.get("Health")
    if isinstance(health, dict) and health.get("Status") != "healthy":
        raise DeploymentError(f"Docker workload is not healthy: {identity}")
    networks = network_settings.get("Networks") if isinstance(network_settings, dict) else None
    if not isinstance(networks, dict) or network not in networks:
        raise DeploymentError(f"Docker workload is not on the candidate bridge: {identity}")
    if not isinstance(config, dict) or config.get("Image") != workload["imageRef"]:
        raise DeploymentError(f"Docker workload image reference differs: {identity}")
    if container.get("Image") != workload["runtimeId"]:
        raise DeploymentError(f"Docker workload runtime image ID differs: {identity}")
    image = run_json(
        ["docker", "image", "inspect", str(workload["imageRef"]), "--format", "{{json .}}"],
        f"inspect image for {identity}",
    )
    if image.get("Id") != workload["runtimeId"] or workload["imageRef"] not in image.get("RepoDigests", []):
        raise DeploymentError(f"Docker daemon did not bind exact digest identity: {identity}")
    return {
        "kind": "docker", "identity": identity, "runtimeId": container["Image"],
        "imageRef": config["Image"], "networkIdentity": network,
        "containerId": container.get("Id"), "ready": True,
    }


def inspect_kubernetes(workload: dict[str, Any]) -> dict[str, object]:
    identity = str(workload["identity"])
    parts = identity.split("/")
    if len(parts) != 3 or any(not SAFE_IDENTITY.fullmatch(part) for part in parts):
        raise DeploymentError("Kubernetes workload identity must be namespace/pod/container")
    namespace, pod_name, container_name = parts
    pod = run_json(
        ["kubectl", "get", "pod", pod_name, "-n", namespace, "-o", "json"],
        f"inspect Kubernetes pod {namespace}/{pod_name}",
    )
    metadata = pod.get("metadata")
    spec = pod.get("spec")
    status = pod.get("status")
    if not isinstance(metadata, dict) or not isinstance(spec, dict) or not isinstance(status, dict):
        raise DeploymentError("Kubernetes pod response is incomplete")
    specs = [item for item in spec.get("containers", []) if item.get("name") == container_name]
    statuses = [item for item in status.get("containerStatuses", []) if item.get("name") == container_name]
    if len(specs) != 1 or len(statuses) != 1 or statuses[0].get("ready") is not True:
        raise DeploymentError(f"Kubernetes workload is absent or not ready: {identity}")
    if specs[0].get("image") != workload["imageRef"]:
        raise DeploymentError(f"Kubernetes workload image reference differs: {identity}")
    observed_id = str(statuses[0].get("imageID", "")).removeprefix("docker-pullable://")
    if not observed_id.endswith("@" + str(workload["imageRef"]).rsplit("@", 1)[-1]):
        raise DeploymentError(f"Kubernetes workload image digest differs: {identity}")
    return {
        "kind": "kubernetes", "identity": identity, "runtimeId": workload["runtimeId"],
        "imageRef": specs[0]["image"], "podUid": metadata.get("uid"), "ready": True,
    }


def validate_capabilities(topology: dict[str, Any], images: dict[str, str]) -> dict[str, object]:
    checked: dict[str, object] = {}
    bindings = topology["adapterCapabilities"]
    for name, contract in CAPABILITIES.items():
        binding = bindings[name]
        image = images[contract["imageKey"]]
        if binding["image"] != image:
            raise DeploymentError(f"{name} capability image differs from the candidate descriptor")
        path = project_file(binding["path"], f"{name} capability")
        if sha256_file(path) != binding["sha256"]:
            raise DeploymentError(f"{name} capability SHA-256 differs")
        value = strict_json(path, f"{name} capability")
        required = {
            "schemaVersion": 1,
            "service": contract["service"],
            "protocol": contract["protocol"],
            "productionReady": True,
            "authRole": contract["authRole"],
            "sourceSha256": binding["sourceSha256"],
            "imageSourceLabelSha256": binding["sourceSha256"],
            "candidateBindingRequired": True,
            "arbitraryTargetsAccepted": False,
        }
        for field, expected in required.items():
            if value.get(field) != expected:
                raise DeploymentError(f"{name} capability differs: {field}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(value.get("openapiSha256", ""))):
            raise DeploymentError(f"{name} capability lacks a concrete OpenAPI SHA-256")
        checked[name] = {
            "path": binding["path"], "sha256": binding["sha256"],
            "sourceSha256": binding["sourceSha256"], "image": image,
            "authRole": contract["authRole"], "protocol": contract["protocol"],
        }
    return checked


def validate(
    candidate_path: Path,
    topology_path: Path,
    inspect_live: bool = True,
    runner: CommandRunner | None = None,
) -> dict[str, object]:
    candidate = strict_json(candidate_path, "candidate descriptor")
    topology = strict_json(topology_path, "candidate topology")
    errors = sorted(jsonschema.Draft202012Validator(load_json(SCHEMA)).iter_errors(topology), key=lambda item: list(item.path))
    if errors:
        where = ".".join(str(part) for part in errors[0].path) or "topology"
        raise DeploymentError(f"candidate topology schema: {where}: {errors[0].message}")
    if topology["candidateId"] != candidate.get("candidateId"):
        raise DeploymentError("candidate topology names another candidate")
    candidate_sha = sha256_file(candidate_path)
    if topology["candidateDescriptorSha256"] != candidate_sha:
        raise DeploymentError("candidate topology descriptor SHA-256 differs")
    owner = candidate.get("ownerBroadSeal")
    if not isinstance(owner, dict) or topology["hostedIntegrationToken"] != owner.get("hostedIntegrationAcceptanceToken"):
        raise DeploymentError("candidate topology hostedIntegration token differs")
    images = candidate.get("images")
    if not isinstance(images, dict):
        raise DeploymentError("candidate descriptor lacks images")
    for key in set(EXPECTED_IMAGE_KEYS.values()) | {
        "postgres", "faultController", "backupRestoreController", "frontDoor",
    }:
        validate_reference(images.get(key), f"candidate images.{key}")
    capabilities = validate_capabilities(topology, images)

    endpoints = topology["endpoints"]
    if set(endpoints) != ENDPOINTS:
        raise DeploymentError("candidate topology endpoint set differs")
    normalized = {name: private_url(value["url"], f"endpoints.{name}") for name, value in endpoints.items()}
    if normalized["queue"] != normalized["coordinator"] or endpoints["queue"]["workload"] != endpoints["coordinator"]["workload"]:
        raise DeploymentError("candidate queue must be the hosted coordinator authority")
    origins: dict[str, str] = {}
    for name, url in normalized.items():
        if name == "queue":
            continue
        origin = urllib.parse.urlsplit(url).netloc.lower()
        if origin in origins:
            raise DeploymentError(f"candidate endpoint {name} shares authority with {origins[origin]}")
        origins[origin] = name

    workloads = topology["workloads"]
    if set(workloads) != REQUIRED_WORKLOADS:
        raise DeploymentError("candidate topology workload set differs")
    platform_kind = "docker" if topology["platform"] == "compose" else "kubernetes"
    for name, workload in workloads.items():
        if workload["kind"] in {"docker", "kubernetes"}:
            image_key = workload["imageKey"]
            if (
                not isinstance(image_key, str)
                or image_key not in images
                or workload["imageRef"] != images[image_key]
                or not RUNTIME_ID.fullmatch(str(workload["runtimeId"]))
            ):
                raise DeploymentError(f"candidate workload {name} lacks an exact candidate image/runtime binding")
        elif any(workload[field] is not None for field in ("imageKey", "imageRef", "runtimeId")):
            raise DeploymentError(f"external workload {name} must not claim a candidate image binding")
    for endpoint, image_key in EXPECTED_IMAGE_KEYS.items():
        workload = workloads[endpoints[endpoint]["workload"]]
        if workload["kind"] != platform_kind or workload["imageKey"] != image_key or workload["imageRef"] != images[image_key]:
            raise DeploymentError(f"candidate endpoint {endpoint} is not bound to images.{image_key}")
    for endpoint in ("l1_rpc_a", "l1_rpc_b"):
        workload = workloads[endpoints[endpoint]["workload"]]
        if workload["kind"] != "external" or workload["chainId"] is None:
            raise DeploymentError(f"candidate endpoint {endpoint} lacks an external chain identity")
    if workloads[endpoints["l1_rpc_a"]["workload"]]["identity"] == workloads[endpoints["l1_rpc_b"]["workload"]]["identity"]:
        raise DeploymentError("candidate L1 RPC providers are not independently identified")

    failover = topology["failoverControl"]
    if failover["activeCoordinatorId"] == failover["standbyCoordinatorId"]:
        raise DeploymentError("candidate failover coordinator identities must differ")
    witnesses = failover["activeWitnesses"]
    witness_urls = [private_url(item["url"], f"failoverControl.activeWitnesses[{index}]") for index, item in enumerate(witnesses)]
    if len({urllib.parse.urlsplit(value).netloc.lower() for value in witness_urls}) != 2:
        raise DeploymentError("candidate active witnesses must use distinct private authorities")
    witness_workloads = [item["workload"] for item in witnesses]
    if len(set(witness_workloads)) != 2:
        raise DeploymentError("candidate active witnesses must use distinct workloads")
    for name in witness_workloads:
        workload = workloads[name]
        if (
            workload["kind"] != platform_kind
            or workload["imageKey"] != "frontDoor"
            or workload["imageRef"] != images["frontDoor"]
        ):
            raise DeploymentError("candidate active witness is not bound to the exact front-door image")
    standby_health = private_url(failover["standbyHealth"]["url"], "failoverControl.standbyHealth")
    promotion_endpoint = private_url(failover["promotionEndpoint"]["url"], "failoverControl.promotionEndpoint")
    standby_workload = workloads[failover["standbyHealth"]["workload"]]
    promotion_workload = workloads[failover["promotionEndpoint"]["workload"]]
    if (
        failover["standbyHealth"]["workload"] != "coordinator-standby"
        or failover["promotionEndpoint"]["workload"] != "coordinator-standby"
        or standby_workload is not promotion_workload
        or standby_workload["kind"] != platform_kind
        or standby_workload["imageKey"] != "coordinator"
        or standby_workload["imageRef"] != images["coordinator"]
    ):
        raise DeploymentError("candidate standby health/promotion is not bound to the standby owner image")
    if urllib.parse.urlsplit(standby_health).path != "/hosting/v1/health":
        raise DeploymentError("candidate standby health path differs from the owner contract")
    if urllib.parse.urlsplit(promotion_endpoint).path != "/hosting/v1/admin/promote":
        raise DeploymentError("candidate promotion path differs from the owner contract")
    for value in witness_urls:
        if urllib.parse.urlsplit(value).path != "/hosting/v1/ready":
            raise DeploymentError("candidate active witness path differs from the owner readiness contract")

    database = topology["databaseHa"]
    if not LSN.fullmatch(database["targetLsn"]) or not LSN.fullmatch(database["replayLsn"]):
        raise DeploymentError("candidate database HA LSN is malformed")
    if lsn_value(database["replayLsn"]) < lsn_value(database["targetLsn"]):
        raise DeploymentError("candidate PostgreSQL standby replay is behind the persisted target")
    primary = workloads[database["primaryWorkload"]]
    standby = workloads[database["standbyWorkload"]]
    for label, workload in (("primary", primary), ("standby", standby)):
        if workload["kind"] != platform_kind or workload["imageKey"] != "postgres" or workload["imageRef"] != images["postgres"]:
            raise DeploymentError(f"candidate PostgreSQL {label} is not bound to images.postgres")
    if primary["identity"] == standby["identity"]:
        raise DeploymentError("candidate PostgreSQL primary and standby are the same instance")

    inspections: dict[str, object] = {}
    replication: dict[str, object] | None = None
    if inspect_live:
        network = topology["bridge"]["networkIdentity"]
        for name, workload in sorted(workloads.items()):
            if workload["kind"] == "docker":
                inspections[name] = inspect_docker(workload, network)
            elif workload["kind"] == "kubernetes":
                inspections[name] = inspect_kubernetes(workload)
            else:
                inspections[name] = {
                    "kind": "external", "identity": workload["identity"],
                    "chainId": workload["chainId"], "ready": True,
                }
        replication = inspect_replication(database, primary, standby, topology["platform"], runner)
        if (
            not isinstance(replication, dict)
            or replication.get("primaryInRecovery") is not False
            or replication.get("standbyInRecovery") is not True
            or replication.get("streamingState") not in STREAMING_STATES
        ):
            raise DeploymentError(
                "candidate claims databaseHA but the live replicationInspection section "
                "is absent or does not prove a streaming primary/standby pair"
            )
    else:
        inspections = {name: {"kind": value["kind"], "identity": value["identity"]} for name, value in workloads.items()}
    return {
        "schema": "zkdeal/candidate-private-topology-verification/v1",
        "passed": True,
        "candidateId": candidate["candidateId"],
        "candidateDescriptorSha256": candidate_sha,
        "topologySha256": sha256_file(topology_path),
        "hostedIntegrationToken": topology["hostedIntegrationToken"],
        "ownerDatabaseSchema": topology["ownerDatabaseSchema"],
        "platform": topology["platform"],
        "bridge": topology["bridge"],
        "endpoints": normalized,
        "failoverControl": {
            **failover,
            "activeWitnesses": [
                {**item, "url": witness_urls[index]} for index, item in enumerate(witnesses)
            ],
            "standbyHealth": {**failover["standbyHealth"], "url": standby_health},
            "promotionEndpoint": {**failover["promotionEndpoint"], "url": promotion_endpoint},
        },
        "adapterCapabilities": capabilities,
        "workloads": inspections,
        "databaseHa": database,
        "replicationInspection": replication,
        "liveReplicationInspected": replication is not None,
        "fixtureEndpointsAccepted": False,
        "standaloneQueueAccepted": False,
        "sharedSinglePostgresAccepted": False,
        "credentialValuesRecorded": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check",))
    parser.add_argument("--candidate-file", required=True)
    parser.add_argument("--topology-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args()
    try:
        require_container()
        candidate_path = Path(args.candidate_file)
        topology_path = Path(args.topology_file)
        if not candidate_path.is_absolute():
            candidate_path = (ROOT / candidate_path).resolve()
        if not topology_path.is_absolute():
            topology_path = (ROOT / topology_path).resolve()
        result = validate(candidate_path, topology_path, inspect_live=not args.static_only)
        output = Path(args.output)
        if not output.is_absolute():
            output = ROOT / output
        if output.exists() or output.is_symlink():
            raise DeploymentError("candidate topology verification output is write-once")
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(output, result)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (DeploymentError, OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
