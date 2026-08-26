from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import DeploymentError  # noqa: E402
from evidence_closure import verify_seal, write_seal  # noqa: E402


class EvidenceClosureTests(unittest.TestCase):
    def setUp(self):
        self.base = ROOT / ".test-tmp" / uuid.uuid4().hex
        self.evidence = self.base / "evidence"
        self.output = self.base / "closures"
        run = self.evidence / "20260821T000000Z-example"
        run.mkdir(parents=True)
        stdout = run / "stdout.log"
        stderr = run / "stderr.log"
        stdout.write_text("ok\n", encoding="utf-8")
        stderr.write_text("", encoding="utf-8")

        def item(path: Path) -> dict[str, object]:
            return {
                "path": path.relative_to(ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }

        record = {
            "gate": "example",
            "classification": "static",
            "passed": True,
            "outputs": {"stdout": item(stdout), "stderr": item(stderr)},
        }
        record_path = run / "record.json"
        record_path.write_text(json.dumps(record), encoding="utf-8")
        digest = hashlib.sha256(record_path.read_bytes()).hexdigest()
        (run / "record.sha256").write_text(f"{digest}  record.json\n", encoding="ascii")
        (self.evidence / "status.json").write_text('{"schemaVersion":1}\n', encoding="utf-8")
        self.key = "42" * 32
        self.previous = os.environ.get("EVIDENCE_SEALING_KEY_HEX")
        os.environ["EVIDENCE_SEALING_KEY_HEX"] = self.key

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("EVIDENCE_SEALING_KEY_HEX", None)
        else:
            os.environ["EVIDENCE_SEALING_KEY_HEX"] = self.previous
        shutil.rmtree(self.base, ignore_errors=True)
        parent = ROOT / ".test-tmp"
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()

    def test_content_addressed_hmac_closure_round_trip(self):
        closure_hash, manifest, mac = write_seal(self.evidence, self.output)
        self.assertEqual(verify_seal(manifest, mac, self.evidence), closure_hash)
        self.assertNotIn(self.key, manifest.read_text(encoding="utf-8"))
        self.assertNotIn(self.key, mac.read_text(encoding="ascii"))

    def test_tamper_and_content_address_overwrite_fail_closed(self):
        _, manifest, mac = write_seal(self.evidence, self.output)
        mac.write_text("00" * 32 + "\n", encoding="ascii")
        with self.assertRaises(DeploymentError):
            verify_seal(manifest, mac, self.evidence)
        with self.assertRaises(DeploymentError):
            write_seal(self.evidence, self.output)

    def test_closed_output_mutation_is_detected(self):
        _, manifest, mac = write_seal(self.evidence, self.output)
        (self.evidence / "20260821T000000Z-example" / "stdout.log").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(DeploymentError):
            verify_seal(manifest, mac, self.evidence)


if __name__ == "__main__":
    unittest.main()
