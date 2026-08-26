#!/usr/bin/env python3
"""Fail-closed preflight and ordered plan for a release candidate.

This command never infers a broad owner seal from a source snapshot.  The
operator must provide the content hashes and owner evidence token in a
candidate descriptor.  `owner` validates the immutable source boundary before
references are regenerated; `release` additionally requires every image used
by Compose, Helm, Kurtosis, security and observability to be an exact digest.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from common import DeploymentError, ROOT, load_json, project_path, require_container, sha256_file
from candidate_topology import validate as validate_candidate_topology
from production_compose import load_env_file, validate_reference
from kurtosis_run import load_and_validate as validate_kurtosis_args
from verify_artifacts import inventory
from verify_owner_capabilities import (
    check as deployment_capability_errors,
    headless_hosted_integration_errors,
    hosted_integration_evidence_errors,
)


CANDIDATE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{7,79}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
TOKEN = re.compile(r"^sha256:[0-9a-f]{64}$")
PROGRAM_ID = re.compile(r"^0x[0-9a-f]{64}$")
REQUIRED_ZKVM_LOCKED_ARTIFACTS = {
    "build/risc0/verifier/r0_wasm_verifier.js",
    "build/risc0/verifier/r0_wasm_verifier_bg.wasm",
    "build/risc0/zkdeal-r0",
    "build/risc0/zkdeal-r0-client",
    "build/risc0/capabilities-v6.json",
    "fixtures/amm-certified-v4.json",
    "fixtures/amm-terminal-close-v4.json",
}
OWNER_GATE_CONTRACT_PATH = ROOT / "config" / "owner-release-gates.json"
OWNER_GATE_CONTRACT = load_json(OWNER_GATE_CONTRACT_PATH)
if OWNER_GATE_CONTRACT.get("schemaVersion") != 1 or not isinstance(OWNER_GATE_CONTRACT.get("gates"), dict):
    raise RuntimeError("config/owner-release-gates.json is not a schemaVersion 1 gate contract")
REQUIRED_OWNER_GATES = set(OWNER_GATE_CONTRACT["gates"])
OWNER_GATE_CONTRACT_SHA256 = sha256_file(OWNER_GATE_CONTRACT_PATH)
REQUIRED_IMAGES = {
    "coordinator", "headlessNode", "docs", "prover", "agent",
    "postgres", "minio", "minioClient", "backup", "promotionController",
    "failoverProvider", "frontDoor", "openbao", "web3signer", "prometheus",
    "grafana", "alertmanager", "loki", "promtail", "acceptanceRunner",
    "failoverRunner", "soakRunner", "faultController", "backupRestoreController",
}
REQUIRED_PHYSICAL_RECORDS = {
    "sourceTransferVerification", "sourceClosure", "stagedZkvmImages",
    "generatedTrustRootClosure", "sourceGeneratedCompositeSeal",
    "trustRootPrePromotionCheck", "proverRuntimePublication",
    "proverRuntimePromotionVerification",
    "ownerDurableCapabilities",
    "freshDeploymentAddresses", "singleCalldataReceipt", "singleBlobReceipt",
    "aggregateSevenPlusOneReceipt", "withdrawalReceipt",
    "sponsorshipRenewalReceipt", "qualifiedGasReport",
    "failoverReorgRecovery", "soakVerification",
}
REQUIRED_PUBLICATION_GATES = {
    "yellowPaperPdfRender", "yellowPaperStyle", "yellowPaperForbiddenHistory",
    "yellowPaperVisualQa", "investorDeckExactlyTwelveSlides",
    "investorDeckEditableShapes", "investorDeckNotesAndHiddenXml",
    "investorDeckOverflow", "investorDeckVisualFidelity",
    "currentFrontFaceNoVersionNarrative",
}
REQUIRED_PUBLICATION_ARTIFACTS = {
    "yellowPaperSourceManifest", "yellowPaperPdf", "yellowPaperQaReport",
    "investorDeckPptx", "investorDeckQaReport",
}
COMPOSE_IMAGE_MAP = {
    "COORDINATOR_IMAGE_DIGEST": "coordinator",
    "HEADLESS_NODE_IMAGE_DIGEST": "headlessNode",
    "DOCS_IMAGE_DIGEST": "docs",
    "PROVER_IMAGE_DIGEST": "prover",
    "AGENT_IMAGE_DIGEST": "agent",
    "POSTGRES_IMAGE_DIGEST": "postgres",
    "MINIO_IMAGE_DIGEST": "minio",
    "MINIO_CLIENT_IMAGE_DIGEST": "minioClient",
    "PROMOTION_CONTROLLER_IMAGE_DIGEST": "promotionController",
    "FAILOVER_PROVIDER_IMAGE_DIGEST": "failoverProvider",
    "NGINX_IMAGE_DIGEST": "frontDoor",
    "PROMETHEUS_IMAGE_DIGEST": "prometheus",
    "GRAFANA_IMAGE_DIGEST": "grafana",
    "ALERTMANAGER_IMAGE_DIGEST": "alertmanager",
    "LOKI_IMAGE_DIGEST": "loki",
    "PROMTAIL_IMAGE_DIGEST": "promtail",
    "WEB3SIGNER_IMAGE_DIGEST": "web3signer",
}
HELM_COMPONENT_IMAGE_MAP = {
    "coordinatorActive": "coordinator",
    "coordinatorStandby": "coordinator",
    "indexer": "coordinator",
    "reconciler": "coordinator",
    "publisher": "coordinator",
    "headlessNode": "headlessNode",
    "prover": "prover",
    "proverAgent": "agent",
    "capacityController": "coordinator",
    "autoClaimer": "coordinator",
    "docs": "docs",
}


PLAN = (
    ("owner-source-seal", "release", "python scripts/final_candidate.py owner --candidate-file <candidate.json>"),
    ("reference-regeneration", "static", "python scripts/render_reference_docs.py"),
    ("owner-capability-sync", "static", "python scripts/verify_owner_capabilities.py"),
    ("deployment-unit-policy", "static", "python scripts/test_all.py"),
    ("candidate-source-bundle", "release", "python scripts/source_bundle.py create --umbrella /workspace --output <candidate/zkdeal-source.tar.gz> && python scripts/source_bundle.py verify --archive <candidate/zkdeal-source.tar.gz> --manifest <candidate/zkdeal-source.tar.gz.manifest.json>"),
    ("4090-transfer-verification", "release", "follow runbooks/4090-source-transfer.md into a fresh candidate namespace; verify archive, outer manifest, embedded manifest, entries and extracted tree hashes in Docker"),
    ("4090-zkvm-source-closure", "release", "run prover-node/zkvm/scripts/build-4090-evidence-requests.mjs source-closure and scenario-check in the pinned Node container; prove SOURCE_ROOTS exclude build/**, artifacts.lock.json and source-manifest.json"),
    ("4090-zkvm-image-staging", "release", "build and push the exact release-orchestrator, CUDA toolchain and source-bound runtime images into the candidate staging namespace; record repository@sha256 refs and runtime source-manifest label; do not sign or promote"),
    ("4090-staged-image-receipt", "release", "run build-4090-evidence-requests.mjs staged-images <candidate-manifest-sha256> <orchestrator@sha256> <toolchain@sha256> <runtime@sha256> <write-once/staged-zkvm-images.json>"),
    ("4090-two-build-trust-root", "release", "run the staged pinned release orchestrator: node zkvm/build.mjs --cuda --check-repro --bootstrap-lock with independent target and registry volumes; this is the sole first writer of build/**, artifacts.lock.json and source-manifest.json and requires matching program ID plus four independently compiled artifact hashes"),
    ("4090-generated-trust-root-closure", "release", "run build-4090-evidence-requests.mjs trust-root-output <zkvm-root> <write-once/staged-zkvm-images.json> <write-once/generated-trust-root-closure.json>; bind candidate==minted manifest, v6 lock, program ID, exact orchestrator/toolchain/runtime refs and every locked artifact hash"),
    ("owner-image-build", "release", "/bin/sh tests/acceptance/owner-image-budget.sh"),
    ("headless-image-build", "release", "/bin/sh tests/acceptance/headless-image-budget.sh"),
    ("prover-agent-image-build", "release", "/bin/sh tests/acceptance/prover-agent-image.sh"),
    ("deployment-image-builds", "release", "/bin/sh tests/acceptance/promotion-controller-image.sh && docker build --pull=false -f failover-provider/Dockerfile ."),
    ("acceptance-runner-image-build", "release", "/bin/sh tests/acceptance/acceptance-runner-image.sh"),
    ("failover-runner-image-build", "release", "/bin/sh tests/acceptance/failover-runner-image.sh"),
    ("soak-runner-image-build", "release", "/bin/sh tests/acceptance/soak-runner-image.sh; the published soak image must be built with the soak-runner Dockerfile `candidate` target so it contains the broad-sealed /opt/zkdeal-owner-soak driver copied fail-closed from the exact owner image (OWNER_SOAK_DRIVER_IMAGE plus OWNER_SOAK_DRIVER_SOURCE_SHA256)"),
    ("candidate-oci-staging", "release", "python scripts/oci_registry.py publish <each non-zkVM image into the same candidate staging namespace>; record immutable refs only and do not promote"),
    ("release-digest-seal", "release", "python scripts/final_candidate.py release --candidate-file <candidate.json> --candidate-topology-file <candidate/candidate-private-topology.json> --compose-env-file <candidate/runtime.env> --helm-values-file <candidate/helm-values.yaml> --kurtosis-local-args <candidate/local.json> --kurtosis-failover-args <candidate/failover.json> --kurtosis-acceptance-args <candidate/acceptance-matrix.json> --kurtosis-soak-args <candidate/soak.json>"),
    ("production-compose-policy", "static", "/bin/sh tests/acceptance/production-compose-policy.sh"),
    ("production-compose-live", "live", "python scripts/production_compose.py check --env-file <candidate release-images.env> --with-signer --profile observability --profile gpu && python scripts/production_compose.py pull --env-file <candidate release-images.env> --with-signer --profile observability --profile gpu && python scripts/production_compose.py up --env-file <candidate release-images.env> --with-signer --profile observability --profile gpu"),
    ("prover-agent-owner-boundary", "live", "PROVER_AGENT_CANDIDATE_IMAGE=<candidate agent digest> /bin/sh tests/acceptance/prover-agent-owner-live.sh"),
    ("owner-openapi-live-replay", "live", "/bin/sh tests/acceptance/openapi-live-replay.sh"),
    ("postgres-ha-rehearsal", "live", "POSTGRES_HA_BASE_IMAGE=<candidate postgres digest> /bin/sh tests/acceptance/postgres-ha.sh"),
    ("database-object-backup-restore", "live", "BACKUP_TOOLS_IMAGE=<candidate backup digest> DEPLOYMENT_TOOLS_IMAGE=<tools digest> POSTGRES_IMAGE=<candidate postgres digest> MINIO_IMAGE=<candidate minio digest> MINIO_CLIENT_IMAGE=<candidate minioClient digest> /bin/sh tests/acceptance/live-backup-restore.sh"),
    ("edge-controls", "live", "FRONT_DOOR_IMAGE=<candidate frontDoor digest> /bin/sh tests/acceptance/front-door.sh"),
    ("openbao-web3signer-boundaries", "live", "OPENBAO_IMAGE=<candidate openbao digest> WEB3SIGNER_IMAGE=<candidate web3signer digest> CURL_IMAGE=<locked curl digest> /bin/sh tests/acceptance/security-services.sh"),
    ("observability-fire-recover", "live", "PROMETHEUS_IMAGE=<candidate prometheus digest> ALERTMANAGER_IMAGE=<candidate alertmanager digest> PYTHON_IMAGE=<locked python digest> /bin/sh tests/acceptance/observability.sh"),
    ("helm-production-render", "static", "helm template zkdeal helm/zkdeal -f <candidate/helm-values.yaml> | python scripts/helm_semantic_test.py"),
    ("kubernetes-real-owner", "live", "KIND_CANDIDATE_MODE=1 KIND_HELM_VALUES_FILE=<candidate/kind-values.yaml> KIND_OWNER_IMAGE=<digest> KIND_HEADLESS_IMAGE=<digest> KIND_DOCS_IMAGE=<digest> KIND_BACKUP_IMAGE=<digest> KIND_PROMOTION_CONTROLLER_IMAGE=<digest> KIND_FAILOVER_PROVIDER_IMAGE=<digest> /bin/sh tests/acceptance/kubernetes-kind.sh"),
    ("docker-failover-provider", "live", "FAILOVER_PROVIDER_CANDIDATE_IMAGE=<candidate failoverProvider digest> POSTGRES_HA_BASE_IMAGE=<candidate postgres digest> /bin/sh tests/acceptance/failover-provider-docker-live.sh"),
    ("kubernetes-failover-provider", "live", "FAILOVER_PROVIDER_CANDIDATE_IMAGE=<candidate failoverProvider digest> POSTGRES_CANDIDATE_IMAGE=<candidate postgres digest> /bin/sh tests/acceptance/failover-provider-kubernetes-live.sh"),
    ("candidate-private-topology-verification", "live", "run scripts/record_gate.py with candidate.json, candidate-private-topology.json and both adapter capability files as explicit inputs, then python scripts/candidate_topology.py check --candidate-file <candidate.json> --topology-file <candidate-private-topology.json> --output <write-once topology-verification.json>; require shared exact-digest stack, distinct fault/backup authorities and independent PostgreSQL HA proven live via exec-into-workload pg_is_in_recovery()/pg_stat_replication/pg_stat_wal_receiver inspection recorded as replicationInspection"),
    ("kurtosis-local", "static", "python scripts/kurtosis_run.py local --args-file <candidate/local.json> --check-only; the hosted-plane local package is development mechanics only and is not release evidence; it never starts the standalone file queue"),
    ("kurtosis-failover", "live", "python scripts/kurtosis_run.py failover --args-file <candidate/failover.json>"),
    ("kurtosis-acceptance-matrix", "live", "run scripts/record_gate.py with --input-file for <candidate/acceptance-matrix.json>, its plan_file, and every used auth_files entry, then execute python scripts/kurtosis_run.py acceptance-matrix --args-file <candidate/acceptance-matrix.json>; record hashes and paths only, never token values"),
    ("load-shadow-rpc-sse-indexer", "release", "run the digest-pinned owner acceptance runner for RPC, SSE reconnect/backpressure, indexer rollback and projection-parity load budgets; retain mismatch and latency reports"),
    ("load-shadow-admission-scheduler", "release", "run the digest-pinned owner acceptance runner for admission WAL, tenant-fair scheduler, queue congestion and deadline budgets; retain mismatch, fairness and cap reports"),
    ("acceptance-credential-revocation", "release", "revoke every enclave-scoped credential used by auth_files through the broad-sealed owner authority, prove subsequent requests are denied, and retain only alias/path/hash/revocation receipt metadata"),
    ("physical-single-calldata-settlement", "release", "follow the pinned 4090 runbook: live engine -> BatchInputV5 -> CUDA proof -> owner durable RoomManager.submitBatch; retain qualified receipt and gas"),
    ("physical-single-blob-settlement", "release", "follow the pinned 4090 runbook: c-kzg-bound blob proof and owner durable type-3 publication; retain blob receipt, precompile trace, blob gas and fees"),
    ("physical-aggregate-seven-plus-one", "release", "follow the pinned 4090 runbook: eight rooms, six blobs, one recursive proof, seven applied plus one stale isolated outcome, successful-member-only charging and retry"),
    ("physical-withdrawal-sponsorship-renewal", "release", "follow the pinned 4090 runbook: real withdrawal root/proof/claim/replay denial plus sponsored payer/refund and finalized renewal semantics"),
    ("physical-failover-reorg-recovery", "release", "follow the pinned 4090 runbook: coordinator promotion, RPC split, pre-finality reorg and post-finality RECOVERY_REQUIRED with stable job/nonce/correlation lineage"),
    ("release-soak-12h", "release", "python scripts/kurtosis_run.py soak --args-file <candidate/soak.json>"),
    ("4090-source-generated-composite-seal", "release", "run build-4090-evidence-requests.mjs evidence-closure <write-once/evidence-closure-plan.json> <write-once/evidence-closure.json>; bind source closure, generated trust-root closure, physical receipts, final soak, v6 lock, program ID and staged runtime digest"),
    ("4090-trust-root-pre-promotion-check", "release", "run build-4090-evidence-requests.mjs trust-root-check <zkvm-root> <staged-zkvm-images.json> <generated-trust-root-closure.json> and re-hash the composite seal immediately before promotion"),
    ("candidate-oci-promotion", "release", "python scripts/oci_registry.py promote --manifest <staged-publication.json> --candidate-file <candidate.json> --composite-seal <evidence-closure.json> --image-key <candidate image key> --release <fresh release id> --key-file /run/secrets/oci-promotion-mac --output <write-once promotion.json> && python scripts/oci_registry.py verify-promotion with the same source/candidate/composite/key inputs; rebuilding or changing the staged digest is forbidden"),
    ("physical-evidence-seal", "release", "python scripts/final_candidate.py physical --candidate-file <candidate.json> --physical-manifest-file <candidate/physical-evidence.json>"),
    ("yellow-paper-pdf-render", "publication", "render the current yellow paper PDF in Docker after physical evidence closure"),
    ("yellow-paper-style-history-visual", "publication", "run Dockerized style, forbidden-history/version-narrative, PDF render and page visual-QA gates"),
    ("investor-deck-twelve-slide-structure", "publication", "run Dockerized exact-12-slide, editable-shape, notes and hidden-XML inspection on outputs/zkdeal-investor-deck-business-model-v3-2026-08.pptx"),
    ("investor-deck-overflow-fidelity", "publication", "render the PPTX in Docker and run overflow, clipping, contrast and visual-fidelity inspection"),
    ("publication-artifact-seal", "publication", "python scripts/final_candidate.py publication --candidate-file <candidate.json> --physical-manifest-file <candidate/physical-evidence.json> --publication-manifest-file <candidate/final-publication.json>"),
    ("candidate-evidence-bundle", "release", "python scripts/source_bundle.py create-evidence --evidence-root evidence --output <candidate/zkdeal-evidence.tar.gz> && python scripts/source_bundle.py verify --archive <candidate/zkdeal-evidence.tar.gz> --manifest <candidate/zkdeal-evidence.tar.gz.manifest.json>"),
    ("evidence-worm-closure", "release", "python scripts/evidence_closure.py seal --evidence-root evidence --output-root <candidate/closure> && python scripts/evidence_closure.py verify --manifest <candidate/closure-manifest.json> --hmac <candidate/closure-manifest.hmac> --evidence-root evidence && python scripts/evidence_closure.py publish --manifest <candidate/closure-manifest.json> --hmac <candidate/closure-manifest.hmac> --retention-mode COMPLIANCE --retention-duration 1y"),
    ("4090-trust-root-post-evidence-check", "release", "rerun trust-root-check against the generated closure, re-hash the source-generated composite seal, pull every promoted digest and verify identity after WORM publication; publish this read-only verification as a separate immutable audit record"),
)


def candidate(path: str | Path) -> dict[str, Any]:
    value = load_json(project_path(path))
    if not isinstance(value, dict):
        raise DeploymentError("candidate descriptor must be a JSON object")
    if value.get("schemaVersion") != 1:
        raise DeploymentError("candidate descriptor schemaVersion must be 1")
    if not CANDIDATE_ID.fullmatch(str(value.get("candidateId", ""))):
        raise DeploymentError("candidateId must be 8-80 lowercase safe characters")
    return value


def validate_phase_a_unminted(value: dict[str, Any]) -> None:
    """Ensure the owner/source phase cannot bless pre-existing generated output."""
    boundary = value.get("zkvmGeneratedTrustRoot")
    expected_fields = {
        "passed", "sourceClosure", "stagedImagesReceipt", "generatedClosure",
        "candidateManifestSha256", "artifactLockSha256", "programId", "stagedImages",
    }
    if not isinstance(boundary, dict) or set(boundary) != expected_fields:
        raise DeploymentError("phase-A candidate lacks the explicit unminted zkVM boundary")
    if boundary.get("passed") is not False:
        raise DeploymentError("phase-A owner/source seal requires an unminted generated trust root")
    for field in ("candidateManifestSha256", "artifactLockSha256", "programId"):
        if boundary.get(field) is not None:
            raise DeploymentError(f"phase-A owner/source seal forbids prefilled {field}")
    for field in ("sourceClosure", "stagedImagesReceipt", "generatedClosure"):
        binding = boundary.get(field)
        if not isinstance(binding, dict) or binding != {"path": None, "sha256": None}:
            raise DeploymentError(f"phase-A owner/source seal requires an empty {field} binding")
    staged = boundary.get("stagedImages")
    if not isinstance(staged, dict) or staged != {
        "orchestrator": None, "toolchain": None, "runtime": None,
    }:
        raise DeploymentError("phase-A owner/source seal forbids prefilled staged zkVM images")


def validate_owner_seal(value: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    seal = value.get("ownerBroadSeal")
    if not isinstance(seal, dict) or seal.get("passed") is not True:
        raise DeploymentError("owner broad seal is absent or not passed")
    evidence_sha = seal.get("evidenceSha256")
    if not SHA256.fullmatch(str(evidence_sha or "")):
        raise DeploymentError("owner broad-seal evidence requires a concrete SHA-256")
    gate_contract_sha = seal.get("gateContractSha256")
    if gate_contract_sha != OWNER_GATE_CONTRACT_SHA256:
        raise DeploymentError(
            "owner broad seal does not bind the current config/owner-release-gates.json SHA-256"
        )
    acceptance = seal.get("hostedIntegrationAcceptanceToken")
    if not TOKEN.fullmatch(str(acceptance or "")):
        raise DeploymentError("owner broad seal lacks the SHA-256 hostedIntegration acceptance token")
    gates = seal.get("gates")
    if not isinstance(gates, dict) or set(gates) != REQUIRED_OWNER_GATES:
        raise DeploymentError("owner broad seal gate set is incomplete or has unreviewed additions")
    failed = sorted(name for name, passed in gates.items() if passed is not True)
    if failed:
        raise DeploymentError(f"owner broad seal contains failed gates: {failed}")

    artifact_inventory = inventory()
    artifact_rows = artifact_inventory.get("artifacts", [])
    umbrella = Path(artifact_inventory["umbrellaRoot"]).resolve()
    relative_evidence = Path(str(seal.get("evidenceManifestPath", "")))
    if (
        not str(relative_evidence)
        or relative_evidence.is_absolute()
        or ".." in relative_evidence.parts
        or relative_evidence.suffix.lower() != ".json"
    ):
        raise DeploymentError("owner broad seal requires a safe umbrella-relative JSON evidence manifest path")
    evidence_path = (umbrella / relative_evidence).resolve()
    try:
        evidence_path.relative_to(umbrella)
    except ValueError as exc:
        raise DeploymentError("owner broad-seal evidence resolves outside the umbrella") from exc
    if evidence_path.is_symlink() or not evidence_path.is_file():
        raise DeploymentError("owner broad-seal evidence manifest is absent or not a regular file")
    if sha256_file(evidence_path) != evidence_sha:
        raise DeploymentError("owner broad-seal evidence manifest SHA-256 differs")
    required = {row["id"]: row for row in artifact_rows if row.get("required")}
    supplied = value.get("sourceArtifacts")
    if not isinstance(supplied, dict) or set(supplied) != set(required):
        missing = sorted(set(required) - set(supplied or {}))
        extra = sorted(set(supplied or {}) - set(required))
        raise DeploymentError(f"candidate owner artifact hash set differs: missing={missing} extra={extra}")
    for artifact_id, row in required.items():
        digest = supplied.get(artifact_id)
        if not SHA256.fullmatch(str(digest or "")) or digest != row.get("sha256"):
            raise DeploymentError(f"owner artifact hash differs or is unsealed: {artifact_id}")

    evidence = load_json(evidence_path)
    if not isinstance(evidence, dict) or evidence.get("schemaVersion") != 1 or evidence.get("passed") is not True:
        raise DeploymentError("owner broad-seal evidence manifest is not a passed schemaVersion 1 record")
    if evidence.get("candidateId") != value.get("candidateId"):
        raise DeploymentError("owner broad-seal evidence candidateId differs")
    if evidence.get("gates") != gates:
        raise DeploymentError("owner broad-seal evidence gates differ from the candidate descriptor")
    if evidence.get("gateContractSha256") != OWNER_GATE_CONTRACT_SHA256:
        raise DeploymentError("owner broad-seal evidence does not bind the current gate contract")
    if evidence.get("hostedIntegrationAcceptanceToken") != acceptance:
        raise DeploymentError("owner broad-seal evidence hostedIntegration token differs")
    if evidence.get("sourceArtifacts") != supplied:
        raise DeploymentError("owner broad-seal evidence source-artifact hashes differ")

    by_id = {row["id"]: row for row in artifact_rows}
    hosted = load_json(umbrella / by_id["hosted-service-capabilities"]["path"])
    headless = load_json(umbrella / by_id["headless-room-node-capabilities"]["path"])
    joint_errors = hosted_integration_evidence_errors(
        umbrella,
        hosted,
        by_id["room-batch-hosted-integration-evidence"],
    )
    if joint_errors:
        raise DeploymentError(
            "owner hostedIntegration evidence is not source-bound to current bytes: "
            + "; ".join(joint_errors)
        )
    integration = hosted.get("managedL1Operations", {}).get("roomBatch", {}).get("hostedIntegration", {})
    pins = {
        "hostedAdmissionLease": True,
        "hostedRoomBatchEnabled": True,
        "hostedEngineToBatchInputV5": True,
        "hostedDurablePostgresQueue": True,
        "hostedExternalProver": True,
        "hostedRestartResume": True,
        "hostedFixturePrepare": False,
        "hostedLegacyGroth16": False,
        "hostedIntegrationAcceptanceToken": acceptance,
    }
    errors = headless_hosted_integration_errors(headless, hosted, (("candidate", pins),))
    if errors:
        raise DeploymentError("owner hostedIntegration is not release-sealed: " + "; ".join(errors))
    if integration.get("acceptanceToken") != acceptance:
        raise DeploymentError("candidate and owner hostedIntegration acceptance tokens differ")
    return hosted, headless


def validate_release_images(value: dict[str, Any]) -> dict[str, str]:
    images = value.get("images")
    if not isinstance(images, dict) or set(images) != REQUIRED_IMAGES:
        missing = sorted(REQUIRED_IMAGES - set(images or {}))
        extra = sorted(set(images or {}) - REQUIRED_IMAGES)
        raise DeploymentError(f"candidate release image set differs: missing={missing} extra={extra}")
    checked = {name: validate_reference(reference, f"images.{name}") for name, reference in images.items()}
    if len(set(checked.values())) != len(checked):
        groups: dict[str, set[str]] = {}
        for name, reference in checked.items():
            groups.setdefault(reference, set()).add(name)
        reused = [sorted(names) for names in groups.values() if len(names) > 1]
        raise DeploymentError(f"unreviewed candidate image reference reuse: {reused}")
    return checked


def regular_project_input(value: str, label: str) -> Path:
    path = project_path(value)
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise DeploymentError(f"{label} escaped the deployment project") from exc
    raw = Path(value) if Path(value).is_absolute() else ROOT / value
    if raw.is_symlink() or not path.is_file():
        raise DeploymentError(f"{label} is absent, not regular, or a symlink")
    return path


def umbrella_file_binding(value: object, label: str) -> dict[str, object]:
    """Validate one hash-bound artifact anywhere under the umbrella workspace."""
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise DeploymentError(f"{label} must contain exactly path and sha256")
    relative = Path(str(value.get("path") or ""))
    expected = str(value.get("sha256") or "")
    if not str(relative) or relative.is_absolute() or ".." in relative.parts:
        raise DeploymentError(f"{label} path must be safe and umbrella-relative")
    if not SHA256.fullmatch(expected):
        raise DeploymentError(f"{label} SHA-256 is absent or malformed")
    umbrella = ROOT.parent.resolve()
    path = (umbrella / relative).resolve()
    try:
        path.relative_to(umbrella)
    except ValueError as exc:
        raise DeploymentError(f"{label} resolves outside the umbrella") from exc
    if path.is_symlink() or not path.is_file():
        raise DeploymentError(f"{label} is absent, not regular, or a symlink")
    observed = sha256_file(path)
    if observed != expected:
        raise DeploymentError(f"{label} SHA-256 differs")
    return {"path": relative.as_posix(), "sha256": observed, "bytes": path.stat().st_size}


def load_bound_umbrella_json(binding: object, label: str) -> tuple[dict[str, object], dict[str, Any]]:
    checked = umbrella_file_binding(binding, label)
    path = ROOT.parent.resolve() / str(checked["path"])
    value = load_json(path)
    if not isinstance(value, dict):
        raise DeploymentError(f"{label} must contain a JSON object")
    return checked, value


def validate_generated_trust_root(value: dict[str, Any], images: dict[str, str]) -> dict[str, object]:
    """Validate the non-circular phase-B zkVM trust-root boundary.

    Phase A (owner/source seal) deliberately excludes generated trust roots.
    This phase consumes the owner assembler's write-once source closure,
    unpromoted staged-image receipt, and generated-output closure.
    """
    boundary = value.get("zkvmGeneratedTrustRoot")
    expected_fields = {
        "passed", "sourceClosure", "stagedImagesReceipt", "generatedClosure",
        "candidateManifestSha256", "artifactLockSha256", "programId", "stagedImages",
    }
    if not isinstance(boundary, dict) or set(boundary) != expected_fields:
        raise DeploymentError("zkvmGeneratedTrustRoot has an incomplete or unreviewed field set")
    if boundary.get("passed") is not True:
        raise DeploymentError("zkvmGeneratedTrustRoot is absent or not passed")

    candidate_sha = str(boundary.get("candidateManifestSha256") or "")
    lock_sha = str(boundary.get("artifactLockSha256") or "")
    program_id = str(boundary.get("programId") or "")
    if not SHA256.fullmatch(candidate_sha) or not SHA256.fullmatch(lock_sha):
        raise DeploymentError("generated trust root requires concrete candidate-manifest and lock SHA-256")
    if not PROGRAM_ID.fullmatch(program_id):
        raise DeploymentError("generated trust root programId must be 0x plus 64 lowercase hex")

    declared_images = boundary.get("stagedImages")
    if not isinstance(declared_images, dict) or set(declared_images) != {
        "orchestrator", "toolchain", "runtime",
    }:
        raise DeploymentError("generated trust root stagedImages must contain exactly orchestrator/toolchain/runtime")
    staged = {
        name: validate_reference(reference, f"zkvmGeneratedTrustRoot.stagedImages.{name}")
        for name, reference in declared_images.items()
    }
    if len(set(staged.values())) != 3:
        raise DeploymentError("staged zkVM orchestrator/toolchain/runtime references must be distinct")
    if staged["runtime"] != images.get("prover"):
        raise DeploymentError("candidate prover image is not the exact staged zkVM runtime digest")

    source_binding, source = load_bound_umbrella_json(
        boundary.get("sourceClosure"), "zkVM source closure",
    )
    if (
        source.get("schema") != "zkdeal/4090-source-closure/v1"
        or source.get("algorithm") != "sha256"
        or source.get("noRepositoryHistory") is not True
        or source.get("noSecrets") is not True
        or source.get("zkvmCandidateManifest", {}).get("sha256") != candidate_sha
    ):
        raise DeploymentError("zkVM source closure does not bind the no-history candidate preimage")

    receipt_binding, receipt = load_bound_umbrella_json(
        boundary.get("stagedImagesReceipt"), "staged zkVM image receipt",
    )
    if set(receipt) != {"schema", "candidateManifestSha256", "promoted", "images"}:
        raise DeploymentError("staged zkVM image receipt has an unreviewed field set")
    if (
        receipt.get("schema") != "zkdeal/4090-staged-zkvm-images/v1"
        or receipt.get("candidateManifestSha256") != candidate_sha
        or receipt.get("promoted") is not False
        or receipt.get("images") != staged
    ):
        raise DeploymentError("staged zkVM image receipt does not bind the exact unpromoted candidate images")

    closure_binding, closure = load_bound_umbrella_json(
        boundary.get("generatedClosure"), "generated zkVM trust-root closure",
    )
    preimage = closure.get("buildPreimage", {})
    generated = closure.get("generatedTrustRoot", {})
    closure_staged = closure.get("stagedImages", {})
    ordering = closure.get("orderingContract", {})
    if closure.get("schema") != "zkdeal/4090-generated-trust-root-closure/v1" or closure.get("algorithm") != "sha256":
        raise DeploymentError("generated zkVM trust-root closure has an unsupported schema")
    if (
        preimage.get("verifiedAgainstFilesystem") is not True
        or preimage.get("generatedOutputsExcluded") is not True
        or preimage.get("candidateManifest", {}).get("sha256") != candidate_sha
    ):
        raise DeploymentError("generated zkVM trust-root closure does not preserve the immutable preimage")
    if (
        closure_staged.get("receiptSha256") != receipt_binding["sha256"]
        or closure_staged.get("promoted") is not False
        or any(closure_staged.get(name) != staged[name] for name in staged)
    ):
        raise DeploymentError("generated zkVM trust-root closure disagrees with the staged-image receipt")
    lock = generated.get("artifactLock", {})
    if (
        generated.get("sourceManifest", {}).get("sha256") != candidate_sha
        or generated.get("sourceManifest", {}).get("byteIdenticalToCandidate") is not True
        or lock.get("sha256") != lock_sha
        or lock.get("format") != "zkdeal/zkvm-artifacts-lock/v6"
        or generated.get("programId") != program_id
        or generated.get("toolchainImage") != staged["toolchain"]
        or generated.get("runtimeImage") != staged["runtime"]
    ):
        raise DeploymentError("generated zkVM trust root disagrees with the candidate manifest, v6 lock or staged images")
    artifacts = generated.get("lockedArtifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(REQUIRED_ZKVM_LOCKED_ARTIFACTS):
        raise DeploymentError("generated zkVM trust root must bind exactly seven locked artifacts")
    paths: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "bytes", "sha256"}:
            raise DeploymentError("generated zkVM locked-artifact binding is malformed")
        path = str(artifact.get("path") or "")
        if (
            not path or path.startswith(("/", "\\")) or "\\" in path
            or ".." in Path(path).parts or path in paths
        ):
            raise DeploymentError("generated zkVM locked-artifact paths must be unique and relative")
        paths.add(path)
        if not isinstance(artifact.get("bytes"), int) or artifact["bytes"] < 1:
            raise DeploymentError("generated zkVM locked artifact has an invalid byte count")
        if not SHA256.fullmatch(str(artifact.get("sha256") or "")):
            raise DeploymentError("generated zkVM locked artifact has an invalid SHA-256")
    if paths != REQUIRED_ZKVM_LOCKED_ARTIFACTS:
        missing = sorted(REQUIRED_ZKVM_LOCKED_ARTIFACTS - paths)
        extra = sorted(paths - REQUIRED_ZKVM_LOCKED_ARTIFACTS)
        raise DeploymentError(
            f"generated zkVM locked-artifact set differs: missing={missing} extra={extra}"
        )
    if (
        ordering.get("requiredIndependentCudaBuilds") != 2
        or ordering.get("trustRootWriter") != "zkvm/build.mjs --cuda --check-repro --bootstrap-lock"
        or ordering.get("stagedImagesAreNotReleasePromotion") is not True
        or ordering.get("finalPromotionRequiresExactStagedDigests") is not True
        or ordering.get("rebuildAfterCompositeSealForbidden") is not True
        or ordering.get("postCompositeSealMutationInvalidatesCandidate") is not True
    ):
        raise DeploymentError("generated zkVM trust root lacks the reviewed two-build/promotion ordering contract")
    return {
        "candidateManifestSha256": candidate_sha,
        "artifactLockSha256": lock_sha,
        "programId": program_id,
        "sourceClosure": source_binding,
        "stagedImagesReceipt": receipt_binding,
        "generatedClosure": closure_binding,
        "stagedImages": staged,
        "lockedArtifacts": sorted(paths),
    }


def checked_umbrella_json(binding: dict[str, object], label: str) -> dict[str, Any]:
    path = ROOT.parent.resolve() / str(binding["path"])
    value = load_json(path)
    if not isinstance(value, dict):
        raise DeploymentError(f"{label} must contain a JSON object")
    return value


def validate_physical_trust_chain(
    candidate_value: dict[str, Any], candidate_path: Path,
    checked: dict[str, dict[str, object]],
) -> dict[str, object]:
    boundary = candidate_value.get("zkvmGeneratedTrustRoot")
    if not isinstance(boundary, dict) or boundary.get("passed") is not True:
        raise DeploymentError("physical evidence candidate lacks the generated zkVM trust root")
    for record, candidate_field in (
        ("sourceClosure", "sourceClosure"),
        ("stagedZkvmImages", "stagedImagesReceipt"),
        ("generatedTrustRootClosure", "generatedClosure"),
    ):
        expected = boundary.get(candidate_field)
        if not isinstance(expected, dict) or any(
            checked[record].get(name) != expected.get(name) for name in ("path", "sha256")
        ):
            raise DeploymentError(f"physical {record} differs from the candidate phase-B binding")

    generated = checked_umbrella_json(
        checked["generatedTrustRootClosure"], "physical generated trust-root closure",
    )
    composite = checked_umbrella_json(
        checked["sourceGeneratedCompositeSeal"], "physical source/generated composite seal",
    )
    if composite.get("schema") != "zkdeal/4090-evidence-closure/v2" or composite.get("algorithm") != "sha256":
        raise DeploymentError("physical composite seal is not zkdeal/4090-evidence-closure/v2")
    source = composite.get("source")
    physical = composite.get("physicalAcceptance")
    staged = boundary.get("stagedImages")
    if not isinstance(source, dict) or not isinstance(physical, dict) or not isinstance(staged, dict):
        raise DeploymentError("physical composite seal is incomplete")
    expected_composite = {
        "source.closureSha256": (source.get("closureSha256"), checked["sourceClosure"]["sha256"]),
        "source.generatedTrustRootClosureSha256": (
            source.get("generatedTrustRootClosureSha256"),
            checked["generatedTrustRootClosure"]["sha256"],
        ),
        "source.zkvmManifestSha256": (
            source.get("zkvmManifestSha256"), boundary.get("candidateManifestSha256"),
        ),
        "physical.ownerAcceptanceToken": (
            physical.get("ownerAcceptanceToken"),
            (candidate_value.get("ownerBroadSeal") or {}).get("hostedIntegrationAcceptanceToken"),
        ),
        "physical.ownerDurableCapabilitiesSha256": (
            physical.get("ownerDurableCapabilitiesSha256"),
            checked["ownerDurableCapabilities"]["sha256"],
        ),
        "physical.deploymentAddressesSha256": (
            physical.get("deploymentAddressesSha256"),
            checked["freshDeploymentAddresses"]["sha256"],
        ),
        "physical.soakVerificationSha256": (
            physical.get("soakVerificationSha256"), checked["soakVerification"]["sha256"],
        ),
        "artifactLockSha256": (
            composite.get("artifactLockSha256"), boundary.get("artifactLockSha256"),
        ),
        "orchestratorImage": (composite.get("orchestratorImage"), staged.get("orchestrator")),
        "toolchainImage": (composite.get("toolchainImage"), staged.get("toolchain")),
        "runtimeImage": (composite.get("runtimeImage"), staged.get("runtime")),
        "programId": (composite.get("programId"), boundary.get("programId")),
    }
    for label, (observed, expected) in expected_composite.items():
        if observed != expected:
            raise DeploymentError(f"physical composite seal differs: {label}")
    if not SHA256.fullmatch(str(physical.get("settlementScenarioSha256", ""))):
        raise DeploymentError("physical composite seal lacks the settlement scenario SHA-256")

    composite_path = ROOT.parent.resolve() / str(checked["sourceGeneratedCompositeSeal"]["path"])
    files = composite.get("files")
    if not isinstance(files, list) or not files or len(files) > 100_000:
        raise DeploymentError("physical composite file inventory is absent or over bound")
    seen: set[str] = set()
    included_hashes: set[str] = set()
    total_bytes = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise DeploymentError("physical composite file entry is malformed")
        relative = Path(str(item["path"]))
        if relative.is_absolute() or ".." in relative.parts or not str(relative):
            raise DeploymentError("physical composite file path is unsafe")
        portable = relative.as_posix()
        if portable in seen:
            raise DeploymentError("physical composite file path is duplicated")
        seen.add(portable)
        path = (composite_path.parent / relative).resolve()
        try:
            path.relative_to(composite_path.parent.resolve())
        except ValueError as exc:
            raise DeploymentError("physical composite file escaped its evidence root") from exc
        if path.is_symlink() or not path.is_file():
            raise DeploymentError("physical composite file is absent, not regular, or a symlink")
        size = path.stat().st_size
        if not isinstance(item["size"], int) or isinstance(item["size"], bool) or item["size"] != size:
            raise DeploymentError("physical composite file size differs")
        if item["sha256"] != sha256_file(path):
            raise DeploymentError("physical composite file SHA-256 differs")
        total_bytes += size
        if total_bytes > (1 << 40):
            raise DeploymentError("physical composite file inventory exceeds one TiB")
        included_hashes.add(str(item["sha256"]))
    if checked["generatedTrustRootClosure"]["sha256"] not in included_hashes:
        raise DeploymentError("physical composite does not include the generated trust-root closure")

    precheck = checked_umbrella_json(
        checked["trustRootPrePromotionCheck"], "trust-root pre-promotion check",
    )
    if precheck != {
        "verified": True,
        "schema": "zkdeal/4090-generated-trust-root-closure/v1",
        "sha256": checked["generatedTrustRootClosure"]["sha256"],
    }:
        raise DeploymentError("trust-root pre-promotion check differs from the generated closure")

    publication = checked_umbrella_json(
        checked["proverRuntimePublication"], "prover runtime promotion receipt",
    )
    if (
        set(publication) != {"schema", "payload", "authentication"}
        or publication.get("schema") != "zkdeal/oci-promotion-envelope/v1"
        or not isinstance(publication.get("payload"), dict)
        or not isinstance(publication.get("authentication"), dict)
    ):
        raise DeploymentError("prover runtime promotion receipt envelope is malformed")
    payload = publication["payload"]
    authentication = publication["authentication"]
    candidate_sha = sha256_file(candidate_path)
    source_ref = str(payload.get("sourceImmutableReference", ""))
    promoted_ref = str(payload.get("promotedImmutableReference", ""))
    expected_publication = {
        "schema": "zkdeal/oci-promotion/v1",
        "candidateId": candidate_value.get("candidateId"),
        "imageKey": "prover",
        "candidateDescriptorSha256": candidate_sha,
        "sourceGeneratedCompositeSealSha256": checked["sourceGeneratedCompositeSeal"]["sha256"],
        "sourceImmutableReference": (candidate_value.get("images") or {}).get("prover"),
        "exactDigestPreserved": True,
        "sameDaemonImageId": True,
        "rebuilt": False,
        "mutableReferenceRecorded": False,
        "transportReferenceRemovedLocally": True,
        "promotionOccurredAfterCompositeSeal": True,
    }
    for name, expected in expected_publication.items():
        if payload.get(name) != expected:
            raise DeploymentError(f"prover runtime promotion receipt differs: {name}")
    if (
        "@sha256:" not in source_ref
        or source_ref.rsplit("@", 1)[-1] != promoted_ref.rsplit("@", 1)[-1]
        or payload.get("digest") != source_ref.rsplit("@", 1)[-1]
        or payload.get("sourceDaemonImageId") != payload.get("promotedDaemonImageId")
        or not TOKEN.fullmatch(str(payload.get("sourceDaemonImageId", "")))
    ):
        raise DeploymentError("prover runtime promotion changed digest or daemon identity")
    if (
        set(authentication) != {"algorithm", "keyId", "mac"}
        or authentication.get("algorithm") != "hmac-sha256"
        or not TOKEN.fullmatch(str(authentication.get("keyId", "")))
        or not re.fullmatch(r"hmac-sha256:[0-9a-f]{64}", str(authentication.get("mac", "")))
    ):
        raise DeploymentError("prover runtime promotion authentication is malformed")

    verification = checked_umbrella_json(
        checked["proverRuntimePromotionVerification"], "prover runtime promotion verification",
    )
    expected_verification = {
        "verified": True,
        "receiptSha256": checked["proverRuntimePublication"]["sha256"],
        "keyId": authentication["keyId"],
        "sourceImmutableReference": source_ref,
        "promotedImmutableReference": promoted_ref,
        "daemonImageId": payload.get("sourceDaemonImageId"),
        "exactDigestPreserved": True,
        "sameDaemonImageId": True,
    }
    if verification != expected_verification:
        raise DeploymentError("prover runtime promotion verification differs from its signed receipt")
    return {
        "compositeSealSha256": checked["sourceGeneratedCompositeSeal"]["sha256"],
        "generatedTrustRootClosureSha256": checked["generatedTrustRootClosure"]["sha256"],
        "promotionReceiptSha256": checked["proverRuntimePublication"]["sha256"],
        "promotionVerificationSha256": checked["proverRuntimePromotionVerification"]["sha256"],
        "promotedRuntime": promoted_ref,
        "fileInventoryCount": len(files),
        "fileInventoryBytes": total_bytes,
    }


def validate_physical_evidence(
    candidate_value: dict[str, Any], candidate_path: Path, manifest_path: Path,
) -> dict[str, object]:
    manifest = load_json(manifest_path)
    if manifest.get("schemaVersion") != 1 or manifest.get("passed") is not True:
        raise DeploymentError("physical evidence manifest is not a passed schemaVersion 1 record")
    if manifest.get("candidateId") != candidate_value.get("candidateId"):
        raise DeploymentError("physical evidence candidateId differs")
    if manifest.get("candidateDescriptorSha256") != sha256_file(candidate_path):
        raise DeploymentError("physical evidence does not bind the exact candidate descriptor")
    records = manifest.get("records")
    if not isinstance(records, dict) or set(records) != REQUIRED_PHYSICAL_RECORDS:
        missing = sorted(REQUIRED_PHYSICAL_RECORDS - set(records or {}))
        extra = sorted(set(records or {}) - REQUIRED_PHYSICAL_RECORDS)
        raise DeploymentError(f"physical evidence record set differs: missing={missing} extra={extra}")
    checked = {
        name: umbrella_file_binding(binding, f"physical evidence {name}")
        for name, binding in sorted(records.items())
    }
    trust_chain = validate_physical_trust_chain(candidate_value, candidate_path, checked)
    return {
        "manifestSha256": sha256_file(manifest_path),
        "records": checked,
        "trustChain": trust_chain,
    }


def validate_publication(
    candidate_value: dict[str, Any], candidate_path: Path,
    physical_path: Path, publication_path: Path,
) -> dict[str, object]:
    physical = validate_physical_evidence(candidate_value, candidate_path, physical_path)
    manifest = load_json(publication_path)
    if manifest.get("schemaVersion") != 1 or manifest.get("passed") is not True:
        raise DeploymentError("publication manifest is not a passed schemaVersion 1 record")
    if manifest.get("candidateId") != candidate_value.get("candidateId"):
        raise DeploymentError("publication candidateId differs")
    if manifest.get("candidateDescriptorSha256") != sha256_file(candidate_path):
        raise DeploymentError("publication does not bind the exact candidate descriptor")
    if manifest.get("physicalEvidenceManifestSha256") != sha256_file(physical_path):
        raise DeploymentError("publication does not bind the exact physical evidence manifest")
    gates = manifest.get("gates")
    if not isinstance(gates, dict) or set(gates) != REQUIRED_PUBLICATION_GATES:
        raise DeploymentError("publication gate set is incomplete or has unreviewed additions")
    failed = sorted(name for name, passed in gates.items() if passed is not True)
    if failed:
        raise DeploymentError(f"publication contains failed gates: {failed}")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != REQUIRED_PUBLICATION_ARTIFACTS:
        missing = sorted(REQUIRED_PUBLICATION_ARTIFACTS - set(artifacts or {}))
        extra = sorted(set(artifacts or {}) - REQUIRED_PUBLICATION_ARTIFACTS)
        raise DeploymentError(f"publication artifact set differs: missing={missing} extra={extra}")
    checked = {
        name: umbrella_file_binding(binding, f"publication artifact {name}")
        for name, binding in sorted(artifacts.items())
    }
    if checked["investorDeckPptx"]["path"] != "outputs/zkdeal-investor-deck-business-model-v3-2026-08.pptx":
        raise DeploymentError("publication binds the wrong investor deck artifact")
    return {
        "manifestSha256": sha256_file(publication_path),
        "physicalEvidenceManifestSha256": physical["manifestSha256"],
        "artifacts": checked,
        "gates": sorted(gates),
    }


def validate_compose_candidate_images(path: Path, images: dict[str, str]) -> None:
    values = load_env_file(path)
    for variable, image_name in COMPOSE_IMAGE_MAP.items():
        observed = validate_reference(values.get(variable), f"compose {variable}")
        if observed != images[image_name]:
            raise DeploymentError(
                f"compose {variable} differs from candidate images.{image_name}"
            )


def merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def helm_image_reference(value: object, label: str) -> str:
    if not isinstance(value, dict):
        raise DeploymentError(f"{label} image is not an object")
    repository = value.get("repository")
    digest = value.get("digest")
    tag = value.get("tag")
    if tag not in (None, ""):
        raise DeploymentError(f"{label} image retains a tag")
    return validate_reference(f"{repository}@{digest}", f"{label} image")


def validate_helm_candidate_images(path: Path, images: dict[str, str]) -> None:
    try:
        defaults = yaml.safe_load((ROOT / "helm/zkdeal/values.yaml").read_text(encoding="utf-8"))
        overlay = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DeploymentError(f"cannot load candidate Helm values: {exc}") from exc
    if not isinstance(defaults, dict) or not isinstance(overlay, dict):
        raise DeploymentError("candidate Helm values must be a YAML object")
    values = merge_dict(defaults, overlay)
    components = values.get("components")
    if not isinstance(components, dict):
        raise DeploymentError("candidate Helm values have no components")
    for component, image_name in HELM_COMPONENT_IMAGE_MAP.items():
        config = components.get(component)
        if not isinstance(config, dict):
            raise DeploymentError(f"candidate Helm values omit component {component}")
        if config.get("enabled", True) is False:
            continue
        observed = helm_image_reference(config.get("image"), f"components.{component}")
        if observed != images[image_name]:
            raise DeploymentError(
                f"Helm components.{component} image differs from candidate images.{image_name}"
            )
    unexpected_enabled = sorted(
        name
        for name, config in components.items()
        if isinstance(config, dict)
        and config.get("enabled", True) is not False
        and name not in HELM_COMPONENT_IMAGE_MAP
    )
    if unexpected_enabled:
        raise DeploymentError(
            f"candidate Helm values enable unmapped owner components: {unexpected_enabled}"
        )
    operations = values.get("operations")
    if not isinstance(operations, dict):
        raise DeploymentError("candidate Helm values have no operations")
    backup = operations.get("backup")
    if isinstance(backup, dict) and backup.get("enabled") is True:
        if helm_image_reference(backup.get("image"), "operations.backup") != images["backup"]:
            raise DeploymentError("Helm backup image differs from candidate images.backup")
    restore = operations.get("restore")
    if isinstance(restore, dict) and restore.get("enabled") is True:
        if helm_image_reference(restore.get("image"), "operations.restore") != images["backup"]:
            raise DeploymentError("Helm restore image differs from candidate images.backup")
    promotion = operations.get("promotion")
    if isinstance(promotion, dict) and promotion.get("enabled") is True:
        for section, image_name in (("controller", "promotionController"), ("provider", "failoverProvider")):
            config = promotion.get(section)
            if not isinstance(config, dict) or config.get("enabled") is not True:
                raise DeploymentError(f"candidate Helm values do not enable promotion {section}")
            if helm_image_reference(config.get("image"), f"operations.promotion.{section}") != images[image_name]:
                raise DeploymentError(
                    f"Helm promotion {section} image differs from candidate images.{image_name}"
                )
        if promotion.get("manualJobEnabled") is True:
            if helm_image_reference(promotion.get("image"), "operations.promotion.manualJob") != images["postgres"]:
                raise DeploymentError("Helm manual promotion image differs from candidate images.postgres")
        authority = promotion.get("provider", {}).get("signerAuthority")
        if isinstance(authority, dict) and authority.get("enabled") is True:
            if helm_image_reference(authority.get("image"), "operations.promotion.provider.signerAuthority") != images["frontDoor"]:
                raise DeploymentError("Helm signer authority image differs from candidate images.frontDoor")
    migration = values.get("migration")
    if isinstance(migration, dict) and migration.get("enabled") is True:
        if helm_image_reference(migration.get("image"), "migration") != images["coordinator"]:
            raise DeploymentError("Helm migration image differs from candidate images.coordinator")


def validate_kurtosis_candidate_images(
    paths: dict[str, Path], images: dict[str, str],
) -> dict[str, dict[str, object]]:
    expected = {
        # The local package provisions the development-shape hosted plane from
        # the exact candidate image set; the three assertion packages start
        # only their digest-pinned runner against the verified candidate
        # topology (scripts/candidate_topology.py check).
        "local": {
            "server": images["coordinator"],
            "headless": images["headlessNode"],
            "prover": images["prover"],
            "agent": images["agent"],
            "postgres": images["postgres"],
            "minio": images["minio"],
            "minio_client": images["minioClient"],
        },
        "failover": {"failover_runner": images["failoverRunner"]},
        "acceptance-matrix": {"acceptance_runner": images["acceptanceRunner"]},
        "soak": {"runner": images["soakRunner"]},
    }
    payloads: dict[str, dict[str, object]] = {}
    for package, path in paths.items():
        payload = validate_kurtosis_args(package, path)
        if payload["images"] != expected[package]:
            raise DeploymentError(f"Kurtosis {package} images differ from the candidate descriptor")
        payloads[package] = payload
    return payloads


def nested_candidate_input(base: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise DeploymentError(f"{label} must be a nonempty file path")
    raw = Path(value)
    if not raw.is_absolute():
        raw = base.parent / raw
    if raw.is_symlink():
        raise DeploymentError(f"{label} must not be a symlink")
    path = raw.resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise DeploymentError(f"{label} escaped the deployment project") from exc
    if not path.is_file() or path.is_symlink():
        raise DeploymentError(f"{label} is absent, not regular, or a symlink")
    return path


def acceptance_nested_input_hashes(
    args_path: Path, payload: dict[str, object],
) -> dict[str, object]:
    """Bind nested plan/credential bytes without returning credential values."""
    plan_path = nested_candidate_input(args_path, payload.get("plan_file"), "acceptance plan_file")
    plan_hash = sha256_file(plan_path)
    if plan_hash != payload.get("plan_sha256"):
        raise DeploymentError("acceptance plan_file differs from plan_sha256")
    plan = load_json(plan_path)
    expected_auth = plan.get("authTokenSha256")
    auth_files = payload.get("auth_files")
    if not isinstance(expected_auth, dict) or not isinstance(auth_files, dict):
        raise DeploymentError("acceptance plan/auth_files bindings are absent")
    if set(expected_auth) != set(auth_files):
        raise DeploymentError("acceptance plan and auth_files role sets differ")
    checked_auth: dict[str, dict[str, object]] = {}
    for alias in sorted(auth_files):
        path = nested_candidate_input(
            args_path, auth_files[alias], f"acceptance auth_files.{alias}",
        )
        raw = path.read_bytes()
        try:
            token = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DeploymentError(f"acceptance auth_files.{alias} is not UTF-8") from exc
        if token != token.strip():
            raise DeploymentError(
                f"acceptance auth_files.{alias} must contain exact token bytes without whitespace",
            )
        file_hash = sha256_file(path)
        if file_hash != expected_auth[alias]:
            raise DeploymentError(
                f"acceptance auth_files.{alias} raw bytes differ from authTokenSha256",
            )
        checked_auth[alias] = {
            "path": path.relative_to(ROOT.resolve()).as_posix(),
            "sha256": file_hash,
        }
    return {
        "plan": {
            "path": plan_path.relative_to(ROOT.resolve()).as_posix(),
            "sha256": plan_hash,
        },
        "authFiles": checked_auth,
        "credentialValuesRecorded": False,
    }


def validate_candidate_deployment_inputs(
    args: argparse.Namespace, images: dict[str, str],
) -> dict[str, object]:
    paths = {
        "candidateTopology": regular_project_input(
            args.candidate_topology_file, "candidate private topology",
        ),
        "composeEnv": regular_project_input(args.compose_env_file, "candidate Compose environment"),
        "helmValues": regular_project_input(args.helm_values_file, "candidate Helm values"),
        "kurtosisLocal": regular_project_input(args.kurtosis_local_args, "candidate Kurtosis local args"),
        "kurtosisFailover": regular_project_input(args.kurtosis_failover_args, "candidate Kurtosis failover args"),
        "kurtosisAcceptance": regular_project_input(args.kurtosis_acceptance_args, "candidate Kurtosis acceptance args"),
        "kurtosisSoak": regular_project_input(args.kurtosis_soak_args, "candidate Kurtosis soak args"),
    }
    validate_compose_candidate_images(paths["composeEnv"], images)
    validate_helm_candidate_images(paths["helmValues"], images)
    candidate_path = regular_project_input(args.candidate_file, "candidate descriptor")
    topology = validate_candidate_topology(
        candidate_path, paths["candidateTopology"], inspect_live=False,
    )
    payloads = validate_kurtosis_candidate_images({
        "local": paths["kurtosisLocal"],
        "failover": paths["kurtosisFailover"],
        "acceptance-matrix": paths["kurtosisAcceptance"],
        "soak": paths["kurtosisSoak"],
    }, images)
    result: dict[str, object] = {name: sha256_file(path) for name, path in paths.items()}
    result["candidateTopologyVerification"] = topology
    result["kurtosisAcceptanceNested"] = acceptance_nested_input_hashes(
        paths["kurtosisAcceptance"], payloads["acceptance-matrix"],
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan")
    owner = sub.add_parser("owner")
    owner.add_argument("--candidate-file", required=True)
    release = sub.add_parser("release")
    release.add_argument("--candidate-file", required=True)
    release.add_argument("--candidate-topology-file", required=True)
    release.add_argument("--compose-env-file", required=True)
    release.add_argument("--helm-values-file", required=True)
    release.add_argument("--kurtosis-local-args", required=True)
    release.add_argument("--kurtosis-failover-args", required=True)
    release.add_argument("--kurtosis-acceptance-args", required=True)
    release.add_argument("--kurtosis-soak-args", required=True)
    physical = sub.add_parser("physical")
    physical.add_argument("--candidate-file", required=True)
    physical.add_argument("--physical-manifest-file", required=True)
    publication = sub.add_parser("publication")
    publication.add_argument("--candidate-file", required=True)
    publication.add_argument("--physical-manifest-file", required=True)
    publication.add_argument("--publication-manifest-file", required=True)
    args = parser.parse_args()
    try:
        require_container()
        if args.command == "plan":
            print(json.dumps({
                "schemaVersion": 1,
                "ordered": True,
                "gates": [
                    {"position": index, "name": name, "classification": classification, "command": command}
                    for index, (name, classification, command) in enumerate(PLAN, 1)
                ],
            }, indent=2))
            return 0
        value = candidate(args.candidate_file)
        hosted, _ = validate_owner_seal(value)
        if args.command == "owner":
            validate_phase_a_unminted(value)
        result: dict[str, Any] = {
            "candidateId": value["candidateId"],
            "phase": args.command,
            "ownerDatabaseSchema": hosted.get("databaseSchema"),
            "ownerProtocolJournalSchema": hosted.get("protocolJournalSchema"),
            "ownerBroadSeal": "passed",
            "hostedIntegrationAcceptanceToken": value["ownerBroadSeal"]["hostedIntegrationAcceptanceToken"],
        }
        if args.command in {"release", "physical", "publication"}:
            result["images"] = validate_release_images(value)
            result["zkvmGeneratedTrustRoot"] = validate_generated_trust_root(
                value, result["images"],
            )
        if args.command == "release":
            result["deploymentInputs"] = validate_candidate_deployment_inputs(args, result["images"])
            errors = deployment_capability_errors()
            if errors:
                raise DeploymentError("deployment capability sync is not green: " + "; ".join(errors))
            result["deploymentCapabilitySync"] = "passed"
        elif args.command == "physical":
            candidate_path = regular_project_input(args.candidate_file, "candidate descriptor")
            physical_path = regular_project_input(
                args.physical_manifest_file, "physical evidence manifest",
            )
            result["physicalEvidence"] = validate_physical_evidence(
                value, candidate_path, physical_path,
            )
        elif args.command == "publication":
            candidate_path = regular_project_input(args.candidate_file, "candidate descriptor")
            physical_path = regular_project_input(
                args.physical_manifest_file, "physical evidence manifest",
            )
            publication_path = regular_project_input(
                args.publication_manifest_file, "publication manifest",
            )
            result["publication"] = validate_publication(
                value, candidate_path, physical_path, publication_path,
            )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (DeploymentError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
