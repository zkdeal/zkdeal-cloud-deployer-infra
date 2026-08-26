from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "zkdeal_failover_runner", ROOT / "failover-runner/zkdeal_failover.py",
)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class FailoverRunnerTests(unittest.TestCase):
    def args(self):
        return runner.parser().parse_args([
            "--terminate-active", "--persist-primary-target-lsn",
            "--assert-standby-replay", "--promote",
            "--assert-stale-writer-denied", "--assert-rto-seconds", "300",
        ])

    def test_exact_kurtosis_cli_is_required(self):
        parsed = self.args()
        self.assertTrue(parsed.terminate_active)
        self.assertEqual(parsed.assert_rto_seconds, 300)
        with self.assertRaises(SystemExit):
            runner.parser().parse_args([
                "--terminate-active", "--persist-primary-target-lsn",
                "--assert-standby-replay", "--promote", "--assert-rto-seconds", "300",
            ])
        package = (ROOT / "kurtosis/failover/main.star").read_text(encoding="utf-8")
        for marker in (
            "--terminate-active", "--persist-primary-target-lsn",
            "--assert-standby-replay", "--assert-stale-writer-denied",
        ):
            self.assertIn(marker, package)

    def test_controller_and_independent_acceptance_both_have_fixed_invariants(self):
        environment = {"PROMOTION_CANDIDATE_ID": "candidate-failover-001"}
        controller = {
            "status": "promotion-complete", "candidateId": "candidate-failover-001",
            "promoted": True, "oldWriterFenced": True, "databasePromoted": True,
            "ownerPromoted": True, "writerRouteCommitted": True,
            "signerActivatedOnlyAfterFence": True, "rtoSeconds": 42.5,
        }
        acceptance = {
            "schemaVersion": 1, "passed": True, "candidateId": "candidate-failover-001",
            "scenario": "split-brain-promotion",
            "evidence": {
                "fenced": True, "promotedReady": True,
                "targetLsn": "0/100", "replayLsn": "0/101", "rtoSeconds": 43,
            },
        }
        with patch.dict(os.environ, environment, clear=False):
            runner.validate_controller_state(controller, 300)
            runner.validate_acceptance_record(acceptance, 300)
            broken = dict(controller, signerActivatedOnlyAfterFence=False)
            with self.assertRaisesRegex(runner.FailoverRunnerError, "signerActivatedOnlyAfterFence"):
                runner.validate_controller_state(broken, 300)
            broken_acceptance = {
                **acceptance, "evidence": {**acceptance["evidence"], "fenced": False},
            }
            with self.assertRaisesRegex(runner.FailoverRunnerError, "fence and readiness"):
                runner.validate_acceptance_record(broken_acceptance, 300)

    def test_checked_in_example_failover_command_parses(self):
        import json

        example = json.loads(
            (ROOT / "kurtosis/failover/args.example.json").read_text(encoding="utf-8"),
        )
        argv = example["failover_command"].split()
        self.assertEqual(argv[0], "/opt/zkdeal-failover")
        parsed = runner.parser().parse_args(argv[1:])
        self.assertTrue(parsed.terminate_active)
        self.assertTrue(parsed.promote)
        self.assertLessEqual(parsed.assert_rto_seconds, 300)

    def test_image_is_source_bound_nonroot_and_contains_no_platform_credentials(self):
        dockerfile = (ROOT / "failover-runner/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("python:3.13-alpine@sha256:", dockerfile)
        self.assertIn("ARG FAILOVER_RUNNER_SOURCE_SHA256", dockerfile)
        self.assertIn("org.zkdeal.failover-runner.source.sha256", dockerfile)
        self.assertIn("USER 65532:65532", dockerfile)
        self.assertIn('ENTRYPOINT ["/opt/zkdeal-failover"]', dockerfile)
        for forbidden in ("FAILOVER_PROVIDER_TOKEN=", "PROMOTION_PRINCIPAL_TOKEN=", "docker.sock"):
            self.assertNotIn(forbidden, dockerfile)


if __name__ == "__main__":
    unittest.main()
