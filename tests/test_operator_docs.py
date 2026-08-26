from __future__ import annotations

import json
import unittest
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[1]


class OperatorDocsTests(unittest.TestCase):
    def test_first_class_guides_cover_required_boundaries_and_examples(self):
        guides = {
            "node-lifecycle.md": ("authorization", "idempot", "error", "curl", "cast ", "typescript"),
            "room-deployment-monitoring.md": ("authorization", "idempot", "error", "curl", "cast ", "typescript"),
            "indexer-json-rpc.md": ("authentication", "idempot", "error", "curl", "cast ", "typescript"),
            "monitoring-runbooks.md": ("internal-only", "idempotent", "error", "curl", "promtool", "runbook"),
            "contract-reference.md": ("authentication", "idempot", "error", "curl", "cast ", "typescript"),
        }
        for name, markers in guides.items():
            text = (ROOT / "docs" / name).read_text(encoding="utf-8").lower()
            with self.subTest(name=name):
                for marker in markers:
                    self.assertIn(marker, text)
        combined = "\n".join((ROOT / "docs" / name).read_text(encoding="utf-8") for name in guides)
        for boundary in (
            "Last-Event-ID", "Content-Schema-Version", "HOSTED_WORKER_ID",
            "statusRetracted", "publisher", "PostgreSQL", "Web3Signer",
        ):
            self.assertIn(boundary, combined)

    def test_json_rpc_examples_validate_against_machine_schema(self):
        schema = json.loads((ROOT / "docs/schemas/hosting-json-rpc.schema.json").read_text(encoding="utf-8"))
        examples = [
            {"jsonrpc": "2.0", "id": "cap-1", "method": "hosting_capabilities", "params": {}},
            {"jsonrpc": "2.0", "id": "events-1", "method": "zkdeal_getRoomEvents", "params": {"roomId": "42", "after": "0", "limit": 200, "kinds": ["batch"]}},
            {"jsonrpc": "2.0", "id": "claim-1", "method": "zkdeal_requestWithdrawalClaim", "params": {"roomId": "42", "epoch": "7", "withdrawalIndex": "3"}},
            {"jsonrpc": "2.0", "id": "usage-1", "method": "zkdeal_getUsage", "params": {"after": "0", "limit": 200}},
            {"jsonrpc": "2.0", "id": "retention-1", "method": "zkdeal_adminRetention", "params": {"transientRetentionDays": 30, "auditRetentionDays": 365}},
            {"jsonrpc": "2.0", "id": "ok-1", "result": {"blobArchiveReady": True}},
            {"jsonrpc": "2.0", "id": "bad-1", "error": {"code": -32601, "message": "method not found"}},
        ]
        for example in examples:
            jsonschema.Draft202012Validator(schema).validate(example)

        invalid_retention_windows = (
            {"transientRetentionDays": 29},
            {"auditRetentionDays": 364},
            {"resolvedSafetyRetentionDays": 29},
        )
        for params in invalid_retention_windows:
            with self.subTest(params=params), self.assertRaises(jsonschema.ValidationError):
                jsonschema.Draft202012Validator(schema).validate({
                    "jsonrpc": "2.0",
                    "id": "retention-too-short",
                    "method": "zkdeal_adminRetention",
                    "params": params,
                })

    def test_docs_portal_does_not_publish_stale_filesystem_authority_claim(self):
        index = (ROOT / "docs/site/index.html").read_text(encoding="utf-8")
        self.assertNotIn("filesystem-authoritative services single replica", index)
        self.assertNotRegex(index, r"schema\s+(9|10|12|13)")
        self.assertIn("current owner capability manifest", index)
        dockerfile = (ROOT / "docs/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("docs/*.md", dockerfile)
        self.assertIn("docs/schemas", dockerfile)

    def test_node_guide_uses_current_liveness_and_lifecycle_contract(self):
        text = (ROOT / "docs/node-lifecycle.md").read_text(encoding="utf-8")
        for marker in (
            "NODE_LIVENESS_COORDINATOR_URL", "NODE_LIVENESS_ACCOUNT",
            "NODE_LIVENESS_COORDINATOR_AUTH_TOKEN", "configureNodeAuthorities",
            "admin/proof-profiles", "admin/provider-nodes", "configureSlot",
            "confirmCapacityProfile", "publishPriceEpoch", "NodeStatusChanged(READY)",
            "beginNodeDrain(bytes32)", "retireNode(bytes32)",
            "UnsafeNodeRetirement", "NodeDrainStarted", "NodeRetired",
            "0xd7ceb78e", "0x13ca0607", "0xa4588ca0",
            "l1-operations/node-heartbeats", "provider-nodes/{principalId}/drain",
            "provider-nodes/{principalId}/retire",
            "node /app/agent/agent.js", "10001:10001",
        ):
            self.assertIn(marker, text)
        self.assertIn("Recovery applies to `DEGRADED` and `OFFLINE`", text)
        self.assertIn("`NODE_SERVICE_KEY`, `NODE_LIVENESS_SIGNER_*`", text)
        self.assertNotRegex(text, r"publishes no transition that\s+sets it")
        self.assertNotIn("A future/independent liveness transaction sender", text)
        self.assertNotIn("serve --host 0.0.0.0 --port 8080 --token", text)

    def test_room_guide_covers_typed_funding_settlement_and_resume_paths(self):
        text = (ROOT / "docs/room-deployment-monitoring.md").read_text(encoding="utf-8")
        for marker in (
            '"$NODE_ID" "$SLOT_ID" "$DEADLINE_BLOCKS" "$PRICE_EPOCH"',
            "/hosting/v1/entitlements", "/hosting/v1/sponsorships",
            "reserveAndStartWithDataAvailabilityWithPermit",
            "CALLDATA_REQUIRED", "BLOB_REQUIRED", "BLOB_PREFERRED",
            "/hosting/v1/admissions/$ROOM_ID/lease", "publishL1StateInput",
            "AggregateMemberOutcome.applied=true", "/renewals",
            "/withdrawal-claims/{claimId}", "statusRetracted",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)
        self.assertRegex(
            text,
            r"Queue position and deadline slack must\s+come from owner-published typed fields/metrics",
        )
        for honest_boundary in (
            "earlier acceptance token was superseded",
            "must not race a running room node",
            "require the replacement joint token",
        ):
            self.assertIn(honest_boundary, text)
        node = (ROOT / "docs/node-lifecycle.md").read_text(encoding="utf-8")
        self.assertIn("Current deployment status is deliberately fail-closed", node)
        self.assertIn("hostedIntegration", node)


if __name__ == "__main__":
    unittest.main()
