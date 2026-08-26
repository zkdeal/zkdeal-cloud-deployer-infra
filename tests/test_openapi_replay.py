from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import DeploymentError  # noqa: E402
from openapi_live_replay import json_pointer, replay_example  # noqa: E402


class OpenApiLiveReplayPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.openapi = json.loads(
            (ROOT / "tests/fixtures/hosting-v1.openapi.fixture.json").read_text(encoding="utf-8")
        )

    def mutation(self) -> dict:
        return {
            "name": "mutation-policy-test",
            "method": "POST",
            "path": "/hosting/v1/admin/reconcile",
            "auth": "bearer",
            "body": {"roomId": "42"},
            "mutation": True,
            "idempotencyKey": "mutation-policy-test-v1",
            "stableResponsePointers": ["/operationId"],
            "expectedStatus": 202,
        }

    def test_mutation_requires_a_key_and_stable_replay_assertion_before_network(self):
        no_key = self.mutation()
        no_key.pop("idempotencyKey")
        with self.assertRaisesRegex(DeploymentError, "idempotencyKey"):
            replay_example(self.openapi, "http://unreachable.invalid", no_key, "token", 0.01)

        no_stable_pointer = self.mutation()
        no_stable_pointer["stableResponsePointers"] = []
        with self.assertRaisesRegex(DeploymentError, "stableResponsePointers"):
            replay_example(self.openapi, "http://unreachable.invalid", no_stable_pointer, "token", 0.01)

    def test_example_files_cannot_embed_authorization_secrets(self):
        example = self.mutation()
        example["headers"] = {"Authorization": "Bearer checked-in-secret"}
        with self.assertRaisesRegex(DeploymentError, "embeds an authorization secret"):
            replay_example(self.openapi, "http://unreachable.invalid", example, "token", 0.01)

    def test_json_pointer_requires_an_exact_existing_value(self):
        self.assertEqual(json_pointer({"result": {"operationId": "op-1"}}, "/result/operationId"), "op-1")
        with self.assertRaisesRegex(DeploymentError, "lacks stable pointer"):
            json_pointer({"result": {}}, "/result/operationId")

    def test_acceptance_is_explicitly_fixture_only(self):
        acceptance = (ROOT / "tests/acceptance/openapi-live-replay.sh").read_text(encoding="utf-8")
        self.assertIn('"fixtureOnly":true', acceptance)
        self.assertIn('"ownerReleaseReplayPending":true', acceptance)
        self.assertIn("wrong bearer token", acceptance)


if __name__ == "__main__":
    unittest.main()
