from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tarfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from source_bundle import (  # noqa: E402
    BundleError,
    RELEASE_BINDING_PATHS,
    create_bundle,
    create_evidence_bundle,
    extract_bundle,
    verify_bundle,
    write_json_exclusive,
)


class SourceBundleTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".test-tmp" / uuid.uuid4().hex
        self.umbrella = self.root / "umbrella"
        for name, content in {
            ".dockerignore": "**/node_modules\noutputs\n",
            "docker-compose.yml": "services: {}\n",
            "README.md": "# fixture\n",
        }.items():
            (self.umbrella / name).parent.mkdir(parents=True, exist_ok=True)
            (self.umbrella / name).write_text(content, encoding="utf-8")
        (self.umbrella / "owner" / "src").mkdir(parents=True)
        (self.umbrella / "owner" / "src" / "main.ts").write_text("export const value = 1\n", encoding="utf-8")
        (self.umbrella / "owner" / ".git").mkdir()
        (self.umbrella / "owner" / ".git" / "config").write_text("history", encoding="utf-8")
        (self.umbrella / "owner" / "node_modules").mkdir()
        (self.umbrella / "owner" / "node_modules" / "cache").write_text("cache", encoding="utf-8")
        (self.umbrella / "owner" / ".env").write_text("SECRET=x", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        parent = ROOT / ".test-tmp"
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()

    def test_deterministic_no_history_round_trip(self):
        first = self.root / "first.tar.gz"
        second = self.root / "second.tar.gz"
        create_bundle(self.umbrella, first, ("owner",), 1024 * 1024)
        create_bundle(self.umbrella, second, ("owner",), 1024 * 1024)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        result = verify_bundle(first, first.with_suffix(first.suffix + ".manifest.json"))
        self.assertEqual(result["files"], 4)
        self.assertRegex(result["embeddedManifestSha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["entriesSha256"], r"^[0-9a-f]{64}$")
        self.assertFalse(result["historyIncluded"])
        self.assertFalse(result["secretsIncluded"])
        target = self.root / "target"
        extract_bundle(first, target, first.with_suffix(first.suffix + ".manifest.json"))
        self.assertTrue((target / "owner/src/main.ts").is_file())
        self.assertTrue((target / ".dockerignore").is_file())
        self.assertFalse((target / "owner/.git").exists())
        self.assertFalse((target / "owner/.env").exists())

    def test_release_binding_report_names_the_packaged_agent_inputs(self):
        self.assertIn("prover-node/agent/liveness-capability.json", RELEASE_BINDING_PATHS)
        self.assertIn("prover-node/agent/trace-capability.json", RELEASE_BINDING_PATHS)
        self.assertIn("prover-node/agent/test/fixtures/hosted-trace-join.json", RELEASE_BINDING_PATHS)
        self.assertIn("prover-node/agent/src/agent.ts", RELEASE_BINDING_PATHS)
        self.assertIn("prover-node/agent/src/heartbeat.ts", RELEASE_BINDING_PATHS)
        self.assertIn("prover-node/agent/src/local-prover.ts", RELEASE_BINDING_PATHS)
        self.assertIn("prover-node/agent/src/structured-log.ts", RELEASE_BINDING_PATHS)

    def test_source_policy_excludes_generated_output_and_evidence_but_keeps_owner_runtime_inputs(self):
        (self.umbrella / "owner" / "dist").mkdir()
        (self.umbrella / "owner" / "dist" / "bundle.js").write_text("stale", encoding="utf-8")
        (self.umbrella / "owner" / "fixtures").mkdir()
        (self.umbrella / "owner" / "fixtures" / "case.json").write_text("{}", encoding="utf-8")
        (self.umbrella / "owner" / "package-lock.json").write_text("{}", encoding="utf-8")
        (self.umbrella / "web3-protocol" / "circuits" / "build").mkdir(parents=True)
        (self.umbrella / "web3-protocol" / "circuits" / "build" / "duel.zkey").write_bytes(b"zkey")
        (self.umbrella / "web3-protocol" / "contracts" / "out").mkdir(parents=True)
        (self.umbrella / "web3-protocol" / "contracts" / "out" / "Room.json").write_text("{}", encoding="utf-8")
        (self.umbrella / "cloud-deployer-infra" / "evidence" / "run").mkdir(parents=True)
        (self.umbrella / "cloud-deployer-infra" / "evidence" / "run" / "record.json").write_text("{}", encoding="utf-8")
        (self.umbrella / "prover-node" / "zkvm").mkdir(parents=True)
        (self.umbrella / "prover-node" / "zkvm" / "source-manifest.candidate.json").write_text(
            '{"format":"zkdeal/zkvm-source-manifest/v1"}', encoding="utf-8",
        )
        (self.umbrella / "prover-node" / "zkvm" / "source-manifest.json").write_text(
            "stale-minted-manifest", encoding="utf-8",
        )
        (self.umbrella / "prover-node" / "zkvm" / "artifacts.lock.json").write_text(
            "stale-generated-lock", encoding="utf-8",
        )
        archive = self.root / "policy.tar.gz"
        create_bundle(
            self.umbrella, archive,
            ("owner", "web3-protocol", "prover-node", "cloud-deployer-infra"),
            1024 * 1024,
        )
        with tarfile.open(archive, "r:gz") as bundle:
            names = set(bundle.getnames())
        self.assertIn("owner/fixtures/case.json", names)
        self.assertIn("owner/package-lock.json", names)
        self.assertIn("web3-protocol/circuits/build/duel.zkey", names)
        self.assertIn("web3-protocol/contracts/out/Room.json", names)
        self.assertIn("prover-node/zkvm/source-manifest.candidate.json", names)
        self.assertNotIn("owner/dist/bundle.js", names)
        self.assertNotIn("cloud-deployer-infra/evidence/run/record.json", names)
        self.assertNotIn("prover-node/zkvm/source-manifest.json", names)
        self.assertNotIn("prover-node/zkvm/artifacts.lock.json", names)

    def test_archive_and_outer_manifest_tamper_are_rejected(self):
        archive = self.root / "tamper.tar.gz"
        create_bundle(self.umbrella, archive, ("owner",), 1024 * 1024)
        outer_path = archive.with_suffix(archive.suffix + ".manifest.json")
        outer = json.loads(outer_path.read_text(encoding="utf-8"))
        outer["archiveSha256"] = "0" * 64
        outer_path.write_text(json.dumps(outer), encoding="utf-8")
        with self.assertRaises(BundleError):
            verify_bundle(archive, outer_path)

        create_bundle(self.umbrella, archive, ("owner",), 1024 * 1024)
        data = bytearray(archive.read_bytes())
        data[len(data) // 2] ^= 1
        archive.write_bytes(data)
        with self.assertRaises(BundleError):
            verify_bundle(archive, outer_path)

    def test_executable_mode_is_hash_bound_and_verified(self):
        executable = self.umbrella / "owner" / "src" / "run.sh"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        archive = self.root / "mode.tar.gz"
        create_bundle(self.umbrella, archive, ("owner",), 1024 * 1024)
        with tarfile.open(archive, "r:gz") as bundle:
            manifest = json.loads(bundle.extractfile("SOURCE-MANIFEST.json").read())
            entry = next(item for item in manifest["entries"] if item["path"] == "owner/src/run.sh")
            self.assertEqual(entry["mode"], 0o755)
            self.assertEqual(bundle.getmember("owner/src/run.sh").mode, 0o755)
        self.assertTrue(verify_bundle(archive, archive.with_suffix(archive.suffix + ".manifest.json"))["verified"])

    def test_evidence_bundle_is_separate_deterministic_and_complete_only(self):
        evidence = self.root / "evidence"
        run = evidence / "20260821T000000Z-pass"
        run.mkdir(parents=True)
        stdout = run / "stdout.log"
        stdout.write_text("passed\n", encoding="utf-8")
        relative_stdout = stdout.relative_to(ROOT).as_posix()
        output_hash = hashlib.sha256(stdout.read_bytes()).hexdigest()
        record = {
            "passed": True,
            "outputs": {"stdout": {"path": relative_stdout, "sha256": output_hash}},
        }
        record_path = run / "record.json"
        record_path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        record_hash = hashlib.sha256(record_path.read_bytes()).hexdigest()
        (run / "record.sha256").write_text(f"{record_hash}  record.json\n", encoding="utf-8")
        (evidence / "incomplete-run").mkdir()
        closures = evidence / "closures"
        closures.mkdir()
        (closures / "sha256-fixture.json").write_text("{}", encoding="utf-8")

        first = self.root / "evidence-first.tar.gz"
        second = self.root / "evidence-second.tar.gz"
        create_evidence_bundle(evidence, first, 1024 * 1024)
        create_evidence_bundle(evidence, second, 1024 * 1024)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        result = verify_bundle(first, first.with_suffix(first.suffix + ".manifest.json"))
        self.assertEqual(result["format"], "zkdeal-evidence-bundle")
        self.assertEqual(result["records"], [run.name])
        with tarfile.open(first, "r:gz") as bundle:
            names = set(bundle.getnames())
        evidence_prefix = evidence.relative_to(ROOT).as_posix()
        self.assertIn(f"{evidence_prefix}/{run.name}/record.json", names)
        self.assertIn(f"{evidence_prefix}/closures/sha256-fixture.json", names)
        self.assertFalse(any("incomplete-run" in name for name in names))

    def test_extract_refuses_populated_target(self):
        archive = self.root / "bundle.tar.gz"
        create_bundle(self.umbrella, archive, ("owner",), 1024 * 1024)
        target = self.root / "target"
        target.mkdir()
        (target / "existing").write_text("keep", encoding="utf-8")
        with self.assertRaises(BundleError):
            extract_bundle(archive, target, archive.with_suffix(archive.suffix + ".manifest.json"))

    def test_verification_report_is_write_once(self):
        report = self.root / "verification.json"
        write_json_exclusive(report, {"verified": True})
        with self.assertRaises(FileExistsError):
            write_json_exclusive(report, {"verified": False})


if __name__ == "__main__":
    unittest.main()
