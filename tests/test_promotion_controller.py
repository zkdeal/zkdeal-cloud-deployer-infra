from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from promotion_controller import (  # noqa: E402
    Config,
    ControllerError,
    active_witness_healthy,
    lsn_value,
    standby_ready,
    validate_commit,
    validate_owner_promotion,
    validate_prepare,
)


class PromotionControllerTests(unittest.TestCase):
    def environment(self) -> dict[str, str]:
        return {
            "ACTIVE_HEALTH_URLS": "https://witness-a.example/health,https://witness-b.example/health",
            "STANDBY_HEALTH_URL": "https://standby.example/hosting/v1/health",
            "FAILOVER_PROVIDER_URL": "https://failover.example/v1/failovers",
            "PROMOTION_ENDPOINT": "https://standby.example/hosting/v1/admin/promote",
            "ACTIVE_COORDINATOR_ID": "primary-region-a",
            "STANDBY_COORDINATOR_ID": "standby-region-b",
            "PROMOTION_CANDIDATE_ID": "candidate-20260821-001",
            "FAILOVER_PROVIDER_TOKEN": "provider-token-0001",
            "FAILOVER_APPROVAL_TOKEN": "approval-token-0001",
            "PROMOTION_PRINCIPAL_TOKEN": "principal-token-0001",
            "PROMOTION_CONTROLLER_ARMED": "true",
        }

    def config(self) -> Config:
        with patch.dict(os.environ, self.environment(), clear=True):
            return Config.from_environment()

    def safe_prepare(self) -> dict[str, object]:
        return {
            "operationId": "candidate-20260821-001",
            "status": "READY_FOR_APPLICATION_PROMOTION",
            "activeFenced": True,
            "oldWriterTerminated": True,
            "targetCapturedByProvider": True,
            "databasePromoted": True,
            "standbyReplayAtOrAfterTarget": True,
            "indexerHeadMatchesL1": True,
            "stableDatabaseEndpointRouted": True,
            "standbySignerAuthorityActive": False,
            "primaryTargetSource": "durable-fenced-wal-checkpoint",
            "primaryTargetLsn": "0/16B6C50",
            "standbyReplayLsn": "0/16B6D00",
            "checkpointAgeSeconds": 2,
        }

    def test_configuration_requires_independent_https_witnesses_and_scoped_secrets(self):
        with patch.dict(os.environ, self.environment(), clear=True):
            self.assertEqual(len(Config.from_environment().active_health_urls), 2)
        internal = self.environment()
        internal["STANDBY_HEALTH_URL"] = "http://zkdeal-coordinator-standby:3000/hosting/v1/health"
        internal["PROMOTION_ENDPOINT"] = "http://zkdeal-coordinator-standby:3000/hosting/v1/admin/promote"
        with patch.dict(os.environ, internal, clear=True):
            self.assertEqual(
                Config.from_environment().standby_health_url,
                internal["STANDBY_HEALTH_URL"],
            )
        bad = self.environment()
        bad["ACTIVE_HEALTH_URLS"] = "https://same.example/a,https://same.example/b"
        with patch.dict(os.environ, bad, clear=True), self.assertRaisesRegex(
            ControllerError, "distinct network identities",
        ):
            Config.from_environment()
        bad = self.environment()
        bad["FAILOVER_PROVIDER_URL"] = "http://failover.example/v1/failovers"
        with patch.dict(os.environ, bad, clear=True), self.assertRaisesRegex(
            ControllerError, "must use HTTPS",
        ):
            Config.from_environment()

    def test_health_contracts_are_exact_and_fail_closed(self):
        self.assertTrue(active_witness_healthy({
            "coordinatorId": "primary-region-a", "effectiveRole": "active",
            "acceptingWrites": True, "fenceFresh": True,
        }, "primary-region-a"))
        self.assertFalse(active_witness_healthy({
            "coordinatorId": "primary-region-a", "effectiveRole": "active",
            "acceptingWrites": True, "fenceFresh": False,
        }, "primary-region-a"))
        self.assertTrue(standby_ready({
            "coordinatorId": "standby-region-b", "configuredRole": "standby",
            "effectiveRole": "standby", "acceptingWrites": False,
        }, "standby-region-b"))

    def test_prepare_requires_fence_replay_freshness_and_no_early_signer(self):
        config = self.config()
        self.assertEqual(validate_prepare(config, self.safe_prepare()), config.candidate_id)
        for field, unsafe in (
            ("activeFenced", False),
            ("oldWriterTerminated", False),
            ("standbyReplayAtOrAfterTarget", False),
            ("standbySignerAuthorityActive", True),
        ):
            value = self.safe_prepare()
            value[field] = unsafe
            with self.subTest(field=field), self.assertRaises(ControllerError):
                validate_prepare(config, value)
        stale = self.safe_prepare()
        stale["standbyReplayLsn"] = "0/16B6B00"
        with self.assertRaisesRegex(ControllerError, "behind"):
            validate_prepare(config, stale)

    def test_owner_and_route_commit_are_both_required(self):
        config = self.config()
        owner = {
            "promoted": True, "effectiveRole": "active",
            "indexerHeadMatchesL1": True, "promotionReplication": {"targetLsn": "0/1"},
        }
        validate_owner_promotion(owner)
        with self.assertRaises(ControllerError):
            validate_owner_promotion({**owner, "indexerHeadMatchesL1": False})
        commit = {
            "operationId": config.candidate_id,
            "writerRouteCommitted": True,
            "writerCoordinatorId": config.standby_coordinator_id,
            "oldWriterRouteRemoved": True,
            "stableDatabaseEndpointRouted": True,
            "signerAuthorityActivatedAfterFence": True,
        }
        validate_commit(config, config.candidate_id, commit)
        with self.assertRaises(ControllerError):
            validate_commit(config, config.candidate_id, {**commit, "oldWriterRouteRemoved": False})

    def test_lsn_comparison_uses_postgresql_numeric_order(self):
        self.assertGreater(lsn_value("1/0"), lsn_value("0/FFFFFFFF"))

    def test_failover_provider_openapi_is_exact_and_replay_safe(self):
        document = json.loads(
            (ROOT / "promotion-controller/failover-provider-v1.openapi.json").read_text(
                encoding="utf-8",
            )
        )
        self.assertEqual(document["openapi"], "3.1.0")
        self.assertEqual(document["security"], [{"providerBearer": []}])
        self.assertEqual(
            set(document["paths"]),
            {
                "/v1/failovers",
                "/v1/failovers/{operationId}/commit",
                "/v1/failovers/{operationId}",
            },
        )
        for operation in (
            document["paths"]["/v1/failovers"]["post"],
            document["paths"]["/v1/failovers/{operationId}/commit"]["post"],
        ):
            references = {
                parameter.get("$ref") for parameter in operation["parameters"]
            }
            self.assertIn("#/components/parameters/IdempotencyKey", references)
            self.assertIn("#/components/parameters/ApprovalToken", references)
            self.assertTrue(operation["requestBody"]["required"])
            self.assertEqual(
                set(operation["responses"]),
                {"200", "400", "401", "403", "409", "503"},
            )
        parameters = document["components"]["parameters"]
        self.assertEqual(parameters["IdempotencyKey"]["name"], "Idempotency-Key")
        self.assertTrue(parameters["IdempotencyKey"]["required"])
        self.assertEqual(
            parameters["ApprovalToken"]["name"],
            "X-Zkdeal-Failover-Approval",
        )
        prepare = document["components"]["schemas"]["PrepareResponse"]
        commit = document["components"]["schemas"]["CommitResponse"]
        self.assertFalse(prepare["additionalProperties"])
        self.assertFalse(commit["additionalProperties"])
        for field, expected in (
            ("activeFenced", True),
            ("oldWriterTerminated", True),
            ("targetCapturedByProvider", True),
            ("standbyReplayAtOrAfterTarget", True),
            ("standbySignerAuthorityActive", False),
        ):
            self.assertEqual(prepare["properties"][field]["const"], expected)
        for field in (
            "writerRouteCommitted", "oldWriterRouteRemoved",
            "stableDatabaseEndpointRouted", "signerAuthorityActivatedAfterFence",
        ):
            self.assertEqual(commit["properties"][field]["const"], True)
        # The measured promotion duration is a required commit-response field,
        # so an acceptance plan can bind rtoSeconds to a real measurement.
        for field in ("rtoSeconds", "targetCapturedAtUnixMs", "committedAtUnixMs"):
            self.assertIn(field, commit["required"])
            self.assertEqual(commit["properties"][field]["type"], "integer")
        status = document["paths"]["/v1/failovers/{operationId}"]["get"]
        references = {parameter.get("$ref") for parameter in status["parameters"] if "$ref" in parameter}
        self.assertIn("#/components/parameters/ApprovalToken", references)


if __name__ == "__main__":
    unittest.main()
