#!/usr/bin/env python3
"""Digest-gated launcher for every deployment-owned Kurtosis package."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

from common import DeploymentError, ROOT, require_container
from production_compose import validate_reference


REQUIRED_IMAGES = {
    "local": ("server", "headless", "prover", "agent", "postgres", "minio", "minio_client"),
    "failover": ("failover_runner",),
    "soak": ("runner",),
    "acceptance-matrix": ("acceptance_runner",),
}

# Static args-file shape per package. Nested file/hash bindings are enforced by
# the materializers; the key set itself is checkable before the candidate
# topology receipt exists.
EXPECTED_ARGS_KEYS = {
    "local": {
        "images", "chain_id", "l1_rpc_url_a", "l1_rpc_url_b",
        "l1_rpc_provider_ids", "beacon_sidecar_urls", "room_manager",
        "room_pool", "access_token", "prover_mode", "node_id",
        "indexer_bootstrap_block", "secrets", "headless",
    },
    "failover": {
        "images", "candidate_id", "hosted_integration_token",
        "topology_verification_file", "topology_verification_sha256",
        "plan_file", "plan_sha256", "auth_files",
        "promotion_principal_file", "promotion_principal_sha256", "failover_command",
    },
    "soak": {
        "images", "candidate_id", "hosted_integration_token",
        "topology_verification_file", "topology_verification_sha256",
        "manifest_file", "manifest_sha256", "owner_command_file", "owner_command_sha256",
        "owner_driver_source_sha256", "auth_files", "auth_token_sha256",
        "deployment_addresses", "duration_seconds", "soak_command",
    },
    "acceptance-matrix": {
        "images", "candidate_id", "hosted_integration_token",
        "topology_verification_file", "topology_verification_sha256",
        "plan_file", "plan_sha256", "auth_files", "commands",
    },
}

LOCAL_PROVER_MODES = {"gpu-prover", "declared-fixture"}
LOCAL_SECRET_KEYS = {
    "postgres_password", "minio_root_user", "minio_root_password",
    "api_key_pepper", "indexer_token", "admission_token",
    "queue_node_token", "prover_token",
}
LOCAL_HEADLESS_KEYS = {"config_json", "keys_json", "control_token"}

SAFE_COMMAND = re.compile(r"^[A-Za-z0-9_./,:=-]+(?: [A-Za-z0-9_./,:=-]+)*$")
ACCEPTANCE_ASSERTIONS = {
    "reorg": ("--assert-rollback-and-retraction",),
    "rpc-disagreement": ("--assert-fail-closed",),
    "split-brain-promotion": ("--kill-active", "--assert-fence", "--assert-rto-seconds", "300"),
    "database-object-restore": ("--fresh-database", "--fresh-object-store", "--assert-hashes"),
    "sponsorship": ("--assert-tenant-isolation", "--assert-idempotency"),
    "queue-congestion": ("--assert-fairness", "--assert-backoff"),
    "headless-restart": ("--assert-state-continuity", "--assert-single-writer"),
    "blob": ("--assert-hash-binding", "--assert-restart-resume"),
    "partial-aggregate": ("--assert-member-outcomes",),
    "renewal": ("--assert-deadline-and-metering",),
    "withdrawal": ("--assert-finality", "--assert-replay-denial"),
    "rpc-load-shadow": ("--assert-latency-budget-ms", "500", "--assert-mismatch-rate", "0"),
    "sse-load-shadow": ("--assert-reconnect", "--assert-backpressure", "--assert-subscriber-cap"),
    "indexer-load-shadow": ("--assert-rollback", "--assert-lag-budget-blocks", "8"),
    "admission-load-shadow": ("--assert-wal-durable", "--assert-backpressure", "--assert-cap"),
    "scheduler-load-shadow": ("--assert-mixed-tenant-fairness", "--assert-deadline-budget"),
    "projection-parity-shadow": ("--assert-projection-parity", "--assert-mismatch-rate", "0"),
    "prover-agent-trace-join": ("--assert-coordinator-agent-prover-heartbeat",),
}

ACCEPTANCE_ENDPOINTS = {
    "coordinator", "indexer", "queue", "headless", "l1_rpc_a", "l1_rpc_b",
    "fault", "backup", "failover", "prover", "logs",
}
ACCEPTANCE_AUTH = {
    "admin", "tenant_a", "tenant_b", "node_a", "node_b", "l1_liveness",
    "l1_room", "l1_aggregate", "l1_sponsor", "withdrawal", "fault_control",
    "backup_restore", "failover_control", "failover_approval",
}
EPHEMERAL_TOKEN = re.compile(r"^eph_[A-Za-z0-9_-]{28,120}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
HOSTED_TOKEN = re.compile(r"^sha256:[0-9a-f]{64}$")
CANDIDATE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{7,79}$")
ETH_ADDRESS = re.compile(r"^0x[0-9a-f]{40}$")
SOAK_AUTH = {
    "tenant_a", "tenant_b", "node_a", "node_b", "l1_liveness", "l1_room",
    "l1_aggregate", "l1_sponsor", "withdrawal", "fault_control",
    "backup_restore", "failover_control", "failover_approval",
}
# The owner soak driver addresses these deployed identities directly (env
# fallback for GET /hosting/v1/capabilities bodies that do not publish an
# `addresses` block).
SOAK_DEPLOYMENT_ADDRESSES = {
    "roomManager", "operationsAccount", "roomPool", "sponsorAccount",
}


def validate_assertion_command(label: str, value: object, executable: str, markers: tuple[str, ...]) -> str:
    if not isinstance(value, str) or not SAFE_COMMAND.fullmatch(value):
        raise DeploymentError(f"{label} must be a safe, whitespace-delimited owner assertion command")
    argv = value.split()
    if not argv or argv[0] != executable:
        raise DeploymentError(f"{label} must execute {executable}")
    missing = [marker for marker in markers if marker not in argv]
    if missing:
        raise DeploymentError(f"{label} lacks required assertion flags: {missing}")
    return value


def validate_commands(package: str, payload: dict[str, object]) -> None:
    if package == "local":
        return
    if package == "failover":
        validate_assertion_command(
            "failover_command", payload.get("failover_command"), "/opt/zkdeal-failover",
            (
                "--terminate-active", "--persist-primary-target-lsn", "--assert-standby-replay",
                "--promote", "--assert-stale-writer-denied", "--assert-rto-seconds", "300",
            ),
        )
        return
    if package == "soak":
        validate_assertion_command(
            "soak_command", payload.get("soak_command"), "/opt/zkdeal-soak",
            (
                "--submit-real-proof-jobs", "--restart", "--assert-durable-results,cursors,nonces,charges,sealed-output,safety,claims,fairness,deadlines",
                "--bounded-backoff", "--emit-evidence-closure",
            ),
        )
        return
    commands = payload.get("commands")
    if not isinstance(commands, dict) or set(commands) != set(ACCEPTANCE_ASSERTIONS):
        raise DeploymentError("acceptance-matrix commands must exactly cover every required scenario")
    for scenario, markers in ACCEPTANCE_ASSERTIONS.items():
        command = validate_assertion_command(
            f"commands.{scenario}", commands.get(scenario), "/opt/zkdeal-acceptance", (scenario, *markers),
        )
        if command.split()[1] != scenario:
            raise DeploymentError(f"commands.{scenario} must select scenario {scenario} as argv[1]")


def strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DeploymentError(f"JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def read_regular(path: Path, label: str, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise DeploymentError(f"{label} must be an existing regular non-symlink file")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DeploymentError(f"cannot read {label}: {exc}") from exc
    if not raw or len(raw) > maximum:
        raise DeploymentError(f"{label} must contain 1..{maximum} bytes")
    return raw


def resolve_input(args_path: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise DeploymentError(f"{label} must be a file path")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = args_path.parent / candidate
    return candidate


def acceptance_module():
    module_path = ROOT / "acceptance-runner" / "zkdeal_acceptance.py"
    spec = importlib.util.spec_from_file_location("zkdeal_acceptance_kurtosis_gate", module_path)
    if spec is None or spec.loader is None:
        raise DeploymentError("cannot load the first-party acceptance verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def normalized_endpoint(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise DeploymentError(f"{label} must be an HTTP(S) URL")
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise DeploymentError(f"{label} is malformed") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise DeploymentError(f"{label} must be an HTTP(S) URL without user information")
    if parsed.query or parsed.fragment:
        raise DeploymentError(f"{label} must not contain a query or fragment")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def bound_json_document(
    args_path: Path, path_value: object, expected_hash: object, label: str, maximum: int,
) -> tuple[Path, bytes, dict[str, object]]:
    if not isinstance(expected_hash, str) or not SHA256.fullmatch(expected_hash):
        raise DeploymentError(f"{label} SHA-256 is malformed")
    path = resolve_input(args_path, path_value, label)
    raw = read_regular(path, label, maximum)
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise DeploymentError(f"{label} bytes differ from their SHA-256 binding")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentError(f"{label} is not strict UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise DeploymentError(f"{label} must contain a JSON object")
    return path, raw, value


def validate_candidate_identity(payload: dict[str, object]) -> tuple[str, str]:
    candidate_id = payload.get("candidate_id")
    hosted_token = payload.get("hosted_integration_token")
    if not isinstance(candidate_id, str) or not CANDIDATE_ID.fullmatch(candidate_id):
        raise DeploymentError("candidate_id is malformed")
    if not isinstance(hosted_token, str) or not HOSTED_TOKEN.fullmatch(hosted_token):
        raise DeploymentError("hosted_integration_token must be sha256:<64 lowercase hex>")
    return candidate_id, hosted_token


def load_topology_verification(
    payload: dict[str, object], args_path: Path, candidate_id: str, hosted_token: str,
) -> tuple[dict[str, str], dict[str, object], str, int]:
    _path, _raw, value = bound_json_document(
        args_path, payload.get("topology_verification_file"),
        payload.get("topology_verification_sha256"), "candidate topology verification", 4 * 1024 * 1024,
    )
    if (
        value.get("schema") != "zkdeal/candidate-private-topology-verification/v1"
        or value.get("passed") is not True
        or value.get("candidateId") != candidate_id
        or value.get("hostedIntegrationToken") != hosted_token
        or value.get("fixtureEndpointsAccepted") is not False
        or value.get("standaloneQueueAccepted") is not False
        or value.get("sharedSinglePostgresAccepted") is not False
        or value.get("credentialValuesRecorded") is not False
    ):
        raise DeploymentError("candidate topology verification is not a release-eligible exact-stack receipt")
    if not isinstance(value.get("ownerDatabaseSchema"), int) or value["ownerDatabaseSchema"] < 1:
        raise DeploymentError("candidate topology verification lacks ownerDatabaseSchema")
    endpoints = value.get("endpoints")
    if not isinstance(endpoints, dict) or set(endpoints) != ACCEPTANCE_ENDPOINTS:
        raise DeploymentError("candidate topology verification endpoint set differs")
    normalized = {name: normalized_endpoint(endpoint, f"topology endpoints.{name}") for name, endpoint in endpoints.items()}
    if normalized["queue"] != normalized["coordinator"]:
        raise DeploymentError("candidate topology verification uses a standalone queue")
    control = value.get("failoverControl")
    if not isinstance(control, dict):
        raise DeploymentError("candidate topology verification lacks failoverControl")
    required_control = {
        "activeCoordinatorId", "standbyCoordinatorId", "activeWitnesses",
        "standbyHealth", "promotionEndpoint", "failureThreshold", "maxRtoSeconds",
        "independentWitnesses", "scopedApprovalRequired", "standbySignerlessBeforeFence",
    }
    if set(control) != required_control:
        raise DeploymentError("candidate topology failoverControl fields differ")
    if (
        control.get("independentWitnesses") is not True
        or control.get("scopedApprovalRequired") is not True
        or control.get("standbySignerlessBeforeFence") is not True
    ):
        raise DeploymentError("candidate topology failover safety controls are not enabled")
    witnesses = control.get("activeWitnesses")
    if not isinstance(witnesses, list) or len(witnesses) != 2:
        raise DeploymentError("candidate topology requires exactly two active witnesses")
    witness_urls: list[str] = []
    witness_workloads: set[str] = set()
    for index, item in enumerate(witnesses):
        if not isinstance(item, dict) or set(item) != {"url", "workload"}:
            raise DeploymentError("candidate topology active witness binding is malformed")
        witness_urls.append(normalized_endpoint(item["url"], f"activeWitnesses[{index}].url"))
        witness_workloads.add(str(item["workload"]))
    if len(witness_workloads) != 2 or len({urllib.parse.urlsplit(url).netloc.lower() for url in witness_urls}) != 2:
        raise DeploymentError("candidate topology active witnesses are not independent")
    standby = control.get("standbyHealth")
    promotion = control.get("promotionEndpoint")
    if (
        not isinstance(standby, dict) or set(standby) != {"url", "workload"}
        or not isinstance(promotion, dict) or set(promotion) != {"url", "workload"}
        or standby.get("workload") != promotion.get("workload")
    ):
        raise DeploymentError("candidate topology standby health/promotion binding is malformed")
    standby_url = normalized_endpoint(standby["url"], "failoverControl.standbyHealth.url")
    promotion_url = normalized_endpoint(promotion["url"], "failoverControl.promotionEndpoint.url")
    failure_threshold = control.get("failureThreshold")
    max_rto = control.get("maxRtoSeconds")
    if (
        not isinstance(failure_threshold, int) or isinstance(failure_threshold, bool)
        or not 3 <= failure_threshold <= 20
        or not isinstance(max_rto, int) or isinstance(max_rto, bool)
        or not 30 <= max_rto <= 300
    ):
        raise DeploymentError("candidate topology failover threshold/RTO is out of bounds")
    failover_control = {
        "active_coordinator_id": control["activeCoordinatorId"],
        "standby_coordinator_id": control["standbyCoordinatorId"],
        "active_witness_urls": witness_urls,
        "standby_health_url": standby_url,
        "promotion_endpoint": promotion_url,
        "provider_operation_url": normalized["failover"] + "/v1/failovers",
        "failure_threshold": failure_threshold,
        "max_rto_seconds": max_rto,
    }
    return (
        normalized, failover_control, str(payload["topology_verification_sha256"]),
        int(value["ownerDatabaseSchema"]),
    )


def load_acceptance_plan(
    payload: dict[str, object], args_path: Path, candidate_id: str, hosted_token: str,
) -> tuple[bytes, dict[str, object]]:
    _path, raw, plan = bound_json_document(
        args_path, payload.get("plan_file"), payload.get("plan_sha256"),
        "acceptance plan", 4 * 1024 * 1024,
    )
    if (
        set(plan) != {
            "schemaVersion", "candidateId", "hostedIntegrationToken", "ownerDatabaseSchema",
            "authTokenSha256", "scenarios",
        }
        or plan.get("schemaVersion") != 1
        or plan.get("candidateId") != candidate_id
        or plan.get("hostedIntegrationToken") != hosted_token
    ):
        raise DeploymentError("acceptance plan candidate/schema binding differs")
    return raw, plan


def materialize_token_files(
    payload: dict[str, object], args_path: Path, aliases: set[str], expected_hashes: dict[str, object],
) -> dict[str, str]:
    auth_files = payload.get("auth_files")
    if not isinstance(auth_files, dict) or set(auth_files) != aliases:
        raise DeploymentError("auth_files must exactly cover the required scoped roles")
    values: dict[str, str] = {}
    for alias in sorted(aliases):
        expected = expected_hashes.get(alias)
        if not isinstance(expected, str) or not SHA256.fullmatch(expected):
            raise DeploymentError(f"credential hash for {alias} is malformed")
        path = resolve_input(args_path, auth_files[alias], f"auth_files.{alias}")
        raw = read_regular(path, f"auth_files.{alias}", 4096)
        try:
            token = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DeploymentError(f"auth_files.{alias} is not UTF-8") from exc
        if token != token.strip() or not EPHEMERAL_TOKEN.fullmatch(token):
            raise DeploymentError(f"auth_files.{alias} must contain exact eph_ token bytes without whitespace")
        if hashlib.sha256(raw).hexdigest() != expected:
            raise DeploymentError(f"auth_files.{alias} differs from its hash binding")
        values[alias] = token
    if len(set(values.values())) != len(values):
        raise DeploymentError("each scoped role must use an independently revocable token")
    return values


def validate_acceptance_policy(
    payload: dict[str, object], args_path: Path,
) -> tuple[str, str, bytes, dict[str, object], dict[str, str], dict[str, list[str]]]:
    """Candidate-independent acceptance policy: args shape, plan binding,
    scenario shapes, and role credential bindings. Runnable before the live
    candidate topology receipt exists."""
    expected_keys = EXPECTED_ARGS_KEYS["acceptance-matrix"]
    if set(payload) != expected_keys:
        raise DeploymentError(
            f"acceptance-matrix args differ: missing={sorted(expected_keys-set(payload))} "
            f"extra={sorted(set(payload)-expected_keys)}"
        )
    candidate_id = payload.get("candidate_id")
    hosted_token = payload.get("hosted_integration_token")
    plan_hash = payload.get("plan_sha256")
    if not isinstance(candidate_id, str) or not CANDIDATE_ID.fullmatch(candidate_id):
        raise DeploymentError("candidate_id is malformed")
    if not isinstance(hosted_token, str) or not HOSTED_TOKEN.fullmatch(hosted_token):
        raise DeploymentError("hosted_integration_token must be sha256:<64 lowercase hex>")
    if not isinstance(plan_hash, str) or not SHA256.fullmatch(plan_hash):
        raise DeploymentError("plan_sha256 must be 64 lowercase hex characters")
    plan_path = resolve_input(args_path, payload.get("plan_file"), "plan_file")
    raw_plan = read_regular(plan_path, "acceptance plan", 4 * 1024 * 1024)
    if hashlib.sha256(raw_plan).hexdigest() != plan_hash:
        raise DeploymentError("acceptance plan bytes differ from plan_sha256")
    try:
        plan = json.loads(raw_plan.decode("utf-8"), object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentError(f"acceptance plan is not strict UTF-8 JSON: {exc}") from exc
    if not isinstance(plan, dict) or set(plan) != {
        "schemaVersion", "candidateId", "hostedIntegrationToken", "ownerDatabaseSchema",
        "authTokenSha256", "scenarios",
    } or plan.get("schemaVersion") != 1:
        raise DeploymentError("acceptance plan must be an exact schemaVersion 1 object")
    if plan.get("candidateId") != candidate_id or plan.get("hostedIntegrationToken") != hosted_token:
        raise DeploymentError("acceptance plan candidate or hostedIntegration token differs from args")
    if not isinstance(plan.get("ownerDatabaseSchema"), int) or plan["ownerDatabaseSchema"] < 1:
        raise DeploymentError("acceptance plan ownerDatabaseSchema must be a positive integer")
    scenarios = plan.get("scenarios")
    if not isinstance(scenarios, dict) or set(scenarios) != set(ACCEPTANCE_ASSERTIONS):
        raise DeploymentError("acceptance plan must exactly cover every required scenario")
    verifier = acceptance_module()
    scenario_auth: dict[str, list[str]] = {}
    used_auth: set[str] = set()
    for scenario in ACCEPTANCE_ASSERTIONS:
        try:
            steps, _ = verifier.validate_scenario_shape(scenario, scenarios[scenario])
        except Exception as exc:  # verifier owns exact evidence/source rules
            raise DeploymentError(f"acceptance plan scenario {scenario} is invalid: {exc}") from exc
        aliases = sorted({
            str(step[field])
            for step in steps
            for field in ("auth", "approvalAuth")
            if step.get(field) is not None
        })
        if any(alias not in ACCEPTANCE_AUTH for alias in aliases):
            raise DeploymentError(f"acceptance plan scenario {scenario} uses an unknown auth alias")
        scenario_auth[scenario] = aliases
        used_auth.update(aliases)
    auth_hashes = plan.get("authTokenSha256")
    if not isinstance(auth_hashes, dict) or set(auth_hashes) != used_auth:
        raise DeploymentError("acceptance plan authTokenSha256 must exactly cover used role credentials")
    auth_files = payload.get("auth_files")
    if not isinstance(auth_files, dict) or set(auth_files) != used_auth:
        raise DeploymentError("auth_files must exactly cover role credentials used by the plan")
    auth: dict[str, str] = {}
    for alias in sorted(used_auth):
        expected = auth_hashes.get(alias)
        if not isinstance(expected, str) or not SHA256.fullmatch(expected):
            raise DeploymentError(f"acceptance plan authTokenSha256.{alias} is malformed")
        token_path = resolve_input(args_path, auth_files.get(alias), f"auth_files.{alias}")
        raw_token = read_regular(token_path, f"auth_files.{alias}", 4096)
        try:
            token = raw_token.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise DeploymentError(f"auth_files.{alias} is not UTF-8") from exc
        if not EPHEMERAL_TOKEN.fullmatch(token):
            raise DeploymentError(f"auth_files.{alias} must contain an enclave-scoped eph_ token")
        if hashlib.sha256(token.encode()).hexdigest() != expected:
            raise DeploymentError(f"auth_files.{alias} differs from the plan credential binding")
        auth[alias] = token
    if len(set(auth.values())) != len(auth):
        raise DeploymentError("each acceptance role must use an independently revocable token")
    return candidate_id, hosted_token, raw_plan, plan, auth, scenario_auth


def materialize_acceptance_payload(payload: dict[str, object], args_path: Path) -> dict[str, object]:
    candidate_id, hosted_token, raw_plan, plan, auth, scenario_auth = validate_acceptance_policy(
        payload, args_path,
    )
    endpoints, _control, topology_sha, topology_database_schema = load_topology_verification(
        payload, args_path, candidate_id, hosted_token,
    )
    if plan.get("ownerDatabaseSchema") != topology_database_schema:
        raise DeploymentError("acceptance plan database schema differs from candidate topology")
    if endpoints["l1_rpc_a"] == endpoints["l1_rpc_b"]:
        raise DeploymentError("l1_rpc_a and l1_rpc_b must be distinct provider URLs")
    source_boundaries = {name: value for name, value in endpoints.items() if name != "queue"}
    origins: dict[str, str] = {}
    for name, value in source_boundaries.items():
        origin = urllib.parse.urlsplit(value).netloc.lower()
        if origin in origins:
            raise DeploymentError(
                f"acceptance endpoint {name} shares authority with {origins[origin]}; "
                "independent evidence sources require distinct service authorities"
            )
        origins[origin] = name
    expanded = dict(payload)
    expanded.pop("plan_file")
    expanded.pop("auth_files")
    expanded.pop("topology_verification_file")
    expanded["input_contract"] = "candidate-topology-expanded-v1"
    expanded["plan_json"] = raw_plan.decode("utf-8")
    expanded["auth"] = auth
    expanded["scenario_auth"] = scenario_auth
    expanded["endpoints"] = endpoints
    expanded["topology_verification_sha256"] = topology_sha
    return expanded


def materialize_failover_payload(payload: dict[str, object], args_path: Path) -> dict[str, object]:
    expected_keys = EXPECTED_ARGS_KEYS["failover"]
    if set(payload) != expected_keys:
        raise DeploymentError(
            f"failover args differ: missing={sorted(expected_keys-set(payload))} "
            f"extra={sorted(set(payload)-expected_keys)}"
        )
    candidate_id, hosted_token = validate_candidate_identity(payload)
    endpoints, control, topology_sha, topology_database_schema = load_topology_verification(
        payload, args_path, candidate_id, hosted_token,
    )
    raw_plan, plan = load_acceptance_plan(payload, args_path, candidate_id, hosted_token)
    if plan.get("ownerDatabaseSchema") != topology_database_schema:
        raise DeploymentError("failover plan database schema differs from candidate topology")
    scenarios = plan.get("scenarios")
    if not isinstance(scenarios, dict) or set(scenarios) != set(ACCEPTANCE_ASSERTIONS):
        raise DeploymentError("failover plan must be the full frozen acceptance plan")
    verifier = acceptance_module()
    try:
        steps, _refs = verifier.validate_scenario_shape(
            "split-brain-promotion", scenarios["split-brain-promotion"],
        )
    except Exception as exc:
        raise DeploymentError(f"split-brain acceptance plan is invalid: {exc}") from exc
    aliases = {
        str(step[field])
        for step in steps
        for field in ("auth", "approvalAuth")
        if step.get(field) is not None
    }
    if not {"failover_control", "failover_approval"}.issubset(aliases):
        raise DeploymentError("split-brain plan lacks separate failover control and approval roles")
    auth_hashes = plan.get("authTokenSha256")
    if not isinstance(auth_hashes, dict):
        raise DeploymentError("failover plan lacks authTokenSha256")
    auth = materialize_token_files(payload, args_path, aliases, auth_hashes)
    promotion_hash = payload.get("promotion_principal_sha256")
    if not isinstance(promotion_hash, str) or not SHA256.fullmatch(promotion_hash):
        raise DeploymentError("promotion_principal_sha256 is malformed")
    promotion_path = resolve_input(
        args_path, payload.get("promotion_principal_file"), "promotion principal",
    )
    promotion_raw = read_regular(promotion_path, "promotion principal", 4096)
    try:
        promotion_principal = promotion_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeploymentError("promotion principal is not UTF-8") from exc
    if (
        promotion_principal != promotion_principal.strip()
        or not EPHEMERAL_TOKEN.fullmatch(promotion_principal)
        or hashlib.sha256(promotion_raw).hexdigest() != promotion_hash
        or promotion_principal in auth.values()
    ):
        raise DeploymentError("promotion principal is malformed, hash-mismatched, or reused")
    expanded = {
        "images": payload["images"],
        "input_contract": "candidate-topology-expanded-v1",
        "candidate_id": candidate_id,
        "hosted_integration_token": hosted_token,
        "topology_verification_sha256": topology_sha,
        "plan_json": raw_plan.decode("utf-8"),
        "plan_sha256": payload["plan_sha256"],
        "auth": auth,
        "promotion_principal": promotion_principal,
        "endpoints": endpoints,
        "failover_control": control,
        "failover_command": payload["failover_command"],
    }
    return expanded


def materialize_soak_payload(payload: dict[str, object], args_path: Path) -> dict[str, object]:
    expected_keys = EXPECTED_ARGS_KEYS["soak"]
    if set(payload) != expected_keys:
        raise DeploymentError(
            f"soak args differ: missing={sorted(expected_keys-set(payload))} "
            f"extra={sorted(set(payload)-expected_keys)}"
        )
    candidate_id, hosted_token = validate_candidate_identity(payload)
    endpoints, control, topology_sha, _topology_database_schema = load_topology_verification(
        payload, args_path, candidate_id, hosted_token,
    )
    _manifest_path, manifest_raw, manifest = bound_json_document(
        args_path, payload.get("manifest_file"), payload.get("manifest_sha256"),
        "release soak manifest", 4 * 1024 * 1024,
    )
    from soak import validate_manifest  # local deployment verifier; no package installation
    validate_manifest(manifest)
    scenario = manifest.get("physicalScenario")
    if not isinstance(scenario, dict) or scenario.get("ownerAcceptanceToken") != hosted_token:
        raise DeploymentError("release soak manifest names another hosted-integration token")
    try:
        duration = int(str(payload.get("duration_seconds")))
    except ValueError as exc:
        raise DeploymentError("duration_seconds must be an integer") from exc
    if duration != manifest.get("durationSeconds") or not 43_200 <= duration <= 86_400:
        raise DeploymentError("release soak duration differs from manifest or is outside 12..24 hours")
    command_hash = payload.get("owner_command_sha256")
    if not isinstance(command_hash, str) or not SHA256.fullmatch(command_hash):
        raise DeploymentError("owner_command_sha256 is malformed")
    command_path = resolve_input(args_path, payload.get("owner_command_file"), "owner soak command")
    command_raw = read_regular(command_path, "owner soak command", 64 * 1024)
    if hashlib.sha256(command_raw).hexdigest() != command_hash:
        raise DeploymentError("owner soak command bytes differ from owner_command_sha256")
    try:
        owner_command = json.loads(command_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentError("owner soak command is not strict UTF-8 JSON") from exc
    if (
        not isinstance(owner_command, list) or not owner_command
        or not all(isinstance(item, str) and item for item in owner_command)
        or owner_command[0] != "/opt/zkdeal-owner-soak"
    ):
        raise DeploymentError("owner soak command must be a JSON argv beginning /opt/zkdeal-owner-soak")
    source_sha = payload.get("owner_driver_source_sha256")
    if not isinstance(source_sha, str) or not SHA256.fullmatch(source_sha):
        raise DeploymentError("owner_driver_source_sha256 is malformed")
    auth_hashes = payload.get("auth_token_sha256")
    if not isinstance(auth_hashes, dict) or set(auth_hashes) != SOAK_AUTH:
        raise DeploymentError("auth_token_sha256 must exactly cover every release-soak role")
    auth = materialize_token_files(payload, args_path, SOAK_AUTH, auth_hashes)
    supplied_addresses = payload.get("deployment_addresses")
    if not isinstance(supplied_addresses, dict) or set(supplied_addresses) != SOAK_DEPLOYMENT_ADDRESSES:
        raise DeploymentError(
            "deployment_addresses must exactly cover roomManager, operationsAccount, "
            "roomPool and sponsorAccount"
        )
    deployment_addresses = {}
    for name in sorted(SOAK_DEPLOYMENT_ADDRESSES):
        value = supplied_addresses[name]
        if not isinstance(value, str) or not ETH_ADDRESS.fullmatch(value.lower()):
            raise DeploymentError(f"deployment_addresses.{name} must be 0x plus 40 hex characters")
        deployment_addresses[name] = value.lower()
    return {
        "images": payload["images"],
        "input_contract": "candidate-topology-expanded-v1",
        "candidate_id": candidate_id,
        "hosted_integration_token": hosted_token,
        "topology_verification_sha256": topology_sha,
        "manifest_json": manifest_raw.decode("utf-8"),
        "manifest_sha256": payload["manifest_sha256"],
        "owner_command_json": command_raw.decode("utf-8"),
        "owner_command_sha256": command_hash,
        "owner_driver_source_sha256": source_sha,
        "owner_driver_image_label_sha256": source_sha,
        "auth": auth,
        "endpoints": endpoints,
        # The owner driver's coordinator-promotion fault addresses the same
        # verified topology identities the failover provider is configured
        # with; surface them so the soak package can hand them to the driver.
        "active_coordinator_id": control["active_coordinator_id"],
        "standby_coordinator_id": control["standby_coordinator_id"],
        "failover_witness_count": str(len(control["active_witness_urls"])),
        "deployment_addresses": deployment_addresses,
        "duration_seconds": str(duration),
        "soak_command": payload["soak_command"],
    }


def materialize_package_payload(
    package: str, payload: dict[str, object], args_path: Path,
) -> dict[str, object]:
    if package == "acceptance-matrix":
        return materialize_acceptance_payload(payload, args_path)
    if package == "failover":
        return materialize_failover_payload(payload, args_path)
    if package == "soak":
        return materialize_soak_payload(payload, args_path)
    return payload


def validate_local_payload(payload: dict[str, object]) -> None:
    prover_mode = payload.get("prover_mode")
    if prover_mode not in LOCAL_PROVER_MODES:
        raise DeploymentError(f"local prover_mode must be one of {sorted(LOCAL_PROVER_MODES)}")
    rpc_a = payload.get("l1_rpc_url_a")
    rpc_b = payload.get("l1_rpc_url_b")
    if (
        not isinstance(rpc_a, str) or not isinstance(rpc_b, str)
        or normalized_endpoint(rpc_a, "l1_rpc_url_a") == normalized_endpoint(rpc_b, "l1_rpc_url_b")
    ):
        raise DeploymentError("local l1_rpc_url_a and l1_rpc_url_b must be two independent providers")
    secrets = payload.get("secrets")
    if not isinstance(secrets, dict) or set(secrets) != LOCAL_SECRET_KEYS:
        raise DeploymentError("local secrets must exactly cover the hosted-plane secret roles")
    for name, value in secrets.items():
        if not isinstance(value, str) or not value:
            raise DeploymentError(f"local secrets.{name} must be a nonempty string")
    headless = payload.get("headless")
    if not isinstance(headless, dict) or set(headless) != LOCAL_HEADLESS_KEYS:
        raise DeploymentError("local headless material must supply config_json, keys_json and control_token")
    for name, value in headless.items():
        if not isinstance(value, str) or not value:
            raise DeploymentError(f"local headless.{name} must be a nonempty string")


def load_and_validate(package: str, path: Path) -> dict[str, object]:
    try:
        raw = read_regular(path, "Kurtosis args", 4 * 1024 * 1024)
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentError(f"cannot read Kurtosis args {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("images"), dict):
        raise DeploymentError("Kurtosis args must contain an images object")
    expected_keys = EXPECTED_ARGS_KEYS[package]
    if set(payload) != expected_keys:
        raise DeploymentError(
            f"Kurtosis {package} args differ: missing={sorted(expected_keys-set(payload))} "
            f"extra={sorted(set(payload)-expected_keys)}"
        )
    images = payload["images"]
    if set(images) != set(REQUIRED_IMAGES[package]):
        raise DeploymentError(
            f"Kurtosis {package} images must be exactly {sorted(REQUIRED_IMAGES[package])}"
        )
    for name, reference in images.items():
        validate_reference(reference, f"args.images.{name}")
    validate_commands(package, payload)
    if package == "local":
        validate_local_payload(payload)
    if package == "acceptance-matrix":
        validate_acceptance_policy(payload, path)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", choices=tuple(REQUIRED_IMAGES))
    parser.add_argument("--args-file", required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--enclave")
    args = parser.parse_args()
    try:
        require_container()
        args_path = Path(args.args_file)
        if not args_path.is_absolute():
            args_path = (ROOT / args_path).resolve()
        payload = load_and_validate(args.package, args_path)
        if args.check_only:
            acceptance_summary = {}
            if args.package == "acceptance-matrix":
                _cid, _token, _raw, _plan, auth, scenario_auth = validate_acceptance_policy(
                    payload, args_path,
                )
                acceptance_summary = {
                    "candidateId": payload["candidate_id"],
                    "planSha256": payload["plan_sha256"],
                    "scenarios": sorted(scenario_auth),
                    "credentialAliases": sorted(auth),
                }
            print(json.dumps({
                "kurtosisImagePolicy": "passed",
                "package": args.package,
                "images": payload["images"],
                **acceptance_summary,
                "executed": False,
            }, sort_keys=True, separators=(",", ":")))
            return 0
        if args.package == "local":
            command = ["kurtosis", "run", str(ROOT / "kurtosis" / args.package), "--args-file", str(args_path)]
            if args.enclave:
                command.extend(("--enclave", args.enclave))
            return subprocess.run(command, cwd=ROOT, check=False).returncode
        # Every stack-consuming package receives only the candidate-topology
        # expanded payload; the raw args file never reaches Kurtosis directly.
        expanded = materialize_package_payload(args.package, payload, args_path)
        with tempfile.TemporaryDirectory(prefix="zkdeal-kurtosis-expanded-") as directory:
            expanded_path = Path(directory) / "args.expanded.json"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(expanded_path, flags, 0o600)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(expanded, handle, sort_keys=True, separators=(",", ":"))
                    handle.write("\n")
            except Exception:
                expanded_path.unlink(missing_ok=True)
                raise
            command = [
                "kurtosis", "run", str(ROOT / "kurtosis" / args.package),
                "--args-file", str(expanded_path),
            ]
            if args.enclave:
                command.extend(("--enclave", args.enclave))
            return subprocess.run(command, cwd=ROOT, check=False).returncode
    except DeploymentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
