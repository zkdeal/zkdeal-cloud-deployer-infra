#!/usr/bin/env python3
"""Seal, verify, and publish deployment evidence to versioned object lock storage.

The local ``evidence`` tree is convenient working state, not the release trust
anchor.  This command creates a deterministic, content-addressed manifest,
authenticates it with an operator-supplied HMAC key, and can publish both files
to a separately administered S3/MinIO bucket.  The key is accepted only through
the environment and is never written to the manifest, receipt, or command log.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from common import (
    DeploymentError,
    ROOT,
    atomic_write,
    require_container,
    require_project_write_path,
    sha256_file,
)


EVIDENCE_ROOT = ROOT / "evidence"
CLOSURE_ROOT = EVIDENCE_ROOT / "closures"
RUN_ID = re.compile(r"^\d{8}T\d{6}Z-[a-z0-9][a-z0-9._-]{0,79}$")
BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
HEX_256 = re.compile(r"^[0-9a-fA-F]{64,}$")
MAX_FILES = 100_000
MAX_BYTES = 10 * 1024 * 1024 * 1024


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def sealing_key() -> bytes:
    value = os.environ.get("EVIDENCE_SEALING_KEY_HEX", "")
    if not HEX_256.fullmatch(value) or len(value) % 2:
        raise DeploymentError(
            "EVIDENCE_SEALING_KEY_HEX must contain at least 32 bytes encoded as hexadecimal"
        )
    key = bytes.fromhex(value)
    if len(key) < 32:
        raise DeploymentError("evidence sealing key must be at least 32 bytes")
    return key


def safe_evidence_root(value: str | Path) -> Path:
    root = require_project_write_path(Path(value) if Path(value).is_absolute() else ROOT / value)
    if not root.exists() or not root.is_dir() or root.is_symlink():
        raise DeploymentError(f"evidence root is not a real directory: {root}")
    return root


def checked_file(path: Path, relative_to: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise DeploymentError(f"evidence entry must be a regular file: {path}")
    relative = path.relative_to(relative_to).as_posix()
    size = path.stat().st_size
    return {"path": relative, "bytes": size, "sha256": sha256_file(path)}


def _verify_record(run_dir: Path, root: Path) -> dict[str, object]:
    record_path = run_dir / "record.json"
    checksum_path = run_dir / "record.sha256"
    if not record_path.is_file() or not checksum_path.is_file():
        raise DeploymentError(f"incomplete evidence record: {run_dir.name}")
    checksum = checksum_path.read_text(encoding="utf-8").strip().split()
    if len(checksum) != 2 or checksum[1] != "record.json" or checksum[0] != sha256_file(record_path):
        raise DeploymentError(f"record checksum mismatch: {run_dir.name}")
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DeploymentError(f"invalid record JSON: {run_dir.name}: {exc}") from exc
    if not isinstance(record, dict) or not isinstance(record.get("passed"), bool):
        raise DeploymentError(f"record has no boolean passed result: {run_dir.name}")
    for output in record.get("outputs", {}).values():
        if not isinstance(output, dict):
            raise DeploymentError(f"malformed output declaration: {run_dir.name}")
        output_path = ROOT / str(output.get("path", ""))
        try:
            output_path.resolve().relative_to(run_dir.resolve())
        except ValueError as exc:
            raise DeploymentError(f"record output escaped its run directory: {run_dir.name}") from exc
        if (
            not output_path.is_file()
            or output_path.stat().st_size != output.get("bytes")
            or sha256_file(output_path) != output.get("sha256")
        ):
            raise DeploymentError(f"record output checksum mismatch: {output_path}")
    files = [checked_file(path, root) for path in sorted(run_dir.rglob("*")) if path.is_file()]
    return {
        "runId": run_dir.name,
        "gate": record.get("gate"),
        "classification": record.get("classification"),
        "passed": record["passed"],
        "recordSha256": checksum[0],
        "files": files,
    }


def build_closure(root: Path) -> dict[str, object]:
    records: list[dict[str, object]] = []
    files: list[dict[str, object]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name == "closures":
            continue
        # An outer record_gate directory exists while its command runs.  It is
        # intentionally omitted until record.json and record.sha256 make it a
        # complete record; other incomplete directories fail closed.
        has_record_parts = (child / "record.json").exists() or (child / "record.sha256").exists()
        if not has_record_parts:
            continue
        if not RUN_ID.fullmatch(child.name):
            raise DeploymentError(f"unexpected evidence record directory: {child.name}")
        record = _verify_record(child, root)
        records.append(record)
        files.extend(record["files"])  # type: ignore[arg-type]
    for name in ("README.md", "status.json"):
        path = root / name
        if path.exists():
            files.append(checked_file(path, root))
    if not records:
        raise DeploymentError("no complete evidence records are available to seal")
    files.sort(key=lambda item: str(item["path"]))
    total = sum(int(item["bytes"]) for item in files)
    if len(files) > MAX_FILES or total > MAX_BYTES:
        raise DeploymentError("evidence closure exceeds the file-count or byte safety bound")
    return {
        "schemaVersion": 1,
        "kind": "zkdeal-deployment-evidence-closure",
        "hashAlgorithm": "sha256",
        "evidenceRoot": "cloud-deployer-infra/evidence",
        "records": records,
        "files": files,
        "fileCount": len(files),
        "totalBytes": total,
    }


def write_seal(root: Path, output_root: Path) -> tuple[str, Path, Path]:
    payload = canonical_json(build_closure(root))
    closure_hash = hashlib.sha256(payload).hexdigest()
    mac = hmac.new(sealing_key(), payload, hashlib.sha256).hexdigest()
    output_root = require_project_write_path(output_root)
    manifest_path = output_root / f"sha256-{closure_hash}.json"
    mac_path = output_root / f"sha256-{closure_hash}.hmac"
    for path, data in ((manifest_path, payload), (mac_path, f"{mac}\n".encode())):
        if path.exists():
            if path.read_bytes() != data:
                raise DeploymentError(f"refusing to overwrite a content-addressed closure: {path.name}")
        else:
            atomic_write(path, data)
    return closure_hash, manifest_path, mac_path


def verify_seal(manifest_path: Path, mac_path: Path, root: Path | None) -> str:
    if manifest_path.is_symlink() or mac_path.is_symlink():
        raise DeploymentError("closure paths may not be symlinks")
    payload = manifest_path.read_bytes()
    closure_hash = hashlib.sha256(payload).hexdigest()
    if manifest_path.name != f"sha256-{closure_hash}.json":
        raise DeploymentError("closure filename does not match its SHA-256 content address")
    supplied_mac = mac_path.read_text(encoding="ascii").strip()
    expected_mac = hmac.new(sealing_key(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied_mac, expected_mac):
        raise DeploymentError("closure HMAC verification failed")
    try:
        manifest = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise DeploymentError(f"invalid closure JSON: {exc}") from exc
    if manifest.get("kind") != "zkdeal-deployment-evidence-closure":
        raise DeploymentError("unexpected closure kind")
    if root is not None:
        expected = {item["path"]: item for item in manifest.get("files", [])}
        if len(expected) != manifest.get("fileCount"):
            raise DeploymentError("closure file count is inconsistent")
        total = 0
        for relative, item in expected.items():
            path = root / relative
            actual = checked_file(path, root)
            if actual != item:
                raise DeploymentError(f"closed evidence file changed: {relative}")
            total += int(actual["bytes"])
        if total != manifest.get("totalBytes"):
            raise DeploymentError("closure byte count is inconsistent")
    return closure_hash


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise DeploymentError(f"required environment variable is missing: {name}")
    return value


class McClient:
    def __init__(self, admin: bool, allow_http_local: bool):
        prefix = "EVIDENCE_WORM_ADMIN_" if admin else "EVIDENCE_WORM_"
        self.endpoint = _required_env("EVIDENCE_WORM_ENDPOINT")
        self.access_key = _required_env(prefix + "ACCESS_KEY")
        self.secret_key = _required_env(prefix + "SECRET_KEY")
        parsed = urlsplit(self.endpoint)
        local_names = {"127.0.0.1", "localhost", "minio", "evidence-minio", "host.docker.internal"}
        if parsed.scheme != "https" and not (
            allow_http_local and parsed.scheme == "http" and parsed.hostname in local_names
        ):
            raise DeploymentError("WORM endpoint must use HTTPS (HTTP is acceptance-local only)")
        self._tmp = tempfile.TemporaryDirectory(prefix="zkdeal-mc-")
        self.env = {
            **os.environ,
            "MC_CONFIG_DIR": self._tmp.name,
            "MC_QUIET": "1",
            "MC_NO_COLOR": "1",
        }
        self.run(["alias", "set", "worm", self.endpoint, self.access_key, self.secret_key])

    def close(self) -> None:
        self._tmp.cleanup()

    def _scrub(self, value: str) -> str:
        return value.replace(self.access_key, "<redacted>").replace(self.secret_key, "<redacted>")

    def run(self, args: Iterable[str], check: bool = True, text: bool = True) -> subprocess.CompletedProcess[Any]:
        completed = subprocess.run(
            ["mc", *args], capture_output=True, text=text, env=self.env, check=False, timeout=120
        )
        if check and completed.returncode:
            stderr = completed.stderr if text else completed.stderr.decode("utf-8", errors="replace")
            raise DeploymentError(f"object-store operation failed: {self._scrub(stderr.strip())[:1000]}")
        return completed


def _bucket_name() -> str:
    bucket = _required_env("EVIDENCE_WORM_BUCKET")
    if not BUCKET.fullmatch(bucket):
        raise DeploymentError("EVIDENCE_WORM_BUCKET is not a valid DNS-style bucket name")
    return bucket


def provision_bucket(mode: str, duration: str, allow_http_local: bool) -> dict[str, str]:
    if not re.fullmatch(r"[1-9][0-9]*[dy]", duration):
        raise DeploymentError("retention duration must use Nd or Ny with a positive integer")
    bucket = _bucket_name()
    client = McClient(admin=True, allow_http_local=allow_http_local)
    try:
        target = f"worm/{bucket}"
        client.run(["mb", "--with-lock", "--ignore-existing", target])
        client.run(["version", "enable", target])
        client.run(["retention", "set", "--default", mode.lower(), duration, target])
        version = client.run(["version", "info", "--json", target]).stdout.strip()
        retention = client.run(["retention", "info", "--json", "--default", target]).stdout.strip()
        if "Enabled" not in version and "enabled" not in version:
            raise DeploymentError("bucket versioning did not report enabled")
        # mc normalizes validity (for example ``1d`` may be reported as an
        # integer day count), so validate the authoritative mode here and
        # retain the requested duration in the publication receipt.
        if mode.lower() not in retention.lower():
            raise DeploymentError("bucket default object retention did not match the request")
        return {"bucket": bucket, "mode": mode.upper(), "duration": duration, "versioning": "enabled"}
    finally:
        client.close()


def _json_line(value: str) -> dict[str, Any]:
    for line in reversed(value.splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise DeploymentError("object store returned no JSON object metadata")


def _object_version(stat: dict[str, Any]) -> str:
    for key in ("versionID", "versionId", "version_id"):
        if isinstance(stat.get(key), str) and stat[key]:
            return stat[key]
    raise DeploymentError("published object metadata contained no version ID")


def _put_immutable(client: McClient, local: Path, remote: str) -> tuple[str, dict[str, Any]]:
    existing = client.run(["stat", "--json", remote], check=False)
    if existing.returncode == 0:
        current = client.run(["cat", remote], text=False).stdout
        expected = local.read_bytes()
        if not hmac.compare_digest(current, expected):
            raise DeploymentError(
                "logical overwrite refused: content-addressed object exists with different bytes"
            )
    else:
        client.run(["cp", "--quiet", str(local), remote])
    stat = _json_line(client.run(["stat", "--json", remote]).stdout)
    version = _object_version(stat)
    retrieved = client.run(["cat", "--version-id", version, remote], text=False).stdout
    if hashlib.sha256(retrieved).hexdigest() != sha256_file(local):
        raise DeploymentError("published object retrieval hash mismatch")
    retention = json.dumps(stat, sort_keys=True).lower()
    if "retain" not in retention and "lock" not in retention:
        raise DeploymentError("published object metadata contains no object-lock retention")
    return version, stat


def publish(manifest_path: Path, mac_path: Path, mode: str, duration: str, allow_http_local: bool) -> Path:
    closure_hash = verify_seal(manifest_path, mac_path, None)
    bucket = _bucket_name()
    client = McClient(admin=False, allow_http_local=allow_http_local)
    try:
        prefix = f"closures/sha256/{closure_hash}"
        manifest_remote = f"worm/{bucket}/{prefix}/manifest.json"
        mac_remote = f"worm/{bucket}/{prefix}/manifest.hmac"
        manifest_version, manifest_stat = _put_immutable(client, manifest_path, manifest_remote)
        mac_version, mac_stat = _put_immutable(client, mac_path, mac_remote)
        receipt = {
            "schemaVersion": 1,
            "kind": "zkdeal-evidence-worm-publication",
            "closureId": f"sha256:{closure_hash}",
            "endpoint": client.endpoint,
            "bucket": bucket,
            "retention": {"mode": mode.upper(), "duration": duration},
            "objects": {
                "manifest": {
                    "key": f"{prefix}/manifest.json",
                    "versionId": manifest_version,
                    "sha256": sha256_file(manifest_path),
                },
                "hmac": {
                    "key": f"{prefix}/manifest.hmac",
                    "versionId": mac_version,
                    "sha256": sha256_file(mac_path),
                },
            },
            "objectLockMetadataObserved": {
                "manifest": "retain" in json.dumps(manifest_stat).lower(),
                "hmac": "retain" in json.dumps(mac_stat).lower(),
            },
            "publicationScope": "acceptance-local" if allow_http_local else "release",
            "logicalOverwritePolicy": "content-addressed bytes must match; newer conflicting versions are refused",
            "sealingKeyRecorded": False,
        }
        receipt_bytes = canonical_json(receipt)
        receipt_mac = hmac.new(sealing_key(), receipt_bytes, hashlib.sha256).hexdigest()
        receipt_path = CLOSURE_ROOT / f"sha256-{closure_hash}.publication.json"
        receipt_mac_path = CLOSURE_ROOT / f"sha256-{closure_hash}.publication.hmac"
        for path, data in (
            (receipt_path, receipt_bytes),
            (receipt_mac_path, f"{receipt_mac}\n".encode()),
        ):
            if path.exists() and path.read_bytes() != data:
                raise DeploymentError(f"refusing to overwrite publication receipt: {path.name}")
            if not path.exists():
                atomic_write(path, data)
        return receipt_path
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    seal_parser = sub.add_parser("seal")
    seal_parser.add_argument("--evidence-root", default="evidence")
    seal_parser.add_argument("--output-root", default="evidence/closures")
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--manifest", required=True)
    verify_parser.add_argument("--hmac", required=True)
    verify_parser.add_argument("--evidence-root")
    provision_parser = sub.add_parser("provision")
    provision_parser.add_argument("--retention-mode", choices=("COMPLIANCE", "GOVERNANCE"), default="COMPLIANCE")
    provision_parser.add_argument("--retention-duration", default="1y")
    provision_parser.add_argument("--allow-http-local", action="store_true")
    publish_parser = sub.add_parser("publish")
    publish_parser.add_argument("--manifest", required=True)
    publish_parser.add_argument("--hmac", required=True)
    publish_parser.add_argument("--retention-mode", choices=("COMPLIANCE", "GOVERNANCE"), default="COMPLIANCE")
    publish_parser.add_argument("--retention-duration", default="1y")
    publish_parser.add_argument("--allow-http-local", action="store_true")
    args = parser.parse_args()
    try:
        require_container()
        if args.command == "seal":
            closure_hash, manifest, mac_path = write_seal(
                safe_evidence_root(args.evidence_root),
                require_project_write_path(ROOT / args.output_root),
            )
            print(json.dumps({"closureId": f"sha256:{closure_hash}", "manifest": str(manifest.relative_to(ROOT)), "hmac": str(mac_path.relative_to(ROOT))}))
        elif args.command == "verify":
            root = safe_evidence_root(args.evidence_root) if args.evidence_root else None
            closure_hash = verify_seal(Path(args.manifest), Path(args.hmac), root)
            print(json.dumps({"verified": True, "closureId": f"sha256:{closure_hash}"}))
        elif args.command == "provision":
            print(json.dumps(provision_bucket(args.retention_mode, args.retention_duration, args.allow_http_local)))
        elif args.command == "publish":
            receipt = publish(Path(args.manifest), Path(args.hmac), args.retention_mode, args.retention_duration, args.allow_http_local)
            print(receipt.read_text(encoding="utf-8"), end="")
        return 0
    except (DeploymentError, OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
