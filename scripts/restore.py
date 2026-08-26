#!/usr/bin/env python3
"""Verify and restore a deployment backup into a fresh directory."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath

from common import DeploymentError, load_profile, profile_state_root, require_container, require_project_write_path, sha256_file


def _safe_relative(name: str, prefix: str = "data/") -> Path:
    value = PurePosixPath(name)
    if value.is_absolute() or ".." in value.parts or not name.startswith(prefix):
        raise DeploymentError(f"unsafe archive member: {name}")
    return Path(*value.parts[1:])


def read_manifest(archive: tarfile.TarFile) -> dict:
    member = archive.getmember("manifest.json")
    stream = archive.extractfile(member)
    if stream is None:
        raise DeploymentError("backup manifest is unreadable")
    manifest = json.loads(stream.read().decode("utf-8"))
    if manifest.get("schemaVersion") != 1 or not isinstance(manifest.get("entries"), list):
        raise DeploymentError("unsupported backup manifest")
    return manifest


def verify_archive(path: Path, expected_profile: str) -> dict:
    with tarfile.open(path, "r:gz") as archive:
        manifest = read_manifest(archive)
        if manifest.get("profile") != expected_profile:
            raise DeploymentError(
                f"backup profile {manifest.get('profile')!r} does not match {expected_profile!r}"
            )
        expected = {f"data/{entry['path']}": entry for entry in manifest["entries"]}
        actual = {member.name: member for member in archive.getmembers() if member.name != "manifest.json"}
        if set(actual) != set(expected):
            raise DeploymentError("archive members do not match manifest")
        for name, member in actual.items():
            _safe_relative(name)
            if member.issym() or member.islnk() or not member.isfile():
                raise DeploymentError(f"unsupported archive member type: {name}")
            stream = archive.extractfile(member)
            if stream is None:
                raise DeploymentError(f"cannot read archive member: {name}")
            import hashlib
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            if digest.hexdigest() != expected[name]["sha256"]:
                raise DeploymentError(f"hash mismatch: {name}")
        return manifest


def restore_archive(path: Path, target: Path, manifest: dict, allow_external: bool = False) -> None:
    if target.exists():
        raise DeploymentError(f"restore target already exists: {target}")
    if not allow_external:
        require_project_write_path(target)
    temporary = target.with_name(f".{target.name}.partial")
    if temporary.exists():
        raise DeploymentError(f"stale partial restore exists: {temporary}")
    temporary.mkdir(parents=True)
    try:
        with tarfile.open(path, "r:gz") as archive:
            for entry in manifest["entries"]:
                name = f"data/{entry['path']}"
                member = archive.getmember(name)
                destination = temporary / _safe_relative(name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise DeploymentError(f"cannot read {name}")
                with destination.open("wb") as output:
                    shutil.copyfileobj(stream, output)
                if sha256_file(destination) != entry["sha256"]:
                    raise DeploymentError(f"restored hash mismatch: {entry['path']}")
        os.replace(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--target")
    parser.add_argument("--confirm", help="must equal the profile name before extraction")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--allow-external-paths", action="store_true")
    args = parser.parse_args()
    try:
        require_container()
        _, profile = load_profile(args.profile)
        archive_path = Path(args.archive).resolve()
        if not archive_path.is_file():
            raise DeploymentError(f"backup archive not found: {archive_path}")
        manifest = verify_archive(archive_path, profile["name"])
        result = {
            "archive": str(archive_path),
            "archiveSha256": sha256_file(archive_path),
            "profile": profile["name"],
            "files": len(manifest["entries"]),
            "bytes": manifest["totalBytes"],
            "verified": True,
        }
        if args.verify_only:
            print(json.dumps(result, indent=2))
            return 0
        if args.confirm != profile["name"]:
            raise DeploymentError(f"restore requires --confirm {profile['name']}")
        target = Path(args.target).resolve() if args.target else profile_state_root(profile) / "restore" / archive_path.name.removesuffix(".tar.gz")
        restore_archive(archive_path, target, manifest, args.allow_external_paths)
        result["restoredTo"] = str(target)
        print(json.dumps(result, indent=2))
        return 0
    except (DeploymentError, OSError, tarfile.TarError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
