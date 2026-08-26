#!/usr/bin/env python3
"""Fail-closed production Compose preflight and launcher.

This is the only documented production Compose entrypoint.  It renders the
canonical overlays, requires exact digest references before Docker is allowed
to pull or start anything, and rejects any build fallback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Mapping

from common import DeploymentError, ROOT, require_container, sha256_file
from verify_owner_capabilities import check as owner_capability_errors


REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$")
RESERVED_REGISTRIES = {"registry.invalid", "registry.example"}
PRODUCTION_FILES = (
    ROOT / "compose/compose.yaml",
    ROOT / "compose/compose.dependencies.yaml",
    ROOT / "compose/compose.hosted.yaml",
    ROOT / "compose/compose.failover-provider.yaml",
    ROOT / "compose/compose.production.yaml",
)
REQUIRED_ENV = {
    "COORDINATOR_IMAGE_DIGEST": (
        "coordinator", "coordinator-standby", "indexer", "reconciler",
        "publisher", "auto-claimer", "capacity-controller",
    ),
    "HEADLESS_NODE_IMAGE_DIGEST": ("headless-node-secret-init", "headless-node"),
    "DOCS_IMAGE_DIGEST": ("docs",),
    "PROVER_IMAGE_DIGEST": ("prover",),
    "AGENT_IMAGE_DIGEST": ("agent",),
    "POSTGRES_IMAGE_DIGEST": ("postgres", "postgres-standby", "postgres-ha-ready"),
    "MINIO_IMAGE_DIGEST": ("minio",),
    "MINIO_CLIENT_IMAGE_DIGEST": ("minio-init",),
    "PROMOTION_CONTROLLER_IMAGE_DIGEST": ("promotion-controller",),
    "FAILOVER_PROVIDER_IMAGE_DIGEST": ("failover-provider-docker",),
}


def load_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise DeploymentError(f"production env file does not exist: {path}")
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise DeploymentError(f"invalid env assignment at {path}:{number}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key):
            raise DeploymentError(f"invalid env name at {path}:{number}: {key!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def validate_reference(reference: object, label: str) -> str:
    if not isinstance(reference, str) or not REFERENCE.fullmatch(reference):
        raise DeploymentError(
            f"{label} must be exact lowercase repository@sha256:<64 hex>; got {reference!r}"
        )
    repository, digest = reference.rsplit("@", 1)
    # A tag plus digest is immutable, but is deliberately rejected so release
    # evidence has one canonical reference form. Registry ports remain valid.
    if ":" in repository.rsplit("/", 1)[-1]:
        raise DeploymentError(f"{label} must not contain a mutable tag before @{digest}")
    registry = repository.split("/", 1)[0]
    if registry in RESERVED_REGISTRIES or registry.endswith((".invalid", ".example")):
        raise DeploymentError(f"{label} uses a reserved placeholder registry: {registry}")
    return reference


def effective_environment(env_file: Path, process: Mapping[str, str]) -> dict[str, str]:
    values = load_env_file(env_file)
    for name in REQUIRED_ENV:
        if name in process:
            values[name] = process[name]
    return values


def validate_required_environment(values: Mapping[str, str]) -> dict[str, str]:
    checked: dict[str, str] = {}
    for name in REQUIRED_ENV:
        checked[name] = validate_reference(values.get(name), name)
    if checked["AGENT_IMAGE_DIGEST"] == checked["COORDINATOR_IMAGE_DIGEST"]:
        raise DeploymentError(
            "AGENT_IMAGE_DIGEST must be the packaged prover-agent artifact, not the coordinator image"
        )
    return checked


def compose_prefix(env_file: Path, signer: bool, profiles: list[str]) -> list[str]:
    command = ["docker", "compose", "--env-file", str(env_file)]
    for path in PRODUCTION_FILES:
        command.extend(("-f", str(path)))
    if signer:
        command.extend(("-f", str(ROOT / "compose/compose.signer-production.example.yaml")))
    for profile in profiles:
        command.extend(("--profile", profile))
    return command


def run_checked(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-4000:]
        raise DeploymentError(f"command failed ({completed.returncode}): {detail}")
    return completed


def render_compose(prefix: list[str]) -> dict[str, object]:
    completed = run_checked([*prefix, "config", "--format", "json"], timeout=120)
    try:
        rendered = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DeploymentError(f"Compose did not emit JSON: {exc}") from exc
    if not isinstance(rendered, dict) or not isinstance(rendered.get("services"), dict):
        raise DeploymentError("Compose render has no services mapping")
    return rendered


def validate_rendered(rendered: Mapping[str, object], required: Mapping[str, str]) -> dict[str, str]:
    raw_services = rendered.get("services")
    if not isinstance(raw_services, dict):
        raise DeploymentError("Compose render has no services mapping")
    images: dict[str, str] = {}
    for service, raw in sorted(raw_services.items()):
        if not isinstance(raw, dict):
            raise DeploymentError(f"service {service} is not an object")
        if raw.get("build") is not None:
            raise DeploymentError(f"production service {service} retains a build fallback")
        if "image" not in raw:
            raise DeploymentError(f"production service {service} has no immutable image")
        images[str(service)] = validate_reference(raw.get("image"), f"service {service} image")

    for variable, services in REQUIRED_ENV.items():
        expected = required[variable]
        for service in services:
            if service in images and images[service] != expected:
                raise DeploymentError(
                    f"service {service} image {images[service]!r} does not equal {variable}={expected!r}"
                )

    # The standalone file-backed queue and the legacy prover-agent signer are
    # valid only for the explicit local development loop.  A hosted release
    # must consume the owner's durable PostgreSQL queue and owner-mediated L1
    # heartbeat operation.  Keep these structural checks ahead of capability
    # validation so a future green owner manifest cannot accidentally promote
    # an obsolete Compose surface.
    queue = raw_services.get("queue")
    if isinstance(queue, dict):
        if queue.get("image") != required["COORDINATOR_IMAGE_DIGEST"]:
            raise DeploymentError(
                "production-disabled queue must reuse the immutable coordinator image"
            )
        queue_command = queue.get("command")
        rendered_command = " ".join(str(item) for item in queue_command) if isinstance(
            queue_command, list
        ) else str(queue_command or "")
        queue_profiles = queue.get("profiles")
        if "prove-queue/standalone" in rendered_command or queue_profiles != ["never-production"]:
            raise DeploymentError(
                "production render retains an active standalone filesystem proof queue"
            )
    agent = raw_services.get("agent")
    if isinstance(agent, dict):
        agent_environment = agent.get("environment")
        if isinstance(agent_environment, dict):
            coordinator_url = "http://coordinator:3000"
            if agent_environment.get("QUEUE_URL") != coordinator_url:
                raise DeploymentError(
                    "production prover agent must lease from the hosted coordinator queue API"
                )
            if agent_environment.get("NODE_LIVENESS_COORDINATOR_URL") != coordinator_url:
                raise DeploymentError(
                    "production prover agent must use the hosted coordinator liveness-operation API"
                )
            required_agent_fields = {
                "ZKDEAL_QUEUE_NODE_TOKEN", "NODE_ID", "PROVER_URL",
                "ZKDEAL_PROVER_TOKEN", "ROOM_POOL",
                "NODE_LIVENESS_COORDINATOR_AUTH_TOKEN", "NODE_LIVENESS_ACCOUNT",
                "L1_CHAIN_ID",
            }
            missing_agent_fields = sorted(required_agent_fields - set(agent_environment))
            if missing_agent_fields:
                raise DeploymentError(
                    "production prover agent lacks owner durable queue/liveness fields: "
                    + ",".join(missing_agent_fields)
                )
            forbidden_signer_fields = {
                "NODE_LIVENESS_SIGNER_URL", "NODE_LIVENESS_SIGNER_AUTH_TOKEN",
                "NODE_LIVENESS_DEV_MODE", "NODE_LIVENESS_DEV_PRIVATE_KEY",
                "NODE_SERVICE_KEY", "L1_RPC_URL",
            }
            present = sorted(forbidden_signer_fields & set(agent_environment))
            if present:
                raise DeploymentError(
                    "production prover agent retains direct liveness signer authority: "
                    + ",".join(present)
                )
        if agent.get("command") != ["node", "/app/agent/agent.js"]:
            raise DeploymentError("production prover agent command differs from the packaged owner entrypoint")
        if agent.get("working_dir") != "/app":
            raise DeploymentError("production prover agent working directory differs from the owner image")
    controller = raw_services.get("promotion-controller")
    if not isinstance(controller, dict):
        raise DeploymentError("production render omits the automated promotion controller")
    environment = controller.get("environment")
    if not isinstance(environment, dict):
        raise DeploymentError("promotion controller has no environment contract")
    if environment.get("PROMOTION_CONTROLLER_ARMED") != "true":
        raise DeploymentError("promotion controller requires an explicit incident approval arm")
    witnesses = [
        item.strip() for item in str(environment.get("ACTIVE_HEALTH_URLS", "")).split(",")
        if item.strip()
    ]
    if len(witnesses) < 2 or len(set(witnesses)) != len(witnesses) or not all(
        item.startswith("https://") for item in witnesses
    ):
        raise DeploymentError("promotion controller requires independent HTTPS health witnesses")
    if not str(environment.get("FAILOVER_PROVIDER_URL", "")).startswith("https://"):
        raise DeploymentError("promotion controller requires an HTTPS failover provider")
    if not re.fullmatch(
        r"[a-z0-9][a-z0-9._:-]{7,199}", str(environment.get("PROMOTION_CANDIDATE_ID", "")),
    ):
        raise DeploymentError("promotion controller requires a unique lowercase incident candidate ID")
    direct_secrets = {
        "FAILOVER_PROVIDER_TOKEN", "FAILOVER_APPROVAL_TOKEN", "PROMOTION_PRINCIPAL_TOKEN",
    }
    if direct_secrets & set(environment):
        raise DeploymentError("promotion controller embeds a credential instead of using file secrets")
    for name in (
        "FAILOVER_PROVIDER_TOKEN_FILE", "FAILOVER_APPROVAL_TOKEN_FILE",
        "PROMOTION_PRINCIPAL_TOKEN_FILE",
    ):
        if not str(environment.get(name, "")).startswith("/run/secrets/"):
            raise DeploymentError(f"promotion controller lacks scoped file secret {name}")

    provider = raw_services.get("failover-provider-docker")
    if not isinstance(provider, dict):
        raise DeploymentError("production render omits the first-party Docker failover provider")
    provider_environment = provider.get("environment")
    if not isinstance(provider_environment, dict) or provider_environment.get("FAILOVER_PLATFORM") != "docker":
        raise DeploymentError("failover provider does not select the fixed Docker adapter")
    provider_witnesses = [
        item.strip() for item in str(provider_environment.get("ACTIVE_HEALTH_URLS", "")).split(",")
        if item.strip()
    ]
    if provider_witnesses != witnesses:
        raise DeploymentError("controller and provider must use the same ordered independent witnesses")
    if environment.get("FAILOVER_PROVIDER_URL") != "https://failover-provider-docker:8443/v1/failovers":
        raise DeploymentError("production controller must use the first-party internal failover provider")
    for name in (
        "FAILOVER_PROVIDER_TOKEN_FILE", "FAILOVER_APPROVAL_TOKEN_FILE",
        "FAILOVER_PROVIDER_TLS_CERT_FILE", "FAILOVER_PROVIDER_TLS_KEY_FILE",
    ):
        if not str(provider_environment.get(name, "")).startswith("/run/secrets/"):
            raise DeploymentError(f"failover provider lacks scoped file secret {name}")
    if set(environment) & {"DOCKER_HOST", "KUBECONFIG", "K8S_FAILOVER_NAMESPACE"}:
        raise DeploymentError("promotion controller received platform authority")
    provider_volumes = provider.get("volumes") or []
    if not any(
        isinstance(item, dict)
        and item.get("target") == "/var/run/docker.sock"
        for item in provider_volumes
    ):
        raise DeploymentError("Docker failover provider lacks its isolated platform socket")
    controller_volumes = controller.get("volumes") or []
    if any(
        isinstance(item, dict) and item.get("target") == "/var/run/docker.sock"
        for item in controller_volumes
    ):
        raise DeploymentError("promotion controller must not receive the Docker socket")
    signer_authority = raw_services.get("standby-signer-authority")
    if not isinstance(signer_authority, dict) or signer_authority.get("restart") not in {"no", "\"no\""}:
        raise DeploymentError("standby signer authority must remain a one-shot provider-controlled boundary")
    return images


def validate_owner_capability_wiring() -> None:
    errors = owner_capability_errors()
    if errors:
        raise DeploymentError(
            "owner capability preflight failed closed: " + "; ".join(errors)
        )


def expected_agent_source_hash() -> str:
    source = ROOT.parent / "prover-node/agent/src"
    if not source.is_dir():
        raise DeploymentError("owner prover-agent source directory is absent")
    rows = bytearray()
    for path in sorted(item for item in source.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise DeploymentError(f"owner prover-agent source contains a symlink: {path}")
        relative = path.relative_to(source).as_posix()
        rows.extend(f"{sha256_file(path)}  agent/src/{relative}\n".encode())
    return hashlib.sha256(rows).hexdigest()


def validate_agent_image_labels(payload: Mapping[str, object]) -> dict[str, str]:
    labels = payload.get("Config")
    labels = labels.get("Labels") if isinstance(labels, dict) else None
    if not isinstance(labels, dict):
        raise DeploymentError("packaged prover-agent image has no content labels")
    expected_source = expected_agent_source_hash()
    capability_path = ROOT.parent / "prover-node/agent/liveness-capability.json"
    if not capability_path.is_file():
        raise DeploymentError("owner prover-agent liveness capability is absent")
    expected_capability = sha256_file(capability_path)
    trace_capability_path = ROOT.parent / "prover-node/agent/trace-capability.json"
    if not trace_capability_path.is_file():
        raise DeploymentError("owner prover-agent trace capability is absent")
    expected_trace_capability = sha256_file(trace_capability_path)
    observed_source = labels.get("org.zkdeal.owner.source.sha256")
    observed_capability = labels.get("org.zkdeal.owner.liveness-capability.sha256")
    observed_trace_capability = labels.get("org.zkdeal.owner.trace-capability.sha256")
    if observed_source != expected_source:
        raise DeploymentError("packaged prover-agent image source label differs from current sealed owner bytes")
    if observed_capability != expected_capability:
        raise DeploymentError("packaged prover-agent image capability label differs from current sealed owner bytes")
    if observed_trace_capability != expected_trace_capability:
        raise DeploymentError("packaged prover-agent image trace capability label differs from current sealed owner bytes")
    return {
        "sourceSha256": expected_source,
        "livenessCapabilitySha256": expected_capability,
        "traceCapabilitySha256": expected_trace_capability,
    }


def verify_local_images(images: Mapping[str, str]) -> dict[str, str]:
    identities: dict[str, str] = {}
    for reference in sorted(set(images.values())):
        run_checked(["docker", "pull", reference], timeout=1800)
        inspected = run_checked(
            ["docker", "image", "inspect", reference, "--format", "{{json .}}"], timeout=30,
        )
        payload = json.loads(inspected.stdout)
        if not isinstance(payload, dict) or not isinstance(payload.get("Id"), str):
            raise DeploymentError(f"Docker did not return an image ID for {reference}")
        expected_digest = reference.rsplit("@", 1)[1]
        repo_digests = payload.get("RepoDigests") or []
        if not any(str(item).endswith(f"@{expected_digest}") for item in repo_digests):
            raise DeploymentError(f"daemon did not verify the requested digest for {reference}")
        if reference == images.get("agent"):
            validate_agent_image_labels(payload)
        identities[reference] = payload["Id"]
    return identities


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check", "pull", "up"))
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--with-signer", action="store_true")
    args = parser.parse_args()
    try:
        require_container()
        env_file = Path(args.env_file)
        if not env_file.is_absolute():
            env_file = (ROOT / env_file).resolve()
        profiles = list(dict.fromkeys(["hosted", "promotion-controller", "promotion-provider", *args.profile]))
        if args.with_signer and "signer-production" not in profiles:
            profiles.append("signer-production")
        required = validate_required_environment(effective_environment(env_file, os.environ))
        prefix = compose_prefix(env_file, args.with_signer, profiles)
        rendered = render_compose(prefix)
        images = validate_rendered(rendered, required)
        # Digest correctness cannot make an unpublished or unaccepted owner
        # service deployable. Keep this after render validation so both the
        # immutable-image graph and owner capability graph are checked before
        # any pull or start action is permitted.
        validate_owner_capability_wiring()
        identities: dict[str, str] = {}
        if args.action in {"pull", "up"}:
            identities = verify_local_images(images)
        if args.action == "up":
            if not args.with_signer:
                raise DeploymentError("first-party failover provider startup requires --with-signer role endpoints")
            # Materialize the post-fence signer proxy without starting it. The
            # provider is the only component allowed to start this container.
            run_checked(
                [*prefix, "create", "--no-build", "standby-signer-authority"], timeout=120,
            )
            targets = sorted(
                name for name in images
                if name not in {"standby-signer-authority", "queue"}
            )
            run_checked(
                [*prefix, "up", "-d", "--wait", "--no-build", *targets], timeout=1800,
            )
        print(json.dumps({
            "productionCompose": "passed",
            "action": args.action,
            "profiles": profiles,
            "services": len(images),
            "images": images,
            "daemonImageIds": identities,
        }, sort_keys=True, separators=(",", ":")))
        return 0
    except (DeploymentError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
