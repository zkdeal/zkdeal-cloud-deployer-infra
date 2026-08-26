from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UMBRELLA = ROOT.parent


class BuildContextPolicyTests(unittest.TestCase):
    def test_umbrella_dockerignore_bounds_owner_context(self):
        policy = (UMBRELLA / ".dockerignore").read_text(encoding="utf-8")
        required = {
            ".git",
            "**/.git",
            "**/node_modules",
            "**/target",
            "web2-api/server/data",
            "kurtosis-testing/test-results",
            "cloud-deployer-infra/evidence",
            "outputs",
            "tmp",
            "review-l2-hosting",
            "review-of-l1-reorgs",
            "**/.env",
            "**/*.pem",
        }
        lines = {
            line.strip()
            for line in policy.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertFalse(required - lines, f"missing critical context exclusions: {sorted(required - lines)}")

        owner = (UMBRELLA / "web2-api" / "server" / "Dockerfile").read_text(encoding="utf-8")
        for required_input in (
            "web3-protocol/circuits/build",
            "web3-protocol/contracts/out",
            "prover-node/zkvm/artifacts.lock.json",
            "prover-node/zkvm/fixtures",
        ):
            self.assertIn(f"COPY {required_input}", owner)

    def test_kind_gate_has_explicit_owner_size_and_load_budgets(self):
        gate = (ROOT / "tests" / "acceptance" / "kubernetes-kind.sh").read_text(encoding="utf-8")
        self.assertIn("OWNER_IMAGE_MAX_BYTES", gate)
        self.assertIn("KIND_IMAGE_LOAD_TIMEOUT_SECONDS", gate)
        self.assertIn("owner-image-budget.sh", gate)

    def test_pruned_owner_images_have_tight_explicit_budgets(self):
        coordinator = (ROOT / "tests/acceptance/owner-image-budget.sh").read_text(encoding="utf-8")
        headless = (ROOT / "tests/acceptance/headless-image-budget.sh").read_text(encoding="utf-8")
        self.assertIn("OWNER_IMAGE_MAX_BYTES:-1180000000", coordinator)
        self.assertNotIn("4026531840", coordinator)
        self.assertIn("OWNER_IMAGE_REUSE_ID", coordinator)
        self.assertIn('"$image_id" != "$reuse_id"', coordinator)
        self.assertIn('"sourceRebuilt":%s', coordinator)
        self.assertIn("HEADLESS_IMAGE_MAX_BYTES:-680000000", headless)
        self.assertIn("app-node/packages/room-node/Dockerfile", headless)

    def test_promotion_controller_image_is_small_and_authority_minimal(self):
        dockerfile = (ROOT / "promotion-controller/Dockerfile").read_text(encoding="utf-8")
        acceptance = (ROOT / "tests/acceptance/promotion-controller-image.sh").read_text(encoding="utf-8")
        self.assertIn("python:3.13-alpine@sha256:", dockerfile)
        self.assertIn("USER 65532:65532", dockerfile)
        self.assertIn("PROMOTION_CONTROLLER_IMAGE_MAX_BYTES:-80000000", acceptance)
        self.assertIn("noDockerKubectlPsql", acceptance)
        self.assertIn("emptyConfigRejected", acceptance)


if __name__ == "__main__":
    unittest.main()
