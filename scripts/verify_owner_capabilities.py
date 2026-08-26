#!/usr/bin/env python3
"""Verify deployment wiring against the owner-published hosted-service manifest."""

from __future__ import annotations

import json
import re
import sys

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from common import DeploymentError, ROOT, load_json, require_container, resolve_artifacts, sha256_file


class ComposeLoader(yaml.SafeLoader):
    """Read Compose override tags as their underlying YAML value."""


def _compose_override(loader: ComposeLoader, _suffix: str, node: yaml.Node):
    if isinstance(node, MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, ScalarNode):
        return loader.construct_scalar(node)
    raise yaml.YAMLError(f"unsupported Compose tag node: {type(node).__name__}")


ComposeLoader.add_multi_constructor("!", _compose_override)


ACCEPTANCE_TOKEN = re.compile(r"^sha256:[0-9a-f]{64}$")


def prover_agent_trace_errors(umbrella, artifacts: dict[str, dict]) -> list[str]:
    errors: list[str] = []
    capability_spec = artifacts.get("prover-agent-trace-capabilities")
    fixture_spec = artifacts.get("prover-agent-trace-join-fixture")
    if not capability_spec or not fixture_spec:
        return ["prover-agent trace capability or join fixture is absent from the artifact map"]
    capability = load_json(umbrella / capability_spec["path"])
    fixture = load_json(umbrella / fixture_spec["path"])
    if capability.get("format") != "zkdeal/prover-agent-trace-capability/current" or capability.get("enabled") is not True:
        errors.append("prover-agent structured trace capability is not enabled")
    lineage = capability.get("lineageSource", {})
    if not (
        lineage.get("endpoint") == "POST /queue/v1/lease"
        and lineage.get("requiredFields") == ["correlationId", "tenantId", "roomId", "jobId"]
        and lineage.get("missingOrUnsafe") == "fail-closed-without-forwarding"
        and lineage.get("roomIdNullableForNonRoomJobs") is True
    ):
        errors.append("prover-agent leased-job trace lineage contract changed")
    record = capability.get("record", {})
    required_fields = [
        "schemaVersion", "timestamp", "correlationId", "tenantId", "roomId",
        "jobId", "operationId", "component", "event", "outcome",
    ]
    if not (
        record.get("schemaVersion") == 1
        and record.get("component") == "prover-agent"
        and record.get("fields") == required_fields
        and record.get("operationId") is None
        and record.get("maximumIdentifierCharacters") == 200
        and record.get("maximumSerializedBytes") == 2048
    ):
        errors.append("prover-agent structured trace record schema changed")
    expected_events = {
        "start": {"event": "job.start", "outcome": "started"},
        "completion": {"event": "job.complete", "outcome": "succeeded"},
        "retryableError": {"event": "job.error", "outcome": "retrying"},
        "terminalError": {"event": "job.error", "outcome": "failed"},
    }
    if capability.get("events") != expected_events:
        errors.append("prover-agent structured trace event catalog changed")
    redaction = capability.get("redaction", {})
    forbidden = {
        "authorization", "token", "request", "result", "proof", "seal",
        "errorReason", "environment",
    }
    if redaction.get("allowlistOnly") is not True or set(redaction.get("forbiddenFields", [])) != forbidden:
        errors.append("prover-agent structured trace redaction contract changed")
    metrics = capability.get("metrics", {})
    if set(metrics) != {"correlationIdAsLabel", "tenantIdAsLabel", "roomIdAsLabel", "jobIdAsLabel"} or any(metrics.values()):
        errors.append("prover-agent trace identifiers are not explicitly forbidden as metric labels")
    if capability.get("testFixture") != "test/fixtures/hosted-trace-join.json":
        errors.append("prover-agent trace capability names a different join fixture")
    if set(capability.get("exports", [])) != {"agentTraceRecord", "emitAgentTrace", "parseLeasedJob"}:
        errors.append("prover-agent trace capability exports changed")

    if fixture.get("schema") != "zkdeal/hosted-trace-join/current" or fixture.get("joinFields") != [
        "correlationId", "tenantId", "roomId", "jobId",
    ]:
        errors.append("prover-agent trace join fixture schema changed")
    records = fixture.get("records")
    if not isinstance(records, list) or len(records) < 4:
        errors.append("prover-agent trace join fixture is incomplete")
    else:
        joins = {tuple(record.get(field) for field in fixture["joinFields"]) for record in records}
        if len(joins) != 1 or any(value in (None, "") for value in next(iter(joins), ())):
            errors.append("prover-agent trace fixture does not preserve one complete join lineage")
        agent_events = {
            (record.get("event"), record.get("outcome"))
            for record in records if record.get("component") == "prover-agent"
        }
        if not {("job.start", "started"), ("job.complete", "succeeded")} <= agent_events:
            errors.append("prover-agent trace fixture omits start or completion records")
        allowed = set(required_fields)
        if any(set(record) != allowed or forbidden & set(record) for record in records):
            errors.append("prover-agent trace fixture contains missing or non-allowlisted fields")
    return errors


def hosted_integration_evidence_errors(umbrella, manifest: dict, evidence_spec: dict | None) -> list[str]:
    """Bind the hostedIntegration claim to its canonical evidence bytes."""
    errors: list[str] = []
    integration = manifest.get("managedL1Operations", {}).get("roomBatch", {}).get("hostedIntegration", {})
    expected_path = integration.get("acceptanceManifest")
    if not isinstance(evidence_spec, dict) or evidence_spec.get("path") != expected_path:
        return ["hostedIntegration acceptance manifest is absent from the required artifact map"]
    evidence_path = (umbrella / str(expected_path)).resolve()
    try:
        evidence_path.relative_to(umbrella.resolve())
    except ValueError:
        return ["hostedIntegration acceptance manifest resolves outside the umbrella"]
    if evidence_path.is_symlink() or not evidence_path.is_file():
        return ["hostedIntegration acceptance manifest is absent or not a regular file"]
    token = "sha256:" + sha256_file(evidence_path)
    if integration.get("acceptanceToken") != token:
        errors.append("hostedIntegration acceptance token does not hash its manifest bytes")
    evidence = load_json(evidence_path)
    if evidence.get("schemaVersion") != 1 or evidence.get("passed") is not True:
        errors.append("hostedIntegration evidence is not a passed schemaVersion 1 manifest")
    if evidence.get("acceptanceId") != "zkdeal-hosted-room-batch-current-zkvm":
        errors.append("hostedIntegration evidence acceptanceId changed")
    expected_contract = {
        "fixturePrepare": False,
        "hostedLegacyGroth16": False,
        "engineToBatchInputV5": True,
        "durablePostgresQueue": True,
        "externalProver": True,
        "ownerDerivedSubmitBatch": True,
        "restartResume": True,
        "canonicalFinalityEvidence": True,
    }
    if evidence.get("contract") != expected_contract:
        errors.append("hostedIntegration evidence contract is incomplete or changed")
    gates = evidence.get("gates", {})
    required_gates = {
        "ownerJointHttp", "l2Engine", "roomNode", "roomClient",
        "zkvmTypescript", "rustLivePrepare", "rustOwnerVectors",
    }
    if not isinstance(gates, dict) or set(gates) != required_gates:
        errors.append("hostedIntegration evidence gate set is incomplete or changed")
    else:
        for name, gate in gates.items():
            if not isinstance(gate, dict) or gate.get("passed") is not True or int(gate.get("tests", 0)) < 1:
                errors.append(f"hostedIntegration evidence gate is not green: {name}")
    proof_gates = evidence.get("externalProofGates", {})
    if not (
        proof_gates.get("routineSemanticCoverageComplete") is True
        and int(proof_gates.get("realRisc0ProofCases", 0)) >= 4
        and int(proof_gates.get("realLigeroProofCases", 0)) >= 1
        and isinstance(proof_gates.get("releaseRequirement"), str)
        and proof_gates.get("releaseRequirement")
    ):
        errors.append("hostedIntegration external-proof evidence is incomplete")
    sources = evidence.get("sourceArtifacts")
    if not isinstance(sources, dict) or not sources:
        errors.append("hostedIntegration evidence has no source-artifact hash bindings")
    else:
        for relative, digest in sources.items():
            source = (umbrella / str(relative)).resolve()
            try:
                source.relative_to(umbrella.resolve())
            except ValueError:
                errors.append(f"hostedIntegration evidence source resolves outside umbrella: {relative}")
                continue
            if (
                source.is_symlink() or not source.is_file()
                or not re.fullmatch(r"[0-9a-f]{64}", str(digest))
                or sha256_file(source) != digest
            ):
                errors.append(f"hostedIntegration evidence source hash differs: {relative}")
    return errors


def headless_hosted_integration_errors(
    headless_manifest: dict,
    owner_manifest: dict,
    capability_pins: tuple[tuple[str, dict], ...],
) -> list[str]:
    """Require an owner-published, live-tested hosted room-node data path.

    The standalone room-node lifecycle and restart-safety contract is useful,
    but it is not proof that jobs flow through the hosted admission lease,
    PostgreSQL WAL/queue, and authenticated external CUDA prover.  The owner
    may publish this object in the room-node manifest or alongside the hosted
    service entrypoint; absence is intentionally release-blocking.
    """
    errors: list[str] = []
    room_batch = owner_manifest.get("managedL1Operations", {}).get("roomBatch", {})
    integration = room_batch.get("hostedIntegration") if isinstance(room_batch, dict) else None
    if not isinstance(integration, dict):
        errors.append(
            "owner hosted room-batch has no hostedIntegration capability for the "
            "current zkVM BatchInputV5 queue/prove/submitBatch path"
        )
    else:
        for field in (
            "enabled", "engineToBatchInputV5", "durablePostgresQueue",
            "externalProver", "restartResume",
        ):
            if integration.get(field) is not True:
                errors.append(f"owner headless hostedIntegration is not green: {field}")
        for field in ("fixturePrepare", "hostedLegacyGroth16"):
            if integration.get(field) is not False:
                errors.append(f"owner headless hostedIntegration must explicitly disable {field}")
        if not ACCEPTANCE_TOKEN.fullmatch(str(integration.get("acceptanceToken", ""))):
            errors.append("owner headless hostedIntegration lacks a SHA-256 joint-acceptance token")
        if room_batch.get("enabled") is not True:
            errors.append("owner managed room-batch publication is not enabled")

    hosted = headless_manifest.get("modes", {}).get("hosted", {})
    admissions = hosted.get("admissions", {}) if isinstance(hosted, dict) else {}
    queue = hosted.get("proofQueue", {}) if isinstance(hosted, dict) else {}
    l1 = hosted.get("l1", {}) if isinstance(hosted, dict) else {}
    if not (
        hosted.get("production") is True
        and admissions.get("lease") == "/hosting/v1/admissions/:roomId/lease"
        and admissions.get("ack") == "/hosting/v1/admissions/:roomId/ack"
        and admissions.get("durableContiguousPrefix") is True
    ):
        errors.append("owner room-node hosted admission lease/ACK contract is incomplete")
    if not (
        queue.get("transport") == "remote-postgresql-minio"
        and queue.get("contentDigestVerified") is True
        and queue.get("gpuOnlyStage") == "/v5/rooms/prove"
        and set(queue.get("endpoints", [])) == {
            "/hosting/v1/rooms/prepare-batch", "/v5/rooms/prove", "/v5/rooms/verify",
        }
    ):
        errors.append("owner room-node hosted current-zkVM proof queue contract is incomplete")
    if not (
        l1.get("directSender") is False
        and l1.get("operation") == "RoomManager.submitBatch"
        and l1.get("durableNonceSignerWatcher") is True
        and l1.get("requireFinalized") is True
        and hosted.get("fixturePrepare") is False
        and hosted.get("legacyGroth16Checkpoint") is False
    ):
        errors.append("owner room-node hosted submitBatch/no-fixture/no-legacy contract is incomplete")

    expected_pins = {
        "hostedAdmissionLease": True,
        "hostedRoomBatchEnabled": True,
        "hostedEngineToBatchInputV5": True,
        "hostedDurablePostgresQueue": True,
        "hostedExternalProver": True,
        "hostedRestartResume": True,
        "hostedFixturePrepare": False,
        "hostedLegacyGroth16": False,
    }
    for label, pins in capability_pins:
        for field, expected in expected_pins.items():
            if pins.get(field) != expected:
                errors.append(f"{label} headless capability pin differs: {field}")
        token = pins.get("hostedIntegrationAcceptanceToken")
        if not isinstance(integration, dict) or token != integration.get("acceptanceToken"):
            errors.append(f"{label} headless capability pin differs: hostedIntegrationAcceptanceToken")
    return errors


def check() -> list[str]:
    umbrella, artifacts = resolve_artifacts()
    by_id = {item["id"]: item for item in artifacts}
    manifest_spec = by_id.get("hosted-service-capabilities")
    if not manifest_spec:
        return ["hosted-service-capabilities is absent from config/artifacts.json"]
    manifest = load_json(umbrella / manifest_spec["path"])
    headless_spec = by_id.get("headless-room-node-capabilities")
    if not headless_spec:
        return ["headless-room-node-capabilities is absent from config/artifacts.json"]
    headless_manifest = load_json(umbrella / headless_spec["path"])
    values = yaml.safe_load((ROOT / "helm" / "zkdeal" / "values.yaml").read_text(encoding="utf-8"))
    production = yaml.safe_load((ROOT / "helm" / "zkdeal" / "values-production.example.yaml").read_text(encoding="utf-8"))
    compose = yaml.load(
        (ROOT / "compose" / "compose.hosted.yaml").read_text(encoding="utf-8"),
        Loader=ComposeLoader,
    )
    errors: list[str] = []
    errors.extend(prover_agent_trace_errors(umbrella, by_id))
    errors.extend(hosted_integration_evidence_errors(
        umbrella,
        manifest,
        by_id.get("room-batch-hosted-integration-evidence"),
    ))
    owner_image = manifest.get("image", {})
    if not isinstance(owner_image, dict) or any(owner_image.get(key) != value for key, value in {
        "dockerfile": "web2-api/server/Dockerfile", "context": ".",
    }.items()):
        errors.append("owner image declaration changed")
    expected = {
        "coordinator": "coordinatorActive",
        "indexer": "indexer",
        "reconciler": "reconciler",
        "publisher": "publisher",
        "autoClaimer": "autoClaimer",
        "capacity": "capacityController",
    }
    for owner_name, helm_name in expected.items():
        owner = manifest.get("entrypoints", {}).get(owner_name)
        component = values.get("components", {}).get(helm_name)
        if not isinstance(owner, dict) or not isinstance(component, dict):
            errors.append(f"missing owner or Helm component: {owner_name}")
            continue
        if component.get("command") != owner.get("command"):
            errors.append(f"Helm {helm_name} command differs from owner manifest")
        expected_port = owner.get("port", owner.get("defaultPort"))
        if component.get("port") != expected_port:
            errors.append(f"Helm {helm_name} port differs from owner manifest")
        if component.get("healthPath") != owner.get("health"):
            errors.append(f"Helm {helm_name} health path differs from owner manifest")
        if component.get("readinessPath") != owner.get("readiness"):
            errors.append(f"Helm {helm_name} readiness path differs from owner manifest")
        if owner_name in {"indexer", "reconciler", "publisher", "autoClaimer", "capacity"}:
            if owner.get("workerIdEnvironment") != "HOSTED_WORKER_ID":
                errors.append(f"owner {owner_name} worker identity contract changed")
            if owner.get("fencing") != "component-lease-bound-to-current-coordinator-epoch":
                errors.append(f"owner {owner_name} fencing contract changed")
    auto_claimer_owner = manifest.get("entrypoints", {}).get("autoClaimer", {})
    if auto_claimer_owner.get("workerComponent") != "withdrawal":
        errors.append("owner autoClaimer is not bound to the withdrawal component lease")
    if "withdrawal" in values.get("components", {}):
        errors.append("Helm defines a duplicate withdrawal worker beside autoClaimer")
    capacity_owner = manifest.get("entrypoints", {}).get("capacity", {})
    if capacity_owner.get("workerComponent") != "capacity":
        errors.append("owner capacity entrypoint is not bound to the capacity component lease")
    capacity_contract = manifest.get("capacity", {})
    if capacity_contract.get("providerProtocol") != "zkdeal-capacity-provider-v1":
        errors.append("owner capacity provider protocol changed")
    if capacity_contract.get("providerManifest") != "web2-api/server/capabilities/capacity-provider-v1.openapi.json":
        errors.append("owner capacity provider manifest changed")
    if set(capacity_contract.get("requiredEnvironment", [])) != {
        "CAPACITY_CONTROLLER_ENABLED", "CAPACITY_PROVIDER_URL", "CAPACITY_PROVIDER_AUTH_TOKEN",
    }:
        errors.append("owner capacity required environment changed")
    for field in (
        "durableProviderOperations", "immutableIdempotencyKeys", "queueDemandSignals",
        "canonicalDeadlineUrgency", "staleProfileScaleDownGate",
    ):
        if capacity_contract.get(field) is not True:
            errors.append(f"owner capacity capability is not green: {field}")
    if capacity_contract.get("retryAndAlertState") != "postgresql":
        errors.append("owner capacity retry/alert state is not PostgreSQL-authoritative")
    for name in ("indexer", "reconciler", "publisher", "autoClaimer", "capacityController"):
        if production["components"][name].get("enabled") and not production["ownerCapabilities"].get("delegatedWorkerMutations"):
            errors.append(f"production enables unsafe independent {name} worker")
        if production["components"][name]["image"].get("repository") != production["components"]["coordinatorActive"]["image"].get("repository"):
            errors.append(f"production {name} does not consume the owner coordinator image")
    if values["ownerCapabilities"].get("protocolJournalSchema") != manifest.get("protocolJournalSchema"):
        errors.append("protocol journal schema pin differs from owner manifest")
    if values["ownerCapabilities"].get("databaseSchema") != manifest.get("databaseSchema"):
        errors.append("database schema pin differs from owner manifest")
    filesystem = manifest.get("filesystem", {})
    expected_filesystem = {
        "applicationStateAuthority": False,
        "dataDirRequired": False,
        "readOnlyRootSupported": True,
        "legacyObserverRoutes": "retired-410",
        "observationAuthority": "postgresql-canonical-projection",
    }
    for field, expected_value in expected_filesystem.items():
        if filesystem.get(field) != expected_value:
            errors.append(f"owner filesystem authority capability differs: {field}")
    helm_filesystem = {
        "filesystemApplicationStateAuthority": filesystem.get("applicationStateAuthority"),
        "dataDirRequired": filesystem.get("dataDirRequired"),
        "readOnlyRootSupported": filesystem.get("readOnlyRootSupported"),
        "legacyObserverRoutes": filesystem.get("legacyObserverRoutes"),
        "observationAuthority": filesystem.get("observationAuthority"),
    }
    for field, expected_value in helm_filesystem.items():
        if values["ownerCapabilities"].get(field) != expected_value:
            errors.append(f"Helm filesystem authority capability pin differs: {field}")
    authority = manifest.get("authority", {})
    if authority.get("workerDelegation") != "component-lease-bound-to-current-coordinator-epoch":
        errors.append("owner worker delegation is not bound to the active coordinator epoch")
    if authority.get("promotionInvalidatesOldWorkers") is not True:
        errors.append("owner promotion does not invalidate stale worker delegations")
    if authority.get("coordinatorIdMustMatchActiveEpoch") is not True:
        errors.append("owner worker delegations do not require the active coordinator identity")
    availability = manifest.get("dataAvailability", {})
    for capability in (
        "blobBundleArchiveVerified", "cKzgProofVerification",
        "hostedPrepublishArchivePrimitive", "beaconSidecarFallback",
        "finalityGateOnArchive", "canonicalTransactionBinding",
        "reorgRetainsArchiveRetractsRequirement", "publisherBroadcastEntrypoint",
        "archiveBeforeBroadcast", "durableNonceJournal",
    ):
        if availability.get(capability) is not True:
            errors.append(f"owner data-availability capability is not green: {capability}")
    if availability.get("postFinalitySurprise") != "RECOVERY_REQUIRED":
        errors.append("publisher does not fail closed on post-finality surprise")
    if set(availability.get("requiredEnvironment", [])) != {
        "BEACON_SIDECAR_URLS", "OBJECT_STORE_ENDPOINT", "BLOB_PUBLISHER_ENABLED",
        "L1_SIGNER_URL", "L1_SIGNER_ADDRESS", "L1_SIGNER_AUTH_TOKEN",
    }:
        errors.append("owner data-availability required environment changed")
    if not production.get("chain", {}).get("beaconSidecarUrls"):
        errors.append("production does not configure beacon sidecar fallbacks")
    if not values["ownerCapabilities"].get("delegatedWorkerMutations"):
        errors.append("Helm owner capability pin does not enable delegated worker mutations")
    if values["ownerCapabilities"].get("blobArchivePrimitive") is not True:
        errors.append("Helm owner capability pin does not enable the verified blob archive primitive")
    for field, expected_value in (
        ("publisherBroadcastEntrypoint", True),
        ("publisherArchiveBeforeBroadcast", True),
        ("publisherDurableNonceJournal", True),
        ("publisherPostFinalitySurprise", "RECOVERY_REQUIRED"),
    ):
        if values["ownerCapabilities"].get(field) != expected_value:
            errors.append(f"Helm publisher capability pin differs: {field}")
    if values["ownerCapabilities"].get("autoClaimerEntrypoint") is not True:
        errors.append("Helm autoClaimer capability pin is not enabled")
    if values["ownerCapabilities"].get("autoClaimerWorkerComponent") != "withdrawal":
        errors.append("Helm autoClaimer capability pin has the wrong component lease")
    if values["ownerCapabilities"].get("withdrawalAcceptanceStatus") != "owner-live-pg-exact-byte-restart-and-external-race-gates-passed":
        errors.append("Helm withdrawal acceptance pin is not release-qualified")
    for field, expected_value in (
        ("capacityEntrypoint", True),
        ("capacityWorkerComponent", "capacity"),
        ("capacityProviderProtocol", "zkdeal-capacity-provider-v1"),
        ("capacityAcceptanceStatus", "owner-live-pg-minio-provider-and-queue-gates-passed"),
    ):
        if values["ownerCapabilities"].get(field) != expected_value:
            errors.append(f"Helm capacity capability pin differs: {field}")
    if not all(production["components"][name].get("enabled") for name in ("indexer", "reconciler", "publisher", "autoClaimer", "capacityController")):
        errors.append("production does not enable every owner-published delegated worker")
    if production["availability"].get("activeCoordinatorId") == production["availability"].get("standbyCoordinatorId"):
        errors.append("production active and standby coordinator identities are not distinct")
    compose_services = compose.get("services", {})
    compose_names = {
        "coordinator": "coordinator",
        "indexer": "indexer",
        "reconciler": "reconciler",
        "publisher": "publisher",
        "autoClaimer": "auto-claimer",
        "capacity": "capacity-controller",
    }
    for owner_name, compose_name in compose_names.items():
        owner = manifest.get("entrypoints", {}).get(owner_name, {})
        service = compose_services.get(compose_name, {})
        if service.get("command") != owner.get("command"):
            errors.append(f"Compose {compose_name} command differs from owner manifest")
        if service.get("profiles") != ["hosted"]:
            errors.append(f"Compose {compose_name} is not isolated to the hosted profile")
        if service.get("ports"):
            errors.append(f"Compose {compose_name} exposes a host port")
    compose_active_id = compose_services.get("coordinator", {}).get("environment", {}).get("COORDINATOR_ID")
    compose_standby_id = compose_services.get("coordinator-standby", {}).get("environment", {}).get("COORDINATOR_ID")
    if not compose_active_id or compose_active_id == compose_standby_id:
        errors.append("Compose active and standby coordinator identities are absent or equal")
    worker_ids: set[str] = set()
    if "withdrawal" in compose_services:
        errors.append("Compose defines a duplicate withdrawal worker beside auto-claimer")
    for name in ("indexer", "reconciler", "publisher", "auto-claimer", "capacity-controller"):
        environment = compose_services.get(name, {}).get("environment", {})
        if environment.get("COORDINATOR_ID") != compose_active_id:
            errors.append(f"Compose {name} is not bound to the active coordinator identity")
        worker_id = environment.get("HOSTED_WORKER_ID")
        if not isinstance(worker_id, str) or not worker_id:
            errors.append(f"Compose {name} lacks a stable worker identity")
        elif worker_id in worker_ids:
            errors.append("Compose hosted worker identities are not unique")
        else:
            worker_ids.add(worker_id)
    publisher_env = compose_services.get("publisher", {}).get("environment", {})
    for required in ("BLOB_PUBLISHER_ENABLED", "L1_SIGNER_URL", "L1_SIGNER_ADDRESS", "L1_SIGNER_AUTH_TOKEN"):
        if not publisher_env.get(required):
            errors.append(f"Compose publisher lacks {required}")
    auto_claimer_env = compose_services.get("auto-claimer", {}).get("environment", {})
    for required in (
        "WITHDRAWAL_CLAIMER_ENABLED", "WITHDRAWAL_SIGNER_URL",
        "WITHDRAWAL_SIGNER_ADDRESS", "WITHDRAWAL_SIGNER_AUTH_TOKEN",
    ):
        if not auto_claimer_env.get(required):
            errors.append(f"Compose auto-claimer lacks {required}")
    if auto_claimer_env.get("COORDINATOR_ID") != compose_active_id:
        errors.append("Compose auto-claimer is not bound to the active coordinator identity")
    capacity_env = compose_services.get("capacity-controller", {}).get("environment", {})
    for required in (
        "CAPACITY_CONTROLLER_ENABLED", "CAPACITY_PROVIDER_URL", "CAPACITY_PROVIDER_AUTH_TOKEN",
    ):
        if not capacity_env.get(required):
            errors.append(f"Compose capacity-controller lacks {required}")
    if capacity_env.get("COORDINATOR_ID") != compose_active_id:
        errors.append("Compose capacity-controller is not bound to the active coordinator identity")
    owner_headless = manifest.get("entrypoints", {}).get("headlessRoomNode", {})
    headless_entrypoint = headless_manifest.get("entrypoint", {})
    errors.extend(headless_hosted_integration_errors(
        headless_manifest,
        manifest,
        (
            ("Helm defaults", values.get("ownerCapabilities", {})),
            ("Helm production example", production.get("ownerCapabilities", {})),
        ),
    ))
    for field, expected_value in (
        ("image", "app-node/packages/room-node/Dockerfile"),
        ("capabilityManifest", "app-node/packages/room-node/capabilities/room-node.json"),
    ):
        if owner_headless.get(field) != expected_value:
            errors.append(f"hosted capability headlessRoomNode {field} changed")
    for field in ("command", "port", "health", "readiness", "metrics", "capabilities"):
        if owner_headless.get(field) != headless_entrypoint.get(field):
            errors.append(f"headless manifest entrypoint differs from hosted capability: {field}")
    if headless_manifest.get("image", {}).get("dockerfile") != "app-node/packages/room-node/Dockerfile":
        errors.append("headless owner image declaration changed")
    restart_safety = headless_manifest.get("restartSafety", {})
    if not all(restart_safety.get(name) is True for name in (
        "exclusiveStateLock", "checksummedAtomicClientState",
        "checksummedAtomicHostedLifecycle", "correlationAndIdempotencyPersisted",
        "queueJobAndResultDigestsPersisted", "ownerOperationIdPersisted",
        "privateKeysExcludedFromState",
    )):
        errors.append("headless owner restart-safety contract is incomplete")
    custody = headless_manifest.get("keyCustody", {})
    if (
        custody.get("source") != "operator-mounted-0600-files"
        or custody.get("privateKeyEnvironment") is not False
        or custody.get("hostedL1TransactionKey") is not False
    ):
        errors.append("headless owner key-custody contract changed")
    helm_headless = values.get("components", {}).get("headlessNode", {})
    if helm_headless.get("command") != headless_entrypoint.get("command"):
        errors.append("Helm headlessNode command differs from owner manifest")
    if helm_headless.get("port") != headless_entrypoint.get("port"):
        errors.append("Helm headlessNode port differs from owner manifest")
    if helm_headless.get("healthPath") != headless_entrypoint.get("health") or helm_headless.get("readinessPath") != headless_entrypoint.get("readiness"):
        errors.append("Helm headlessNode probes differ from owner manifest")
    if helm_headless.get("secretEnv"):
        errors.append("Helm headlessNode injects environment secrets")
    if not production.get("components", {}).get("headlessNode", {}).get("enabled"):
        errors.append("production does not enable the owner-published headless room-node")
    compose_headless = compose_services.get("headless-node", {})
    if compose_headless.get("command") != headless_entrypoint.get("command"):
        errors.append("Compose headless-node command differs from owner manifest")
    if compose_headless.get("build", {}).get("dockerfile") != "app-node/packages/room-node/Dockerfile":
        errors.append("Compose headless-node does not build the owner image")
    if set(compose_headless.get("environment", {})) != {"ROOM_NODE_CONFIG_PATH"}:
        errors.append("Compose headless-node must receive config path only, not private key environment")
    return errors


def main() -> int:
    try:
        require_container()
        errors = check()
        if errors:
            print(json.dumps({"valid": False, "errors": errors}, indent=2), file=sys.stderr)
            return 1
        print(json.dumps({
            "valid": True,
            "owner": "web2-api/server/capabilities/hosted-service.json",
            "workersDeployable": True,
            "blobArchivePrimitiveDeployable": True,
            "publisherDeployable": True,
            "withdrawalAutoClaimerDeployable": True,
            "capacityControllerDeployable": True,
            "headlessRoomNodeDeployable": True,
            "headlessHostedPathAcceptance": "sha256-bound-owner-joint-evidence",
        }))
        return 0
    except (DeploymentError, OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
