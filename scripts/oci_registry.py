#!/usr/bin/env python3
"""Publish and back up candidate images by immutable digest, without a VCS."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from common import DeploymentError, ROOT, atomic_write_json, require_container, sha256_file


REGISTRY_IMAGE = "registry@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373"
PYTHON_IMAGE = "python@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d"
SAFE_PART = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IMAGE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,63}$")
REGISTRY = re.compile(r"^[a-zA-Z0-9.-]+(?::[0-9]{1,5})?$")
MAX_MEMBERS = 1_000_000
MAX_TOTAL_BYTES = 1 << 40


ARCHIVE_PROGRAM = r'''
import gzip, os, stat, sys, tarfile
from pathlib import Path
root = Path('/registry')
with gzip.GzipFile(fileobj=sys.stdout.buffer, mode='wb', mtime=0, filename='') as compressed:
  with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.PAX_FORMAT) as archive:
    for path in sorted(root.rglob('*'), key=lambda value: value.relative_to(root).as_posix()):
      relative = path.relative_to(root).as_posix()
      info = archive.gettarinfo(str(path), arcname=relative)
      if not (info.isfile() or info.isdir()):
        raise SystemExit('unsupported registry member: ' + relative)
      info.uid = info.gid = 0
      info.uname = info.gname = ''
      info.mtime = 0
      if info.isfile():
        with path.open('rb') as stream:
          archive.addfile(info, stream)
      else:
        archive.addfile(info)
'''


RESTORE_PROGRAM = r'''
import gzip, os, shutil, stat, sys, tarfile
from pathlib import Path, PurePosixPath
root = Path('/registry')
with gzip.GzipFile(fileobj=sys.stdin.buffer, mode='rb') as compressed:
  with tarfile.open(fileobj=compressed, mode='r|') as archive:
    for member in archive:
      pure = PurePosixPath(member.name)
      if pure.is_absolute() or '..' in pure.parts or not pure.parts:
        raise SystemExit('unsafe archive path')
      if not (member.isfile() or member.isdir()):
        raise SystemExit('unsafe archive member type')
      target = root.joinpath(*pure.parts)
      if member.isdir():
        target.mkdir(parents=True, exist_ok=True)
      else:
        target.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
          raise SystemExit('missing archive member body')
        with target.open('xb') as output:
          shutil.copyfileobj(source, output)
      os.chmod(target, member.mode & 0o777)
'''


def docker(*args: str, capture: bool = True, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["docker", *args], text=True, capture_output=capture, check=False,
        env={**os.environ, "DOCKER_CONTENT_TRUST": "0"},
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-2000:]
        raise DeploymentError(f"docker {' '.join(args[:3])} failed: {detail}")
    return completed


def safe_part(value: str, label: str) -> str:
    clean = value.strip().lower()
    if not SAFE_PART.fullmatch(clean):
        raise DeploymentError(f"{label} must match {SAFE_PART.pattern}")
    return clean


def safe_registry(value: str) -> str:
    clean = value.strip().removeprefix("http://").removeprefix("https://").rstrip("/")
    if not REGISTRY.fullmatch(clean):
        raise DeploymentError("registry must be a host with an optional numeric port")
    return clean


def write_json(path: Path, value: object) -> None:
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, value)


def write_json_exclusive(path: Path, value: object) -> None:
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def regular_json(path_value: str, label: str) -> tuple[Path, bytes, dict[str, object]]:
    path = Path(path_value)
    if not path.is_absolute():
        path = ROOT / path
    if path.is_symlink() or not path.is_file():
        raise DeploymentError(f"{label} is absent, not regular, or a symlink")
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeploymentError(f"{label} is not JSON") from exc
    if not isinstance(value, dict):
        raise DeploymentError(f"{label} must be a JSON object")
    return path.resolve(), raw, value


def promotion_key(path_value: str) -> tuple[bytes, str]:
    path = Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise DeploymentError("promotion MAC key is absent, not regular, or a symlink")
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise DeploymentError("promotion MAC key must be mounted outside the deployment/evidence tree")
    mode = stat.S_IMODE(resolved.stat().st_mode)
    if mode & 0o077:
        raise DeploymentError("promotion MAC key must not be group/world accessible")
    key = resolved.read_bytes()
    if len(key) < 32 or len(key) > 4096:
        raise DeploymentError("promotion MAC key must contain 32..4096 bytes")
    return key, "sha256:" + hashlib.sha256(key).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def image_inspect(reference: str) -> dict[str, object]:
    result = docker("image", "inspect", reference, "--format", "{{json .}}")
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict):
        raise DeploymentError(f"invalid image inspection for {reference}")
    return parsed


def pushed_digest(output: str) -> str:
    for line in output.splitlines():
        match = re.search(r"\bdigest:\s+(sha256:[0-9a-f]{64})\b", line.strip())
        if match:
            return match.group(1)
    raise DeploymentError("Docker push did not return an immutable registry digest")


def immutable_ref(registry: str, repository_path: str, digest: str) -> str:
    if not DIGEST.fullmatch(digest):
        raise DeploymentError("invalid OCI digest")
    return f"{safe_registry(registry)}/{repository_path}@{digest}"


def publish(args: argparse.Namespace) -> int:
    registry = safe_registry(args.registry)
    candidate = safe_part(args.candidate, "candidate")
    artifact = safe_part(args.artifact, "artifact")
    namespace = "/".join(safe_part(part, "namespace segment") for part in args.namespace.split("/"))
    repository_path = f"{namespace}/{candidate}/{artifact}"
    source = image_inspect(args.source)
    source_id = str(source.get("Id", ""))
    if not DIGEST.fullmatch(source_id):
        raise DeploymentError("source image lacks a sha256 content ID")
    transport = f"{registry}/{repository_path}:transfer-{source_id.removeprefix('sha256:')[:16]}"
    if docker("image", "pull", transport, check=False).returncode == 0:
        raise DeploymentError("candidate transport reference already exists; use a new candidate namespace")
    docker("image", "tag", args.source, transport)
    try:
        pushed = docker("image", "push", transport)
        digest = pushed_digest(f"{pushed.stdout}\n{pushed.stderr}")
    finally:
        docker("image", "rm", transport, check=False)
    immutable = immutable_ref(registry, repository_path, digest)
    docker("image", "pull", immutable)
    pulled = image_inspect(immutable)
    repo_digests = pulled.get("RepoDigests", [])
    if immutable not in repo_digests:
        raise DeploymentError("daemon inspection did not retain the exact immutable reference")
    result = {
        "schemaVersion": 1,
        "candidate": candidate,
        "artifact": artifact,
        "repositoryPath": repository_path,
        "digest": digest,
        "immutableReference": immutable,
        "sourceImageId": source_id,
        "pulledImageId": pulled.get("Id"),
        "sizeBytes": pulled.get("Size"),
        "daemonRepoDigestVerified": True,
        "mutableReferenceRecorded": False,
        "transportReferenceRemovedLocally": True,
    }
    if ":transfer-" in json.dumps(result):
        raise DeploymentError("mutable transport reference leaked into publication evidence")
    write_json(Path(args.output), result)
    print(json.dumps(result, sort_keys=True))
    return 0


def load_publication(path: Path) -> dict[str, object]:
    if not path.is_absolute():
        path = ROOT / path
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise DeploymentError("publication manifest must be an object")
    if value.get("mutableReferenceRecorded") is not False:
        raise DeploymentError("publication manifest is not immutable-only")
    repository_path = value.get("repositoryPath")
    digest = value.get("digest")
    if not isinstance(repository_path, str) or not all(
        SAFE_PART.fullmatch(part) for part in repository_path.split("/")
    ):
        raise DeploymentError("publication repository path is invalid")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise DeploymentError("publication digest is invalid")
    return value


def verify_image(args: argparse.Namespace) -> int:
    publication = load_publication(Path(args.manifest))
    registry = safe_registry(args.registry) if args.registry else str(publication["immutableReference"]).split("/", 1)[0]
    reference = immutable_ref(registry, str(publication["repositoryPath"]), str(publication["digest"]))
    docker("image", "pull", reference)
    inspected = image_inspect(reference)
    if reference not in inspected.get("RepoDigests", []):
        raise DeploymentError("daemon did not verify the exact digest reference")
    if publication.get("pulledImageId") and inspected.get("Id") != publication["pulledImageId"]:
        raise DeploymentError("pulled image ID differs from the publication closure")
    result = {
        "verified": True, "immutableReference": reference,
        "digest": publication["digest"], "daemonImageId": inspected.get("Id"),
        "daemonRepoDigestVerified": True,
    }
    print(json.dumps(result, sort_keys=True))
    return 0


def promotion_context(
    args: argparse.Namespace, publication: dict[str, object],
) -> tuple[dict[str, object], str, dict[str, object], str]:
    candidate_path, candidate_raw, candidate = regular_json(
        args.candidate_file, "candidate descriptor",
    )
    composite_path, composite_raw, composite = regular_json(
        args.composite_seal, "source/generated composite seal",
    )
    candidate_id = candidate.get("candidateId")
    if not isinstance(candidate_id, str) or safe_part(candidate_id, "candidateId") != publication.get("candidate"):
        raise DeploymentError("candidate descriptor and staged publication name different candidates")
    image_key = args.image_key
    if not isinstance(image_key, str) or not IMAGE_KEY.fullmatch(image_key):
        raise DeploymentError("image-key is malformed")
    images = candidate.get("images")
    staged_ref = publication.get("immutableReference")
    if not isinstance(images, dict) or images.get(image_key) != staged_ref:
        raise DeploymentError("candidate image reference differs from the staged publication")
    if composite.get("schema") != "zkdeal/4090-evidence-closure/v2" or composite.get("algorithm") != "sha256":
        raise DeploymentError("source/generated composite seal is not schema v2 SHA-256 evidence")
    physical = composite.get("physicalAcceptance")
    owner = candidate.get("ownerBroadSeal")
    if (
        not isinstance(physical, dict)
        or not isinstance(owner, dict)
        or physical.get("ownerAcceptanceToken") != owner.get("hostedIntegrationAcceptanceToken")
    ):
        raise DeploymentError("composite owner token differs from the candidate descriptor")
    generated = candidate.get("zkvmGeneratedTrustRoot")
    source = composite.get("source")
    if (
        not isinstance(generated, dict)
        or not isinstance(source, dict)
        or source.get("generatedTrustRootClosureSha256")
        != (generated.get("generatedClosure") or {}).get("sha256")
        or composite.get("artifactLockSha256") != generated.get("artifactLockSha256")
        or composite.get("programId") != generated.get("programId")
    ):
        raise DeploymentError("composite generated trust root differs from the candidate descriptor")
    if image_key == "prover" and composite.get("runtimeImage") != staged_ref:
        raise DeploymentError("composite runtime image differs from the staged prover image")
    return candidate, hashlib.sha256(candidate_raw).hexdigest(), composite, hashlib.sha256(composite_raw).hexdigest()


def promote(args: argparse.Namespace) -> int:
    publication_path = Path(args.manifest)
    if not publication_path.is_absolute():
        publication_path = ROOT / publication_path
    publication = load_publication(publication_path)
    candidate, candidate_sha, _composite, composite_sha = promotion_context(args, publication)
    key, key_id = promotion_key(args.key_file)
    registry = safe_registry(args.registry) if args.registry else str(publication["immutableReference"]).split("/", 1)[0]
    release = safe_part(args.release, "release")
    namespace = "/".join(safe_part(part, "namespace segment") for part in args.namespace.split("/"))
    artifact = safe_part(str(publication.get("artifact", "")), "artifact")
    digest = str(publication["digest"])
    source_ref = immutable_ref(registry, str(publication["repositoryPath"]), digest)
    target_path = f"{namespace}/{release}/{artifact}"
    target_ref = immutable_ref(registry, target_path, digest)
    # `docker image pull` can report a cached digest even after a fresh local
    # registry volume is created. Manifest inspection contacts the registry
    # and therefore distinguishes a genuinely occupied release namespace.
    if docker("manifest", "inspect", target_ref, check=False).returncode == 0:
        raise DeploymentError("release digest already exists; use a fresh release namespace")
    docker("image", "pull", source_ref)
    source = image_inspect(source_ref)
    if source_ref not in source.get("RepoDigests", []):
        raise DeploymentError("staged daemon image lacks its immutable source reference")
    transport = f"{registry}/{target_path}:promotion-{digest.removeprefix('sha256:')[:16]}"
    docker("image", "tag", source_ref, transport)
    try:
        pushed = docker("image", "push", transport)
        promoted_digest = pushed_digest(f"{pushed.stdout}\n{pushed.stderr}")
    finally:
        docker("image", "rm", transport, check=False)
    if promoted_digest != digest:
        raise DeploymentError("release promotion changed the staged manifest digest")
    docker("image", "pull", target_ref)
    promoted = image_inspect(target_ref)
    if target_ref not in promoted.get("RepoDigests", []):
        raise DeploymentError("promoted daemon image lacks its immutable release reference")
    if source.get("Id") != promoted.get("Id"):
        raise DeploymentError("promoted daemon image ID differs from the staged artifact")
    payload = {
        "schema": "zkdeal/oci-promotion/v1",
        "candidateId": candidate["candidateId"],
        "release": release,
        "artifact": artifact,
        "imageKey": args.image_key,
        "candidateDescriptorSha256": candidate_sha,
        "sourceGeneratedCompositeSealSha256": composite_sha,
        "sourcePublicationManifestSha256": sha256_file(publication_path),
        "sourceImmutableReference": source_ref,
        "promotedImmutableReference": target_ref,
        "digest": digest,
        "sourceDaemonImageId": source.get("Id"),
        "promotedDaemonImageId": promoted.get("Id"),
        "sizeBytes": promoted.get("Size"),
        "exactDigestPreserved": True,
        "sameDaemonImageId": True,
        "rebuilt": False,
        "mutableReferenceRecorded": False,
        "transportReferenceRemovedLocally": True,
        "promotionOccurredAfterCompositeSeal": True,
    }
    envelope = {
        "schema": "zkdeal/oci-promotion-envelope/v1",
        "payload": payload,
        "authentication": {
            "algorithm": "hmac-sha256",
            "keyId": key_id,
            "mac": "hmac-sha256:" + hmac.new(key, canonical(payload), hashlib.sha256).hexdigest(),
        },
    }
    if ":promotion-" in json.dumps(envelope):
        raise DeploymentError("mutable promotion transport leaked into the signed receipt")
    write_json_exclusive(Path(args.output), envelope)
    print(json.dumps(envelope, sort_keys=True))
    return 0


def load_promotion(path_value: str, key: bytes) -> tuple[Path, dict[str, object]]:
    path, _raw, envelope = regular_json(path_value, "promotion receipt")
    if set(envelope) != {"schema", "payload", "authentication"} or envelope.get("schema") != "zkdeal/oci-promotion-envelope/v1":
        raise DeploymentError("promotion receipt envelope is malformed")
    payload = envelope.get("payload")
    authentication = envelope.get("authentication")
    if not isinstance(payload, dict) or not isinstance(authentication, dict):
        raise DeploymentError("promotion receipt lacks payload/authentication")
    expected = "hmac-sha256:" + hmac.new(key, canonical(payload), hashlib.sha256).hexdigest()
    if (
        authentication.get("algorithm") != "hmac-sha256"
        or not hmac.compare_digest(str(authentication.get("mac", "")), expected)
    ):
        raise DeploymentError("promotion receipt MAC verification failed")
    return path, envelope


def verify_promotion(args: argparse.Namespace) -> int:
    key, key_id = promotion_key(args.key_file)
    receipt_path, envelope = load_promotion(args.receipt, key)
    authentication = envelope["authentication"]
    payload = envelope["payload"]
    if authentication.get("keyId") != key_id:
        raise DeploymentError("promotion receipt names another MAC key")
    publication_path = Path(args.manifest)
    if not publication_path.is_absolute():
        publication_path = ROOT / publication_path
    publication = load_publication(publication_path)
    _candidate, candidate_sha, _composite, composite_sha = promotion_context(args, publication)
    expected = {
        "candidateDescriptorSha256": candidate_sha,
        "sourceGeneratedCompositeSealSha256": composite_sha,
        "sourcePublicationManifestSha256": sha256_file(publication_path),
        "sourceImmutableReference": publication["immutableReference"],
        "digest": publication["digest"],
        "imageKey": args.image_key,
        "exactDigestPreserved": True,
        "sameDaemonImageId": True,
        "rebuilt": False,
        "mutableReferenceRecorded": False,
        "transportReferenceRemovedLocally": True,
        "promotionOccurredAfterCompositeSeal": True,
    }
    for name, value in expected.items():
        if payload.get(name) != value:
            raise DeploymentError(f"promotion receipt differs: {name}")
    source_ref = str(payload.get("sourceImmutableReference", ""))
    target_ref = str(payload.get("promotedImmutableReference", ""))
    docker("image", "pull", source_ref)
    docker("image", "pull", target_ref)
    source = image_inspect(source_ref)
    target = image_inspect(target_ref)
    if (
        source.get("Id") != payload.get("sourceDaemonImageId")
        or target.get("Id") != payload.get("promotedDaemonImageId")
        or source.get("Id") != target.get("Id")
        or source_ref not in source.get("RepoDigests", [])
        or target_ref not in target.get("RepoDigests", [])
    ):
        raise DeploymentError("promotion daemon identity verification failed")
    result = {
        "verified": True,
        "receiptSha256": sha256_file(receipt_path),
        "keyId": key_id,
        "sourceImmutableReference": source_ref,
        "promotedImmutableReference": target_ref,
        "daemonImageId": source.get("Id"),
        "exactDigestPreserved": True,
        "sameDaemonImageId": True,
    }
    if args.output:
        write_json_exclusive(Path(args.output), result)
    print(json.dumps(result, sort_keys=True))
    return 0


def archive_inventory(path: Path) -> tuple[list[dict[str, object]], int]:
    rows: list[dict[str, object]] = []
    total = 0
    with tarfile.open(path, "r:gz") as archive:
        for index, member in enumerate(archive):
            if index >= MAX_MEMBERS:
                raise DeploymentError("registry archive exceeds member bound")
            pure = PurePosixPath(member.name)
            if pure.is_absolute() or ".." in pure.parts or not pure.parts:
                raise DeploymentError("registry archive contains an unsafe path")
            if not (member.isfile() or member.isdir()):
                raise DeploymentError("registry archive contains a non-file member")
            row: dict[str, object] = {
                "path": pure.as_posix(), "type": "file" if member.isfile() else "directory",
                "mode": member.mode & 0o777, "bytes": member.size,
            }
            if member.isfile():
                total += member.size
                if total > MAX_TOTAL_BYTES:
                    raise DeploymentError("registry archive exceeds total-byte bound")
                stream = archive.extractfile(member)
                if stream is None:
                    raise DeploymentError("registry archive file body is missing")
                digest = hashlib.sha256()
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                row["sha256"] = digest.hexdigest()
            rows.append(row)
    return rows, total


def content_hash(rows: list[dict[str, object]]) -> str:
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def backup(args: argparse.Namespace) -> int:
    volume = safe_part(args.volume, "volume")
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    if docker("volume", "inspect", volume, check=False).returncode != 0:
        raise DeploymentError(f"registry volume does not exist: {volume}")
    users = docker("ps", "--filter", f"volume={volume}", "--quiet").stdout.strip()
    if users:
        raise DeploymentError("registry volume is still mounted by a running container; quiesce it first")
    temporary = output.with_name(output.name + ".partial")
    temporary.unlink(missing_ok=True)
    command = [
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "-v", f"{volume}:/registry:ro",
        "--entrypoint", "python", PYTHON_IMAGE, "-c", ARCHIVE_PROGRAM,
    ]
    with temporary.open("wb") as stream:
        completed = subprocess.run(command, stdout=stream, stderr=subprocess.PIPE, check=False)
    if completed.returncode != 0:
        temporary.unlink(missing_ok=True)
        raise DeploymentError(f"registry archive failed: {completed.stderr.decode(errors='replace')[-2000:]}")
    rows, total = archive_inventory(temporary)
    os.replace(temporary, output)
    references: list[dict[str, str]] = []
    for manifest_name in args.publication_manifest:
        manifest_path = Path(manifest_name)
        publication = load_publication(manifest_path)
        if not manifest_path.is_absolute():
            manifest_path = ROOT / manifest_path
        references.append({
            "immutableReference": str(publication["immutableReference"]),
            "digest": str(publication["digest"]),
            "publicationManifestSha256": sha256_file(manifest_path),
        })
    outer = {
        "schemaVersion": 1, "sourceVolume": volume,
        "archive": {"path": output.name, "bytes": output.stat().st_size, "sha256": sha256_file(output)},
        "content": {"members": len(rows), "totalFileBytes": total, "manifestSha256": content_hash(rows)},
        "members": rows, "immutableReferences": references,
        "archiveRunnerImage": PYTHON_IMAGE,
    }
    manifest_path = Path(args.manifest) if args.manifest else output.with_name(output.name + ".manifest.json")
    write_json(manifest_path, outer)
    print(json.dumps({"backup": str(output), "manifest": str(manifest_path), **outer["archive"], **outer["content"]}, sort_keys=True))
    return 0


def load_backup(archive_path: Path, manifest_path: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    if not archive_path.is_absolute():
        archive_path = ROOT / archive_path
    if not manifest_path.is_absolute():
        manifest_path = ROOT / manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = manifest.get("archive", {})
    if expected.get("sha256") != sha256_file(archive_path) or expected.get("bytes") != archive_path.stat().st_size:
        raise DeploymentError("registry backup archive hash/size mismatch")
    rows, total = archive_inventory(archive_path)
    content = manifest.get("content", {})
    if content.get("members") != len(rows) or content.get("totalFileBytes") != total:
        raise DeploymentError("registry backup member/byte counts differ")
    if content.get("manifestSha256") != content_hash(rows) or rows != manifest.get("members"):
        raise DeploymentError("registry backup content manifest differs")
    return manifest, rows


def verify_backup(args: argparse.Namespace) -> int:
    manifest, rows = load_backup(Path(args.archive), Path(args.manifest))
    print(json.dumps({
        "verified": True, "archiveSha256": manifest["archive"]["sha256"],
        "contentManifestSha256": manifest["content"]["manifestSha256"],
        "members": len(rows), "totalFileBytes": manifest["content"]["totalFileBytes"],
    }, sort_keys=True))
    return 0


def restore(args: argparse.Namespace) -> int:
    archive_path = Path(args.archive)
    if not archive_path.is_absolute():
        archive_path = ROOT / archive_path
    manifest, _rows = load_backup(archive_path, Path(args.manifest))
    volume = safe_part(args.volume, "restore volume")
    if docker("volume", "inspect", volume, check=False).returncode == 0:
        raise DeploymentError("restore target volume already exists")
    docker("volume", "create", volume)
    try:
        command = [
            "docker", "run", "--rm", "-i", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "-v", f"{volume}:/registry",
            "--entrypoint", "python", PYTHON_IMAGE, "-c", RESTORE_PROGRAM,
        ]
        with archive_path.open("rb") as stream:
            completed = subprocess.run(command, stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if completed.returncode != 0:
            raise DeploymentError(f"registry restore failed: {completed.stderr.decode(errors='replace')[-2000:]}")
    except Exception:
        docker("volume", "rm", volume, check=False)
        raise
    result = {
        "restored": True, "targetVolume": volume,
        "archiveSha256": manifest["archive"]["sha256"],
        "contentManifestSha256": manifest["content"]["manifestSha256"],
        "targetWasNew": True,
    }
    if args.output:
        write_json(Path(args.output), result)
    print(json.dumps(result, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    item = commands.add_parser("publish")
    item.add_argument("--source", required=True)
    item.add_argument("--candidate", required=True)
    item.add_argument("--artifact", required=True)
    item.add_argument("--registry", default="localhost:5000")
    item.add_argument("--namespace", default="zkdeal/candidates")
    item.add_argument("--output", required=True)
    item.set_defaults(handler=publish)
    item = commands.add_parser("verify-image")
    item.add_argument("--manifest", required=True)
    item.add_argument("--registry")
    item.set_defaults(handler=verify_image)
    item = commands.add_parser("promote")
    item.add_argument("--manifest", required=True)
    item.add_argument("--candidate-file", required=True)
    item.add_argument("--composite-seal", required=True)
    item.add_argument("--image-key", required=True)
    item.add_argument("--release", required=True)
    item.add_argument("--registry")
    item.add_argument("--namespace", default="zkdeal/releases")
    item.add_argument("--key-file", required=True)
    item.add_argument("--output", required=True)
    item.set_defaults(handler=promote)
    item = commands.add_parser("verify-promotion")
    item.add_argument("--receipt", required=True)
    item.add_argument("--manifest", required=True)
    item.add_argument("--candidate-file", required=True)
    item.add_argument("--composite-seal", required=True)
    item.add_argument("--image-key", required=True)
    item.add_argument("--key-file", required=True)
    item.add_argument("--output")
    item.set_defaults(handler=verify_promotion)
    item = commands.add_parser("backup")
    item.add_argument("--volume", required=True)
    item.add_argument("--output", required=True)
    item.add_argument("--manifest")
    item.add_argument("--publication-manifest", action="append", default=[])
    item.set_defaults(handler=backup)
    item = commands.add_parser("verify-backup")
    item.add_argument("--archive", required=True)
    item.add_argument("--manifest", required=True)
    item.set_defaults(handler=verify_backup)
    item = commands.add_parser("restore")
    item.add_argument("--archive", required=True)
    item.add_argument("--manifest", required=True)
    item.add_argument("--volume", required=True)
    item.add_argument("--output")
    item.set_defaults(handler=restore)
    return root


def main() -> int:
    try:
        require_container()
        args = parser().parse_args()
        return args.handler(args)
    except (DeploymentError, OSError, ValueError, json.JSONDecodeError, tarfile.TarError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
