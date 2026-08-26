from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "contract-reference.md"


class ContractDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = GUIDE.read_text(encoding="utf-8")

    def test_role_and_signer_boundaries_are_explicit(self):
        required = {
            "SERVICE_MANAGER_ROLE": "createManagedRoomWithDataAvailability",
            "TREASURY_ROLE": "claimProtocolFees",
            "UPGRADER_ROLE": "configureFacets",
            "NODE_ADMIN_ROLE": "configureNodeAuthorities",
            "POOL_CONTROLLER_ROLE": "confirmCapacityProfile",
            "SPONSOR_ROLE": "renewRoomForWithPermit",
            "FINALITY_ORACLE_ROLE": "recordFinalizedCheckpoint",
        }
        for role, method in required.items():
            with self.subTest(role=role):
                self.assertIn(role, self.text)
                self.assertIn(method, self.text)
        self.assertIn("The three node accounts must be nonzero and pairwise distinct", self.text)
        self.assertIn("A standby receives no signer address", self.text)
        self.assertIn("withdrawal-relayer identity and never the provider payout", self.text)
        for role, method in (("NODE_ADMIN_ROLE", "retireNode"), ("POOL_CONTROLLER_ROLE", "beginNodeDrain")):
            with self.subTest(lifecycle_role=role):
                self.assertIn(role, self.text)
                self.assertIn(method, self.text)
        for marker in (
            "0xd7ceb78e", "0x13ca0607", "0xa4588ca0", "DRAINING` (enum 7)",
            "RETIRED` (6)", "UnsafeNodeRetirement", "NodeDrainStarted", "NodeRetired",
        ):
            self.assertIn(marker, self.text)

    def test_data_availability_and_aggregate_invariants_are_exact(self):
        for value in ("CALLDATA_REQUIRED", "BLOB_REQUIRED", "BLOB_PREFERRED"):
            self.assertIn(value, self.text)
        for field in (
            "canonicalDataHash", "canonicalDataLength", "blobStartIndex",
            "blobVersionedHashes", "commitments", "evaluationPoints",
            "evaluations", "kzgProofs", "equivalenceSeal",
            "fallbackDeadlineBlock", "fallbackSignature",
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.text)
        self.assertIn("MAX_AGGREGATE_ROOMS = 8", self.text)
        self.assertIn("MAX_BLOBS_PER_BATCH = 6", self.text)
        self.assertIn("transaction can succeed with member-level partial success", self.text)
        self.assertNotIn("A partial aggregate is not success", self.text)
        self.assertIn("failureSelector", self.text)

    def test_sponsorship_renewal_and_withdrawal_are_typed(self):
        for method in (
            "reserveRoomForWithPermit", "reserveAndStartForWithPermit",
            "reserveAndStartForWithDataAvailabilityWithPermit",
            "requestColdPreparationForWithPermit", "renewRoomForWithPermit",
        ):
            self.assertIn(method, self.text)
        self.assertIn("unused-token refund", self.text)
        self.assertIn("strictly newer", self.text)
        self.assertIn("MAX_WITHDRAWAL_PROOF_DEPTH = 15", self.text)
        self.assertIn("MAX_WITHDRAWALS_PER_EPOCH = 32768", self.text)
        self.assertIn("deploymentDomain, roomId, outboxEpoch, index, approverEpoch", self.text)
        self.assertIn("The proof is positional", self.text)
        self.assertIn("isWithdrawalClaimed", self.text)

    def test_field_level_event_contract_and_invariants_are_named(self):
        for phrase in (
            "ABI signature/topic", "destination projection", "retraction behavior",
            "Escrow solvency", "No duplicate outcome charge",
            "One-time renewal checkpoint", "One-time withdrawal index",
            "Blob equivalence", "Fence before mutation",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)


if __name__ == "__main__":
    unittest.main()
