#!/usr/bin/env python3
"""Create a content-addressed backup of the configured deployment state."""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import tarfile
import tempfile
from pathlib import Path

from common import DeploymentError, load_profile, profile_state_root, project_path, require_container, require_project_write_path, sha256_file


SENSITIVE_NAME = re.compile(r"(^|[._-])(secret|private|password|credential)([._-]|$)|\.key$|^\.env$", re.I)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def build_manifest(source: Path, excluded: tuple[Path, ...], profile_name: str) -> dict:
    entries = []
    for path in sorted(source.rglob("*")):
        if any(_inside(path, item) for item in excluded):
            continue
        if path.is_symlink():
            raise DeploymentError(f"backup refuses symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(source).as_posix()
        if SENSITIVE_NAME.search(path.name):
            raise DeploymentError(f"backup refuses possible secret material: {relative}")
        entries.append({
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return {
        "schemaVersion": 1,
        "profile": profile_name,
        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": str(source),
        "entries": entries,
        "totalBytes": sum(item["bytes"] for item in entries),
    }


def create_backup(source: Path, output: Path, manifest: dict, allow_external: bool = False) -> None:
    if not allow_external:
        require_project_write_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".partial", dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with tarfile.open(temporary, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(manifest_bytes)
            info.mode = 0o600
            info.mtime = 0
            archive.addfile(info, io.BytesIO(manifest_bytes))
            for entry in manifest["entries"]:
                path = source / entry["path"]
                info = archive.gettarinfo(str(path), arcname=f"data/{entry['path']}")
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--source")
    parser.add_argument("--output")
    parser.add_argument("--label", default="manual")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-external-paths", action="store_true")
    args = parser.parse_args()
    try:
        require_container()
        _, profile = load_profile(args.profile)
        source = Path(args.source).resolve() if args.source else profile_state_root(profile)
        backup_root = project_path(profile["storage"]["backupRoot"])
        if not source.is_dir():
            raise DeploymentError(f"backup source is not a directory: {source}")
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_label = re.sub(r"[^a-zA-Z0-9._-]", "-", args.label)[:40]
        output = Path(args.output).resolve() if args.output else backup_root / f"{profile['name']}-{timestamp}-{safe_label}.tar.gz"
        manifest = build_manifest(source, (backup_root, source / "restore"), profile["name"])
        result = {"output": str(output), "files": len(manifest["entries"]), "bytes": manifest["totalBytes"]}
        if args.dry_run:
            result["dryRun"] = True
        else:
            create_backup(source, output, manifest, args.allow_external_paths)
            result["archiveSha256"] = sha256_file(output)
        print(json.dumps(result, indent=2))
        return 0
    except (DeploymentError, OSError, tarfile.TarError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
