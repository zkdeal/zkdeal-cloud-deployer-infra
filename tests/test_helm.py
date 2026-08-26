from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "helm/zkdeal"


class HelmTests(unittest.TestCase):
    def test_production_digest_gate_exists(self):
        text = (CHART / "templates/validate.yaml").read_text(encoding="utf-8")
        self.assertIn("sha256", text)
        self.assertIn("external-transactional", text)
        self.assertIn("transactional fencing", text)

    def test_pods_are_hardened_and_probed(self):
        text = (CHART / "templates/components.yaml").read_text(encoding="utf-8")
        for value in ("runAsNonRoot", "readOnlyRootFilesystem", "allowPrivilegeEscalation: false", "livenessProbe", "readinessProbe"):
            self.assertIn(value, text)
        self.assertIn("runAsUser: {{ $component.runtimeIdentity.uid }}", text)
        self.assertIn("runAsGroup: {{ $component.runtimeIdentity.gid }}", text)
        self.assertIn("fsGroup: {{ $component.runtimeIdentity.gid }}", text)

    def test_docs_image_is_numeric_nonroot_with_tmp_only_runtime_state(self):
        dockerfile = (ROOT / "docs/Dockerfile").read_text(encoding="utf-8")
        nginx = (ROOT / "docs/site/nginx-main.conf").read_text(encoding="utf-8")
        self.assertIn("USER 101:101", dockerfile)
        self.assertIn("pid /tmp/nginx.pid", nginx)
        for path in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"):
            self.assertIn(f"{path}_temp_path /tmp/", nginx)

    def test_chart_never_materializes_a_secret_value(self):
        for path in CHART.rglob("*.yaml"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertNotRegex(text, r"(?i)kind:\s*Secret")
                self.assertNotRegex(text, r"(?i)stringData:")

    def test_single_writer_filesystem_gate_exists(self):
        text = (CHART / "templates/validate.yaml").read_text(encoding="utf-8")
        self.assertIn("filesystem-authoritative", text)

    def test_production_requires_postgresql_observation_authority_and_read_only_root(self):
        text = (CHART / "templates/validate.yaml").read_text(encoding="utf-8")
        for marker in (
            "forbids filesystem application-state authority",
            "must not require DATA_DIR state",
            "requires owner read-only-root support",
            "legacy observer mutations to be retired with HTTP 410",
            "PostgreSQL canonical projection",
        ):
            self.assertIn(marker, text)
        acceptance = (ROOT / "tests/acceptance/helm-filesystem-capability-policy.sh").read_text(encoding="utf-8")
        self.assertIn("negativeCapabilitiesRejected", acceptance)
        for unsafe in (
            "filesystemApplicationStateAuthority=true", "dataDirRequired=true",
            "readOnlyRootSupported=false", "active-file-store", "replica-local-json",
        ):
            self.assertIn(unsafe, acceptance)

    def test_production_headless_path_is_capability_and_live_acceptance_gated(self):
        values = (CHART / "values.yaml").read_text(encoding="utf-8")
        production = (CHART / "values-production.example.yaml").read_text(encoding="utf-8")
        validation = (CHART / "templates/validate.yaml").read_text(encoding="utf-8")
        fixture = (ROOT / "tests/fixtures/helm-production-render.yaml").read_text(encoding="utf-8")
        for field in (
            "hostedAdmissionLease", "hostedRoomBatchEnabled", "hostedEngineToBatchInputV5",
            "hostedDurablePostgresQueue", "hostedExternalProver", "hostedRestartResume",
            "hostedFixturePrepare", "hostedLegacyGroth16", "hostedIntegrationAcceptanceToken",
        ):
            self.assertIn(field, values)
            self.assertIn(field, production)
            self.assertIn(field, validation)
            self.assertIn(field, fixture)
        # Pins mirror the owner room-node hosted contract; the joint-acceptance
        # token must equal the sealed owner hostedIntegration manifest hash.
        for owner_true in (
            "hostedAdmissionLease: true", "hostedRoomBatchEnabled: true",
            "hostedEngineToBatchInputV5: true", "hostedDurablePostgresQueue: true",
            "hostedExternalProver: true", "hostedRestartResume: true",
        ):
            self.assertIn(owner_true, values)
            self.assertIn(owner_true, production)
        for owner_false in ("hostedFixturePrepare: false", "hostedLegacyGroth16: false"):
            self.assertIn(owner_false, values)
            self.assertIn(owner_false, production)
        capability = json.loads(
            (ROOT.parent / "web2-api/server/capabilities/hosted-service.json").read_text(encoding="utf-8")
        )
        sealed = capability["managedL1Operations"]["roomBatch"]["hostedIntegration"]["acceptanceToken"]
        self.assertRegex(str(sealed), r"^sha256:[0-9a-f]{64}$")
        self.assertIn(f'hostedIntegrationAcceptanceToken: "{sealed}"', values)
        self.assertIn(f'hostedIntegrationAcceptanceToken: "{sealed}"', production)
        self.assertIn("Structural Helm fixture only", fixture)
        acceptance = (
            ROOT / "tests/acceptance/helm-headless-hosted-path-policy.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("negativeCapabilitiesRejected", acceptance)
        self.assertIn("ownerEvidence\":false", acceptance)

    def test_schema_pins_match_the_owner_capability_manifests(self):
        import json

        manifest = json.loads(
            (ROOT.parent / "web2-api/server/capabilities/hosted-service.json").read_text(encoding="utf-8")
        )
        for name in ("values.yaml", "values-production.example.yaml"):
            text = (CHART / name).read_text(encoding="utf-8")
            with self.subTest(values=name):
                self.assertIn(f"databaseSchema: {manifest['databaseSchema']}", text)
                self.assertIn(f"protocolJournalSchema: {manifest['protocolJournalSchema']}", text)
        for path in list(CHART.rglob("*.yaml")) + list((CHART / "files").glob("*")):
            with self.subTest(path=path):
                self.assertNotRegex(
                    path.read_text(encoding="utf-8"),
                    r"databaseSchema:\s*(?!" + str(manifest["databaseSchema"]) + r"\b)\d+",
                )

    def test_hosting_service_topology_has_no_placeholder_slots(self):
        values = (CHART / "values.yaml").read_text(encoding="utf-8")
        production = (CHART / "values-production.example.yaml").read_text(encoding="utf-8")
        validation = (CHART / "templates/validate.yaml").read_text(encoding="utf-8")
        schema = (CHART / "values.schema.json").read_text(encoding="utf-8")
        for text_name, text in (("values", values), ("production", production)):
            for retired in ("tenantApi", "sponsorship", "finalityOracle", "providerPayout"):
                with self.subTest(values=text_name, slot=retired):
                    self.assertNotRegex(text, rf"(?m)^  {retired}:")
            with self.subTest(values=text_name, image="zkdeal-hosting-services"):
                self.assertNotIn("zkdeal-hosting-services", text)
        # Tenant/entitlement ownership is documented at the consolidation site.
        self.assertIn("coordinator routes", values)
        self.assertIn("coordinator routes", production)
        # The gate is enforced in the chart itself, for every profile.
        self.assertIn("retired zkdeal-hosting-services placeholder image", validation)
        self.assertIn("retired placeholder slot", validation)
        self.assertIn("enabled without an owner-published command", validation)
        for retired in ("tenantApi", "sponsorship", "finalityOracle", "providerPayout"):
            self.assertIn(f'"required": ["{retired}"]', schema)
        self.assertIn('"not": { "pattern": "zkdeal-hosting-services" }', schema)
        # Signer roles remain declared for the post-fence signer authority even
        # though no placeholder service consumes them any longer.
        for role in ("payout:", "finalityOracle:", "sponsorRelayer:"):
            self.assertIn(role, values)
            self.assertIn(role, production)

    def test_promotion_is_replay_safe_and_lsn_gated(self):
        script = (CHART / "files/promotion_gate.sh").read_text(encoding="utf-8")
        for marker in (
            "PROMOTION_IDEMPOTENCY_KEY", "Idempotency-Key:",
            "pg_last_wal_replay_lsn()", "pg_wal_lsn_diff",
            "hosted_idempotency_records", "duplicate or replayed",
            '\"configuredRole\":\"standby\"', '\"effectiveRole\":\"standby\"',
            '\"primaryTarget\":\"durable-fenced-wal-checkpoint\"',
            '\"atomicWithFenceTransfer\":true', '\"promotionReplication\":{',
        ):
            self.assertIn(marker, script)
        template = (CHART / "templates/promotion-job.yaml").read_text(encoding="utf-8")
        values = (CHART / "values.yaml").read_text(encoding="utf-8")
        production = (CHART / "values-production.example.yaml").read_text(encoding="utf-8")
        self.assertIn("PROMOTION_REQUIRED_REPLAY_LSN", template)
        self.assertIn("PROMOTION_IDEMPOTENCY_KEY", template)
        self.assertIn("manualJobEnabled", template)
        self.assertIn("manualJobEnabled: false", values)
        self.assertIn("manualJobEnabled: false", production)
        policy = (ROOT / "tests/acceptance/helm-promotion-manual-job-policy.sh").read_text(encoding="utf-8")
        self.assertIn("routineInstallStartsManualJob", policy)
        self.assertIn("automatedControllerPreserved", policy)

    def test_automated_promotion_controller_has_independent_fence_and_route_gates(self):
        script = (ROOT / "scripts/promotion_controller.py").read_text(encoding="utf-8")
        template = (CHART / "templates/promotion-controller.yaml").read_text(encoding="utf-8")
        validation = (CHART / "templates/validate.yaml").read_text(encoding="utf-8")
        provider = (ROOT / "promotion-controller/failover-provider-v1.openapi.json").read_text(encoding="utf-8")
        for marker in (
            "activeFenced", "oldWriterTerminated", "targetCapturedByProvider",
            "standbyReplayAtOrAfterTarget", "standbySignerAuthorityActive",
            "writerRouteCommitted", "signerAuthorityActivatedAfterFence",
        ):
            self.assertIn(marker, script)
            self.assertIn(marker, provider)
        self.assertIn("owner-promoted", script)
        for marker in (
            "ACTIVE_HEALTH_URLS", "FAILOVER_PROVIDER_TOKEN", "FAILOVER_APPROVAL_TOKEN",
            "PROMOTION_PRINCIPAL_TOKEN", "automountServiceAccountToken: false",
            "readOnlyRootFilesystem: true", "strategy: { type: Recreate }",
        ):
            self.assertIn(marker, template)
        self.assertIn("requires the automated promotion controller", validation)
        self.assertIn("requires two independent active-health witnesses", validation)
        acceptance = (ROOT / "tests/acceptance/helm-promotion-controller-policy.sh").read_text(encoding="utf-8")
        self.assertIn("negativeConfigurationsRejected", acceptance)
        self.assertIn("realProviderEvidence\":false", acceptance)

    def test_prover_agent_uses_owner_durable_liveness_operation_only(self):
        values = (CHART / "values.yaml").read_text(encoding="utf-8")
        components = (CHART / "templates/components.yaml").read_text(encoding="utf-8")
        validation = (CHART / "templates/validate.yaml").read_text(encoding="utf-8")
        self.assertIn('signerRole: ""', values)
        self.assertIn("NODE_LIVENESS_COORDINATOR_AUTH_TOKEN", values)
        self.assertNotIn("NODE_SERVICE_KEY: node-service-key", values)
        self.assertIn("L1_CHAIN_ID", components)
        self.assertIn("ROOM_POOL", components)
        self.assertIn('eq $component.role "prover-agent"', components)
        self.assertIn('ne $component.role "prover-agent"', components)
        self.assertIn("livenessCommand", values)
        self.assertIn("readinessCommand", values)
        self.assertIn("managedL1Operations?.nodeHeartbeat?.enabledWhenConfigured", values)
        self.assertIn("repository: zkdeal-prover-agent", values)
        self.assertIn('["node", "/app/agent/agent.js"]', values)
        self.assertIn("must not receive any direct signer role", validation)
        self.assertIn("independently packaged owner-source image", validation)
        self.assertIn("forbids direct sender environment", validation)
        self.assertIn("forbids the standalone filesystem proof queue", validation)
        acceptance = (ROOT / "tests/acceptance/helm-prover-liveness-policy.sh").read_text(encoding="utf-8")
        self.assertIn("negativeConfigurationsRejected", acceptance)
        live = (ROOT / "tests/acceptance/prover-agent-readiness-live.sh").read_text(encoding="utf-8")
        self.assertIn("extract_prover_agent_probe.py", live)
        self.assertIn("disabled heartbeat capability", live)
        self.assertIn("invalid coordinator credential", live)
        self.assertIn("unavailable prover", live)

    def test_kind_gate_uses_real_owner_and_live_storage_with_failure_diagnostics(self):
        script = (ROOT / "tests/acceptance/kubernetes-kind.sh").read_text(encoding="utf-8")
        self.assertNotIn("zkdeal-conformance-stub", script)
        for marker in (
            "owner-image-budget.sh", "zkdeal-coordinator:kind",
            "StatefulSet", "postgres", "minio", "wait_api",
            "docker inspect", "docker logs", "docker-memory-bytes",
            "host.docker.internal", "--tls-server-name=127.0.0.1",
            "pin_kind_alias", "source_id", "local_id",
            'OWNER_IMAGE_TAG="$owner_kind_image"',
            "postgres:17.6-alpine@sha256:",
            "minio/minio:RELEASE.2025-04-22T22-12-26Z@sha256:",
            "MC_CONFIG_DIR", "secretKeyRef", "readOnlyRootFilesystem: true",
            "conformanceWorkloads\":false", "helm-kind-live.yaml",
            "KIND_CANDIDATE_MODE", "KIND_OWNER_IMAGE", "KIND_HEADLESS_IMAGE",
            "KIND_PROMOTION_CONTROLLER_IMAGE", "KIND_FAILOVER_PROVIDER_IMAGE",
            "require_digest_image", 'grep -F "$alias" "$KIND_HELM_VALUES_FILE"',
            "coordinator-standby indexer reconciler publisher headless-node",
            "capacity-controller auto-claimer promotion-controller failover-provider",
        ):
            self.assertIn(marker, script)
        values = (ROOT / "tests/fixtures/helm-kind-live.yaml").read_text(encoding="utf-8")
        self.assertIn("backend: external-transactional", values)
        self.assertIn("queue: { enabled: false }", values)


if __name__ == "__main__":
    unittest.main()
