"""Shared, dependency-light helpers for the deployment project."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "config" / "schema" / "deployment.schema.json"
ARTIFACTS_PATH = ROOT / "config" / "artifacts.json"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ZERO_ADDRESS = "0x" + "0" * 40
KNOWN_DEV_PRIVATE_KEYS = {
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
}
PRODUCTION_SIGNER_BOUNDARIES = {
    "liveness": {"coordinator-active"},
    "operationsSettlement": {"coordinator-active"},
    "payout": {"provider-payout"},
    "finalityOracle": {"finality-oracle"},
    "sponsorRelayer": {"sponsorship"},
    "withdrawalRelayer": {"auto-claimer"},
}


class DeploymentError(RuntimeError):
    """A user-actionable deployment validation error."""


def require_container() -> None:
    """Fail closed when an executable deployment command runs on the host."""
    docker_marker = Path("/.dockerenv").exists()
    podman_marker = Path("/run/.containerenv").exists()
    if not (docker_marker or podman_marker):
        raise DeploymentError(
            "this command is container-only; use the documented deployment-tools container"
        )


@dataclass(frozen=True)
class CheckResult:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DeploymentError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DeploymentError(f"invalid JSON in {path}: {exc}") from exc


def project_path(value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def require_project_write_path(path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise DeploymentError(
            f"refusing to write outside deployment project: {resolved}"
        ) from exc
    return resolved


def load_profile(path: str | Path) -> tuple[Path, dict[str, Any]]:
    profile_path = project_path(path)
    value = load_json(profile_path)
    if not isinstance(value, dict):
        raise DeploymentError(f"profile must be a JSON object: {profile_path}")
    return profile_path, value


def atomic_write(path: Path, data: bytes) -> None:
    path = require_project_write_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(path: Path) -> tuple[str, int, int]:
    """Hash relative names, sizes and file content without following symlinks."""
    digest = hashlib.sha256()
    count = 0
    total = 0
    for child in sorted((item for item in path.rglob("*") if item.is_file())):
        if child.is_symlink():
            raise DeploymentError(f"refusing symlink in owner artifact tree: {child}")
        relative = child.relative_to(path).as_posix().encode()
        size = child.stat().st_size
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(size.to_bytes(8, "big"))
        digest.update(bytes.fromhex(sha256_file(child)))
        count += 1
        total += size
    return digest.hexdigest(), count, total


def resolve_artifacts() -> tuple[Path, list[dict[str, Any]]]:
    spec = load_json(ARTIFACTS_PATH)
    umbrella = (ARTIFACTS_PATH.parent / spec["umbrellaRoot"]).resolve()
    return umbrella, list(spec["artifacts"])


def owner_artifact_checks() -> CheckResult:
    umbrella, artifacts = resolve_artifacts()
    errors: list[str] = []
    warnings: list[str] = []
    for artifact in artifacts:
        target = (umbrella / artifact["path"]).resolve()
        if target.exists():
            continue
        message = f"owner artifact {artifact['id']} is missing: {target}"
        (errors if artifact.get("required", False) else warnings).append(message)
    return CheckResult(tuple(errors), tuple(warnings))


def _schema_errors(profile: dict[str, Any]) -> list[str]:
    try:
        import jsonschema  # type: ignore
    except ImportError:
        required = {
            "schemaVersion", "name", "environment", "publicExposure", "chain",
            "services", "storage", "availability", "security", "observability", "images",
        }
        return [f"missing required property: {key}" for key in sorted(required - profile.keys())]
    schema = load_json(SCHEMA_PATH)
    validator = jsonschema.Draft202012Validator(schema)
    errors = []
    for error in sorted(validator.iter_errors(profile), key=lambda item: list(item.path)):
        where = ".".join(str(part) for part in error.path) or "profile"
        errors.append(f"{where}: {error.message}")
    return errors


def _image_policy(profile: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = project_path(profile["images"]["manifest"])
    try:
        manifest = load_json(manifest_path)
    except DeploymentError as exc:
        return [str(exc)], warnings
    for name, image in manifest.get("images", {}).items():
        digest = image.get("digest")
        required = image.get("requiredInProduction", False)
        if digest is not None and not DIGEST_RE.fullmatch(str(digest)):
            errors.append(f"image {name} has malformed digest {digest!r}")
        if required and not digest:
            message = f"image {name} has no verified digest"
            if profile["environment"] == "production":
                errors.append(message)
            else:
                warnings.append(message)
    return errors, warnings


def validate_profile(profile: dict[str, Any], check_artifacts: bool = False) -> CheckResult:
    errors = _schema_errors(profile)
    warnings: list[str] = []
    if errors:
        return CheckResult(tuple(errors), tuple(warnings))

    environment = profile["environment"]
    public = profile["publicExposure"]
    security = profile["security"]
    availability = profile["availability"]
    storage = profile["storage"]
    services = profile["services"]
    availability_mode = availability["mode"]

    if environment != "local":
        def walk_strings(value: Any) -> Iterable[str]:
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for child in value.values():
                    yield from walk_strings(child)
            elif isinstance(value, list):
                for child in value:
                    yield from walk_strings(child)

        if any(value.lower() in KNOWN_DEV_PRIVATE_KEYS for value in walk_strings(profile)):
            errors.append("non-local profiles must not contain a known development private key")

    if public and security["tlsMode"] == "disabled":
        errors.append("public exposure requires TLS termination")
    if public and not security["trustedProxyCidrs"]:
        errors.append("public exposure requires explicit trusted proxy CIDRs")
    if "*" in security["corsOrigins"]:
        errors.append("wildcard CORS is forbidden")

    authoritative = ("coordinator",) + (("queue",) if services["queue"]["enabled"] else ())
    if storage["backend"] == "external-transactional" and services["queue"]["enabled"]:
        errors.append(
            "external transactional profiles must disable the standalone queue and use the hosted coordinator queue"
        )
    if storage["backend"] == "filesystem":
        for name in authoritative:
            if services[name]["replicas"] > 1:
                errors.append(f"filesystem-authoritative {name} must have at most one replica")
        if availability["fencing"] != "none":
            errors.append("filesystem authority cannot claim transactional fencing")
    else:
        for name in authoritative:
            if services[name]["replicas"] > 1 and availability["fencing"] != "owner-service-transactional":
                errors.append(f"multi-replica {name} requires owner-service transactional fencing")

    if availability_mode == "single-instance":
        if services["coordinator"]["replicas"] != 1 or availability.get("warmStandbyReplicas", 0) != 0:
            errors.append("single-instance mode requires one coordinator and no warm standby")
    elif availability_mode == "single-region":
        if services["coordinator"]["replicas"] != 1 or availability.get("warmStandbyReplicas", 0) != 0:
            errors.append("single-region mode requires one coordinator and no warm standby")
    elif availability_mode == "warm-standby":
        if storage["backend"] != "external-transactional":
            errors.append("warm-standby mode requires external transactional storage")
        if availability["fencing"] != "owner-service-transactional":
            errors.append("warm-standby mode requires owner-service transactional fencing")
        if services["coordinator"]["replicas"] < 2 or availability.get("warmStandbyReplicas", 0) < 1:
            errors.append("warm-standby mode requires an active and at least one standby")
        if availability["rtoMinutes"] > 5:
            errors.append("warm-standby recovery objective must be five minutes or less")

    if environment == "production":
        if availability_mode != "warm-standby":
            errors.append("production requires warm-standby availability mode")
        if storage["backend"] != "external-transactional":
            errors.append("production requires an owner-supported external transactional state backend")
        if security["secretSource"] == "dotenv-local":
            errors.append("production secrets may not come from dotenv")
        if not security.get("existingSecret"):
            errors.append("production requires a named external/existing secret")
        if profile["images"]["allowTagOverrides"]:
            errors.append("production forbids tag image overrides")
        if len(profile["chain"]["rpcUrls"]) < 2:
            errors.append("production requires two independent L1 RPC URLs")
        if any("invalid.example" in url for url in profile["chain"]["rpcUrls"]):
            errors.append("production profile still contains placeholder RPC URLs")
        if any(value.lower() == ZERO_ADDRESS for value in profile["chain"]["contracts"].values()):
            errors.append("production profile still contains zero contract addresses")
        if not profile["observability"]["enabled"] or not profile["observability"]["alertsEnabled"]:
            errors.append("production requires metrics and alert rules")
        if availability.get("warmStandbyReplicas", 0) < 1 or services["coordinator"]["replicas"] < 2:
            errors.append("production requires at least one warm standby coordinator")
        if storage.get("hotRetentionDays") != 30 or storage.get("archiveRetentionDays", 0) < 365:
            errors.append("production retention policy must provide a 30-day hot and 365-day archive tier")
        if availability.get("multiRegion", False):
            errors.append("multi-region is not accepted until the owner capability manifest advertises it")
        signer_roles = security.get("signerRoles")
        if not isinstance(signer_roles, dict):
            errors.append("production requires isolated signer role boundaries")
        else:
            addresses: list[str] = []
            for role_name, required_services in PRODUCTION_SIGNER_BOUNDARIES.items():
                role = signer_roles.get(role_name)
                if not isinstance(role, dict) or not role.get("enabled"):
                    errors.append(f"production signer role {role_name} must be enabled")
                    continue
                address = role.get("address")
                if not isinstance(address, str) or not address.startswith("https://"):
                    errors.append(f"production signer role {role_name} requires an HTTPS address")
                else:
                    addresses.append(address)
                allowed = set(role.get("allowedServices", []))
                if allowed != required_services:
                    errors.append(
                        f"production signer role {role_name} must be isolated to "
                        f"{sorted(required_services)}"
                    )
            if len(addresses) != len(set(addresses)):
                errors.append("production signer roles require distinct endpoints")

    image_errors, image_warnings = _image_policy(profile)
    errors.extend(image_errors)
    warnings.extend(image_warnings)
    if check_artifacts:
        artifact_result = owner_artifact_checks()
        errors.extend(artifact_result.errors)
        warnings.extend(artifact_result.warnings)
    return CheckResult(tuple(errors), tuple(warnings))


def profile_state_root(profile: dict[str, Any]) -> Path:
    return project_path(profile["storage"]["dataRoot"])


def run_command(command: Sequence[str], cwd: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    if not command:
        raise DeploymentError("empty command")
    if any(Path(token).name.lower() in {"git", "git.exe"} for token in command):
        raise DeploymentError("repository-history commands are forbidden")
    return subprocess.run(
        list(command),
        cwd=cwd or ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def redact_env(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in items:
        upper = key.upper()
        redacted[key] = "<redacted>" if any(word in upper for word in ("TOKEN", "KEY", "PASSWORD", "SECRET")) else value
    return redacted
