#!/usr/bin/env python3
"""Candidate-bound PostgreSQL and MinIO backup/restore acceptance controller."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import threading
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_PROCESS_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_ENCRYPT_BYTES = 2 * 1024 * 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
KEY_ID = re.compile(r"^[0-9a-f]{32}$")
TOKEN = re.compile(r"^sha256:[0-9a-f]{64}$")
CANDIDATE = re.compile(r"^[a-z0-9][a-z0-9._-]{7,79}$")
SAFE_ID = re.compile(r"^[a-z][a-z0-9._-]{2,79}$")
IDEMPOTENCY = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")
CORRELATION = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
OCI_DIGEST = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
DB_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$")
BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
EPHEMERAL_TOKEN = re.compile(r"^eph_[A-Za-z0-9_-]{28,120}$")

# AES-256-GCM authenticated-encryption envelope.  The topology master key never
# touches an artifact directly: a fixed-label HMAC gives a public key
# fingerprint (keyId) so a wrong key is rejected before any restore side effect,
# and every artifact is bound to its candidate, backup, and logical name through
# additional authenticated data so a ciphertext cannot be replayed under another
# identity.
AEAD_ALGORITHM = "AES-256-GCM"
KEY_ID_LABEL = b"zkdeal-backup-key-id-v1"
NONCE_BYTES = 12
TAG_BYTES = 16


class ControlError(RuntimeError):
    def __init__(self, message: str, status: int = 400, code: str = "INVALID_REQUEST"):
        super().__init__(message)
        self.status = status
        self.code = code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fsync_path(path: Path) -> None:
    """Best-effort durable flush of a closed file.

    Reopening a file read-only to fsync is a POSIX durability idiom; on
    platforms that refuse to fsync such a descriptor the record is still fully
    written, so the flush is skipped rather than failing the operation.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def fsync_dir(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def key_fingerprint(key_hex: str) -> str:
    """Public, non-secret fingerprint of a master key.

    A wrong key derives a different fingerprint, so a restore can reject it
    before touching any fresh target instead of relying only on an
    indistinguishable authenticated-decryption failure.
    """
    return hmac.new(bytes.fromhex(key_hex), KEY_ID_LABEL, hashlib.sha256).hexdigest()[:32]


def artifact_aad(candidate_id: str, backup_id: str, name: str, key_id: str) -> bytes:
    return canonical_bytes({
        "aad": "zkdeal-backup-artifact-v1",
        "candidateId": candidate_id,
        "backupId": backup_id,
        "artifact": name,
        "keyId": key_id,
    })


def encrypt_artifact(
    key_hex: str,
    key_id: str,
    candidate_id: str,
    backup_id: str,
    name: str,
    plaintext_path: Path,
    encrypted_path: Path,
) -> dict[str, Any]:
    plaintext = plaintext_path.read_bytes()
    if len(plaintext) > MAX_ENCRYPT_BYTES:
        raise ControlError(f"artifact {name} exceeds the encryption size limit", 502, "ENCRYPTION_FAILED")
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(bytes.fromhex(key_hex)).encrypt(
        nonce, plaintext, artifact_aad(candidate_id, backup_id, name, key_id),
    )
    with encrypted_path.open("xb") as stream:
        stream.write(ciphertext)
        stream.flush()
        os.fsync(stream.fileno())
    return {
        "artifact": name,
        "nonce": nonce.hex(),
        "plaintextSha256": sha256_bytes(plaintext),
        "encryptedSha256": sha256_file(encrypted_path),
        "encryptedBytes": len(ciphertext),
    }


def decrypt_artifact(
    key_hex: str,
    key_id: str,
    candidate_id: str,
    backup_id: str,
    name: str,
    meta: dict[str, Any],
    encrypted_path: Path,
    plaintext_path: Path,
) -> bytes:
    if not isinstance(meta, dict) or not {"nonce", "plaintextSha256", "encryptedSha256"} <= set(meta):
        raise ControlError(f"backup artifact {name} metadata is incomplete", 409, "UNSAFE_BACKUP_ARTIFACT")
    nonce_hex = meta["nonce"]
    if not isinstance(nonce_hex, str) or not re.fullmatch(rf"[0-9a-f]{{{NONCE_BYTES * 2}}}", nonce_hex):
        raise ControlError(f"backup artifact {name} nonce is malformed", 409, "UNSAFE_BACKUP_ARTIFACT")
    if sha256_file(encrypted_path) != meta["encryptedSha256"]:
        raise ControlError(
            f"backup artifact {name} ciphertext differs from its sealed digest",
            409, "BACKUP_ARTIFACT_TAMPERED",
        )
    ciphertext = encrypted_path.read_bytes()
    try:
        plaintext = AESGCM(bytes.fromhex(key_hex)).decrypt(
            bytes.fromhex(nonce_hex), ciphertext, artifact_aad(candidate_id, backup_id, name, key_id),
        )
    except InvalidTag as exc:
        raise ControlError(
            f"backup artifact {name} failed authenticated decryption",
            409, "BACKUP_ARTIFACT_TAMPERED",
        ) from exc
    if sha256_bytes(plaintext) != meta["plaintextSha256"]:
        raise ControlError(
            f"backup artifact {name} plaintext differs from its sealed digest",
            409, "BACKUP_ARTIFACT_TAMPERED",
        )
    with plaintext_path.open("xb") as stream:
        stream.write(plaintext)
        stream.flush()
        os.fsync(stream.fileno())
    return plaintext


def strict_object(raw: bytes, label: str, maximum: int = MAX_REQUEST_BYTES) -> dict[str, Any]:
    if len(raw) > maximum:
        raise ControlError(f"{label} exceeds {maximum} bytes", 413, "DOCUMENT_TOO_LARGE")

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ControlError(f"{label} contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ControlError(f"{label} must contain an object")
    return value


def exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ControlError(
            f"{label} keys differ; missing={sorted(expected - set(value))}, "
            f"extra={sorted(set(value) - expected)}",
        )


def bounded_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ControlError(f"{label} must be an integer from {minimum} through {maximum}")
    return value


def read_secret(
    path_value: Any,
    expected_hash: Any,
    label: str,
    *,
    require_ephemeral: bool = False,
) -> str:
    if not isinstance(path_value, str) or not isinstance(expected_hash, str):
        raise ControlError(f"{label} binding is malformed", 503, "CONFIGURATION_ERROR")
    path = Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise ControlError(f"{label} is absent, not regular, or a symlink", 503, "CONFIGURATION_ERROR")
    # POSIX permission bits are the enforced boundary in the Linux runtime;
    # they are not meaningful on platforms that do not honor st_mode, where the
    # controller never runs, so the check applies only where it is enforceable.
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ControlError(f"{label} must have mode 0600 or stricter", 503, "CONFIGURATION_ERROR")
    raw = path.read_bytes()
    if len(raw) > 4096:
        raise ControlError(f"{label} exceeds 4096 bytes", 503, "CONFIGURATION_ERROR")
    try:
        value = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ControlError(f"{label} is not UTF-8", 503, "CONFIGURATION_ERROR") from exc
    if len(value) < 8 or any(character.isspace() for character in value):
        raise ControlError(f"{label} is empty or contains whitespace", 503, "CONFIGURATION_ERROR")
    if require_ephemeral and not EPHEMERAL_TOKEN.fullmatch(value):
        raise ControlError(f"{label} must contain an enclave-scoped eph_ token", 503, "CONFIGURATION_ERROR")
    if not SHA256.fullmatch(expected_hash) or sha256_bytes(value.encode()) != expected_hash:
        raise ControlError(f"{label} differs from the topology hash", 503, "CONFIGURATION_ERROR")
    return value


def normalized_endpoint(value: Any, allowed_hosts: set[str], label: str) -> str:
    if not isinstance(value, str) or len(value) > 512:
        raise ControlError(f"{label} is not a bounded endpoint", 503, "CONFIGURATION_ERROR")
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise ControlError(f"{label} is malformed", 503, "CONFIGURATION_ERROR") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.hostname not in allowed_hosts
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ControlError(f"{label} is outside the fixed topology", 503, "CONFIGURATION_ERROR")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


@dataclass(frozen=True)
class PostgresTarget:
    host: str
    port: int
    database: str
    user: str
    password: str

    @property
    def identity(self) -> tuple[str, int, str]:
        return self.host, self.port, self.database


@dataclass(frozen=True)
class MinioTarget:
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str

    @property
    def credential_identity(self) -> tuple[str, str, str]:
        return self.endpoint, self.access_key, self.secret_key


@dataclass(frozen=True)
class RestoreTarget:
    postgres: PostgresTarget
    minio: MinioTarget


@dataclass(frozen=True)
class Topology:
    classification: str
    candidate_id: str
    plan_sha256: str
    hosted_token: str
    scoped_token_sha256: str
    source_postgres: PostgresTarget
    source_minio: MinioTarget
    backup_store: MinioTarget
    encryption_key: str
    encryption_key_id: str
    restore_targets: dict[str, RestoreTarget]
    timeout_seconds: int
    topology_sha256: str

    @property
    def binding(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidate_id,
            "planSha256": self.plan_sha256,
            "hostedIntegrationToken": self.hosted_token,
        }


def parse_postgres(value: Any, allowed_hosts: set[str], label: str) -> PostgresTarget:
    if not isinstance(value, dict):
        raise ControlError(f"{label} must be an object", 503, "CONFIGURATION_ERROR")
    exact_keys(value, {"host", "port", "database", "user", "passwordFile", "passwordSha256"}, label)
    host = value["host"]
    database = value["database"]
    user = value["user"]
    if not isinstance(host, str) or not HOST.fullmatch(host) or host not in allowed_hosts:
        raise ControlError(f"{label}.host is outside the topology", 503, "CONFIGURATION_ERROR")
    if not isinstance(database, str) or not DB_NAME.fullmatch(database):
        raise ControlError(f"{label}.database is malformed", 503, "CONFIGURATION_ERROR")
    if not isinstance(user, str) or not DB_NAME.fullmatch(user):
        raise ControlError(f"{label}.user is malformed", 503, "CONFIGURATION_ERROR")
    port = bounded_int(value["port"], f"{label}.port", 1, 65535)
    password = read_secret(value["passwordFile"], value["passwordSha256"], f"{label}.passwordFile")
    return PostgresTarget(host, port, database, user, password)


def parse_minio(value: Any, allowed_hosts: set[str], label: str) -> MinioTarget:
    if not isinstance(value, dict):
        raise ControlError(f"{label} must be an object", 503, "CONFIGURATION_ERROR")
    exact_keys(value, {
        "endpoint", "accessKeyFile", "accessKeySha256", "secretKeyFile", "secretKeySha256", "bucket",
    }, label)
    bucket = value["bucket"]
    if not isinstance(bucket, str) or not BUCKET.fullmatch(bucket):
        raise ControlError(f"{label}.bucket is malformed", 503, "CONFIGURATION_ERROR")
    return MinioTarget(
        normalized_endpoint(value["endpoint"], allowed_hosts, f"{label}.endpoint"),
        read_secret(value["accessKeyFile"], value["accessKeySha256"], f"{label}.accessKeyFile"),
        read_secret(value["secretKeyFile"], value["secretKeySha256"], f"{label}.secretKeyFile"),
        bucket,
    )


def load_topology(path_value: str, expected_sha256: str) -> Topology:
    path = Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise ControlError("BACKUP_RESTORE_TOPOLOGY_FILE is absent or unsafe", 503, "CONFIGURATION_ERROR")
    raw = path.read_bytes()
    observed = sha256_bytes(raw)
    if not SHA256.fullmatch(expected_sha256) or observed != expected_sha256:
        raise ControlError("backup topology hash differs", 503, "CONFIGURATION_ERROR")
    value = strict_object(raw, "backup topology", MAX_RESPONSE_BYTES)
    exact_keys(value, {
        "schemaVersion", "classification", "candidateId", "planSha256",
        "hostedIntegrationToken", "backupRestoreTokenSha256", "allowedHosts",
        "source", "backupStore", "encryption", "freshTargets", "processTimeoutSeconds",
    }, "backup topology")
    if value["schemaVersion"] != 1 or value["classification"] not in {
        "release-candidate", "non-release-fixture",
    }:
        raise ControlError("backup topology schema/classification is invalid", 503, "CONFIGURATION_ERROR")
    candidate = value["candidateId"]
    plan_sha = value["planSha256"]
    hosted = value["hostedIntegrationToken"]
    token_hash = value["backupRestoreTokenSha256"]
    if not isinstance(candidate, str) or not CANDIDATE.fullmatch(candidate):
        raise ControlError("backup topology candidateId is malformed", 503, "CONFIGURATION_ERROR")
    if not isinstance(plan_sha, str) or not SHA256.fullmatch(plan_sha):
        raise ControlError("backup topology planSha256 is malformed", 503, "CONFIGURATION_ERROR")
    if not isinstance(hosted, str) or not TOKEN.fullmatch(hosted):
        raise ControlError("backup topology hostedIntegrationToken is malformed", 503, "CONFIGURATION_ERROR")
    if not isinstance(token_hash, str) or not SHA256.fullmatch(token_hash):
        raise ControlError("backup topology scoped token hash is malformed", 503, "CONFIGURATION_ERROR")
    hosts = value["allowedHosts"]
    if (
        not isinstance(hosts, list)
        or not 2 <= len(hosts) <= 32
        or len(set(hosts)) != len(hosts)
        or any(not isinstance(host, str) or not HOST.fullmatch(host) for host in hosts)
    ):
        raise ControlError("backup topology allowedHosts is invalid", 503, "CONFIGURATION_ERROR")
    allowed_hosts = set(hosts)
    source = value["source"]
    if not isinstance(source, dict):
        raise ControlError("backup topology source must be an object", 503, "CONFIGURATION_ERROR")
    exact_keys(source, {"postgres", "minio"}, "backup topology source")
    source_postgres = parse_postgres(source["postgres"], allowed_hosts, "source.postgres")
    source_minio = parse_minio(source["minio"], allowed_hosts, "source.minio")
    # The backup store is a second, separately credentialed object authority.
    # Backups must never land in the primary data MinIO, so its endpoint and its
    # credentials must both differ; identical configuration fails closed.
    backup_store = parse_minio(value["backupStore"], allowed_hosts, "backupStore")
    if backup_store.endpoint == source_minio.endpoint:
        raise ControlError(
            "backupStore endpoint must differ from the primary data MinIO",
            503, "CONFIGURATION_ERROR",
        )
    if (backup_store.access_key, backup_store.secret_key) == (source_minio.access_key, source_minio.secret_key):
        raise ControlError(
            "backupStore credentials must differ from the primary data MinIO",
            503, "CONFIGURATION_ERROR",
        )
    encryption = value["encryption"]
    if not isinstance(encryption, dict):
        raise ControlError("backup topology encryption must be an object", 503, "CONFIGURATION_ERROR")
    exact_keys(encryption, {"algorithm", "keyFile", "keySha256"}, "backup topology encryption")
    if encryption["algorithm"] != AEAD_ALGORITHM:
        raise ControlError(f"backup encryption algorithm must be {AEAD_ALGORITHM}", 503, "CONFIGURATION_ERROR")
    encryption_key = read_secret(encryption["keyFile"], encryption["keySha256"], "backupStore.encryption.keyFile")
    if not HEX64.fullmatch(encryption_key):
        raise ControlError(
            "backup encryption key must be 32 bytes of lowercase hexadecimal",
            503, "CONFIGURATION_ERROR",
        )
    encryption_key_id = key_fingerprint(encryption_key)
    targets = value["freshTargets"]
    if not isinstance(targets, dict) or not 1 <= len(targets) <= 8:
        raise ControlError("freshTargets must contain one through eight targets", 503, "CONFIGURATION_ERROR")
    restore_targets: dict[str, RestoreTarget] = {}
    for name, target in targets.items():
        if not isinstance(name, str) or not SAFE_ID.fullmatch(name) or not isinstance(target, dict):
            raise ControlError("fresh target name/value is malformed", 503, "CONFIGURATION_ERROR")
        exact_keys(target, {"postgres", "minio"}, f"fresh target {name}")
        pg = parse_postgres(target["postgres"], allowed_hosts, f"freshTargets.{name}.postgres")
        minio = parse_minio(target["minio"], allowed_hosts, f"freshTargets.{name}.minio")
        if pg.identity == source_postgres.identity:
            raise ControlError("fresh PostgreSQL target equals the source", 503, "CONFIGURATION_ERROR")
        if minio.endpoint == source_minio.endpoint:
            raise ControlError("fresh MinIO target endpoint equals the source", 503, "CONFIGURATION_ERROR")
        if minio.endpoint == backup_store.endpoint:
            raise ControlError("fresh MinIO target endpoint equals the backup store", 503, "CONFIGURATION_ERROR")
        restore_targets[name] = RestoreTarget(pg, minio)
    timeout = bounded_int(value["processTimeoutSeconds"], "processTimeoutSeconds", 30, 3600)
    return Topology(
        value["classification"], candidate, plan_sha, hosted, token_hash,
        source_postgres, source_minio, backup_store, encryption_key, encryption_key_id,
        restore_targets, timeout, observed,
    )


def durable_create(path: Path, value: dict[str, Any]) -> str:
    payload = canonical_bytes(value)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ControlError(f"write-once record exists: {path.name}", 409, "JOURNAL_CONFLICT") from exc
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_dir(path.parent)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return sha256_bytes(payload)


def read_record(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ControlError("journal record is absent or unsafe", 404, "NOT_FOUND")
    return strict_object(path.read_bytes(), "journal record", MAX_RESPONSE_BYTES)


class Journal:
    def __init__(self, root: Path, binding_digest: str):
        if root.is_symlink() or not root.is_dir():
            raise ControlError("BACKUP_RESTORE_JOURNAL_DIR is absent or unsafe", 503, "CONFIGURATION_ERROR")
        probe = root / ".write-probe"
        try:
            descriptor = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            probe.unlink()
        except OSError as exc:
            raise ControlError("backup journal is not writable", 503, "CONFIGURATION_ERROR") from exc
        self.root = root
        self.binding_digest = binding_digest
        self.lock = threading.Lock()

    def execute(
        self,
        kind: str,
        body: dict[str, Any],
        key: str,
        correlation_id: str,
        callback: Callable[[str, Path], tuple[dict[str, Any], dict[str, Any]]],
    ) -> tuple[dict[str, Any], bool]:
        if kind not in {"backup", "restore"} or not IDEMPOTENCY.fullmatch(key):
            raise ControlError("operation kind or Idempotency-Key is malformed")
        if not CORRELATION.fullmatch(correlation_id):
            raise ControlError("X-Correlation-Id is malformed")
        request_digest = sha256_bytes(canonical_bytes({
            "kind": kind,
            "bindingDigest": self.binding_digest,
            "body": body,
            "correlationId": correlation_id,
        }))
        prefix = "bk" if kind == "backup" else "rs"
        operation_id = f"{prefix}-{request_digest[:40]}"
        key_hash = sha256_bytes(key.encode())
        intent_path = self.root / f"{key_hash}.intent.json"
        closure_path = self.root / f"{operation_id}.closure.json"
        artifact_root = self.root / operation_id
        with self.lock:
            if intent_path.exists():
                intent = read_record(intent_path)
                if intent.get("requestDigest") != request_digest or intent.get("operationId") != operation_id:
                    raise ControlError("idempotency key was reused with changed input", 409, "IDEMPOTENCY_CONFLICT")
                if not closure_path.exists():
                    raise ControlError("prior operation stopped after durable intent", 409, "INCOMPLETE_OPERATION")
                closure = read_record(closure_path)
                replay = dict(closure["publicResult"])
                replay.update({
                    "operationId": operation_id,
                    "closureSha256": sha256_bytes(canonical_bytes(closure)),
                    "correlationId": closure["correlationId"],
                })
                return replay, True
            durable_create(intent_path, {
                "schemaVersion": 1,
                "service": "zkdeal-backup-restore-control",
                "kind": kind,
                "operationId": operation_id,
                "bindingDigest": self.binding_digest,
                "idempotencyKeySha256": key_hash,
                "requestDigest": request_digest,
                "correlationId": correlation_id,
                "startedAt": utc_now(),
            })
            artifact_root.mkdir(mode=0o700)
            public, private = callback(operation_id, artifact_root)
            closure = {
                "schemaVersion": 1,
                "service": "zkdeal-backup-restore-control",
                "kind": kind,
                "operationId": operation_id,
                "bindingDigest": self.binding_digest,
                "requestDigest": request_digest,
                "correlationId": correlation_id,
                "completedAt": utc_now(),
                "publicResult": public,
                "privateResult": private,
            }
            closure_digest = durable_create(closure_path, closure)
            response = dict(public)
            response.update({
                "operationId": operation_id,
                "closureSha256": closure_digest,
                "correlationId": correlation_id,
            })
            return response, False

    def status(self, operation_id: str, kind: str) -> dict[str, Any]:
        prefix = "bk" if kind == "backup" else "rs"
        if not re.fullmatch(rf"{prefix}-[0-9a-f]{{40}}", operation_id):
            raise ControlError("operationId is malformed")
        closure = read_record(self.root / f"{operation_id}.closure.json")
        if closure.get("kind") != kind or closure.get("bindingDigest") != self.binding_digest:
            raise ControlError("operation belongs to another kind or candidate", 409, "CANDIDATE_BINDING_MISMATCH")
        result = dict(closure["publicResult"])
        result.update({
            "operationId": operation_id,
            "closureSha256": sha256_bytes(canonical_bytes(closure)),
            "correlationId": closure["correlationId"],
        })
        return result


def command_env(target: PostgresTarget) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PGPASSWORD": target.password,
        "PGCONNECT_TIMEOUT": "10",
        "PGAPPNAME": "zkdeal-backup-restore-control",
    }


def pg_args(target: PostgresTarget) -> list[str]:
    return ["--host", target.host, "--port", str(target.port), "--username", target.user, "--dbname", target.database]


def run_checked(command: list[str], env: dict[str, str], timeout: int, label: str) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ControlError(f"{label} failed to execute", 502, "BACKUP_TOOL_FAILED") from exc
    if len(result.stdout) > MAX_PROCESS_OUTPUT_BYTES or len(result.stderr) > MAX_PROCESS_OUTPUT_BYTES:
        raise ControlError(f"{label} output exceeded 4 MiB", 502, "BACKUP_TOOL_FAILED")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[-1000:]
        raise ControlError(f"{label} exited {result.returncode}: {detail}", 502, "BACKUP_TOOL_FAILED")
    return result


def mc_env(
    source: MinioTarget | None = None,
    restore: MinioTarget | None = None,
    backup: MinioTarget | None = None,
) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "MC_CONFIG_DIR": "/tmp/mc-config",
        "MC_JSON": "1",
    }
    for alias, target in (("zksource", source), ("zkrestore", restore), ("zkbackup", backup)):
        if target:
            quoted_access = urllib.parse.quote(target.access_key, safe="")
            quoted_secret = urllib.parse.quote(target.secret_key, safe="")
            parsed = urllib.parse.urlsplit(target.endpoint)
            env[f"MC_HOST_{alias}"] = urllib.parse.urlunsplit((
                parsed.scheme, f"{quoted_access}:{quoted_secret}@{parsed.netloc}", "", "", "",
            ))
    return env


def normalized_dump(path: Path) -> bytes:
    output: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("-- Dumped from database version") or line.startswith("-- Dumped by pg_dump version"):
            continue
        if line.startswith("\\restrict ") or line.startswith("\\unrestrict "):
            continue
        output.append(line.rstrip())
    return ("\n".join(output) + "\n").encode()


def logical_dump(target: PostgresTarget, path: Path, timeout: int) -> str:
    run_checked([
        "pg_dump", *pg_args(target), "--format=plain", "--no-owner", "--no-privileges",
        "--inserts", "--column-inserts", "--quote-all-identifiers", "--file", str(path),
    ], command_env(target), timeout, "logical pg_dump")
    return sha256_bytes(normalized_dump(path))


def tree_manifest(root: Path) -> tuple[dict[str, Any], str]:
    files: list[dict[str, Any]] = []
    if root.is_symlink() or not root.is_dir():
        raise ControlError("object archive root is absent or unsafe", 502, "OBJECT_ARCHIVE_FAILED")
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ControlError("object archive contains a symlink", 502, "OBJECT_ARCHIVE_FAILED")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            files.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    value = {"schemaVersion": 1, "algorithm": "sha256", "files": files}
    return value, sha256_bytes(canonical_bytes(value))


def create_object_bundle(root: Path, destination: Path) -> str:
    """Create one deterministic, symlink-free archive for durable object backup."""
    if root.is_symlink() or not root.is_dir():
        raise ControlError("object archive root is absent or unsafe", 502, "OBJECT_ARCHIVE_FAILED")
    with tarfile.open(destination, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ControlError("object archive contains a symlink", 502, "OBJECT_ARCHIVE_FAILED")
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            info = tarfile.TarInfo(relative)
            info.size = path.stat().st_size
            info.mode = 0o600
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            with path.open("rb") as stream:
                archive.addfile(info, stream)
    fsync_path(destination)
    return sha256_file(destination)


def extract_object_bundle(bundle: Path, destination: Path) -> None:
    """Extract only reviewed regular-file members without tar path traversal."""
    destination.mkdir(mode=0o700)
    seen: set[str] = set()
    with tarfile.open(bundle, mode="r:") as archive:
        for member in archive:
            parts = Path(member.name).parts
            if (
                not member.isfile()
                or not member.name
                or member.name.startswith(("/", "\\"))
                or "\\" in member.name
                or any(part in {"", ".", ".."} for part in parts)
                or member.name in seen
            ):
                raise ControlError("object bundle contains an unsafe member", 409, "UNSAFE_BACKUP_ARTIFACT")
            seen.add(member.name)
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            source = archive.extractfile(member)
            if source is None:
                raise ControlError("object bundle member has no content", 409, "UNSAFE_BACKUP_ARTIFACT")
            try:
                with target.open("xb") as stream:
                    shutil.copyfileobj(source, stream, length=1024 * 1024)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                source.close()


def ensure_empty_postgres(target: PostgresTarget, timeout: int) -> None:
    result = run_checked([
        "psql", *pg_args(target), "--no-psqlrc", "--tuples-only", "--no-align",
        "--command",
        "SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p','S','v','m','f') "
        "AND n.nspname NOT IN ('pg_catalog','information_schema');",
    ], command_env(target), timeout, "fresh PostgreSQL check")
    if result.stdout.decode("utf-8", errors="replace").strip() != "0":
        raise ControlError("fresh PostgreSQL target already contains user objects", 409, "TARGET_NOT_FRESH")


def ensure_empty_bucket(target: MinioTarget, timeout: int) -> None:
    env = mc_env(restore=target)
    run_checked(["mc", "mb", "--ignore-existing", f"zkrestore/{target.bucket}"], env, timeout, "fresh bucket create")
    result = run_checked(["mc", "find", f"zkrestore/{target.bucket}", "--json"], env, timeout, "fresh bucket check")
    if result.stdout.strip():
        raise ControlError("fresh MinIO target bucket is not empty", 409, "TARGET_NOT_FRESH")


class Controller:
    def __init__(
        self,
        topology: Topology,
        scoped_token: str,
        journal: Journal,
        receipt_binding: dict[str, Any],
    ):
        self.topology = topology
        self.scoped_token = scoped_token
        self.journal = journal
        self.receipt_binding = receipt_binding

    def authenticate(self, authorization: str | None) -> None:
        supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        if not supplied or not hmac.compare_digest(
            hashlib.sha256(supplied.encode()).digest(),
            hashlib.sha256(self.scoped_token.encode()).digest(),
        ):
            raise ControlError("backup_restore scoped bearer token required", 401, "UNAUTHORIZED")

    def validate_binding(self, body: dict[str, Any]) -> None:
        if body.get("binding") != self.topology.binding:
            raise ControlError("request binding differs from the exact candidate plan", 409, "CANDIDATE_BINDING_MISMATCH")

    def receipt(self, result: dict[str, Any]) -> dict[str, Any]:
        return {**result, **self.receipt_binding}

    def capabilities(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "service": "zkdeal-backup-restore-control",
            "classification": self.topology.classification,
            "candidateId": self.topology.candidate_id,
            "planSha256": self.topology.plan_sha256,
            "hostedIntegrationToken": self.topology.hosted_token,
            "scopedBearer": "backup_restore",
            "candidateDescriptorSha256": self.receipt_binding["candidateDescriptorSha256"],
            "topologyReceiptSha256": self.receipt_binding["topologyReceiptSha256"],
            "platform": self.receipt_binding["platform"],
            "adapterImage": self.receipt_binding["adapterImage"],
            "adapterSourceSha256": self.receipt_binding["adapterSourceSha256"],
            "freshTargets": sorted(self.topology.restore_targets),
            "postgresLogicalDigest": "normalized-pg-dump-sha256",
            "objectDigest": "canonical-relative-path-size-content-sha256-manifest",
            "backupMaterial": "candidate-prefixed-independent-minio-bucket",
            "independentBackupStore": True,
            "encryption": {
                "algorithm": AEAD_ALGORITHM,
                "keyId": self.topology.encryption_key_id,
                "nonceBytes": NONCE_BYTES,
                "tagBytes": TAG_BYTES,
                "integrityVerifiedBeforeRestore": True,
            },
            "requestControlledUrls": False,
            "requestControlledCommands": False,
            "requestControlledTargetBodies": False,
            "durableWriteOnceJournal": True,
        }

    def backup(
        self,
        body: dict[str, Any],
        key: str,
        correlation_id: str,
    ) -> tuple[dict[str, Any], bool]:
        exact_keys(body, {"schemaVersion", "binding"}, "backup request")
        if body["schemaVersion"] != 1:
            raise ControlError("backup schemaVersion must equal 1")
        self.validate_binding(body)
        result, replay = self.journal.execute("backup", body, key, correlation_id, self._backup)
        return self.receipt(result), replay

    def restore(
        self,
        body: dict[str, Any],
        key: str,
        correlation_id: str,
    ) -> tuple[dict[str, Any], bool]:
        exact_keys(body, {"schemaVersion", "binding", "backupId", "freshTarget"}, "restore request")
        if body["schemaVersion"] != 1:
            raise ControlError("restore schemaVersion must equal 1")
        self.validate_binding(body)
        backup_id = body["backupId"]
        target_name = body["freshTarget"]
        if not isinstance(backup_id, str) or not re.fullmatch(r"bk-[0-9a-f]{40}", backup_id):
            raise ControlError("restore requires a completed backupId")
        if target_name not in self.topology.restore_targets:
            raise ControlError("freshTarget is not in the fixed candidate topology")
        backup = read_record(self.journal.root / f"{backup_id}.closure.json")
        if backup.get("kind") != "backup" or backup.get("bindingDigest") != self.journal.binding_digest:
            raise ControlError("backupId belongs to another candidate", 409, "CANDIDATE_BINDING_MISMATCH")
        result, replay = self.journal.execute(
            "restore", body, key, correlation_id,
            lambda operation_id, root: self._restore(operation_id, root, backup_id, backup, target_name),
        )
        return self.receipt(result), replay

    def _encrypt_upload(
        self,
        backup_id: str,
        root: Path,
        remote_prefix: str,
        backup_env: dict[str, str],
        name: str,
        plaintext_path: Path,
    ) -> dict[str, Any]:
        key_id = self.topology.encryption_key_id
        encrypted_path = root / f"{name}.enc"
        meta = encrypt_artifact(
            self.topology.encryption_key, key_id, self.topology.candidate_id, backup_id,
            name, plaintext_path, encrypted_path,
        )
        run_checked(
            ["mc", "cp", str(encrypted_path), f"{remote_prefix}/{name}.enc"],
            backup_env, self.topology.timeout_seconds, f"{name} encrypted upload",
        )
        verify_path = root / f"{name}.remote-verify.enc"
        run_checked(
            ["mc", "cp", f"{remote_prefix}/{name}.enc", str(verify_path)],
            backup_env, self.topology.timeout_seconds, f"{name} encrypted download verification",
        )
        if sha256_file(verify_path) != meta["encryptedSha256"]:
            raise ControlError("independent backup store changed the ciphertext hash", 502, "BACKUP_HASH_MISMATCH")
        return meta

    def _backup(self, backup_id: str, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        database_dump = root / "database.dump"
        logical_source = root / "source.logical.sql"
        source_objects = root / "source-objects"
        source_objects.mkdir(mode=0o700)
        run_checked([
            "pg_dump", *pg_args(self.topology.source_postgres), "--format=custom",
            "--no-owner", "--no-privileges", "--file", str(database_dump),
        ], command_env(self.topology.source_postgres), self.topology.timeout_seconds, "PostgreSQL backup")
        source_database_digest = logical_dump(
            self.topology.source_postgres, logical_source, self.topology.timeout_seconds,
        )
        source_env = mc_env(source=self.topology.source_minio)
        run_checked([
            "mc", "mirror", "--overwrite", f"zksource/{self.topology.source_minio.bucket}", str(source_objects),
        ], source_env, self.topology.timeout_seconds, "MinIO source mirror")
        object_manifest, source_object_digest = tree_manifest(source_objects)
        object_manifest_path = root / "objects.manifest.json"
        object_manifest_path.write_bytes(canonical_bytes(object_manifest))
        fsync_path(object_manifest_path)
        object_bundle = root / "objects.bundle.tar"
        create_object_bundle(source_objects, object_bundle)
        # Encrypt every artifact with AES-256-GCM and upload the ciphertext to
        # the separately credentialed backup store; the primary data MinIO never
        # holds a backup and never sees plaintext leave the enclave.
        backup_env = mc_env(backup=self.topology.backup_store)
        run_checked([
            "mc", "mb", "--ignore-existing", f"zkbackup/{self.topology.backup_store.bucket}",
        ], backup_env, self.topology.timeout_seconds, "backup bucket create")
        remote_prefix = f"zkbackup/{self.topology.backup_store.bucket}/{self.topology.candidate_id}/{backup_id}"
        artifacts = {
            "database.dump": self._encrypt_upload(backup_id, root, remote_prefix, backup_env, "database.dump", database_dump),
            "objects.bundle.tar": self._encrypt_upload(backup_id, root, remote_prefix, backup_env, "objects.bundle.tar", object_bundle),
            "objects.manifest.json": self._encrypt_upload(backup_id, root, remote_prefix, backup_env, "objects.manifest.json", object_manifest_path),
        }
        public = {
            "status": "COMPLETED",
            "backupId": backup_id,
            "candidateId": self.topology.candidate_id,
            "sourceDatabaseDigest": source_database_digest,
            "sourceObjectDigest": source_object_digest,
            "encryption": {
                "algorithm": AEAD_ALGORITHM,
                "keyId": self.topology.encryption_key_id,
                "nonceBytes": NONCE_BYTES,
                "tagBytes": TAG_BYTES,
            },
            "artifacts": artifacts,
            "independentBackupStore": True,
            "backupMaterialVerified": True,
        }
        private = {
            "backupNamespace": f"{self.topology.candidate_id}/{backup_id}",
        }
        return public, private

    def _restore(
        self,
        operation_id: str,
        root: Path,
        backup_id: str,
        backup: dict[str, Any],
        target_name: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        target = self.topology.restore_targets[target_name]
        public_backup = backup.get("publicResult")
        private_backup = backup.get("privateResult")
        if not isinstance(public_backup, dict) or public_backup.get("status") != "COMPLETED" or not isinstance(private_backup, dict):
            raise ControlError("backupId is not completed", 409, "BACKUP_NOT_COMPLETED")
        expected_namespace = f"{self.topology.candidate_id}/{backup_id}"
        if private_backup.get("backupNamespace") != expected_namespace:
            raise ControlError("backup namespace differs from the candidate", 409, "UNSAFE_BACKUP_ARTIFACT")
        # A wrong encryption key is rejected before any fresh target is touched,
        # so a wrong-key restore leaves no partial side effect.  This is a
        # distinct failure from a tampered artifact, whose ciphertext or
        # authenticated-decryption check fails later.
        recorded_encryption = public_backup.get("encryption")
        key_id = self.topology.encryption_key_id
        if not isinstance(recorded_encryption, dict) or recorded_encryption.get("algorithm") != AEAD_ALGORITHM:
            raise ControlError("backup was not sealed with the supported AEAD envelope", 409, "UNSAFE_BACKUP_ARTIFACT")
        if recorded_encryption.get("keyId") != key_id:
            raise ControlError(
                "restore encryption key id does not match the sealed backup",
                409, "RESTORE_KEY_ID_MISMATCH",
            )
        artifacts = public_backup.get("artifacts")
        if not isinstance(artifacts, dict) or set(artifacts) != {"database.dump", "objects.bundle.tar", "objects.manifest.json"}:
            raise ControlError("backup artifact set is incomplete", 409, "UNSAFE_BACKUP_ARTIFACT")
        remote_prefix = f"zkbackup/{self.topology.backup_store.bucket}/{expected_namespace}"
        backup_env = mc_env(backup=self.topology.backup_store)
        database_dump = root / "database.downloaded.dump"
        object_bundle = root / "objects.downloaded.bundle.tar"
        object_manifest_path = root / "objects.downloaded.manifest.json"
        plaintext_by_name = {
            "database.dump": database_dump,
            "objects.bundle.tar": object_bundle,
            "objects.manifest.json": object_manifest_path,
        }
        # Download every ciphertext and authenticate-decrypt it in the
        # operation's private workspace BEFORE any fresh target is mutated.  A
        # tampered or wrong-key artifact fails here, leaving the fresh database
        # and object store untouched.
        for name, plaintext_path in plaintext_by_name.items():
            encrypted_path = root / f"{name}.downloaded.enc"
            run_checked(
                ["mc", "cp", f"{remote_prefix}/{name}.enc", str(encrypted_path)],
                backup_env, self.topology.timeout_seconds, f"{name} encrypted download",
            )
            decrypt_artifact(
                self.topology.encryption_key, key_id, self.topology.candidate_id, backup_id,
                name, artifacts[name], encrypted_path, plaintext_path,
            )
        source_objects = root / "source-objects"
        extract_object_bundle(object_bundle, source_objects)
        restored_source_manifest, restored_source_object_digest = tree_manifest(source_objects)
        recorded_manifest = strict_object(
            object_manifest_path.read_bytes(), "downloaded object manifest", MAX_RESPONSE_BYTES,
        )
        if (
            recorded_manifest != restored_source_manifest
            or restored_source_object_digest != public_backup.get("sourceObjectDigest")
        ):
            raise ControlError("downloaded object backup differs from its manifest", 409, "BACKUP_HASH_MISMATCH")
        ensure_empty_postgres(target.postgres, self.topology.timeout_seconds)
        ensure_empty_bucket(target.minio, self.topology.timeout_seconds)
        run_checked([
            "pg_restore", *pg_args(target.postgres), "--exit-on-error", "--no-owner", "--no-privileges", str(database_dump),
        ], command_env(target.postgres), self.topology.timeout_seconds, "PostgreSQL restore")
        restored_logical = root / "restored.logical.sql"
        restored_database_digest = logical_dump(target.postgres, restored_logical, self.topology.timeout_seconds)
        restore_env = mc_env(restore=target.minio)
        run_checked([
            "mc", "mirror", "--overwrite", str(source_objects), f"zkrestore/{target.minio.bucket}",
        ], restore_env, self.topology.timeout_seconds, "MinIO restore")
        restored_objects = root / "restored-objects"
        restored_objects.mkdir(mode=0o700)
        run_checked([
            "mc", "mirror", "--overwrite", f"zkrestore/{target.minio.bucket}", str(restored_objects),
        ], restore_env, self.topology.timeout_seconds, "MinIO restored download")
        _restored_manifest, restored_object_digest = tree_manifest(restored_objects)
        source_database_digest = str(public_backup.get("sourceDatabaseDigest", ""))
        source_object_digest = str(public_backup.get("sourceObjectDigest", ""))
        hashes_verified = (
            SHA256.fullmatch(source_database_digest) is not None
            and SHA256.fullmatch(source_object_digest) is not None
            and restored_database_digest == source_database_digest
            and restored_object_digest == source_object_digest
        )
        if not hashes_verified:
            raise ControlError("restored PostgreSQL or MinIO digest differs", 409, "RESTORE_HASH_MISMATCH")
        run_checked([
            "psql", *pg_args(target.postgres), "--no-psqlrc", "--tuples-only", "--no-align", "--command", "SELECT 1;",
        ], command_env(target.postgres), self.topology.timeout_seconds, "restored PostgreSQL readiness")
        run_checked(["mc", "stat", f"zkrestore/{target.minio.bucket}"], restore_env, self.topology.timeout_seconds, "restored MinIO readiness")
        return {
            "status": "COMPLETED",
            "restoreId": operation_id,
            "backupId": backup_id,
            "freshTarget": target_name,
            "candidateId": self.topology.candidate_id,
            "sourceDatabaseDigest": source_database_digest,
            "restoredDatabaseDigest": restored_database_digest,
            "sourceObjectDigest": source_object_digest,
            "restoredObjectDigest": restored_object_digest,
            "freshDatabase": True,
            "freshObjectStore": True,
            "hashesVerified": True,
            "integrityVerifiedBeforeRestore": True,
            "independentBackupStore": True,
            "encryptionKeyId": key_id,
            "serviceReady": True,
        }, {
            "restoredLogical": str(restored_logical),
            "restoredObjects": str(restored_objects),
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "zkdeal-backup-restore-control/1"
    controller: Controller

    def log_message(self, format_string: str, *args: Any) -> None:
        print(json.dumps({
            "component": "backup-restore-control-http",
            "event": "access",
            "message": format_string % args,
        }, sort_keys=True), flush=True)

    def send_json(self, status: int, value: dict[str, Any], replay: bool = False) -> None:
        payload = canonical_bytes(value)
        if len(payload) > MAX_RESPONSE_BYTES:
            status = 500
            payload = canonical_bytes({"error": {"code": "RESPONSE_TOO_LARGE", "message": "response exceeded 1 MiB"}})
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        if replay:
            self.send_header("idempotent-replay", "true")
        self.end_headers()
        self.wfile.write(payload)

    def handle_error(self, error: Exception) -> None:
        if isinstance(error, ControlError):
            self.send_json(error.status, {"error": {"code": error.code, "message": str(error)}})
        else:
            self.send_json(500, {"error": {"code": "INTERNAL_ERROR", "message": "control operation failed"}})

    def body(self) -> dict[str, Any]:
        if self.headers.get("transfer-encoding"):
            raise ControlError("chunked bodies are forbidden", 411, "CONTENT_LENGTH_REQUIRED")
        try:
            length = int(self.headers.get("content-length", "-1"))
        except ValueError as exc:
            raise ControlError("Content-Length is malformed", 411, "CONTENT_LENGTH_REQUIRED") from exc
        if not 0 <= length <= MAX_REQUEST_BYTES:
            raise ControlError("Content-Length is absent or too large", 413, "DOCUMENT_TOO_LARGE")
        if self.headers.get_content_type() != "application/json":
            raise ControlError("content-type must be application/json", 415, "UNSUPPORTED_MEDIA_TYPE")
        return strict_object(self.rfile.read(length), "request")

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.query or parsed.fragment:
                raise ControlError("query and fragment data are forbidden")
            if parsed.path == "/health":
                self.send_json(200, {"status": "healthy", "service": "zkdeal-backup-restore-control"})
                return
            if parsed.path == "/ready":
                self.send_json(200, {
                    "status": "ready",
                    "candidateId": self.controller.topology.candidate_id,
                    "topologyReceiptSha256": self.controller.topology.topology_sha256,
                })
                return
            if parsed.path == "/capabilities":
                self.send_json(200, self.controller.capabilities())
                return
            match = re.fullmatch(r"/v1/(backups|restores)/((?:bk|rs)-[0-9a-f]{40})", parsed.path)
            if match:
                self.controller.authenticate(self.headers.get("authorization"))
                kind = "backup" if match.group(1) == "backups" else "restore"
                self.send_json(200, self.controller.receipt(self.controller.journal.status(match.group(2), kind)))
                return
            raise ControlError("route not found", 404, "NOT_FOUND")
        except Exception as exc:
            self.handle_error(exc)

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.path not in {"/v1/backups", "/v1/restores"}:
                raise ControlError("route not found", 404, "NOT_FOUND")
            self.controller.authenticate(self.headers.get("authorization"))
            key = self.headers.get("idempotency-key", "")
            correlation_id = self.headers.get("x-correlation-id", "")
            body = self.body()
            if self.path == "/v1/backups":
                result, replay = self.controller.backup(body, key, correlation_id)
            else:
                result, replay = self.controller.restore(body, key, correlation_id)
            self.send_json(200, result, replay)
        except Exception as exc:
            self.handle_error(exc)


def build_controller(environ: dict[str, str] | None = None) -> Controller:
    env = os.environ if environ is None else environ
    topology = load_topology(
        env.get("BACKUP_RESTORE_TOPOLOGY_FILE", ""),
        env.get("BACKUP_RESTORE_TOPOLOGY_SHA256", ""),
    )
    for name, expected in (
        ("BACKUP_RESTORE_CANDIDATE_ID", topology.candidate_id),
        ("BACKUP_RESTORE_PLAN_SHA256", topology.plan_sha256),
        ("BACKUP_RESTORE_HOSTED_INTEGRATION_TOKEN", topology.hosted_token),
    ):
        if env.get(name) != expected:
            raise ControlError(f"{name} differs from the hash-bound topology", 503, "CONFIGURATION_ERROR")
    token = read_secret(
        env.get("BACKUP_RESTORE_TOKEN_FILE", ""),
        topology.scoped_token_sha256,
        "BACKUP_RESTORE_TOKEN_FILE",
        require_ephemeral=True,
    )
    descriptor_sha = env.get("BACKUP_RESTORE_CANDIDATE_DESCRIPTOR_SHA256", "")
    source_sha = env.get("BACKUP_RESTORE_ADAPTER_SOURCE_SHA256", "")
    image = env.get("BACKUP_RESTORE_ADAPTER_IMAGE", "")
    platform = env.get("BACKUP_RESTORE_PLATFORM", "")
    if not SHA256.fullmatch(descriptor_sha) or not SHA256.fullmatch(source_sha):
        raise ControlError("candidate descriptor or adapter source SHA-256 is malformed", 503, "CONFIGURATION_ERROR")
    if not OCI_DIGEST.fullmatch(image):
        raise ControlError("backup adapter image must be repository@sha256", 503, "CONFIGURATION_ERROR")
    if platform != "docker":
        raise ControlError("BACKUP_RESTORE_PLATFORM must equal docker", 503, "CONFIGURATION_ERROR")
    receipt_binding = {
        "candidateDescriptorSha256": descriptor_sha,
        "topologyReceiptSha256": topology.topology_sha256,
        "platform": platform,
        "adapterImage": image,
        "adapterSourceSha256": source_sha,
    }
    binding_digest = sha256_bytes(canonical_bytes({**topology.binding, **receipt_binding}))
    journal = Journal(Path(env.get("BACKUP_RESTORE_JOURNAL_DIR", "/journal")), binding_digest)
    return Controller(topology, token, journal, receipt_binding)


def main() -> int:
    try:
        controller = build_controller()
        Handler.controller = controller
        host = os.environ.get("BACKUP_RESTORE_HOST", "0.0.0.0")
        port = bounded_int(int(os.environ.get("BACKUP_RESTORE_PORT", "8080")), "BACKUP_RESTORE_PORT", 1, 65535)
        server = ThreadingHTTPServer((host, port), Handler)
        print(json.dumps({
            "component": "backup-restore-control",
            "event": "started",
            "candidateId": controller.topology.candidate_id,
            "classification": controller.topology.classification,
            "port": port,
        }, sort_keys=True), flush=True)
        server.serve_forever()
        return 0
    except (ControlError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
