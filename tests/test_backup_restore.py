from __future__ import annotations

import json
import shutil
import sys
import tarfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from backup import build_manifest, create_backup  # noqa: E402
from common import DeploymentError  # noqa: E402
from restore import restore_archive, verify_archive  # noqa: E402


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".test-tmp" / uuid.uuid4().hex
        self.source = self.root / "source"
        self.source.mkdir(parents=True)
        (self.source / "coordinator").mkdir()
        (self.source / "coordinator" / "state.json").write_text('{"revision":1}\n', encoding="utf-8")
        (self.source / "queue").mkdir()
        (self.source / "queue" / "job.json").write_text('{"status":"waiting"}\n', encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        parent = ROOT / ".test-tmp"
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()

    def test_backup_verify_restore_round_trip(self):
        archive = self.root / "backup.tar.gz"
        manifest = build_manifest(self.source, (), "test")
        create_backup(self.source, archive, manifest)
        verified = verify_archive(archive, "test")
        target = self.root / "restored"
        restore_archive(archive, target, verified)
        self.assertEqual((target / "coordinator/state.json").read_text(), '{"revision":1}\n')
        self.assertEqual((target / "queue/job.json").read_text(), '{"status":"waiting"}\n')

    def test_backup_rejects_secret_named_file(self):
        (self.source / "private-key.key").write_text("do-not-back-up", encoding="utf-8")
        with self.assertRaises(DeploymentError):
            build_manifest(self.source, (), "test")

    def test_restore_rejects_profile_mismatch(self):
        archive = self.root / "backup.tar.gz"
        manifest = build_manifest(self.source, (), "test")
        create_backup(self.source, archive, manifest)
        with self.assertRaises(DeploymentError):
            verify_archive(archive, "different")

    def test_live_backup_rehearsal_uses_candidate_or_immutable_minio_client(self):
        script = (ROOT / "tests/acceptance/live-backup-restore.sh").read_text(encoding="utf-8")
        self.assertIn("minio_client_image=${MINIO_CLIENT_IMAGE:-minio/mc@sha256:", script)
        self.assertIn('"$minio_client_image" pipe', script)
        self.assertNotIn("minio/mc:RELEASE.", script)

    def test_live_shell_paths_bind_a_key_id_and_reject_a_wrong_key_distinctly(self):
        backup = (ROOT / "scripts/live_backup.sh").read_text(encoding="utf-8")
        restore = (ROOT / "scripts/live_restore.sh").read_text(encoding="utf-8")
        # Both paths derive the same non-secret key fingerprint from a fixed label.
        for script in (backup, restore):
            self.assertIn("zkdeal-backup-key-id-v1", script)
            self.assertIn("key_id=$(printf '%s' zkdeal-backup-key-id-v1 |", script)
        # The backup records the key id in the clear (sidecar) and in the manifest.
        self.assertIn('printf \'%s\\n\' "$key_id" >"$work/keyid"', backup)
        self.assertIn('"keyId":"$key_id"', backup)
        # The restore rejects a wrong key with a DISTINCT error, before any target
        # write, separate from the HMAC tamper failure.
        self.assertIn("backup encryption key id mismatch", restore)
        self.assertNotIn("backup encryption key id mismatch", "".join(
            line for line in restore.splitlines() if "pg_restore" in line
        ))
        wrong_key_index = restore.index("backup encryption key id mismatch")
        first_restore_index = restore.index("pg_restore")
        self.assertLess(wrong_key_index, first_restore_index)


if __name__ == "__main__":
    unittest.main()
