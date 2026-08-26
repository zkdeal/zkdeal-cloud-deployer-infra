#!/usr/bin/env python3
"""Create, verify, or extract deterministic no-history transfer bundles."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UMBRELLA = SCRIPT_ROOT.parent
DEFAULT_PROJECTS = (
    "app-node",
    "apps-examples",
    "web2-api",
    "web3-protocol",
    "prover-node",
    "kurtosis-testing",
    "cloud-deployer-infra",
)
# Umbrella-root files are never discovered implicitly. These three are the
# bounded, reviewed root inputs needed to reproduce service builds and the
# local umbrella orchestration. Repository metadata is deliberately absent.
ROOT_FILE_ALLOWLIST = (".dockerignore", "docker-compose.yml", "README.md")

# These files anchor the cross-project release boundary. The complete manifest
# still binds every source byte; exposing these selected bindings in the verify
# report lets the offline 4090 request assembler reject a partial or stale
# umbrella bundle without parsing the archive a second time.
RELEASE_BINDING_PATHS = (
    "app-node/packages/room-node/capabilities/room-node.json",
    "prover-node/agent/package.json",
    "prover-node/agent/liveness-capability.json",
    "prover-node/agent/trace-capability.json",
    "prover-node/agent/test/fixtures/hosted-trace-join.json",
    "prover-node/agent/src/agent.ts",
    "prover-node/agent/src/heartbeat.ts",
    "prover-node/agent/src/local-prover.ts",
    "prover-node/agent/src/structured-log.ts",
    "prover-node/zkvm/source-manifest.candidate.json",
    "web3-protocol/contracts/contract-capabilities.json",
    "web2-api/server/capabilities/room-batch-hosted-integration-v1.json",
    "cloud-deployer-infra/config/schemas/release-soak-manifest.schema.json",
)

# Generated directories are excluded by default. The two exact prefixes below
# are owner-published runtime inputs copied by web2-api/server/Dockerfile; they
# are not interchangeable with arbitrary build output elsewhere in the tree.
GENERATED_INPUT_ALLOW_PREFIXES = (
    PurePosixPath("web3-protocol/circuits/build"),
    PurePosixPath("web3-protocol/contracts/out"),
)
ALWAYS_EXCLUDED_PARTS = {
    ".git", "node_modules", ".pnpm-store", "target", ".next", ".turbo",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "coverage", ".cache", "cache", ".state",
}
GENERATED_OUTPUT_PARTS = {
    "out", "dist", "build", "test-results", "tmp", "output", "outputs",
}
EXCLUDED_PREFIXES = (
    PurePosixPath("cloud-deployer-infra/evidence"),
    # The zkVM source bundle is the immutable build preimage.  These two
    # files are generated only by the reviewed two-CUDA-build ceremony on the
    # target and must never be smuggled in from an earlier local closure.
    PurePosixPath("prover-node/zkvm/artifacts.lock.json"),
    PurePosixPath("prover-node/zkvm/source-manifest.json"),
    PurePosixPath("web2-api/server/data"),
)
EXCLUDED_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"}
EXCLUDED_SUFFIXES = {".pem", ".p12", ".pfx", ".jks", ".keystore"}
MAX_ARCHIVE_MEMBERS = 250_000
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 512 * 1024 * 1024


class BundleError(RuntimeError):
    pass


def require_container() -> None:
    if not (Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()):
        raise BundleError("source bundles are created and verified only inside a container")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_exclusive(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def normalized_mode(path: Path) -> int:
    return 0o755 if path.stat().st_mode & 0o111 else 0o644


def entries_sha256(entries: list[dict]) -> str:
    canonical = "".join(
        f"{entry['sha256']} {entry['bytes']} {entry['mode']:04o} {entry['path']}\n"
        for entry in sorted(entries, key=lambda item: item["path"])
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def has_prefix(relative: Path, prefixes: tuple[PurePosixPath, ...]) -> bool:
    pure = PurePosixPath(relative.as_posix())
    return any(pure == prefix or prefix in pure.parents for prefix in prefixes)


def excluded(relative: Path) -> bool:
    if has_prefix(relative, EXCLUDED_PREFIXES):
        return True
    if any(part in ALWAYS_EXCLUDED_PARTS for part in relative.parts):
        return True
    if any(part in GENERATED_OUTPUT_PARTS for part in relative.parts) and not has_prefix(relative, GENERATED_INPUT_ALLOW_PREFIXES):
        return True
    if relative.name in EXCLUDED_NAMES or relative.suffix.lower() in EXCLUDED_SUFFIXES:
        return True
    if relative.name.startswith(".env.") and relative.name != ".env.example":
        return True
    return False


def inventory(umbrella: Path, projects: tuple[str, ...], maximum_bytes: int) -> list[dict]:
    entries: list[dict] = []
    for name in ROOT_FILE_ALLOWLIST:
        path = (umbrella / name).resolve()
        if not path.is_file():
            raise BundleError(f"required umbrella-root input is missing: {path}")
        size = path.stat().st_size
        if size > maximum_bytes:
            raise BundleError(f"root file exceeds bundle limit ({maximum_bytes} bytes): {name}")
        entries.append({
            "path": name,
            "bytes": size,
            "sha256": sha256_file(path),
            "mode": normalized_mode(path),
        })
    for project in projects:
        root = (umbrella / project).resolve()
        if not root.is_dir():
            raise BundleError(f"required project is missing: {root}")
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(umbrella)
            if excluded(relative):
                continue
            if path.is_symlink():
                raise BundleError(f"bundle refuses symlink: {relative}")
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size > maximum_bytes:
                raise BundleError(f"file exceeds bundle limit ({maximum_bytes} bytes): {relative}")
            entries.append({
                "path": relative.as_posix(),
                "bytes": size,
                "sha256": sha256_file(path),
                "mode": normalized_mode(path),
            })
    if len(entries) > MAX_ARCHIVE_MEMBERS:
        raise BundleError(f"source inventory exceeds member bound: {len(entries)}")
    total = sum(int(entry["bytes"]) for entry in entries)
    if total > MAX_ARCHIVE_BYTES:
        raise BundleError(f"source inventory exceeds total-byte bound: {total}")
    return entries


def evidence_inventory(evidence_root: Path, maximum_bytes: int) -> tuple[list[dict], list[str]]:
    """Inventory complete, hash-valid records plus their closure publications."""
    if not evidence_root.is_dir():
        raise BundleError(f"evidence root is missing: {evidence_root}")
    entries: list[dict] = []
    records: list[str] = []
    for run in sorted(path for path in evidence_root.iterdir() if path.is_dir() and path.name != "closures"):
        record = run / "record.json"
        checksum = run / "record.sha256"
        if not record.is_file() or not checksum.is_file():
            continue
        fields = checksum.read_text(encoding="utf-8").strip().split()
        if len(fields) != 2 or fields[1] != "record.json" or fields[0] != sha256_file(record):
            raise BundleError(f"invalid evidence record checksum: {run.name}")
        document = json.loads(record.read_text(encoding="utf-8"))
        for output in document.get("outputs", {}).values():
            relative_output = output.get("path")
            expected_hash = output.get("sha256")
            if not isinstance(relative_output, str) or not isinstance(expected_hash, str):
                raise BundleError(f"malformed evidence output declaration: {run.name}")
            output_path = SCRIPT_ROOT / relative_output
            if not output_path.is_file() or sha256_file(output_path) != expected_hash:
                raise BundleError(f"missing or changed evidence output: {relative_output}")
        records.append(run.name)
        for path in sorted(run.rglob("*")):
            if path.is_symlink():
                raise BundleError(f"evidence bundle refuses symlink: {path}")
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size > maximum_bytes:
                raise BundleError(f"evidence file exceeds bundle limit ({maximum_bytes} bytes): {path}")
            relative = path.relative_to(SCRIPT_ROOT)
            entries.append({
                "path": relative.as_posix(),
                "bytes": size,
                "sha256": sha256_file(path),
                "mode": normalized_mode(path),
            })
    closure_root = evidence_root / "closures"
    if closure_root.is_dir():
        for path in sorted(closure_root.iterdir()):
            if path.is_symlink() or not path.is_file():
                raise BundleError(f"unsupported closure entry: {path}")
            size = path.stat().st_size
            if size > maximum_bytes:
                raise BundleError(f"closure file exceeds bundle limit ({maximum_bytes} bytes): {path}")
            relative = path.relative_to(SCRIPT_ROOT)
            entries.append({
                "path": relative.as_posix(),
                "bytes": size,
                "sha256": sha256_file(path),
                "mode": normalized_mode(path),
            })
    if not records:
        raise BundleError("no complete evidence records found")
    if len(entries) > MAX_ARCHIVE_MEMBERS:
        raise BundleError(f"evidence inventory exceeds member bound: {len(entries)}")
    total = sum(int(entry["bytes"]) for entry in entries)
    if total > MAX_ARCHIVE_BYTES:
        raise BundleError(f"evidence inventory exceeds total-byte bound: {total}")
    return entries, records


def normalized_info(path: Path, arcname: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(arcname)
    stat = path.stat()
    info.size = stat.st_size
    info.mode = normalized_mode(path)
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    return info


def write_bundle(output: Path, manifest: dict, manifest_name: str) -> dict:
    entries = manifest["entries"]
    content_root = Path(manifest.pop("_contentRoot"))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.partial")
    temporary.unlink(missing_ok=True)
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
                    info = tarfile.TarInfo(manifest_name)
                    info.size = len(manifest_bytes)
                    info.mode = 0o644
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    archive.addfile(info, io.BytesIO(manifest_bytes))
                    for entry in entries:
                        path = content_root / entry["path"]
                        with path.open("rb") as stream:
                            info = normalized_info(path, entry["path"])
                            if info.mode != entry["mode"]:
                                raise BundleError(f"source mode changed during bundle creation: {entry['path']}")
                            archive.addfile(info, stream)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    outer = {
        **{key: manifest[key] for key in ("schemaVersion", "format", "historyIncluded", "secretsIncluded", "totalBytes")},
        "archive": output.name,
        "archiveBytes": output.stat().st_size,
        "archiveSha256": sha256_file(output),
        "fileCount": len(entries),
    }
    if "projects" in manifest:
        outer["projects"] = manifest["projects"]
    if "records" in manifest:
        outer["records"] = manifest["records"]
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    temporary_manifest = manifest_path.with_name(f".{manifest_path.name}.partial")
    try:
        with temporary_manifest.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(outer, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_manifest, manifest_path)
    finally:
        temporary_manifest.unlink(missing_ok=True)
    return outer


def create_bundle(umbrella: Path, output: Path, projects: tuple[str, ...], maximum_bytes: int) -> dict:
    entries = inventory(umbrella, projects, maximum_bytes)
    manifest = {
        "schemaVersion": 1,
        "format": "zkdeal-source-bundle",
        "projects": list(projects),
        "rootFiles": list(ROOT_FILE_ALLOWLIST),
        "sourcePolicy": {
            "generatedDirectoriesExcludedByDefault": sorted(GENERATED_OUTPUT_PARTS),
            "generatedRuntimeInputs": [prefix.as_posix() for prefix in GENERATED_INPUT_ALLOW_PREFIXES],
            "deploymentEvidenceIncluded": False,
        },
        "historyIncluded": False,
        "secretsIncluded": False,
        "entries": entries,
        "totalBytes": sum(entry["bytes"] for entry in entries),
        "_contentRoot": str(umbrella),
    }
    return write_bundle(output, manifest, "SOURCE-MANIFEST.json")


def create_evidence_bundle(evidence_root: Path, output: Path, maximum_bytes: int) -> dict:
    entries, records = evidence_inventory(evidence_root, maximum_bytes)
    manifest = {
        "schemaVersion": 1,
        "format": "zkdeal-evidence-bundle",
        "records": records,
        "historyIncluded": False,
        "secretsIncluded": False,
        "entries": entries,
        "totalBytes": sum(int(entry["bytes"]) for entry in entries),
        "_contentRoot": str(SCRIPT_ROOT),
    }
    return write_bundle(output, manifest, "EVIDENCE-MANIFEST.json")


def safe_member(name: str) -> Path:
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts:
        raise BundleError(f"unsafe archive member: {name}")
    return Path(*pure.parts)


def embedded_manifest(archive: tarfile.TarFile) -> tuple[dict, str, bytes]:
    names = [name for name in ("SOURCE-MANIFEST.json", "EVIDENCE-MANIFEST.json") if name in archive.getnames()]
    if len(names) != 1:
        raise BundleError("archive must contain exactly one recognized embedded manifest")
    manifest_name = names[0]
    member = archive.getmember(manifest_name)
    stream = archive.extractfile(member)
    if stream is None:
        raise BundleError("SOURCE-MANIFEST.json is unreadable")
    raw = stream.read()
    value = json.loads(raw)
    if (
        value.get("schemaVersion") != 1
        or value.get("format") not in {"zkdeal-source-bundle", "zkdeal-evidence-bundle"}
        or value.get("historyIncluded") is not False
        or value.get("secretsIncluded") is not False
    ):
        raise BundleError("unsupported, history-bearing, or secret-bearing bundle")
    return value, manifest_name, raw


def verify_bundle(
    archive_path: Path,
    outer_manifest_path: Path | None = None,
    max_members: int = MAX_ARCHIVE_MEMBERS,
    max_total_bytes: int = MAX_ARCHIVE_BYTES,
    max_member_bytes: int = MAX_ARCHIVE_MEMBER_BYTES,
) -> dict:
    if outer_manifest_path:
        outer = json.loads(outer_manifest_path.read_text(encoding="utf-8"))
        if (
            outer.get("schemaVersion") != 1
            or outer.get("historyIncluded") is not False
            or outer.get("secretsIncluded") is not False
        ):
            raise BundleError("outer manifest is unsupported, history-bearing, or secret-bearing")
        if outer.get("archiveSha256") != sha256_file(archive_path):
            raise BundleError("archive SHA-256 does not match outer manifest")
        if outer.get("archiveBytes") != archive_path.stat().st_size:
            raise BundleError("archive byte size does not match outer manifest")
    with tarfile.open(archive_path, "r:gz") as archive:
        manifest, manifest_name, manifest_bytes = embedded_manifest(archive)
        if outer_manifest_path and outer.get("format") != manifest.get("format"):
            raise BundleError("outer and embedded bundle formats disagree")
        if outer_manifest_path and outer.get("fileCount") != len(manifest.get("entries", [])):
            raise BundleError("outer and embedded file counts disagree")
        if outer_manifest_path and outer.get("totalBytes") != manifest.get("totalBytes"):
            raise BundleError("outer and embedded total byte counts disagree")
        if outer_manifest_path and outer.get("projects", []) != manifest.get("projects", []):
            raise BundleError("outer and embedded project lists disagree")
        if len(manifest["entries"]) > max_members:
            raise BundleError(f"archive exceeds member bound: {len(manifest['entries'])} > {max_members}")
        declared_total = int(manifest.get("totalBytes", -1))
        if declared_total < 0 or declared_total > max_total_bytes:
            raise BundleError(f"archive exceeds total-byte bound: {declared_total} > {max_total_bytes}")
        expected = {entry["path"]: entry for entry in manifest["entries"]}
        if len(expected) != len(manifest["entries"]):
            raise BundleError("embedded manifest contains duplicate paths")
        members = archive.getmembers()
        if len(members) > max_members + 1:
            raise BundleError("archive header count exceeds member bound")
        actual_members = {member.name: member for member in members if member.name != manifest_name}
        if len(actual_members) != len(members) - 1:
            raise BundleError("archive contains duplicate member names")
        if set(actual_members) != set(expected):
            raise BundleError("archive file set does not match embedded manifest")
        actual_total = 0
        for name, member in actual_members.items():
            safe_member(name)
            if not member.isfile() or member.issym() or member.islnk():
                raise BundleError(f"unsupported member type: {name}")
            if member.size > max_member_bytes:
                raise BundleError(f"archive member exceeds byte bound: {name}")
            actual_total += member.size
            if actual_total > max_total_bytes:
                raise BundleError("archive members exceed total-byte bound")
            stream = archive.extractfile(member)
            if stream is None:
                raise BundleError(f"cannot read member: {name}")
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            if digest.hexdigest() != expected[name]["sha256"] or member.size != expected[name]["bytes"]:
                raise BundleError(f"content mismatch: {name}")
            expected_mode = expected[name].get("mode")
            if expected_mode not in (0o644, 0o755) or member.mode != expected_mode:
                raise BundleError(f"mode mismatch: {name}")
        if actual_total != declared_total:
            raise BundleError("archive total bytes do not match embedded manifest")
        critical = {
            path: {"sha256": expected[path]["sha256"], "bytes": expected[path]["bytes"], "mode": expected[path]["mode"]}
            for path in RELEASE_BINDING_PATHS
            if path in expected
        }
        return {
            "verified": True,
            "archive": str(archive_path),
            "archiveSha256": sha256_file(archive_path),
            "outerManifest": str(outer_manifest_path) if outer_manifest_path else None,
            "outerManifestSha256": sha256_file(outer_manifest_path) if outer_manifest_path else None,
            "embeddedManifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "entriesSha256": entries_sha256(manifest["entries"]),
            "files": len(expected),
            "bytes": manifest["totalBytes"],
            "format": manifest["format"],
            "projects": manifest.get("projects", []),
            "records": manifest.get("records", []),
            "historyIncluded": False,
            "secretsIncluded": False,
            "criticalSourceBindings": critical,
        }


def extract_bundle(archive_path: Path, target: Path, outer_manifest_path: Path | None) -> dict:
    result = verify_bundle(archive_path, outer_manifest_path)
    if target.exists() and any(target.iterdir()):
        raise BundleError(f"target must be new and empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    temporary = target / ".bundle-extract.partial"
    if temporary.exists():
        raise BundleError(f"stale extraction directory exists: {temporary}")
    temporary.mkdir()
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            manifest, _, _ = embedded_manifest(archive)
            for entry in manifest["entries"]:
                member = archive.getmember(entry["path"])
                destination = temporary / safe_member(entry["path"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise BundleError(f"cannot read member: {entry['path']}")
                with destination.open("wb") as output:
                    shutil.copyfileobj(stream, output)
                os.chmod(destination, member.mode & 0o777)
        for child in temporary.iterdir():
            os.replace(child, target / child.name)
        temporary.rmdir()
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    result["extractedTo"] = str(target)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--umbrella", default=str(DEFAULT_UMBRELLA))
    create.add_argument("--output", required=True)
    create.add_argument("--projects", nargs="*", default=list(DEFAULT_PROJECTS))
    create.add_argument("--maximum-file-mib", type=int, default=512)
    evidence = commands.add_parser("create-evidence")
    evidence.add_argument("--evidence-root", default=str(SCRIPT_ROOT / "evidence"))
    evidence.add_argument("--output", required=True)
    evidence.add_argument("--maximum-file-mib", type=int, default=512)
    verify = commands.add_parser("verify")
    verify.add_argument("--archive", required=True)
    verify.add_argument("--manifest")
    verify.add_argument("--output")
    extract = commands.add_parser("extract")
    extract.add_argument("--archive", required=True)
    extract.add_argument("--manifest")
    extract.add_argument("--target", required=True)
    args = parser.parse_args()
    try:
        require_container()
        if args.command == "create":
            result = create_bundle(Path(args.umbrella).resolve(), Path(args.output).resolve(), tuple(args.projects), args.maximum_file_mib * 1024 * 1024)
        elif args.command == "create-evidence":
            result = create_evidence_bundle(Path(args.evidence_root).resolve(), Path(args.output).resolve(), args.maximum_file_mib * 1024 * 1024)
        elif args.command == "verify":
            result = verify_bundle(Path(args.archive).resolve(), Path(args.manifest).resolve() if args.manifest else None)
        else:
            result = extract_bundle(Path(args.archive).resolve(), Path(args.target).resolve(), Path(args.manifest).resolve() if args.manifest else None)
        if args.command == "verify" and args.output:
            write_json_exclusive(Path(args.output).resolve(), result)
        print(json.dumps(result, indent=2))
        return 0
    except (BundleError, OSError, tarfile.TarError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
