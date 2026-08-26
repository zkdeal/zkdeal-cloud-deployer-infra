from __future__ import annotations

import sys
import unittest
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from render_reference_docs import build  # noqa: E402


class ReferenceDocTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rendered = build()

    def test_generated_reference_semantics_match_owner_sources(self):
        rendered = self.rendered
        self.assertIn(b"/health", rendered["api.md"])
        self.assertIn(b"RoomManager", rendered["abi.md"])
        self.assertIn(b'"openapi": "3.1.0"', rendered["openapi.reference.json"])
        capabilities = json.loads(rendered["hosted-service-capabilities.reference.json"])
        self.assertGreaterEqual(capabilities["databaseSchema"], 17)
        self.assertIn(b'"publisherBroadcastEntrypoint": true', rendered["hosted-service-capabilities.reference.json"])
        self.assertIn(b'"durableNonceJournal": true', rendered["hosted-service-capabilities.reference.json"])
        self.assertIn(b'"zkdeal_getRoomEvents"', rendered["hosted-service-capabilities.reference.json"])
        self.assertIn(b'"zkdeal_getFinalityStatus"', rendered["openapi.reference.json"])
        self.assertIn(b'"zkdeal_adminRetention"', rendered["openapi.reference.json"])
        self.assertIn(b'"service": "zkdeal-headless-room-node"', rendered["headless-room-node-capabilities.reference.json"])
        self.assertIn(b'"exclusiveStateLock": true', rendered["headless-room-node-capabilities.reference.json"])
        self.assertIn(b'"operationId": "applyCapacityOperation"', rendered["capacity-provider-v1.openapi.reference.json"])
        for facet in (b"RoomManagerHostingFacet", b"RoomManagerChallengeFacet", b"RoomPoolHostingFacet"):
            self.assertIn(facet, rendered["abi.md"])
        for lifecycle in (
            b"beginNodeDrain(bytes32)", b"0xd7ceb78e",
            b"retireNode(bytes32)", b"0x13ca0607",
            b"quarantineNode(bytes32)", b"0xa4588ca0",
            b"UnsafeNodeRetirement(uint64,bytes32)", b"NodeDrainStarted(bytes32,uint64,bytes32,uint64)",
            b"NodeRetired(bytes32,uint64)",
        ):
            self.assertIn(lifecycle, rendered["abi.md"])
        self.assertIn(b"Selector", rendered["abi.md"])
        self.assertIn(b"Struct and tuple layouts", rendered["abi.md"])
        self.assertIn(b"FinalizedCheckpointRecorded", rendered["event-to-indexer.md"])
        mapping = json.loads(rendered["event-to-indexer.json"])
        self.assertEqual(mapping["roomPool"]["events"]["FinalizedCheckpointRecorded"], "finality")
        self.assertEqual(mapping["roomManager"]["calls"]["submitBatch"], "batch")
        self.assertEqual(mapping["roomPool"]["events"]["NodeDrainStarted"], "node-lifecycle")
        self.assertEqual(mapping["roomPool"]["events"]["NodeRetired"], "node-lifecycle")
        self.assertEqual(mapping["roomPool"]["calls"]["beginNodeDrain"], "node-lifecycle")
        self.assertEqual(mapping["roomPool"]["calls"]["retireNode"], "node-lifecycle")
        self.assertGreaterEqual(rendered["api.md"].count(b"/hosting/v1/"), 15)
        for content in rendered.values():
            self.assertNotIn(b"C:\\\\work\\\\zkdeal", content)

    def test_checked_in_references_are_fresh(self):
        rendered = self.rendered
        output = ROOT / "docs/generated"
        for name, content in rendered.items():
            with self.subTest(name=name):
                self.assertTrue((output / name).is_file(), f"missing {name}")
                self.assertEqual((output / name).read_bytes(), content, f"stale {name}")


if __name__ == "__main__":
    unittest.main()
