from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import DeploymentError  # noqa: E402
import candidate_topology as topology  # noqa: E402


class CandidateTopologyTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path, dict]:
        digest = "1" * 64
        images = {
            key: f"registry.company.tld/zkdeal/{key.lower()}@sha256:{digest}"
            for key in {
                "coordinator", "faultController", "backupRestoreController",
                "failoverProvider", "prover", "loki", "postgres", "headlessNode",
                "frontDoor",
            }
        }
        token = "sha256:" + "2" * 64
        candidate = {
            "candidateId": "candidate-topology-001",
            "ownerBroadSeal": {"hostedIntegrationAcceptanceToken": token},
            "images": images,
        }
        candidate_path = root / "candidate.json"
        candidate_path.write_text(json.dumps(candidate, sort_keys=True) + "\n", encoding="utf-8")
        capabilities = {}
        for name, contract in topology.CAPABILITIES.items():
            source_sha = ("3" if name == "faultControl" else "4") * 64
            value = {
                "schemaVersion": 1,
                "service": contract["service"],
                "protocol": contract["protocol"],
                "productionReady": True,
                "authRole": contract["authRole"],
                "sourceSha256": source_sha,
                "imageSourceLabelSha256": source_sha,
                "openapiSha256": "5" * 64,
                "candidateBindingRequired": True,
                "arbitraryTargetsAccepted": False,
            }
            path = root / f"{name}.json"
            path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
            capabilities[name] = {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "sourceSha256": source_sha,
                "image": images[contract["imageKey"]],
            }
        workload_images = {
            "coordinator-active": "coordinator", "coordinator-standby": "coordinator",
            "indexer": "coordinator", "headless-node": "headlessNode",
            "active-witness-a": "frontDoor", "active-witness-b": "frontDoor",
            "fault-control": "faultController", "backup-restore-control": "backupRestoreController",
            "failover-provider": "failoverProvider", "prover": "prover", "logs": "loki",
            "postgres-primary": "postgres", "postgres-standby": "postgres",
        }
        workloads = {
            name: {
                "kind": "docker", "imageKey": image_key, "imageRef": images[image_key],
                "runtimeId": "sha256:" + ("6" if name != "postgres-standby" else "7") * 64,
                "identity": name, "ready": True, "private": True, "chainId": None,
            }
            for name, image_key in workload_images.items()
        }
        workloads.update({
            "l1-rpc-a": {
                "kind": "external", "imageKey": None, "imageRef": None, "runtimeId": None,
                "identity": "independent-provider-a", "ready": True, "private": True, "chainId": 1,
            },
            "l1-rpc-b": {
                "kind": "external", "imageKey": None, "imageRef": None, "runtimeId": None,
                "identity": "independent-provider-b", "ready": True, "private": True, "chainId": 1,
            },
        })
        endpoint_workloads = {
            "coordinator": "coordinator-active", "indexer": "indexer",
            "queue": "coordinator-active", "headless": "headless-node",
            "l1_rpc_a": "l1-rpc-a", "l1_rpc_b": "l1-rpc-b",
            "fault": "fault-control", "backup": "backup-restore-control",
            "failover": "failover-provider", "prover": "prover", "logs": "logs",
        }
        ports = {
            "coordinator": 3000, "indexer": 3001, "queue": 3000, "headless": 3005,
            "l1_rpc_a": 8545, "l1_rpc_b": 8546, "fault": 3010,
            "backup": 3011, "failover": 3012, "prover": 8080, "logs": 3100,
        }
        value = {
            "schemaVersion": 1,
            "candidateId": candidate["candidateId"],
            "candidateDescriptorSha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
            "hostedIntegrationToken": token,
            "ownerDatabaseSchema": 26,
            "platform": "compose",
            "bridge": {
                "mode": "docker-desktop-host-gateway", "networkIdentity": "candidate-private",
                "privateOnly": True, "lanExposed": False, "removeAfterRun": True,
                "candidateStackShared": True,
            },
            "adapterCapabilities": capabilities,
            "endpoints": {
                name: {"url": f"http://host.docker.internal:{ports[name]}", "workload": workload}
                for name, workload in endpoint_workloads.items()
            },
            "workloads": workloads,
            "databaseHa": {
                "primaryWorkload": "postgres-primary", "standbyWorkload": "postgres-standby",
                "independentInstances": True, "targetLsn": "0/100", "replayLsn": "0/101",
                "caughtUp": True, "formerPrimaryFenced": True,
            },
            "failoverControl": {
                "activeCoordinatorId": "coordinator-active-001",
                "standbyCoordinatorId": "coordinator-standby-001",
                "activeWitnesses": [
                    {"url": "http://witness-a.internal:8080/hosting/v1/ready", "workload": "active-witness-a"},
                    {"url": "http://witness-b.internal:8080/hosting/v1/ready", "workload": "active-witness-b"},
                ],
                "standbyHealth": {
                    "url": "http://standby.internal:3000/hosting/v1/health",
                    "workload": "coordinator-standby",
                },
                "promotionEndpoint": {
                    "url": "http://standby.internal:3000/hosting/v1/admin/promote",
                    "workload": "coordinator-standby",
                },
                "failureThreshold": 3,
                "maxRtoSeconds": 300,
                "independentWitnesses": True,
                "scopedApprovalRequired": True,
                "standbySignerlessBeforeFence": True,
            },
        }
        topology_path = root / "topology.json"
        topology_path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        return candidate_path, topology_path, value

    def replication_workloads(self) -> tuple[dict, dict, dict]:
        database = {
            "primaryWorkload": "postgres-primary", "standbyWorkload": "postgres-standby",
            "independentInstances": True, "targetLsn": "0/100", "replayLsn": "0/101",
            "caughtUp": True, "formerPrimaryFenced": True,
        }
        primary = {"identity": "postgres-primary"}
        standby = {"identity": "postgres-standby"}
        return database, primary, standby

    def fake_replication_runner(
        self,
        recovery: dict[str, str],
        receiver_status: str = "streaming",
        replication_rows: str = "streaming|async|0/5000148\n",
        calls: list | None = None,
    ):
        def run(command: list[str], label: str) -> str:
            if calls is not None:
                calls.append((list(command), label))
            shell = command[-1]
            identity = command[2] if command[0] == "docker" else command[4]
            if "pg_stat_replication" in shell:
                return replication_rows
            if "pg_stat_wal_receiver" in shell:
                return f"{receiver_status}|0/5000148|0/5000148|0/5000148\n"
            return recovery[identity] + "\n"
        return run

    def test_static_topology_binds_private_endpoints_images_capabilities_and_ha(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            candidate_path, topology_path, _value = self.fixture(root)
            with patch.object(topology, "ROOT", root):
                result = topology.validate(candidate_path, topology_path, inspect_live=False)
            self.assertTrue(result["passed"])
            self.assertFalse(result["fixtureEndpointsAccepted"])
            self.assertFalse(result["standaloneQueueAccepted"])
            self.assertFalse(result["sharedSinglePostgresAccepted"])
            self.assertEqual(set(result["adapterCapabilities"]), {"faultControl", "backupRestore"})
            self.assertEqual(len(result["failoverControl"]["activeWitnesses"]), 2)
            self.assertIn("headless", result["endpoints"])
            # A static receipt never carries live replication claims.
            self.assertIsNone(result["replicationInspection"])
            self.assertFalse(result["liveReplicationInspected"])

    def test_live_replication_inspection_proves_streaming_pair(self):
        database, primary, standby = self.replication_workloads()
        calls: list = []
        runner = self.fake_replication_runner(
            {"postgres-primary": "f", "postgres-standby": "t"}, calls=calls,
        )
        section = topology.inspect_replication(database, primary, standby, "compose", runner)
        self.assertFalse(section["primaryInRecovery"])
        self.assertTrue(section["standbyInRecovery"])
        self.assertEqual(section["streamingState"], "streaming")
        self.assertEqual(section["syncState"], "async")
        self.assertEqual(section["replayLsn"], "0/5000148")
        self.assertEqual(section["standbyWalReceiver"]["status"], "streaming")
        self.assertFalse(section["staticClaimsAccepted"])
        # The receipt records the exact commands and capture timestamps.
        self.assertEqual(
            section["commands"]["primaryRecovery"][:3], ["docker", "exec", "postgres-primary"],
        )
        self.assertEqual(
            section["commands"]["standbyWalReceiver"][:3], ["docker", "exec", "postgres-standby"],
        )
        self.assertIn("pg_is_in_recovery", section["queries"]["recovery"])
        self.assertIn("pg_stat_replication", section["queries"]["primaryReplication"])
        self.assertIn("pg_stat_wal_receiver", section["queries"]["standbyWalReceiver"])
        self.assertTrue(section["capturedAt"]["primary"])
        self.assertTrue(section["capturedAt"]["standby"])
        self.assertEqual(len(calls), 4)

    def test_wrong_role_and_not_streaming_replication_fail_closed(self):
        database, primary, standby = self.replication_workloads()
        # Both instances answer as writable primaries: the standby claim is false.
        runner = self.fake_replication_runner({"postgres-primary": "f", "postgres-standby": "f"})
        with self.assertRaisesRegex(DeploymentError, "standby is not in recovery"):
            topology.inspect_replication(database, primary, standby, "compose", runner)
        # The primary itself is in recovery: no writable primary exists.
        runner = self.fake_replication_runner({"postgres-primary": "t", "postgres-standby": "t"})
        with self.assertRaisesRegex(DeploymentError, "primary reports pg_is_in_recovery"):
            topology.inspect_replication(database, primary, standby, "compose", runner)
        # The standby WAL receiver is present but not streaming.
        runner = self.fake_replication_runner(
            {"postgres-primary": "f", "postgres-standby": "t"}, receiver_status="stopping",
        )
        with self.assertRaisesRegex(DeploymentError, "WAL receiver is not streaming"):
            topology.inspect_replication(database, primary, standby, "compose", runner)
        # The primary shows no streaming standby in pg_stat_replication.
        runner = self.fake_replication_runner(
            {"postgres-primary": "f", "postgres-standby": "t"}, replication_rows="",
        )
        with self.assertRaisesRegex(DeploymentError, "no streaming standby"):
            topology.inspect_replication(database, primary, standby, "compose", runner)

    def test_missing_replication_inspection_fails_closed_for_ha_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            candidate_path, topology_path, _value = self.fixture(root)
            fake_docker = lambda workload, network: {  # noqa: E731
                "kind": "docker", "identity": workload["identity"],
            }
            with patch.object(topology, "ROOT", root), \
                    patch.object(topology, "inspect_docker", fake_docker), \
                    patch.object(topology, "inspect_replication", lambda *args, **kwargs: None), \
                    self.assertRaisesRegex(DeploymentError, "replicationInspection section"):
                topology.validate(candidate_path, topology_path, inspect_live=True)

    def test_live_validate_records_replication_inspection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            candidate_path, topology_path, _value = self.fixture(root)
            fake_docker = lambda workload, network: {  # noqa: E731
                "kind": "docker", "identity": workload["identity"],
            }
            runner = self.fake_replication_runner({"postgres-primary": "f", "postgres-standby": "t"})
            with patch.object(topology, "ROOT", root), \
                    patch.object(topology, "inspect_docker", fake_docker):
                result = topology.validate(
                    candidate_path, topology_path, inspect_live=True, runner=runner,
                )
            self.assertTrue(result["passed"])
            self.assertTrue(result["liveReplicationInspected"])
            section = result["replicationInspection"]
            self.assertFalse(section["primaryInRecovery"])
            self.assertTrue(section["standbyInRecovery"])
            self.assertEqual(section["streamingState"], "streaming")

    def test_replication_exec_command_construction_per_platform(self):
        compose = topology.replication_exec_command(
            "compose", "postgres-primary", topology.RECOVERY_SQL,
        )
        self.assertEqual(compose[:3], ["docker", "exec", "postgres-primary"])
        self.assertEqual(compose[3:5], ["sh", "-c"])
        self.assertIn("pg_is_in_recovery", compose[-1])
        self.assertIn('--username "$POSTGRES_USER"', compose[-1])
        self.assertIn('--dbname "$POSTGRES_DB"', compose[-1])
        kube = topology.replication_exec_command(
            "kubernetes", "zkdeal/postgres-standby-0/postgres", topology.STANDBY_WAL_RECEIVER_SQL,
        )
        self.assertEqual(
            kube[:8],
            ["kubectl", "exec", "-n", "zkdeal", "postgres-standby-0", "-c", "postgres", "--"],
        )
        self.assertEqual(kube[8:10], ["sh", "-c"])
        self.assertIn("pg_stat_wal_receiver", kube[-1])
        with self.assertRaisesRegex(DeploymentError, "safe container name"):
            topology.replication_exec_command("compose", "bad/name", topology.RECOVERY_SQL)
        with self.assertRaisesRegex(DeploymentError, "namespace/pod/container"):
            topology.replication_exec_command("kubernetes", "just-a-name", topology.RECOVERY_SQL)

    def test_public_endpoint_stale_standby_and_missing_adapter_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            candidate_path, topology_path, value = self.fixture(root)
            value["endpoints"]["fault"]["url"] = "https://fault.example.com"
            topology_path.write_text(json.dumps(value), encoding="utf-8")
            with patch.object(topology, "ROOT", root), self.assertRaisesRegex(DeploymentError, "private"):
                topology.validate(candidate_path, topology_path, inspect_live=False)

            candidate_path, topology_path, value = self.fixture(root)
            value["databaseHa"]["replayLsn"] = "0/FF"
            value["databaseHa"]["targetLsn"] = "1/0"
            topology_path.write_text(json.dumps(value), encoding="utf-8")
            with patch.object(topology, "ROOT", root), self.assertRaisesRegex(DeploymentError, "behind"):
                topology.validate(candidate_path, topology_path, inspect_live=False)

            candidate_path, topology_path, value = self.fixture(root)
            capability = root / value["adapterCapabilities"]["faultControl"]["path"]
            capability_value = json.loads(capability.read_text(encoding="utf-8"))
            capability_value["productionReady"] = False
            capability.write_text(json.dumps(capability_value), encoding="utf-8")
            value["adapterCapabilities"]["faultControl"]["sha256"] = hashlib.sha256(capability.read_bytes()).hexdigest()
            topology_path.write_text(json.dumps(value), encoding="utf-8")
            with patch.object(topology, "ROOT", root), self.assertRaisesRegex(DeploymentError, "productionReady"):
                topology.validate(candidate_path, topology_path, inspect_live=False)

    def test_checked_in_example_is_intentionally_fail_closed(self):
        example = json.loads((ROOT / "config/candidate-private-topology.example.json").read_text(encoding="utf-8"))
        self.assertFalse(example["bridge"]["privateOnly"])
        self.assertTrue(example["bridge"]["lanExposed"])
        self.assertFalse(example["databaseHa"]["caughtUp"])
        self.assertIsNone(example["adapterCapabilities"]["faultControl"]["image"])
        self.assertFalse(example["failoverControl"]["independentWitnesses"])

    def test_witness_and_runtime_identity_bypasses_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            candidate_path, topology_path, value = self.fixture(root)
            value["failoverControl"]["activeWitnesses"][1]["url"] = value["failoverControl"]["activeWitnesses"][0]["url"]
            topology_path.write_text(json.dumps(value), encoding="utf-8")
            with patch.object(topology, "ROOT", root), self.assertRaisesRegex(DeploymentError, "distinct private authorities"):
                topology.validate(candidate_path, topology_path, inspect_live=False)

            candidate_path, topology_path, value = self.fixture(root)
            value["workloads"]["headless-node"]["runtimeId"] = None
            topology_path.write_text(json.dumps(value), encoding="utf-8")
            with patch.object(topology, "ROOT", root), self.assertRaisesRegex(DeploymentError, "runtime binding"):
                topology.validate(candidate_path, topology_path, inspect_live=False)


if __name__ == "__main__":
    unittest.main()
