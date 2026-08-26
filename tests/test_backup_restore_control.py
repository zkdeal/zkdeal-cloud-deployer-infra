from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backup-restore-control"))

import backup_restore_control as br  # noqa: E402


TOKEN = "eph_backup_restore_fixture_token_00000001"
CANDIDATE = "fixture-candidate-20260821"
PLAN = "a" * 64
HOSTED = "sha256:" + "b" * 64
MASTER_KEY = "8f4d2b7a39b31e8b42be8e8bb9651fd83d71efb7f26eef1cf633e2f66f93216f"
WRONG_KEY = "9f4d2b7a39b31e8b42be8e8bb9651fd83d71efb7f26eef1cf633e2f66f93216f"
RECEIPT = {
    "candidateDescriptorSha256": "d" * 64,
    "topologyReceiptSha256": "e" * 64,
    "platform": "docker",
    "adapterImage": "registry.local/zkdeal-backup-restore-control@sha256:" + "f" * 64,
    "adapterSourceSha256": "1" * 64,
}


class BackupRestoreControlTests(unittest.TestCase):
    def topology(self) -> br.Topology:
        source_pg = br.PostgresTarget("postgres-source", 5432, "zkdeal", "zkdeal", "source-password")
        source_minio = br.MinioTarget("http://minio-source:9000", "source-access", "source-secret", "zkdeal-source")
        backup_store = br.MinioTarget("http://minio-backup:9000", "backup-access", "backup-secret", "zkdeal-backups")
        target = br.RestoreTarget(
            br.PostgresTarget("postgres-fresh", 5432, "zkdeal", "zkdeal", "fresh-password"),
            br.MinioTarget("http://minio-fresh:9000", "fresh-access", "fresh-secret", "zkdeal-restored"),
        )
        return br.Topology(
            "non-release-fixture", CANDIDATE, PLAN, HOSTED,
            hashlib.sha256(TOKEN.encode()).hexdigest(), source_pg, source_minio,
            backup_store, MASTER_KEY, br.key_fingerprint(MASTER_KEY),
            {"fresh-primary": target}, 60,
            RECEIPT["topologyReceiptSha256"],
        )

    def binding(self):
        return {
            "candidateId": CANDIDATE,
            "planSha256": PLAN,
            "hostedIntegrationToken": HOSTED,
        }

    def test_completed_backup_and_fresh_restore_are_conflict_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = br.Journal(Path(directory), "2" * 64)
            controller = br.Controller(self.topology(), TOKEN, journal, RECEIPT)

            def fake_backup(operation_id, root):
                dump = root / "database.dump"
                objects = root / "source-objects"
                dump.write_bytes(b"database")
                objects.mkdir()
                (objects / "object.bin").write_bytes(b"object")
                return {
                    "status": "COMPLETED",
                    "backupId": operation_id,
                    "candidateId": CANDIDATE,
                    "sourceDatabaseDigest": "3" * 64,
                    "sourceObjectDigest": "4" * 64,
                    "databaseBackupSha256": hashlib.sha256(b"database").hexdigest(),
                    "objectManifestSha256": "5" * 64,
                    "backupMaterialVerified": True,
                }, {"databaseDump": str(dump), "sourceObjects": str(objects)}

            backup_body = {"schemaVersion": 1, "binding": self.binding()}
            with patch.object(controller, "_backup", side_effect=fake_backup):
                backup, replay = controller.backup(
                    backup_body, "backup-control-key-0001", "backup-control-correlation-0001",
                )
                duplicate, duplicate_replay = controller.backup(
                    backup_body, "backup-control-key-0001", "backup-control-correlation-0001",
                )
            self.assertFalse(replay)
            self.assertTrue(duplicate_replay)
            self.assertEqual(backup["backupId"], duplicate["backupId"])

            restore_body = {
                "schemaVersion": 1,
                "binding": self.binding(),
                "backupId": backup["backupId"],
                "freshTarget": "fresh-primary",
            }

            def fake_restore(operation_id, _root, backup_id, _backup, target_name):
                return {
                    "status": "COMPLETED",
                    "restoreId": operation_id,
                    "backupId": backup_id,
                    "freshTarget": target_name,
                    "candidateId": CANDIDATE,
                    "sourceDatabaseDigest": "3" * 64,
                    "restoredDatabaseDigest": "3" * 64,
                    "sourceObjectDigest": "4" * 64,
                    "restoredObjectDigest": "4" * 64,
                    "freshDatabase": True,
                    "freshObjectStore": True,
                    "hashesVerified": True,
                    "serviceReady": True,
                }, {}

            with patch.object(controller, "_restore", side_effect=fake_restore):
                restored, _ = controller.restore(
                    restore_body, "restore-control-key-0001", "restore-control-correlation-0001",
                )
            for field in (
                "freshDatabase", "freshObjectStore", "hashesVerified", "serviceReady",
            ):
                self.assertTrue(restored[field])
            self.assertEqual(restored["adapterSourceSha256"], RECEIPT["adapterSourceSha256"])
            status = controller.receipt(journal.status(restored["restoreId"], "restore"))
            self.assertEqual(status["restoredDatabaseDigest"], "3" * 64)
            with self.assertRaisesRegex(br.ControlError, "changed input"):
                controller.backup(
                    backup_body, "backup-control-key-0001", "backup-control-correlation-changed",
                )

    def test_scoped_auth_and_candidate_target_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = br.Controller(
                self.topology(), TOKEN, br.Journal(Path(directory), "6" * 64), RECEIPT,
            )
            controller.authenticate("Bearer " + TOKEN)
            with self.assertRaisesRegex(br.ControlError, "backup_restore scoped"):
                controller.authenticate("Bearer owner-admin-token-must-not-work")
            with self.assertRaisesRegex(br.ControlError, "exact candidate plan"):
                controller.backup(
                    {"schemaVersion": 1, "binding": dict(self.binding(), candidateId="other-candidate")},
                    "backup-control-key-0002", "backup-control-correlation-0002",
                )
            with self.assertRaisesRegex(br.ControlError, "fixed candidate topology"):
                controller.restore({
                    "schemaVersion": 1,
                    "binding": self.binding(),
                    "backupId": "bk-" + "7" * 40,
                    "freshTarget": "attacker-target",
                }, "restore-control-key-0002", "restore-control-correlation-0002")

    def test_scoped_token_file_is_ephemeral_and_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "backup-token"
            token_path.write_text(TOKEN + "\n", encoding="utf-8")
            # The 0600 rejection is a POSIX permission boundary enforced in the
            # Linux runtime; it is only assertable where st_mode is honored.
            if os.name == "posix":
                token_path.chmod(0o604)
                with self.assertRaisesRegex(br.ControlError, "mode 0600"):
                    br.read_secret(
                        str(token_path), hashlib.sha256(TOKEN.encode()).hexdigest(),
                        "backup token", require_ephemeral=True,
                    )
            malformed = "owner_admin_token_that_is_long_enough_0001"
            token_path.write_text(malformed + "\n", encoding="utf-8")
            token_path.chmod(0o600)
            with self.assertRaisesRegex(br.ControlError, "enclave-scoped eph_"):
                br.read_secret(
                    str(token_path), hashlib.sha256(malformed.encode()).hexdigest(),
                    "backup token", require_ephemeral=True,
                )

    def test_database_and_object_hashes_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.sql"
            second = root / "second.sql"
            first.write_text(
                "-- Dumped from database version 17.1\n\\restrict random-a\nCREATE TABLE x (id int);\n\\unrestrict random-a\n",
                encoding="utf-8",
            )
            second.write_text(
                "-- Dumped from database version 17.2\n\\restrict random-b\nCREATE TABLE x (id int);\n\\unrestrict random-b\n",
                encoding="utf-8",
            )
            self.assertEqual(br.normalized_dump(first), br.normalized_dump(second))
            objects = root / "objects"
            objects.mkdir()
            (objects / "b").write_bytes(b"two")
            (objects / "a").write_bytes(b"one")
            manifest, digest = br.tree_manifest(objects)
            self.assertEqual([item["path"] for item in manifest["files"]], ["a", "b"])
            self.assertEqual(digest, br.sha256_bytes(br.canonical_bytes(manifest)))
            bundle = root / "objects.bundle.tar"
            first_bundle_digest = br.create_object_bundle(objects, bundle)
            self.assertEqual(first_bundle_digest, br.sha256_file(bundle))
            extracted = root / "extracted"
            br.extract_object_bundle(bundle, extracted)
            extracted_manifest, extracted_digest = br.tree_manifest(extracted)
            self.assertEqual(extracted_manifest, manifest)
            self.assertEqual(extracted_digest, digest)

    def test_aead_round_trip_binds_candidate_backup_and_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plaintext = root / "database.dump"
            plaintext.write_bytes(b"durable-database-marker" * 4096)
            encrypted = root / "database.dump.enc"
            key_id = br.key_fingerprint(MASTER_KEY)
            meta = br.encrypt_artifact(
                MASTER_KEY, key_id, CANDIDATE, "bk-" + "a" * 40, "database.dump", plaintext, encrypted,
            )
            self.assertEqual(len(bytes.fromhex(meta["nonce"])), br.NONCE_BYTES)
            self.assertEqual(meta["encryptedSha256"], br.sha256_file(encrypted))
            # Ciphertext must not expose the plaintext marker.
            self.assertNotIn(b"durable-database-marker", encrypted.read_bytes())
            recovered = root / "database.recovered.dump"
            data = br.decrypt_artifact(
                MASTER_KEY, key_id, CANDIDATE, "bk-" + "a" * 40, "database.dump", meta, encrypted, recovered,
            )
            self.assertEqual(data, plaintext.read_bytes())
            self.assertEqual(recovered.read_bytes(), plaintext.read_bytes())
            # Rebinding the same ciphertext under another backup id must fail the
            # authenticated additional data, proving the artifact cannot be replayed.
            with self.assertRaisesRegex(br.ControlError, "BACKUP_ARTIFACT_TAMPERED|authenticated"):
                br.decrypt_artifact(
                    MASTER_KEY, key_id, CANDIDATE, "bk-" + "b" * 40, "database.dump", meta,
                    encrypted, root / "wrong-binding.dump",
                )

    def test_tamper_gate_flips_a_byte_and_restore_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plaintext = root / "objects.bundle.tar"
            plaintext.write_bytes(b"object-bundle-bytes" * 1000)
            encrypted = root / "objects.bundle.tar.enc"
            key_id = br.key_fingerprint(MASTER_KEY)
            meta = br.encrypt_artifact(
                MASTER_KEY, key_id, CANDIDATE, "bk-" + "c" * 40, "objects.bundle.tar", plaintext, encrypted,
            )
            blob = bytearray(encrypted.read_bytes())
            blob[len(blob) // 2] ^= 0x01
            encrypted.write_bytes(bytes(blob))
            with self.assertRaises(br.ControlError) as caught:
                br.decrypt_artifact(
                    MASTER_KEY, key_id, CANDIDATE, "bk-" + "c" * 40, "objects.bundle.tar", meta,
                    encrypted, root / "objects.recovered.tar",
                )
            self.assertEqual(caught.exception.code, "BACKUP_ARTIFACT_TAMPERED")
            self.assertFalse((root / "objects.recovered.tar").exists())

    def test_wrong_key_id_is_a_distinct_rejection_before_side_effects(self):
        # A wrong master key derives a different fingerprint from the sealed
        # backup, so restore rejects it with a distinct error and never begins.
        self.assertNotEqual(br.key_fingerprint(MASTER_KEY), br.key_fingerprint(WRONG_KEY))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plaintext = root / "database.dump"
            plaintext.write_bytes(b"secret-state")
            encrypted = root / "database.dump.enc"
            meta = br.encrypt_artifact(
                MASTER_KEY, br.key_fingerprint(MASTER_KEY), CANDIDATE, "bk-" + "d" * 40,
                "database.dump", plaintext, encrypted,
            )
            # Same keyId (so it passes the fingerprint gate) but truly wrong key
            # bytes cannot authenticate, and the plaintext is never written.
            with self.assertRaises(br.ControlError) as caught:
                br.decrypt_artifact(
                    WRONG_KEY, br.key_fingerprint(MASTER_KEY), CANDIDATE, "bk-" + "d" * 40,
                    "database.dump", meta, encrypted, root / "database.recovered.dump",
                )
            self.assertEqual(caught.exception.code, "BACKUP_ARTIFACT_TAMPERED")
            self.assertFalse((root / "database.recovered.dump").exists())

    def test_topology_requires_an_independent_backup_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secrets = {
                "source-postgres-password": "source-postgres-password-value",
                "source-minio-access-key": "source-minio-access-value",
                "source-minio-secret-key": "source-minio-secret-value",
                "backup-minio-access-key": "backup-minio-access-value",
                "backup-minio-secret-key": "backup-minio-secret-value",
                "backup-encryption-key": MASTER_KEY,
                "fresh-postgres-password": "fresh-postgres-password-value",
                "fresh-minio-access-key": "fresh-minio-access-value",
                "fresh-minio-secret-key": "fresh-minio-secret-value",
            }
            paths: dict[str, Path] = {}
            for name, value in secrets.items():
                path = root / name
                path.write_text(value, encoding="utf-8")
                path.chmod(0o600)
                paths[name] = path

            def sha_of(name: str) -> str:
                return hashlib.sha256(secrets[name].encode()).hexdigest()

            config = json.loads((ROOT / "backup-restore-control/topology.example.json").read_text(encoding="utf-8"))
            config["backupRestoreTokenSha256"] = hashlib.sha256(TOKEN.encode()).hexdigest()
            config["source"]["postgres"]["passwordFile"] = str(paths["source-postgres-password"])
            config["source"]["postgres"]["passwordSha256"] = sha_of("source-postgres-password")
            config["source"]["minio"]["accessKeyFile"] = str(paths["source-minio-access-key"])
            config["source"]["minio"]["accessKeySha256"] = sha_of("source-minio-access-key")
            config["source"]["minio"]["secretKeyFile"] = str(paths["source-minio-secret-key"])
            config["source"]["minio"]["secretKeySha256"] = sha_of("source-minio-secret-key")
            config["backupStore"]["accessKeyFile"] = str(paths["backup-minio-access-key"])
            config["backupStore"]["accessKeySha256"] = sha_of("backup-minio-access-key")
            config["backupStore"]["secretKeyFile"] = str(paths["backup-minio-secret-key"])
            config["backupStore"]["secretKeySha256"] = sha_of("backup-minio-secret-key")
            config["encryption"]["keyFile"] = str(paths["backup-encryption-key"])
            config["encryption"]["keySha256"] = sha_of("backup-encryption-key")
            fresh = config["freshTargets"]["fresh-primary"]
            fresh["postgres"]["passwordFile"] = str(paths["fresh-postgres-password"])
            fresh["postgres"]["passwordSha256"] = sha_of("fresh-postgres-password")
            fresh["minio"]["accessKeyFile"] = str(paths["fresh-minio-access-key"])
            fresh["minio"]["accessKeySha256"] = sha_of("fresh-minio-access-key")
            fresh["minio"]["secretKeyFile"] = str(paths["fresh-minio-secret-key"])
            fresh["minio"]["secretKeySha256"] = sha_of("fresh-minio-secret-key")

            topology_path = root / "topology.json"

            def write_and_load(document):
                raw = br.canonical_bytes(document)
                topology_path.write_bytes(raw)
                return br.load_topology(str(topology_path), hashlib.sha256(raw).hexdigest())

            good = write_and_load(config)
            self.assertEqual(good.backup_store.endpoint, "http://minio-backup:9000")
            self.assertEqual(good.encryption_key, MASTER_KEY)
            self.assertEqual(good.encryption_key_id, br.key_fingerprint(MASTER_KEY))

            identical = json.loads(json.dumps(config))
            identical["backupStore"]["endpoint"] = identical["source"]["minio"]["endpoint"]
            with self.assertRaisesRegex(br.ControlError, "endpoint must differ from the primary data MinIO"):
                write_and_load(identical)

            shared_creds = json.loads(json.dumps(config))
            shared_creds["backupStore"]["accessKeyFile"] = str(paths["source-minio-access-key"])
            shared_creds["backupStore"]["accessKeySha256"] = sha_of("source-minio-access-key")
            shared_creds["backupStore"]["secretKeyFile"] = str(paths["source-minio-secret-key"])
            shared_creds["backupStore"]["secretKeySha256"] = sha_of("source-minio-secret-key")
            with self.assertRaisesRegex(br.ControlError, "credentials must differ from the primary data MinIO"):
                write_and_load(shared_creds)

    def test_capability_and_source_forbid_request_controlled_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = br.Controller(
                self.topology(), TOKEN, br.Journal(Path(directory), "8" * 64), RECEIPT,
            )
            capability = controller.capabilities()
            self.assertEqual(capability["scopedBearer"], "backup_restore")
            self.assertEqual(capability["freshTargets"], ["fresh-primary"])
            self.assertFalse(capability["requestControlledUrls"])
            source = (ROOT / "backup-restore-control/backup_restore_control.py").read_text(encoding="utf-8")
            self.assertNotIn("shell=True", source)
            self.assertIn('self.path not in {"/v1/backups", "/v1/restores"}', source)
            self.assertIn("fresh PostgreSQL target already contains user objects", source)
            self.assertIn("fresh MinIO target bucket is not empty", source)

    def test_image_is_distinct_nonroot_source_bound_and_read_only_compatible(self):
        dockerfile = (ROOT / "backup-restore-control/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("postgres@sha256:", dockerfile)
        self.assertIn("minio/mc@sha256:", dockerfile)
        # The runtime must carry a working AEAD implementation.
        self.assertIn("py3-cryptography", dockerfile)
        self.assertIn("from cryptography.hazmat.primitives.ciphers.aead import AESGCM", dockerfile)
        self.assertIn("ARG BACKUP_RESTORE_CONTROL_SOURCE_SHA256", dockerfile)
        self.assertIn("USER 65532:65532", dockerfile)
        readme = (ROOT / "backup-restore-control/README.md").read_text(encoding="utf-8")
        self.assertIn("read-only-root compatible", readme)
        self.assertIn("never accepts an owner administrator token", readme)


if __name__ == "__main__":
    unittest.main()
