from __future__ import annotations

import hashlib
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import DeploymentError, sha256_file  # noqa: E402
from soak import REQUIRED_FAULTS, canonical_event_bytes, validate_manifest, verify_closure  # noqa: E402


class ReleaseSoakTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".test-tmp" / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        digest = "a" * 64
        self.manifest = {
            "schemaVersion": 1,
            "kind": "zkdeal-release-soak",
            "durationSeconds": 43200,
            "umbrellaSourceManifestSha256": digest,
            "sourceBundleArchiveSha256": digest,
            "sourceClosureSha256": digest,
            "physicalScenario": {
                "settlementScenarioSha256": digest,
                "deploymentAddressesSha256": digest,
                "ownerDurableCapabilitiesSha256": digest,
                "ownerAcceptanceToken": f"sha256:{digest}",
                "hostedBatchInput": "BatchInputV5",
                "fixturePrepare": False,
                "realCudaProof": True,
                "aggregateMembers": 8,
                "transactionBlobs": 6,
                "partialSuccessApplied": 7,
                "partialSuccessFailed": 1,
                "successOnlyCharging": True,
                "withdrawalClaim": True,
                "sponsorship": True,
                "preFinalityReorg": True,
                "freshDeployment": True,
                "restartResume": True,
                "ownerDurablePublishing": True,
                "castEncodingOnly": True,
                "directBroadcastAllowed": False,
            },
            "images": {
                name: f"registry.invalid/zkdeal/{name}@sha256:{digest}"
                for name in ("coordinator", "indexer", "reconciler", "headless", "prover", "ownerAcceptanceRunner")
            },
            "trustRoots": {
                "contractsAbiSha256": digest,
                "circuitManifestSha256": digest,
                "zkvmArtifactsSha256": digest,
                "generatedTrustRootClosureSha256": digest,
            },
            "chainSeed": {
                "chainId": 31337,
                "genesisHash": "0x" + "b" * 64,
                "seedSha256": digest,
                "rpcEndpoints": ["http://rpc-a:8545", "http://rpc-b:8545"],
            },
            "expected": {"usageUnits": 7, "chargesWei": "11"},
            "budgets": {
                "maxUnresolvedSafetyEvents": 0,
                "maxUnresolvedClaims": 0,
                "maxDuplicateNonces": 0,
                "maxDuplicateCharges": 0,
                "maxFairnessWaitMs": 5000,
                "maxDeadlineMisses": 0,
            },
            "scheduledFaults": [
                {"kind": name, "atSecond": (index + 1) * 100}
                for index, name in enumerate(sorted(REQUIRED_FAULTS))
            ],
        }
        self.manifest_path = self.root / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        parent = ROOT / ".test-tmp"
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()

    def body(self) -> list[dict]:
        sealed = "c" * 64
        body = [
            {"kind": "room-create", "roomId": "room-1"},
            {"kind": "submit", "jobId": "job-1", "eventId": "event-1", "cursor": 1},
            {"kind": "lease"},
            {"kind": "tx-broadcast", "nonceId": "signer:1", "txHash": "0x" + "d" * 64, "publisher": "owner-durable-operation", "operationId": "operation-aggregate"},
            {"kind": "live-prepare", "preparedFrom": "live-room-engine-state", "batchInput": "BatchInputV5", "fixture": False},
            {"kind": "prove", "outputId": "proof-1", "sealedOutputSha256": sealed, "realCuda": True, "proofMode": "groth16"},
            {"kind": "verify", "realReceipt": True},
            {"kind": "blob-archive"},
            {"kind": "aggregate-settle", "members": 8, "transactionBlobs": 6, "applied": 7, "failed": 1, "successfulCharges": 7, "failedCharges": 0, "publisher": "owner-durable-operation", "ownerOperationId": "operation-aggregate", "selector": "0x5e8b37ac", "transactionType": 3, "finalizedCanonical": True, "castBroadcast": False},
            {"kind": "sponsor", "payer": "payer-1", "beneficiary": "beneficiary-1", "refundRecipient": "payer-1", "doubleBilled": False, "publisher": "owner-durable-operation", "senderAuthoritySplit": True, "ownerOperations": {"reserve": "operation-reserve", "renew": "operation-renew", "checkpoint": "operation-checkpoint", "dispose": "operation-dispose"}, "selectors": {"reserve": "0x827ac259", "renew": "0xf180fe5d", "checkpoint": "0xe19bc67e", "dispose": "0xed97f11a"}, "finalizedCanonical": True},
            {"kind": "reorg", "preFinalityOrphaned": True, "canonicalRecovery": True, "duplicateNonce": False, "duplicateCharge": False},
            {"kind": "finalize", "outputId": "proof-1", "sealedOutputSha256": sealed},
            {"kind": "withdraw", "realProof": True, "claimed": True, "replayRejected": True, "publisher": "owner-durable-operation", "ownerOperationId": "operation-withdrawal", "selector": "0xb051a9f8", "finalizedCanonical": True},
            {"kind": "reconcile"},
            {"kind": "charge", "chargeId": "charge-1", "usageUnits": 7, "chargeWei": "11"},
        ]
        for fault in self.manifest["scheduledFaults"]:
            body.append({"kind": "fault", "fault": fault["kind"], "scheduledAtSecond": fault["atSecond"]})
            body.append({"kind": "recovered", "fault": fault["kind"]})
        return body

    def write_journal(self, body: list[dict]) -> Path:
        for seq, event in enumerate(body, 1):
            event["seq"] = seq
        closure = {
            "seq": len(body) + 1,
            "kind": "closure",
            "manifestSha256": sha256_file(self.manifest_path),
            "priorEventsSha256": hashlib.sha256(canonical_event_bytes(body)).hexdigest(),
            "durationSeconds": 43200,
            "sealedOutputsUnchanged": True,
            "resumeVerified": True,
            "unresolvedSafetyEvents": 0,
            "unresolvedClaims": 0,
            "duplicateNonces": 0,
            "duplicateCharges": 0,
            "fairnessBudgetMet": True,
            "deadlineBudgetMet": True,
        }
        journal = self.root / "soak.jsonl"
        journal.write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in [*body, closure]),
            encoding="utf-8",
        )
        return journal

    def test_valid_durable_closure_passes(self):
        result = verify_closure(self.manifest_path, self.write_journal(self.body()))
        self.assertTrue(result["passed"])
        self.assertFalse(result["healthPollingOnly"])
        self.assertEqual(set(result["faults"]), REQUIRED_FAULTS)

    def test_duplicate_nonce_fails_independently_of_claimed_closure(self):
        body = self.body()
        body.append({"kind": "tx-broadcast", "nonceId": "signer:1", "txHash": "0x" + "e" * 64, "publisher": "owner-durable-operation", "operationId": "operation-replay"})
        with self.assertRaisesRegex(DeploymentError, "duplicate transaction nonce"):
            verify_closure(self.manifest_path, self.write_journal(body))

    def test_manifest_refuses_tag_only_owner_image(self):
        self.manifest["images"]["headless"] = "zkdeal-headless:latest"
        with self.assertRaisesRegex(DeploymentError, "repository@sha256"):
            validate_manifest(self.manifest)

    def test_manifest_refuses_fixture_or_non_live_physical_scenario(self):
        self.manifest["physicalScenario"]["fixturePrepare"] = True
        with self.assertRaisesRegex(DeploymentError, "fixturePrepare"):
            validate_manifest(self.manifest)

    def test_manifest_and_journal_refuse_direct_broadcast(self):
        self.manifest["physicalScenario"]["directBroadcastAllowed"] = True
        with self.assertRaisesRegex(DeploymentError, "directBroadcastAllowed"):
            validate_manifest(self.manifest)
        self.manifest["physicalScenario"]["directBroadcastAllowed"] = False
        body = self.body()
        body[3]["publisher"] = "cast-send"
        with self.assertRaisesRegex(DeploymentError, "owner durable-operation"):
            verify_closure(self.manifest_path, self.write_journal(body))


if __name__ == "__main__":
    unittest.main()
