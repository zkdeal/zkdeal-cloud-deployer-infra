from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_owner_capabilities import (  # noqa: E402
    headless_hosted_integration_errors,
    hosted_integration_evidence_errors,
    prover_agent_trace_errors,
)


class HeadlessHostedCapabilityTests(unittest.TestCase):
    def test_prover_agent_trace_contract_and_join_fixture_are_exact(self):
        artifacts = json.loads((ROOT / "config/artifacts.json").read_text(encoding="utf-8"))
        by_id = {item["id"]: item for item in artifacts["artifacts"]}
        self.assertEqual(prover_agent_trace_errors(ROOT.parent, by_id), [])

    def test_hosted_integration_manifest_and_source_bytes_are_hash_bound(self):
        with tempfile.TemporaryDirectory() as folder:
            umbrella = Path(folder)
            source = umbrella / "app-node/source.ts"
            source.parent.mkdir(parents=True)
            source.write_text("export const witness = 'BatchInputV5';\n", encoding="utf-8")
            import hashlib
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            evidence = {
                "schemaVersion": 1,
                "acceptanceId": "zkdeal-hosted-room-batch-current-zkvm",
                "passed": True,
                "contract": {
                    "fixturePrepare": False,
                    "hostedLegacyGroth16": False,
                    "engineToBatchInputV5": True,
                    "durablePostgresQueue": True,
                    "externalProver": True,
                    "ownerDerivedSubmitBatch": True,
                    "restartResume": True,
                    "canonicalFinalityEvidence": True,
                },
                "gates": {
                    name: {"passed": True, "tests": 1}
                    for name in (
                        "ownerJointHttp", "l2Engine", "roomNode", "roomClient",
                        "zkvmTypescript", "rustLivePrepare", "rustOwnerVectors",
                    )
                },
                "externalProofGates": {
                    "routineSemanticCoverageComplete": True,
                    "realRisc0ProofCases": 4,
                    "realLigeroProofCases": 1,
                    "releaseRequirement": "selected backend",
                },
                "sourceArtifacts": {"app-node/source.ts": source_hash},
            }
            evidence_path = umbrella / "owner/acceptance.json"
            evidence_path.parent.mkdir(parents=True)
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            token = "sha256:" + hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            manifest = {"managedL1Operations": {"roomBatch": {"hostedIntegration": {
                "acceptanceManifest": "owner/acceptance.json",
                "acceptanceToken": token,
            }}}}
            spec = {"path": "owner/acceptance.json"}
            self.assertEqual(hosted_integration_evidence_errors(umbrella, manifest, spec), [])
            source.write_text("changed\n", encoding="utf-8")
            self.assertTrue(any(
                "source hash differs" in error
                for error in hosted_integration_evidence_errors(umbrella, manifest, spec)
            ))

    def pins(self) -> dict[str, object]:
        return {
            "hostedAdmissionLease": True,
            "hostedRoomBatchEnabled": True,
            "hostedEngineToBatchInputV5": True,
            "hostedDurablePostgresQueue": True,
            "hostedExternalProver": True,
            "hostedRestartResume": True,
            "hostedFixturePrepare": False,
            "hostedLegacyGroth16": False,
            "hostedIntegrationAcceptanceToken": "sha256:" + "a" * 64,
        }

    def room_node(self) -> dict[str, object]:
        return {
            "modes": {"hosted": {
                "production": True,
                "admissions": {
                    "lease": "/hosting/v1/admissions/:roomId/lease",
                    "ack": "/hosting/v1/admissions/:roomId/ack",
                    "durableContiguousPrefix": True,
                },
                "proofQueue": {
                    "transport": "remote-postgresql-minio",
                    "endpoints": [
                        "/hosting/v1/rooms/prepare-batch", "/v5/rooms/prove", "/v5/rooms/verify",
                    ],
                    "contentDigestVerified": True,
                    "gpuOnlyStage": "/v5/rooms/prove",
                },
                "l1": {
                    "directSender": False,
                    "operation": "RoomManager.submitBatch",
                    "durableNonceSignerWatcher": True,
                    "requireFinalized": True,
                },
                "fixturePrepare": False,
                "legacyGroth16Checkpoint": False,
            }},
        }

    def owner(self) -> dict[str, object]:
        return {
            "managedL1Operations": {"roomBatch": {
                "enabled": True,
                "hostedIntegration": {
                    "enabled": True,
                    "acceptanceToken": "sha256:" + "a" * 64,
                    "fixturePrepare": False,
                    "hostedLegacyGroth16": False,
                    "engineToBatchInputV5": True,
                    "durablePostgresQueue": True,
                    "externalProver": True,
                    "restartResume": True,
                },
            }},
        }

    def test_standalone_room_node_is_not_hosted_path_evidence(self):
        errors = headless_hosted_integration_errors(
            {"proving": {"artifactSource": "operator-mounted-local-files"}},
            {},
            (("test", self.pins()),),
        )
        self.assertTrue(any("no hostedIntegration capability" in item for item in errors))

    def test_complete_owner_contract_and_pins_are_accepted(self):
        errors = headless_hosted_integration_errors(
            self.room_node(),
            self.owner(),
            (("test", self.pins()),),
        )
        self.assertEqual(errors, [])

    def test_each_owner_and_deployment_claim_fails_closed(self):
        owner = self.owner()
        owner_fields = (
            "enabled", "engineToBatchInputV5", "durablePostgresQueue",
            "externalProver", "restartResume",
        )
        for field in owner_fields:
            candidate = self.owner()
            candidate["managedL1Operations"]["roomBatch"]["hostedIntegration"][field] = False
            with self.subTest(ownerField=field):
                self.assertTrue(headless_hosted_integration_errors(
                    self.room_node(), candidate, (("test", self.pins()),),
                ))
        for field in ("fixturePrepare", "hostedLegacyGroth16"):
            candidate = self.owner()
            candidate["managedL1Operations"]["roomBatch"]["hostedIntegration"][field] = True
            with self.subTest(ownerForbiddenField=field):
                self.assertTrue(headless_hosted_integration_errors(
                    self.room_node(), candidate, (("test", self.pins()),),
                ))
        candidate = self.owner()
        candidate["managedL1Operations"]["roomBatch"]["hostedIntegration"]["acceptanceToken"] = "manual-pass"
        self.assertTrue(headless_hosted_integration_errors(
            self.room_node(), candidate, (("test", self.pins()),),
        ))
        for field in self.pins():
            pins = self.pins()
            pins[field] = (
                "" if field == "hostedIntegrationAcceptanceToken"
                else not bool(pins[field])
            )
            with self.subTest(pin=field):
                self.assertTrue(headless_hosted_integration_errors(
                    self.room_node(), owner, (("test", pins),),
                ))


if __name__ == "__main__":
    unittest.main()
