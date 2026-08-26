from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "failover-provider"))

import provider as fp  # noqa: E402


class FakeDriver(fp.Driver):
    def __init__(self):
        self.prepare_calls = 0
        self.commit_calls = 0

    def prepare(self, request, existing_target, checkpoint):
        self.prepare_calls += 1
        target = existing_target or "0/16B6C50"
        checkpoint(target)
        return {
            "operationId": request["candidateId"],
            "status": "READY_FOR_APPLICATION_PROMOTION",
            "activeFenced": True,
            "oldWriterTerminated": True,
            "targetCapturedByProvider": True,
            "databasePromoted": True,
            "standbyReplayAtOrAfterTarget": True,
            "indexerHeadMatchesL1": True,
            "stableDatabaseEndpointRouted": True,
            "standbySignerAuthorityActive": False,
            "primaryTargetSource": "durable-fenced-wal-checkpoint",
            "primaryTargetLsn": target,
            "standbyReplayLsn": target,
            "checkpointAgeSeconds": 0,
        }

    def commit(self, operation_id, owner_hash):
        self.commit_calls += 1
        return {
            "operationId": operation_id,
            "writerRouteCommitted": True,
            "writerCoordinatorId": "standby-region-b",
            "oldWriterRouteRemoved": True,
            "stableDatabaseEndpointRouted": True,
            "signerAuthorityActivatedAfterFence": True,
        }


class FailoverProviderTests(unittest.TestCase):
    def config(self, state: Path) -> fp.Config:
        return fp.Config(
            platform="docker",
            active_health_urls=(
                "https://witness-a.example/health",
                "https://witness-b.example/health",
            ),
            standby_health_url="https://standby.example/health",
            indexer_freshness_url="https://indexer.example/freshness",
            signer_authority_health_urls=("https://signer-authority.example/health",),
            active_coordinator_id="active-region-a",
            standby_coordinator_id="standby-region-b",
            provider_token="provider-token-0001",
            approval_token="approval-token-0001",
            request_timeout_seconds=2,
            platform_timeout_seconds=10,
            state_path=state,
            listen_host="127.0.0.1",
            listen_port=8443,
            tls_cert_file=None,
            tls_key_file=None,
            allow_http=True,
        )

    def request(self):
        return {
            "candidateId": "candidate-20260821-001",
            "activeCoordinatorId": "active-region-a",
            "standbyCoordinatorId": "standby-region-b",
            "failedWitnessCount": 2,
        }

    def test_provider_durably_replays_exact_prepare_and_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            driver = FakeDriver()
            service = fp.Provider(self.config(Path(directory) / "state.json"), driver)
            with patch.object(fp, "verify_witness_failures"), patch.object(fp, "verify_standby"):
                first = service.prepare("candidate-prepare-key-0001", self.request())
                replay = service.prepare("candidate-prepare-key-0001", self.request())
            self.assertEqual(first, replay)
            self.assertEqual(driver.prepare_calls, 1)
            owner_hash = "1" * 64
            committed = service.commit(
                self.request()["candidateId"], "candidate-commit-key-0001",
                {"ownerResponseSha256": owner_hash},
            )
            self.assertEqual(
                service.commit(
                    self.request()["candidateId"], "candidate-commit-key-0001",
                    {"ownerResponseSha256": owner_hash},
                ),
                committed,
            )
            self.assertEqual(driver.commit_calls, 1)
            # The commit response carries the measured promotion duration so
            # acceptance plans can bind rtoSeconds to a real measurement.
            self.assertIsInstance(committed["rtoSeconds"], int)
            self.assertGreaterEqual(committed["rtoSeconds"], 0)
            self.assertIsInstance(committed["targetCapturedAtUnixMs"], int)
            self.assertIsInstance(committed["committedAtUnixMs"], int)
            state = json.loads((Path(directory) / "state.json").read_text(encoding="utf-8"))
            operation = state["operations"][self.request()["candidateId"]]
            self.assertEqual(operation["phase"], "COMMITTED")
            self.assertEqual(operation["committedAtUnixMs"], committed["committedAtUnixMs"])
            self.assertNotIn("provider-token-0001", json.dumps(state))
            status = service.status(self.request()["candidateId"])
            self.assertEqual(status["phase"], "COMMITTED")
            self.assertEqual(status["rtoSeconds"], committed["rtoSeconds"])
            self.assertEqual(status["committedAtUnixMs"], committed["committedAtUnixMs"])
            with self.assertRaises(fp.ProviderError) as unknown:
                service.status("candidate-20260821-absent")
            self.assertEqual(unknown.exception.status, 404)

    def test_parallel_duplicate_prepare_executes_the_platform_boundary_once(self):
        class BlockingDriver(FakeDriver):
            def __init__(self):
                super().__init__()
                self.entered = threading.Event()
                self.release = threading.Event()

            def prepare(self, request, existing_target, checkpoint):
                self.entered.set()
                if not self.release.wait(2):
                    raise RuntimeError("test driver was not released")
                return super().prepare(request, existing_target, checkpoint)

        with tempfile.TemporaryDirectory() as directory:
            driver = BlockingDriver()
            service = fp.Provider(self.config(Path(directory) / "state.json"), driver)
            results = []
            failures = []

            def invoke():
                try:
                    results.append(service.prepare("candidate-prepare-key-0001", self.request()))
                except Exception as exc:  # pragma: no cover - asserted below
                    failures.append(exc)

            first = threading.Thread(target=invoke)
            second = threading.Thread(target=invoke)
            with patch.object(fp, "verify_witness_failures"), patch.object(fp, "verify_standby"):
                first.start()
                self.assertTrue(driver.entered.wait(1))
                second.start()
                time.sleep(0.05)
                self.assertEqual(driver.prepare_calls, 0)
                driver.release.set()
                first.join(2)
                second.join(2)
            self.assertFalse(first.is_alive() or second.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0], results[1])
            self.assertEqual(driver.prepare_calls, 1)

    def test_changed_replay_and_unauthorized_boundaries_are_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            service = fp.Provider(self.config(Path(directory) / "state.json"), FakeDriver())
            with patch.object(fp, "verify_witness_failures"), patch.object(fp, "verify_standby"):
                service.prepare("candidate-prepare-key-0001", self.request())
                changed = dict(self.request())
                changed["failedWitnessCount"] = 3
                with self.assertRaisesRegex(fp.ProviderError, "changed input"):
                    service.prepare("candidate-prepare-key-0001", changed)
            with self.assertRaises(fp.ProviderError) as token:
                service.authenticate("Bearer wrong-token-value", "approval-token-0001")
            self.assertEqual(token.exception.status, 401)
            with self.assertRaises(fp.ProviderError) as approval:
                service.authenticate("Bearer provider-token-0001", "wrong-approval-value")
            self.assertEqual(approval.exception.status, 403)

    def test_witnesses_must_resolve_to_disjoint_network_identities(self):
        overlapping = [
            (None, None, None, None, ("192.0.2.10", 443)),
        ]
        with patch.object(fp.socket, "getaddrinfo", side_effect=[overlapping, overlapping]):
            with self.assertRaisesRegex(fp.ProviderError, "overlapping network identity"):
                fp.witness_networks(("https://a.example/health", "https://b.example/health"))
        disjoint = [
            [(None, None, None, None, ("192.0.2.10", 443))],
            [(None, None, None, None, ("192.0.2.11", 443))],
        ]
        with patch.object(fp.socket, "getaddrinfo", side_effect=disjoint):
            self.assertEqual(len(fp.witness_networks((
                "https://a.example/health", "https://b.example/health",
            ))), 2)

    def test_signer_authority_waits_for_every_endpoint(self):
        config = self.config(Path("/tmp/unused-state.json"))
        config = fp.Config(**{
            **config.__dict__,
            "signer_authority_health_urls": (
                "https://signer-a.example/health",
                "https://signer-b.example/health",
            ),
        })
        observations = iter(((0, {}), (200, {}), (200, {}), (200, {})))
        with patch.object(fp, "request_json", side_effect=lambda *_: next(observations)), patch.object(
            fp.time, "sleep", return_value=None,
        ):
            fp.verify_signer_authority(config)

    def test_signer_authority_fails_closed_after_bounded_wait(self):
        config = self.config(Path("/tmp/unused-state.json"))
        clock = iter((0.0, 0.0, 9.0, 11.0))
        with patch.object(fp, "request_json", return_value=(0, {})), patch.object(
            fp.time, "monotonic", side_effect=lambda: next(clock),
        ), patch.object(fp.time, "sleep", return_value=None), self.assertRaisesRegex(
            fp.ProviderError, "did not become healthy",
        ):
            fp.verify_signer_authority(config)

    def test_driver_configs_reject_request_controlled_or_unsafe_targets(self):
        docker = {
            "DOCKER_COMPOSE_PROJECT": "zkdeal-hosting",
            "DOCKER_FAILOVER_NETWORK": "zkdeal-hosting_internal",
            "DOCKER_ACTIVE_SERVICE": "coordinator",
            "DOCKER_STANDBY_SERVICE": "coordinator-standby",
            "DOCKER_PRIMARY_DB_SERVICE": "postgres-primary",
            "DOCKER_STANDBY_DB_SERVICE": "postgres-standby",
            "DOCKER_SIGNER_SERVICE": "standby-signer",
            "DOCKER_DATABASE_ALIAS": "postgres-writer",
            "DOCKER_APPLICATION_ALIAS": "coordinator-writer",
            "FAILOVER_PGUSER": "zkdeal",
            "FAILOVER_PGDATABASE": "zkdeal",
        }
        with patch.dict(os.environ, docker, clear=True):
            self.assertEqual(fp.DockerSettings.from_environment().active_service, "coordinator")
        docker["DOCKER_ACTIVE_SERVICE"] = "coordinator;docker system prune"
        with patch.dict(os.environ, docker, clear=True), self.assertRaises(fp.ProviderError):
            fp.DockerSettings.from_environment()

    def test_kubernetes_driver_uses_only_fixed_resource_commands(self):
        config = self.config(Path("/tmp/unused-state.json"))
        settings = fp.KubernetesSettings(
            namespace="zkdeal-prod",
            active_deployment="coordinator-active",
            standby_deployment="coordinator-standby",
            primary_db_pod="postgres-primary-0",
            standby_db_pod="postgres-standby-0",
            primary_db_statefulset="postgres-primary",
            postgres_container="postgres",
            database_service="postgres-writer",
            application_service="coordinator-writer",
            signer_deployment="standby-signer-authority",
            route_scope_label_key="app.kubernetes.io/instance",
            route_scope_label_value="zkdeal",
            route_label_key="zkdeal.io/failover-target",
            standby_route_value="standby",
            pg_user="zkdeal",
            pg_database="zkdeal",
            pg_data="/var/lib/postgresql/data",
        )
        commands = []

        def runner(command, timeout):
            commands.append(command)
            if command[-2:] == ["-o", "json"]:
                if "service/" in " ".join(command):
                    return subprocess.CompletedProcess(command, 0, json.dumps({
                        "spec": {"selector": {
                            "app.kubernetes.io/instance": "zkdeal",
                            "zkdeal.io/failover-target": "standby",
                        }},
                    }), "")
                return subprocess.CompletedProcess(command, 0, json.dumps({"status": {"availableReplicas": 0}}), "")
            return subprocess.CompletedProcess(command, 0, "", "")

        driver = fp.KubernetesDriver(config, settings, runner)
        driver.scale("coordinator-active", 0)
        driver.scale_workload("statefulset", "postgres-primary", 0)
        driver.patch_route("coordinator-writer")
        rendered = json.dumps(commands)
        self.assertIn("deployment/coordinator-active", rendered)
        self.assertIn("statefulset/postgres-primary", rendered)
        self.assertIn("service/coordinator-writer", rendered)
        self.assertNotIn("--all", rendered)
        self.assertNotIn("delete namespace", rendered)

    def test_live_drills_bind_candidate_digest_to_executed_image_id(self):
        docker_drill = (ROOT / "tests/acceptance/failover-provider-docker-live.sh").read_text(
            encoding="utf-8",
        )
        self.assertIn("FAILOVER_PROVIDER_CANDIDATE_IMAGE", docker_drill)
        self.assertIn("validate_reference", docker_drill)
        self.assertIn("dc up -d --wait --no-build failover-provider-docker", docker_drill)
        self.assertIn("provider container ID differs from the pulled candidate digest", docker_drill)

        kubernetes_drill = (
            ROOT / "tests/acceptance/failover-provider-kubernetes-live.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("FAILOVER_PROVIDER_CANDIDATE_IMAGE", kubernetes_drill)
        self.assertIn("POSTGRES_CANDIDATE_IMAGE", kubernetes_drill)
        self.assertIn("validate_reference", kubernetes_drill)
        self.assertIn('docker tag "$provider_source" "$provider_image"', kubernetes_drill)
        self.assertIn("providerSourceImageId", kubernetes_drill)
        self.assertIn("providerAliasImageId", kubernetes_drill)


if __name__ == "__main__":
    unittest.main()
