from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import load_json, validate_profile  # noqa: E402


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.local = load_json(ROOT / "config/profiles/local.json")

    def test_local_profile_is_valid(self):
        result = validate_profile(self.local, check_artifacts=True)
        self.assertEqual(result.errors, ())
        self.assertTrue(result.warnings)

    def test_named_topology_profiles_are_valid(self):
        for name in ("staging.json", "single-region.json", "warm-standby.json"):
            with self.subTest(name=name):
                profile = load_json(ROOT / "config/profiles" / name)
                result = validate_profile(profile)
                self.assertEqual(result.errors, ())

    def test_production_example_fails_closed(self):
        profile = load_json(ROOT / "config/profiles/production.example.json")
        result = validate_profile(profile)
        joined = "\n".join(result.errors)
        self.assertIn("placeholder RPC", joined)
        self.assertIn("zero contract", joined)
        self.assertIn("no verified digest", joined)
        self.assertNotIn("signer role", joined)

    def test_production_signers_are_distinct_and_role_scoped(self):
        profile = load_json(ROOT / "config/profiles/production.example.json")
        roles = profile["security"]["signerRoles"]
        roles["payout"]["address"] = roles["liveness"]["address"]
        roles["payout"]["allowedServices"].append("sponsorship")
        joined = "\n".join(validate_profile(profile).errors)
        self.assertIn("distinct endpoints", joined)
        self.assertIn("signer role payout must be isolated", joined)

    def test_known_dev_signer_key_is_forbidden_outside_local(self):
        profile = load_json(ROOT / "config/profiles/staging.json")
        profile["security"]["existingSecret"] = (
            "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        )
        joined = "\n".join(validate_profile(profile).errors)
        self.assertIn("known development private key", joined)

    def test_filesystem_rejects_multiple_writers(self):
        profile = copy.deepcopy(self.local)
        profile["services"]["coordinator"]["replicas"] = 2
        result = validate_profile(profile)
        self.assertTrue(any("filesystem-authoritative coordinator" in error for error in result.errors))

    def test_external_backend_requires_fencing_for_multiple_writers(self):
        profile = copy.deepcopy(self.local)
        profile["storage"]["backend"] = "external-transactional"
        profile["services"]["queue"]["enabled"] = False
        profile["services"]["coordinator"]["replicas"] = 2
        result = validate_profile(profile)
        self.assertTrue(any("transactional fencing" in error for error in result.errors))
        profile["availability"]["fencing"] = "owner-service-transactional"
        profile["availability"]["mode"] = "warm-standby"
        profile["availability"]["warmStandbyReplicas"] = 1
        profile["availability"]["rtoMinutes"] = 5
        self.assertFalse(validate_profile(profile).errors)

    def test_all_json_files_parse(self):
        for path in ROOT.rglob("*.json"):
            with self.subTest(path=path):
                json.loads(path.read_text(encoding="utf-8"))

    def test_no_configuration_references_the_retired_placeholder_image(self):
        # The zkdeal-hosting-services placeholder image was removed with the
        # hosting-service topology consolidation and must never reappear in
        # any deployment configuration or image inventory.
        for pattern in ("config/**/*.json", "config/*.json"):
            for path in ROOT.glob(pattern):
                with self.subTest(path=path):
                    self.assertNotIn(
                        "zkdeal-hosting-services",
                        path.read_text(encoding="utf-8"),
                    )


if __name__ == "__main__":
    unittest.main()
