from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RunbookTests(unittest.TestCase):
    def test_required_operator_runbooks_are_first_class_and_served(self):
        names = {
            "database-restore.md", "object-store-loss.md", "pre-finality-reorg.md",
            "hosted-queue-stall.md", "deadline-risk.md", "signer-rotation.md",
            "post-finality-surprise.md", "warm-standby-promotion.md",
        }
        site = (ROOT / "docs/site/index.html").read_text(encoding="utf-8")
        for name in names:
            with self.subTest(runbook=name):
                path = ROOT / "runbooks" / name
                self.assertTrue(path.is_file())
                text = path.read_text(encoding="utf-8")
                self.assertGreater(len(text), 500)
                self.assertIn(f"runbooks/{name}", site)

    def test_recovery_guides_preserve_fencing_and_evidence(self):
        for name in (
            "database-restore.md", "object-store-loss.md", "pre-finality-reorg.md",
            "hosted-queue-stall.md", "deadline-risk.md", "signer-rotation.md",
        ):
            text = (ROOT / "runbooks" / name).read_text(encoding="utf-8").lower()
            with self.subTest(runbook=name):
                self.assertRegex(text, r"freeze|fence")
                self.assertRegex(text, r"abort|never|do not")
                self.assertRegex(text, r"evidence|seal")


if __name__ == "__main__":
    unittest.main()
