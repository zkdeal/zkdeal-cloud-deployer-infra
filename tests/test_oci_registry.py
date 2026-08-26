from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class OciRegistryTests(unittest.TestCase):
    def test_registry_is_pinned_persistent_and_delete_disabled(self):
        compose = yaml.safe_load((ROOT / "compose/compose.registry.yaml").read_text(encoding="utf-8"))
        service = compose["services"]["registry"]
        self.assertRegex(service["image"], r"^registry@sha256:[0-9a-f]{64}$")
        self.assertEqual(service["environment"]["REGISTRY_STORAGE_DELETE_ENABLED"], "false")
        self.assertTrue(any("registry-data:/var/lib/registry" in value for value in service["volumes"]))
        self.assertEqual(service["cap_drop"], ["ALL"])
        lock = json.loads((ROOT / "config/images.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(service["image"].split("@", 1)[1], lock["images"]["localOciRegistry"]["digest"])

    def test_publication_and_backup_are_immutable_and_bounded(self):
        script = (ROOT / "scripts/oci_registry.py").read_text(encoding="utf-8")
        for marker in (
            "immutableReference", "daemonRepoDigestVerified", "mutableReferenceRecorded",
            "candidate transport reference already exists", "MAX_MEMBERS", "MAX_TOTAL_BYTES",
            "registry archive contains an unsafe path", "target volume already exists",
            "zkdeal/oci-promotion-envelope/v1", "sourceGeneratedCompositeSealSha256",
            "promotion MAC key must be mounted outside", "promotion receipt MAC verification failed",
            "release promotion changed the staged manifest digest", "sameDaemonImageId",
        ):
            self.assertIn(marker, script)
        acceptance = (ROOT / "tests/acceptance/oci-registry.sh").read_text(encoding="utf-8")
        for marker in (
            "delete_status", "verify-backup", "verify-promotion", "tampered",
            "signedExactDigestPromotion", "freshVolumeRestore", "daemonDigestPullAfterRestore",
        ):
            self.assertIn(marker, acceptance)


if __name__ == "__main__":
    unittest.main()
