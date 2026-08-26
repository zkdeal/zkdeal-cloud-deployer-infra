from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "zkdeal_soak_runner", ROOT / "soak-runner/zkdeal_soak.py",
)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class SoakRunnerTests(unittest.TestCase):
    def args(self):
        return runner.parser().parse_args([
            "--submit-real-proof-jobs", "--restart",
            "--assert-durable-results,cursors,nonces,charges,sealed-output,safety,claims,fairness,deadlines",
            "--bounded-backoff", "--emit-evidence-closure",
        ])

    def test_exact_release_soak_flags_are_required(self):
        parsed = self.args()
        self.assertTrue(parsed.assert_durable)
        with self.assertRaises(SystemExit):
            runner.parser().parse_args([
                "--submit-real-proof-jobs", "--restart", "--bounded-backoff",
                "--emit-evidence-closure",
            ])
        package = (ROOT / "kurtosis/soak/main.star").read_text(encoding="utf-8")
        for marker in (
            "--submit-real-proof-jobs", "--restart",
            "--assert-durable-results,cursors,nonces,charges,sealed-output,safety,claims,fairness,deadlines",
            "--bounded-backoff", "--emit-evidence-closure",
        ):
            self.assertIn(marker, package)

    def test_runtime_contract_requires_full_lifecycle_faults_and_resume(self):
        exact = {
            "REQUIRE_REAL_PROOF_JOBS": "1",
            "REQUIRE_FULL_LIFECYCLE": runner.REQUIRED_LIFECYCLE,
            "REQUIRE_INDUCED_RESTARTS": runner.REQUIRED_RESTARTS,
            "REQUIRE_DURABLE_ASSERTIONS": runner.REQUIRED_DURABLE,
            "REQUIRE_APPEND_ONLY_JOURNAL": "1",
            "REQUIRE_RESTART_RESUME": "1",
        }
        with patch.dict(os.environ, exact, clear=True):
            runner.require_runtime_contract()
        broken = dict(exact, REQUIRE_RESTART_RESUME="0")
        with patch.dict(os.environ, broken, clear=True):
            with self.assertRaisesRegex(runner.SoakRunnerError, "REQUIRE_RESTART_RESUME"):
                runner.require_runtime_contract()

    def test_image_is_source_bound_nonroot_and_deliberately_has_no_fake_driver(self):
        dockerfile = (ROOT / "soak-runner/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("python:3.13-alpine@sha256:", dockerfile)
        self.assertIn("ARG SOAK_RUNNER_SOURCE_SHA256", dockerfile)
        self.assertIn("org.zkdeal.soak-runner.source.sha256", dockerfile)
        self.assertIn("USER 65532:65532", dockerfile)
        self.assertIn('ENTRYPOINT ["/opt/zkdeal-soak"]', dockerfile)
        # The default build target stays driver-free: the last stage is the
        # bare runner and never synthesizes an owner driver.
        self.assertTrue(dockerfile.rstrip().endswith("FROM base AS runner"))
        base_stage = dockerfile.split("# --- candidate target", 1)[0]
        self.assertNotIn("zkdeal-owner-soak", base_stage)
        readme = (ROOT / "soak-runner/README.md").read_text(encoding="utf-8")
        self.assertIn("does not contain a synthetic", readme)
        self.assertIn("not release evidence", readme)

    def test_candidate_target_binds_owner_driver_from_owner_image_fail_closed(self):
        dockerfile = (ROOT / "soak-runner/Dockerfile").read_text(encoding="utf-8")
        candidate = dockerfile.split("FROM base AS candidate", 1)[1]
        # The driver is copied from the exact digest-pinned owner image, never
        # authored in this repository, and the build fails closed on any
        # missing or mismatched owner source hash.
        self.assertIn("ARG OWNER_SOAK_DRIVER_IMAGE=scratch", dockerfile)
        self.assertIn("FROM ${OWNER_SOAK_DRIVER_IMAGE} AS owner-driver", dockerfile)
        self.assertIn("COPY --from=owner-driver /opt/zkdeal-owner-soak /opt/zkdeal-owner-soak", candidate)
        self.assertIn("ARG OWNER_SOAK_DRIVER_SOURCE_SHA256", candidate)
        self.assertIn('test -n "$OWNER_SOAK_DRIVER_SOURCE_SHA256"', candidate)
        self.assertIn("test ! -L /opt/zkdeal-owner-soak", candidate)
        self.assertIn('test "$actual" = "$OWNER_SOAK_DRIVER_SOURCE_SHA256"', candidate)
        self.assertIn("org.zkdeal.owner-soak-driver.source.sha256", candidate)
        self.assertIn("chmod 0555 /opt/zkdeal-owner-soak", candidate)
        self.assertIn("USER 65532:65532", candidate)

    def test_checked_in_example_soak_command_parses(self):
        import json

        example = json.loads(
            (ROOT / "kurtosis/soak/args.example.json").read_text(encoding="utf-8"),
        )
        argv = example["soak_command"].split()
        self.assertEqual(argv[0], "/opt/zkdeal-soak")
        parsed = runner.parser().parse_args(argv[1:])
        self.assertTrue(parsed.submit_real_proof_jobs)
        self.assertTrue(parsed.restart)
        self.assertTrue(parsed.assert_durable)
        # --restart is a boolean flag; a value after it must be rejected.
        with self.assertRaises(SystemExit):
            runner.parser().parse_args(argv[1:] + ["headless-restart"])


if __name__ == "__main__":
    unittest.main()
