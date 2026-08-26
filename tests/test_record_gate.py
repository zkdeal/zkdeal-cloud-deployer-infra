from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import DeploymentError  # noqa: E402
from record_gate import explicit_input_manifest  # noqa: E402


class RecordGateInputTests(unittest.TestCase):
    def test_explicit_candidate_input_is_hash_bound_and_sorted(self):
        state = ROOT / ".state" / "record-gate-unit"
        state.mkdir(parents=True, exist_ok=True)
        first = state / "candidate.json"
        second = state / "kind-values.yaml"
        try:
            first.write_text('{"candidateId":"candidate-unit"}\n', encoding="utf-8")
            second.write_text("profile: local\n", encoding="utf-8")
            rows = explicit_input_manifest([str(second), str(first)])
            self.assertEqual([row["path"] for row in rows], sorted(row["path"] for row in rows))
            self.assertTrue(all(len(str(row["sha256"])) == 64 for row in rows))
            self.assertTrue(all(row["path"].startswith(".state/record-gate-unit/") for row in rows))
        finally:
            for path in (first, second):
                path.unlink(missing_ok=True)
            state.rmdir()

    def test_duplicate_missing_and_outside_inputs_fail_closed(self):
        with tempfile.NamedTemporaryFile() as outside:
            with self.assertRaisesRegex(DeploymentError, "escaped"):
                explicit_input_manifest([outside.name])
        with self.assertRaisesRegex(DeploymentError, "absent"):
            explicit_input_manifest([".state/does-not-exist.json"])
        with tempfile.TemporaryDirectory(dir=ROOT / ".state") as folder:
            path = Path(folder) / "candidate.json"
            path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(DeploymentError, "duplicate"):
                explicit_input_manifest([str(path), str(path)])

if __name__ == "__main__":
    unittest.main()
