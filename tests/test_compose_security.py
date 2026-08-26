from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

ROOT = Path(__file__).resolve().parents[1]


class ComposeLoader(yaml.SafeLoader):
    """Parse Compose's official reset/override tags without changing values."""


def compose_tag(loader, node):
    if isinstance(node, SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, ScalarNode):
        return loader.construct_scalar(node)
    raise TypeError(f"unsupported Compose tag node: {type(node).__name__}")


for compose_yaml_tag in ("!reset", "!override"):
    ComposeLoader.add_constructor(compose_yaml_tag, compose_tag)


class ComposeSecurityTests(unittest.TestCase):
    def load(self, name):
        return yaml.load(
            (ROOT / "compose" / name).read_text(encoding="utf-8"),
            Loader=ComposeLoader,
        )

    def test_public_ports_are_loopback_in_local_stack(self):
        compose = self.load("compose.yaml")
        for name, service in compose["services"].items():
            for port in service.get("ports", []):
                rendered = str(port)
                with self.subTest(service=name, port=rendered):
                    self.assertTrue(rendered.startswith("127.0.0.1:"), rendered)

    def test_stateful_and_http_services_have_health_gates(self):
        core = self.load("compose.yaml")["services"]
        for name in ("coordinator", "queue", "docs", "front-door", "prometheus", "grafana", "alertmanager", "loki"):
            with self.subTest(service=name):
                self.assertIn("healthcheck", core[name])
        dependencies = self.load("compose.dependencies.yaml")["services"]
        self.assertIn("healthcheck", dependencies["postgres"])
        self.assertIn("healthcheck", dependencies["minio"])

    def test_conformance_services_are_explicitly_non_business(self):
        text = (ROOT / "compose/compose.conformance.yaml").read_text(encoding="utf-8")
        for service in ("indexer", "reconciler", "headless-node", "capacity-controller", "auto-claimer"):
            self.assertRegex(text, rf"(?m)^  {re.escape(service)}:")
        self.assertNotRegex(text, r"(?m)^  withdrawal:")
        # Consolidated topology: tenant/entitlement/sponsorship APIs are
        # coordinator routes; no stub service may reintroduce those slots.
        for retired in ("tenant-api", "sponsorship", "finality-oracle", "provider-payout"):
            self.assertNotRegex(text, rf"(?m)^  {re.escape(retired)}:")
        fixture = (ROOT / "tests/fixtures/conformance_service.py").read_text(encoding="utf-8")
        self.assertIn("CONFORMANCE_STUB_NO_BUSINESS_LOGIC", fixture)

    def test_retired_hosting_services_placeholder_image_never_returns(self):
        for path in sorted((ROOT / "compose").glob("*.yaml")):
            with self.subTest(path=path.name):
                self.assertNotIn("zkdeal-hosting-services", path.read_text(encoding="utf-8"))
        for name in ("values.yaml", "values-production.example.yaml"):
            with self.subTest(path=name):
                self.assertNotIn(
                    "zkdeal-hosting-services",
                    (ROOT / "helm/zkdeal" / name).read_text(encoding="utf-8"),
                )

    def test_no_privileged_or_host_network(self):
        for path in (ROOT / "compose").glob("*.yaml"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertNotRegex(text, r"(?m)^\s*privileged:\s*true")
                self.assertNotRegex(text, r"(?m)^\s*network_mode:\s*host")

    def test_front_door_blocks_operator_routes(self):
        config = (ROOT / "front-door/caddy/Caddyfile.local").read_text(encoding="utf-8")
        for route in ("/metrics", "/admission/*", "/indexer/*", "/queue/*"):
            self.assertIn(route, config)

    def test_production_signers_are_private_and_network_isolated(self):
        compose = self.load("compose.signer-production.example.yaml")
        signer_names = (
            "web3signer-liveness", "web3signer-operations", "web3signer-payout",
            "web3signer-finality", "web3signer-sponsor", "web3signer-withdrawal",
            "web3signer-blob",
        )
        networks = []
        for name in signer_names:
            service = compose["services"][name]
            with self.subTest(service=name):
                self.assertNotIn("ports", service)
                self.assertEqual(len(service["networks"]), 1)
                networks.extend(service["networks"])
        self.assertEqual(len(networks), len(set(networks)))
        for name in networks:
            self.assertTrue(compose["networks"][name]["internal"])
        self.assertNotIn("agent", compose["services"])
        coordinator = compose["services"]["coordinator"]
        self.assertEqual(coordinator["environment"]["NODE_LIVENESS_SIGNER_URL"], "http://web3signer-liveness:9000")
        self.assertIn("signer-liveness", coordinator["networks"])
        self.assertIn("web3signer-liveness-health", coordinator["depends_on"])

    def test_known_acceptance_keys_do_not_leak_to_nonlocal_configuration(self):
        forbidden = (
            "ac0974bec39b31e8b42be8e8bb9651fd83d71efb7f26eef1cf633e2f66f93216f",
            "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
            "59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
        )
        targets = [ROOT / "compose/compose.signer-production.example.yaml"]
        targets.extend(path for path in (ROOT / "config/profiles").glob("*.json") if path.name != "local.json")
        for path in targets:
            text = path.read_text(encoding="utf-8").lower()
            for key in forbidden:
                with self.subTest(path=path, key=key[:8]):
                    self.assertNotIn(key, text)

    def test_hosted_owner_entrypoints_and_headless_file_custody_are_real(self):
        hosted = self.load("compose.hosted.yaml")
        commands = {
            "coordinator": ["node", "dist/index.js"],
            "indexer": ["node", "dist/hosted-worker.js", "indexer"],
            "reconciler": ["node", "dist/hosted-worker.js", "reconciler"],
            "publisher": ["node", "dist/hosted-publisher-worker.js"],
            "auto-claimer": ["node", "dist/hosted-withdrawal-worker.js", "run"],
            "capacity-controller": ["node", "dist/hosted-capacity-worker.js", "run"],
            "headless-node": ["node", "dist/cli.js", "run"],
        }
        for name, command in commands.items():
            with self.subTest(service=name):
                self.assertEqual(hosted["services"][name]["command"], command)
        self.assertNotIn("withdrawal", hosted["services"])
        auto_claimer = hosted["services"]["auto-claimer"]
        auto_env = auto_claimer["environment"]
        self.assertEqual(auto_env["COORDINATOR_ID"], hosted["services"]["coordinator"]["environment"]["COORDINATOR_ID"])
        self.assertEqual(auto_env["WITHDRAWAL_CLAIMER_ENABLED"], "1")
        self.assertEqual(auto_env["WORKER_PORT"], "3003")
        self.assertEqual(
            {"WITHDRAWAL_SIGNER_URL", "WITHDRAWAL_SIGNER_ADDRESS", "WITHDRAWAL_SIGNER_AUTH_TOKEN"} - set(auto_env),
            set(),
        )
        capacity = hosted["services"]["capacity-controller"]
        capacity_env = capacity["environment"]
        self.assertEqual(capacity_env["COORDINATOR_ID"], hosted["services"]["coordinator"]["environment"]["COORDINATOR_ID"])
        self.assertEqual(capacity_env["CAPACITY_CONTROLLER_ENABLED"], "1")
        self.assertEqual(capacity_env["WORKER_PORT"], "3004")
        self.assertEqual(
            {"CAPACITY_PROVIDER_URL", "CAPACITY_PROVIDER_AUTH_TOKEN"} - set(capacity_env),
            set(),
        )
        headless = hosted["services"]["headless-node"]
        self.assertEqual(set(headless["environment"]), {"ROOM_NODE_CONFIG_PATH"})
        self.assertNotIn("egress", headless["networks"])
        rendered_volumes = "\n".join(headless["volumes"])
        self.assertIn("/run/zkdeal-secrets:ro", rendered_volumes)
        self.assertIn("/opt/zkdeal/artifacts:ro", rendered_volumes)
        init = hosted["services"]["headless-node-secret-init"]
        self.assertEqual(init["network_mode"], "none")
        self.assertIn("chmod 0600", "\n".join(init["command"]))

    def test_production_overlay_digest_gates_headless_owner_image(self):
        production = self.load("compose.production.yaml")
        for name in ("headless-node", "headless-node-secret-init"):
            with self.subTest(service=name):
                self.assertIn("HEADLESS_NODE_IMAGE_DIGEST", production["services"][name]["image"])
        self.assertIn("COORDINATOR_IMAGE_DIGEST", production["services"]["auto-claimer"]["image"])
        self.assertIn("COORDINATOR_IMAGE_DIGEST", production["services"]["capacity-controller"]["image"])
        self.assertIn(
            "PROMOTION_CONTROLLER_IMAGE_DIGEST",
            production["services"]["promotion-controller"]["image"],
        )
        self.assertIn(
            "FAILOVER_PROVIDER_IMAGE_DIGEST",
            production["services"]["failover-provider-docker"]["image"],
        )

    def test_first_party_docker_failover_provider_is_the_only_socket_holder(self):
        provider_overlay = self.load("compose.failover-provider.yaml")
        provider = provider_overlay["services"]["failover-provider-docker"]
        self.assertEqual(provider["profiles"], ["promotion-provider"])
        self.assertEqual(provider["environment"]["FAILOVER_PLATFORM"], "docker")
        self.assertEqual(provider["build"]["dockerfile"], "failover-provider/Dockerfile")
        rendered = "\n".join(provider["volumes"])
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock", rendered)
        for field in (
            "FAILOVER_PROVIDER_TOKEN_FILE", "FAILOVER_APPROVAL_TOKEN_FILE",
            "FAILOVER_PROVIDER_TLS_CERT_FILE", "FAILOVER_PROVIDER_TLS_KEY_FILE",
        ):
            self.assertTrue(provider["environment"][field].startswith("/run/secrets/"))
        controller = self.load("compose.hosted.yaml")["services"]["promotion-controller"]
        self.assertNotIn("/var/run/docker.sock", "\n".join(controller.get("volumes", [])))
        signer = provider_overlay["services"]["standby-signer-authority"]
        self.assertEqual(signer["restart"], "no")

    def test_promotion_controller_is_profile_gated_and_has_file_secrets_only(self):
        hosted = self.load("compose.hosted.yaml")
        controller = hosted["services"]["promotion-controller"]
        self.assertEqual(controller["profiles"], ["promotion-controller"])
        self.assertNotIn("ports", controller)
        self.assertEqual(
            controller["build"]["dockerfile"], "promotion-controller/Dockerfile",
        )
        environment = controller["environment"]
        self.assertEqual(environment["PROMOTION_CONTROLLER_STATE_PATH"], "/tmp/promotion-controller-state.json")
        self.assertEqual(environment["FAILURE_THRESHOLD"], "${PROMOTION_FAILURE_THRESHOLD:-3}")
        self.assertNotIn("FAILOVER_PROVIDER_TOKEN", environment)
        self.assertNotIn("FAILOVER_APPROVAL_TOKEN", environment)
        self.assertNotIn("PROMOTION_PRINCIPAL_TOKEN", environment)
        for field in (
            "FAILOVER_PROVIDER_TOKEN_FILE", "FAILOVER_APPROVAL_TOKEN_FILE",
            "PROMOTION_PRINCIPAL_TOKEN_FILE",
        ):
            self.assertTrue(environment[field].startswith("/run/secrets/"))
        self.assertNotIn("/var/run/docker.sock", "\n".join(controller.get("volumes", [])))

    def test_local_gpu_agent_uses_the_packaged_owner_source_without_l1_authority(self):
        compose = self.load("compose.yaml")
        prover = compose["services"]["prover"]
        self.assertEqual(prover["command"], ["serve", "--host", "0.0.0.0", "--port", "8080"])
        self.assertIn("ZKDEAL_PROVER_TOKEN", prover["environment"])
        agent = compose["services"]["agent"]
        self.assertEqual(agent["image"], "${AGENT_IMAGE:-zkdeal-prover-agent:local}")
        self.assertEqual(agent["build"]["dockerfile"], "cloud-deployer-infra/agent/Dockerfile")
        self.assertEqual(agent["command"], ["node", "/app/agent/agent.js"])
        for name in (
            "ROOM_POOL", "L1_RPC_URL", "NODE_LIVENESS_SIGNER_URL",
            "NODE_LIVENESS_ACCOUNT", "NODE_LIVENESS_SIGNER_AUTH_TOKEN",
            "NODE_SERVICE_KEY",
        ):
            self.assertNotIn(name, agent["environment"])

    def test_production_gpu_agent_uses_owner_durable_queue_and_liveness_operation(self):
        production = self.load("compose.production.yaml")["services"]
        self.assertEqual(production["queue"]["profiles"], ["never-production"])
        self.assertEqual(production["queue"]["command"], [])
        agent = production["agent"]
        self.assertEqual(agent["command"], ["node", "/app/agent/agent.js"])
        self.assertEqual(agent["working_dir"], "/app")
        self.assertEqual(agent["environment"]["QUEUE_URL"], "http://coordinator:3000")
        self.assertEqual(
            agent["environment"]["NODE_LIVENESS_COORDINATOR_URL"],
            "http://coordinator:3000",
        )
        for field in (
            "NODE_LIVENESS_COORDINATOR_AUTH_TOKEN", "NODE_LIVENESS_ACCOUNT",
            "L1_CHAIN_ID", "ROOM_POOL", "NODE_ID",
        ):
            self.assertIn(field, agent["environment"])
        for forbidden in (
            "NODE_SERVICE_KEY", "NODE_LIVENESS_SIGNER_URL",
            "NODE_LIVENESS_SIGNER_AUTH_TOKEN", "NODE_LIVENESS_DEV_MODE",
            "NODE_LIVENESS_DEV_PRIVATE_KEY", "L1_RPC_URL",
        ):
            self.assertIn(agent["environment"].get(forbidden), (None, "", "null"))


if __name__ == "__main__":
    unittest.main()
