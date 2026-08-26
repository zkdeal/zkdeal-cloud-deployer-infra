from __future__ import annotations

import unittest
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import DeploymentError  # noqa: E402
from production_compose import (  # noqa: E402
    PRODUCTION_FILES,
    REQUIRED_ENV,
    validate_reference,
    validate_agent_image_labels,
    validate_rendered,
    validate_required_environment,
    validate_owner_capability_wiring,
)


DIGEST = "sha256:" + "1" * 64


class ProductionComposeTests(unittest.TestCase):
    def valid(self, name: str) -> str:
        return f"registry.company.tld/zkdeal/{name}@{DIGEST}"

    def required(self) -> dict[str, str]:
        return {name: self.valid(name.lower()) for name in REQUIRED_ENV}

    def rendered(self, required: dict[str, str]) -> dict[str, object]:
        services: dict[str, object] = {}
        for variable, names in REQUIRED_ENV.items():
            for name in names:
                services[name] = {"image": required[variable]}
        services["queue"] = {"image": required["COORDINATOR_IMAGE_DIGEST"]}
        services["queue"]["profiles"] = ["never-production"]
        services["agent"].update({
            "command": ["node", "/app/agent/agent.js"],
            "working_dir": "/app",
            "environment": {
                "QUEUE_URL": "http://coordinator:3000",
                "ZKDEAL_QUEUE_NODE_TOKEN": "opaque",
                "NODE_ID": "node-a",
                "PROVER_URL": "http://prover:8080",
                "ZKDEAL_PROVER_TOKEN": "opaque",
                "ROOM_POOL": "0x" + "1" * 40,
                "NODE_LIVENESS_COORDINATOR_URL": "http://coordinator:3000",
                "NODE_LIVENESS_COORDINATOR_AUTH_TOKEN": "opaque",
                "NODE_LIVENESS_ACCOUNT": "0x" + "2" * 40,
                "L1_CHAIN_ID": "1",
            },
        })
        services["front-door"] = {"image": self.valid("nginx")}
        services["standby-signer-authority"] = {
            "image": self.valid("nginx"), "restart": "no",
        }
        witnesses = "https://witness-a.example/health,https://witness-b.example/health"
        services["promotion-controller"]["environment"] = {
            "PROMOTION_CONTROLLER_ARMED": "true",
            "ACTIVE_HEALTH_URLS": witnesses,
            "FAILOVER_PROVIDER_URL": "https://failover-provider-docker:8443/v1/failovers",
            "PROMOTION_CANDIDATE_ID": "candidate-render-unit-001",
            "FAILOVER_PROVIDER_TOKEN_FILE": "/run/secrets/provider",
            "FAILOVER_APPROVAL_TOKEN_FILE": "/run/secrets/approval",
            "PROMOTION_PRINCIPAL_TOKEN_FILE": "/run/secrets/principal",
        }
        services["promotion-controller"]["volumes"] = []
        services["failover-provider-docker"]["environment"] = {
            "FAILOVER_PLATFORM": "docker",
            "ACTIVE_HEALTH_URLS": witnesses,
            "FAILOVER_PROVIDER_TOKEN_FILE": "/run/secrets/provider",
            "FAILOVER_APPROVAL_TOKEN_FILE": "/run/secrets/approval",
            "FAILOVER_PROVIDER_TLS_CERT_FILE": "/run/secrets/tls.crt",
            "FAILOVER_PROVIDER_TLS_KEY_FILE": "/run/secrets/tls.key",
        }
        services["failover-provider-docker"]["volumes"] = [
            {"type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock"},
        ]
        return {"services": services}

    def test_exact_digest_reference_accepts_canonical_form(self):
        reference = self.valid("coordinator")
        self.assertEqual(validate_reference(reference, "test"), reference)

    def test_mutable_placeholder_and_malformed_references_are_rejected(self):
        cases = (
            "registry.company.tld/zkdeal/coordinator:latest",
            f"registry.company.tld/zkdeal/coordinator:latest@{DIGEST}",
            "registry.company.tld/zkdeal/coordinator@sha256:REPLACE",
            "registry.company.tld/zkdeal/coordinator@sha256:" + "A" * 64,
            "registry.company.tld/zkdeal/coordinator@sha256:" + "1" * 63,
            f"registry.invalid/zkdeal/coordinator@{DIGEST}",
            f"registry.example/zkdeal/coordinator@{DIGEST}",
        )
        for reference in cases:
            with self.subTest(reference=reference), self.assertRaises(DeploymentError):
                validate_reference(reference, "test")

    def test_every_required_owner_and_dependency_variable_is_validated(self):
        required = self.required()
        self.assertEqual(validate_required_environment(required), required)
        for missing in required:
            values = dict(required)
            values.pop(missing)
            with self.subTest(missing=missing), self.assertRaises(DeploymentError):
                validate_required_environment(values)

        reused = dict(required)
        reused["AGENT_IMAGE_DIGEST"] = reused["COORDINATOR_IMAGE_DIGEST"]
        with self.assertRaisesRegex(DeploymentError, "packaged prover-agent artifact"):
            validate_required_environment(reused)

    def test_render_cross_checks_service_images_and_rejects_build_fallback(self):
        required = self.required()
        rendered = self.rendered(required)
        rendered["services"]["promotion-controller"]["environment"] = {
            "PROMOTION_CONTROLLER_ARMED": "true",
            "ACTIVE_HEALTH_URLS": "https://witness-a.example/health,https://witness-b.example/health",
            "FAILOVER_PROVIDER_URL": "https://failover-provider-docker:8443/v1/failovers",
            "PROMOTION_CANDIDATE_ID": "candidate-render-unit-001",
            "FAILOVER_PROVIDER_TOKEN_FILE": "/run/secrets/provider",
            "FAILOVER_APPROVAL_TOKEN_FILE": "/run/secrets/approval",
            "PROMOTION_PRINCIPAL_TOKEN_FILE": "/run/secrets/principal",
        }
        images = validate_rendered(rendered, required)
        self.assertIn("coordinator", images)

        mismatch = self.rendered(required)
        mismatch["services"]["coordinator"]["image"] = self.valid("wrong")
        with self.assertRaises(DeploymentError):
            validate_rendered(mismatch, required)

        build = self.rendered(required)
        build["services"]["docs"]["build"] = {"context": "."}
        with self.assertRaises(DeploymentError):
            validate_rendered(build, required)

    def test_render_rejects_standalone_queue_and_direct_agent_signer(self):
        required = self.required()
        queue = self.rendered(required)
        queue["services"]["queue"]["command"] = [
            "pnpm", "exec", "tsx", "src/prove-queue/standalone.ts",
        ]
        with self.assertRaisesRegex(DeploymentError, "standalone filesystem proof queue"):
            validate_rendered(queue, required)

        agent_queue = self.rendered(required)
        agent_queue["services"]["agent"]["environment"]["QUEUE_URL"] = "http://queue:3005"
        with self.assertRaisesRegex(DeploymentError, "hosted coordinator queue API"):
            validate_rendered(agent_queue, required)

        direct_signer = self.rendered(required)
        direct_signer["services"]["agent"]["environment"]["NODE_LIVENESS_SIGNER_URL"] = "https://signer.example"
        with self.assertRaisesRegex(DeploymentError, "direct liveness signer authority"):
            validate_rendered(direct_signer, required)

        independent_queue_image = self.rendered(required)
        independent_queue_image["services"]["queue"]["image"] = self.valid("standalone-queue")
        with self.assertRaisesRegex(DeploymentError, "reuse the immutable coordinator image"):
            validate_rendered(independent_queue_image, required)

    def test_render_rejects_unarmed_or_secret_bearing_promotion_controller(self):
        required = self.required()
        rendered = self.rendered(required)
        base = {
            "PROMOTION_CONTROLLER_ARMED": "true",
            "ACTIVE_HEALTH_URLS": "https://witness-a.example/health,https://witness-b.example/health",
            "FAILOVER_PROVIDER_URL": "https://failover-provider-docker:8443/v1/failovers",
            "PROMOTION_CANDIDATE_ID": "candidate-render-unit-001",
            "FAILOVER_PROVIDER_TOKEN_FILE": "/run/secrets/provider",
            "FAILOVER_APPROVAL_TOKEN_FILE": "/run/secrets/approval",
            "PROMOTION_PRINCIPAL_TOKEN_FILE": "/run/secrets/principal",
        }
        rendered["services"]["promotion-controller"]["environment"] = dict(base)
        validate_rendered(rendered, required)
        for field, value in (
            ("PROMOTION_CONTROLLER_ARMED", "false"),
            ("ACTIVE_HEALTH_URLS", "https://witness-a.example/health"),
            ("FAILOVER_PROVIDER_URL", "http://failover.example/v1/failovers"),
            ("PROMOTION_CANDIDATE_ID", "REPLACE"),
        ):
            candidate = self.rendered(required)
            candidate["services"]["promotion-controller"]["environment"] = {**base, field: value}
            with self.subTest(field=field), self.assertRaises(DeploymentError):
                validate_rendered(candidate, required)
        secret = self.rendered(required)
        secret["services"]["promotion-controller"]["environment"] = {
            **base, "FAILOVER_PROVIDER_TOKEN": "embedded-secret",
        }
        with self.assertRaises(DeploymentError):
            validate_rendered(secret, required)

    def test_launcher_uses_only_canonical_production_overlays(self):
        names = [path.name for path in PRODUCTION_FILES]
        self.assertEqual(names, [
            "compose.yaml", "compose.dependencies.yaml", "compose.hosted.yaml",
            "compose.failover-provider.yaml", "compose.production.yaml",
        ])

    def test_production_overlays_contain_no_placeholder_service_slots(self):
        # Consolidated hosting-service topology: tenant/entitlement and
        # sponsorship-registration APIs are coordinator routes; finality and
        # sponsor mutations are owner managed L1 operations. No overlay may
        # reintroduce a placeholder service or the retired placeholder image.
        for path in PRODUCTION_FILES:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("zkdeal-hosting-services", text)
                for retired in ("tenant-api", "sponsorship", "finality-oracle", "provider-payout"):
                    self.assertNotRegex(text, rf"(?m)^  {retired}:")
        production = (ROOT / "compose/compose.production.yaml").read_text(encoding="utf-8")
        # The only neutered inherited services are the dev-only filesystem
        # queue and the smoke-test agent stub; both carry explanatory comments
        # and the never-production profile rather than silent placeholders.
        self.assertEqual(production.count("never-production"), 2)

    def test_policy_gate_transitions_from_blocked_owner_to_sealed_owner(self):
        script = (ROOT / "tests/acceptance/production-compose-policy.sh").read_text(encoding="utf-8")
        self.assertIn("owner_capability_start_blocked=false", script)
        self.assertIn("owner capability preflight failed closed", script)
        self.assertIn("legacy_hosted_surface_blocked=false", script)
        self.assertIn("standalone filesystem proof queue", script)
        self.assertNotIn("owner headless room-node has no hostedIntegration capability", script)

    def test_launcher_rejects_unpublished_owner_capabilities_after_render(self):
        with patch("production_compose.owner_capability_errors", return_value=[]):
            validate_owner_capability_wiring()
        with patch(
            "production_compose.owner_capability_errors",
            return_value=["headless hosted path unpublished"],
        ), self.assertRaisesRegex(DeploymentError, "owner capability preflight failed closed"):
            validate_owner_capability_wiring()

    def test_agent_image_labels_are_bound_to_the_owner_source_and_capability(self):
        with tempfile.TemporaryDirectory() as folder, patch("production_compose.ROOT", Path(folder) / "infra"):
            umbrella = Path(folder)
            source = umbrella / "prover-node/agent/src"
            source.mkdir(parents=True)
            (source / "agent.ts").write_text("export const agent = true\n", encoding="utf-8")
            capability = umbrella / "prover-node/agent/liveness-capability.json"
            capability.write_text("{}\n", encoding="utf-8")
            trace_capability = umbrella / "prover-node/agent/trace-capability.json"
            trace_capability.write_text('{"enabled":true}\n', encoding="utf-8")
            from production_compose import expected_agent_source_hash
            from common import sha256_file
            payload = {"Config": {"Labels": {
                "org.zkdeal.owner.source.sha256": expected_agent_source_hash(),
                "org.zkdeal.owner.liveness-capability.sha256": sha256_file(capability),
                "org.zkdeal.owner.trace-capability.sha256": sha256_file(trace_capability),
            }}}
            self.assertIn("sourceSha256", validate_agent_image_labels(payload))
            payload["Config"]["Labels"]["org.zkdeal.owner.source.sha256"] = "0" * 64
            with self.assertRaisesRegex(DeploymentError, "source label differs"):
                validate_agent_image_labels(payload)
            payload["Config"]["Labels"]["org.zkdeal.owner.source.sha256"] = expected_agent_source_hash()
            payload["Config"]["Labels"]["org.zkdeal.owner.trace-capability.sha256"] = "0" * 64
            with self.assertRaisesRegex(DeploymentError, "trace capability label differs"):
                validate_agent_image_labels(payload)


if __name__ == "__main__":
    unittest.main()
