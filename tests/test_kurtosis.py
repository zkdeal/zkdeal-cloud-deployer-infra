from __future__ import annotations

import importlib.util
import json
import hashlib
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from common import DeploymentError  # noqa: E402
from kurtosis_run import (  # noqa: E402
    ACCEPTANCE_ASSERTIONS, ACCEPTANCE_ENDPOINTS, REQUIRED_IMAGES, SOAK_AUTH,
    acceptance_module, load_and_validate, materialize_acceptance_payload,
    materialize_failover_payload, materialize_soak_payload,
)
import candidate_topology  # noqa: E402


def load_runner(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SOAK_RUNNER = load_runner("zkdeal_soak_kurtosis_join", ROOT / "soak-runner/zkdeal_soak.py")
FAILOVER_RUNNER = load_runner(
    "zkdeal_failover_kurtosis_join", ROOT / "failover-runner/zkdeal_failover.py",
)


def star_endpoint_env(package: str) -> dict[str, str]:
    main = (ROOT / "kurtosis" / package / "main.star").read_text(encoding="utf-8")
    block = main.split("ENDPOINT_ENV = {", 1)[1].split("}", 1)[0]
    return dict(re.findall(r'"([a-z0-9_]+)": "([A-Z0-9_]+)"', block))


def topology_receipt(candidate_id: str, hosted_token: str, schema: int = 26) -> dict:
    endpoints = {
        "coordinator": "http://coordinator-active.internal:3000",
        "queue": "http://coordinator-active.internal:3000",
        "indexer": "http://indexer.internal:3001",
        "headless": "http://headless-room-node.internal:3100",
        "l1_rpc_a": "http://rpc-a.internal:8545",
        "l1_rpc_b": "http://rpc-b.internal:8545",
        "fault": "http://fault-control.internal:3010",
        "backup": "http://backup-restore-control.internal:3011",
        "failover": "http://failover-provider.internal:8080",
        "prover": "http://prover.internal:8080",
        "logs": "http://logs.internal:3100",
    }
    return {
        "schema": "zkdeal/candidate-private-topology-verification/v1",
        "passed": True,
        "candidateId": candidate_id,
        "candidateDescriptorSha256": "b" * 64,
        "topologySha256": "c" * 64,
        "hostedIntegrationToken": hosted_token,
        "ownerDatabaseSchema": schema,
        "platform": "compose",
        "bridge": {"networkIdentity": "zkdeal-candidate"},
        "endpoints": endpoints,
        "failoverControl": {
            "activeCoordinatorId": "candidate-active",
            "standbyCoordinatorId": "candidate-standby",
            "activeWitnesses": [
                {"url": "http://witness-a.internal:8443/hosting/v1/ready", "workload": "active-witness-a"},
                {"url": "http://witness-b.internal:8443/hosting/v1/ready", "workload": "active-witness-b"},
            ],
            "standbyHealth": {
                "url": "http://coordinator-standby.internal:3000/hosting/v1/health",
                "workload": "coordinator-standby",
            },
            "promotionEndpoint": {
                "url": "http://coordinator-standby.internal:3000/hosting/v1/admin/promote",
                "workload": "coordinator-standby",
            },
            "failureThreshold": 3,
            "maxRtoSeconds": 300,
            "independentWitnesses": True,
            "scopedApprovalRequired": True,
            "standbySignerlessBeforeFence": True,
        },
        "adapterCapabilities": {},
        "workloads": {},
        "databaseHa": {"targetLsn": "0/1000", "replayLsn": "0/1001",
                       "primaryWorkload": "postgres-primary", "standbyWorkload": "postgres-standby"},
        "fixtureEndpointsAccepted": False,
        "standaloneQueueAccepted": False,
        "sharedSinglePostgresAccepted": False,
        "credentialValuesRecorded": False,
    }


def write_json(path: Path, value: dict) -> str:
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def release_soak_manifest(hosted_token: str) -> dict:
    digest = "registry.company.tld/zkdeal/%s@sha256:" + "2" * 64
    return {
        "schemaVersion": 1,
        "kind": "zkdeal-release-soak",
        "durationSeconds": 43200,
        "umbrellaSourceManifestSha256": "3" * 64,
        "sourceBundleArchiveSha256": "4" * 64,
        "sourceClosureSha256": "5" * 64,
        "physicalScenario": {
            "settlementScenarioSha256": "6" * 64,
            "deploymentAddressesSha256": "7" * 64,
            "ownerDurableCapabilitiesSha256": "8" * 64,
            "ownerAcceptanceToken": hosted_token,
            "hostedBatchInput": "BatchInputV5",
            "fixturePrepare": False,
            "realCudaProof": True,
            "aggregateMembers": 8,
            "transactionBlobs": 6,
            "partialSuccessApplied": 7,
            "partialSuccessFailed": 1,
            "successOnlyCharging": True,
            "withdrawalClaim": True,
            "sponsorship": True,
            "preFinalityReorg": True,
            "freshDeployment": True,
            "restartResume": True,
            "ownerDurablePublishing": True,
            "castEncodingOnly": True,
            "directBroadcastAllowed": False,
        },
        "images": {
            role: digest % role
            for role in (
                "coordinator", "indexer", "reconciler", "headless", "prover",
                "ownerAcceptanceRunner",
            )
        },
        "trustRoots": {
            "contractsAbiSha256": "9" * 64,
            "circuitManifestSha256": "a" * 64,
            "zkvmArtifactsSha256": "b" * 64,
            "generatedTrustRootClosureSha256": "c" * 64,
        },
        "chainSeed": {
            "chainId": 31337,
            "genesisHash": "0x" + "d" * 64,
            "seedSha256": "e" * 64,
            "rpcEndpoints": ["http://rpc-a.internal:8545", "http://rpc-b.internal:8545"],
        },
        "expected": {"usageUnits": 7, "chargesWei": "700"},
        "budgets": {
            "maxUnresolvedSafetyEvents": 0, "maxUnresolvedClaims": 0,
            "maxDuplicateNonces": 0, "maxDuplicateCharges": 0,
            "maxFairnessWaitMs": 60000, "maxDeadlineMisses": 0,
        },
        "scheduledFaults": [
            {"kind": kind, "atSecond": index * 3600}
            for index, kind in enumerate((
                "headless-restart", "prover-restart", "coordinator-promotion",
                "indexer-rollback", "rpc-split", "object-store-restart",
                "database-restart", "docker-host-restart-resume",
            ))
        ],
    }


class KurtosisScenarioTests(unittest.TestCase):
    def valid_payload(self, package: str, images: dict[str, str], folder: Path) -> dict:
        payload = json.loads((ROOT / "kurtosis" / package / "args.example.json").read_text(encoding="utf-8"))
        payload["images"] = images
        if package == "acceptance-matrix":
            verifier = acceptance_module()
            aliases = (
                "admin", "fault_control", "backup_restore", "failover_control", "failover_approval",
            )
            tokens = {alias: "eph_" + character * 40 for alias, character in zip(aliases, "abcde")}
            token_paths = {}
            for alias, token in tokens.items():
                token_path = folder / f"{alias}.token"
                token_path.write_text(token + "\n", encoding="utf-8")
                token_paths[alias] = token_path
            scenarios = {}
            for scenario in ACCEPTANCE_ASSERTIONS:
                by_endpoint = {}
                evidence = {}
                for field, sources in verifier.SOURCE_RULES[scenario].items():
                    endpoint = sorted(sources)[0]
                    by_endpoint.setdefault(endpoint, []).append(field)
                steps = []
                for index, (endpoint, fields) in enumerate(sorted(by_endpoint.items())):
                    step_id = f"source-{index:02d}"
                    auth = {
                        "fault": "fault_control",
                        "backup": "backup_restore",
                        "failover": "failover_control",
                    }.get(endpoint, "admin")
                    step = {
                        "id": step_id, "endpoint": endpoint, "method": "GET",
                        "path": f"/acceptance/{scenario}/{endpoint}", "auth": auth,
                        "expectStatus": [200],
                    }
                    if endpoint == "failover":
                        step["method"] = "POST"
                        step["approvalAuth"] = "failover_approval"
                    steps.append(step)
                    for field in fields:
                        evidence[field] = {"step": step_id, "pointer": f"/body/{field}"}
                scenarios[scenario] = {"steps": steps, "evidence": evidence}
            plan = {
                "schemaVersion": 1,
                "candidateId": "candidate-acceptance-test-0001",
                "hostedIntegrationToken": "sha256:" + "a" * 64,
                "ownerDatabaseSchema": 26,
                "authTokenSha256": {
                    alias: hashlib.sha256(token.encode()).hexdigest()
                    for alias, token in tokens.items()
                },
                "scenarios": scenarios,
            }
            raw = (json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n").encode()
            plan_path = folder / "acceptance-plan.json"
            plan_path.write_bytes(raw)
            receipt = topology_receipt(plan["candidateId"], plan["hostedIntegrationToken"])
            receipt_path = folder / "topology-verification.json"
            receipt_sha = write_json(receipt_path, receipt)
            payload.update({
                "candidate_id": plan["candidateId"],
                "hosted_integration_token": plan["hostedIntegrationToken"],
                "topology_verification_file": str(receipt_path),
                "topology_verification_sha256": receipt_sha,
                "plan_file": str(plan_path),
                "plan_sha256": hashlib.sha256(raw).hexdigest(),
                "auth_files": {alias: str(path) for alias, path in token_paths.items()},
            })
        return payload

    def test_pinned_kurtosis_cli_and_container_lint_gate_exist(self):
        dockerfile = (ROOT / "tools/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("kurtosis-cli_1.20.0_linux_amd64.tar.gz", dockerfile)
        self.assertIn("3866e805a83a63d3d0b177f54c3fb556066b8c0e12f6442a0579bb7deaa1d5ae", dockerfile)
        gate = (ROOT / "tests/acceptance/kurtosis-lint.sh").read_text(encoding="utf-8")
        self.assertIn('kurtosis lint "kurtosis/$package"', gate)

    def test_scenarios_have_packages_health_waits_and_args(self):
        for name in ("local", "failover", "soak", "acceptance-matrix"):
            folder = ROOT / "kurtosis" / name
            with self.subTest(name=name):
                self.assertTrue((folder / "kurtosis.yml").is_file())
                self.assertTrue((folder / "args.example.json").is_file())
                main = (folder / "main.star").read_text(encoding="utf-8")
                self.assertIn("def run(plan, args={})", main)
                self.assertIn("args.images", main)
                if name == "local":
                    self.assertIn("plan.wait(", main)
                else:
                    # Assertion packages start only their runner against the
                    # verified candidate stack and store write-once evidence.
                    self.assertIn("plan.run_sh(", main)
                    self.assertIn('StoreSpec(src="/evidence"', main)
                    self.assertIn("candidate-topology-expanded-v1", main)
                    self.assertIn('"candidate_stack_started_by_package": False', main)

    def test_every_package_exactly_digest_gates_all_images(self):
        for name in REQUIRED_IMAGES:
            main = (ROOT / "kurtosis" / name / "main.star").read_text(encoding="utf-8")
            with self.subTest(package=name):
                self.assertIn("def _require_digest_image", main)
                self.assertIn('len(digest) != 64', main)
                self.assertIn('character not in HEX', main)
                self.assertIn('registry.invalid', main)
                self.assertNotIn('if "@sha256:" not in', main)

    def test_launcher_rejects_mutable_placeholder_and_malformed_images(self):
        digest = "1" * 64
        valid = f"registry.company.tld/zkdeal/artifact@sha256:{digest}"
        invalid = (
            "zkdeal-coordinator:local",
            "registry.company.tld/zkdeal/coordinator:latest",
            f"registry.company.tld/zkdeal/coordinator:latest@sha256:{digest}",
            "registry.company.tld/zkdeal/coordinator@sha256:REPLACE",
            f"registry.invalid/zkdeal/coordinator@sha256:{digest}",
            "registry.company.tld/zkdeal/coordinator@sha256:" + "1" * 63,
        )
        with tempfile.TemporaryDirectory() as folder:
            args_path = Path(folder) / "args.json"
            for package, names in REQUIRED_IMAGES.items():
                images = {name: valid for name in names}
                payload = self.valid_payload(package, images, Path(folder))
                args_path.write_text(json.dumps(payload), encoding="utf-8")
                self.assertEqual(load_and_validate(package, args_path)["images"], images)
                for bad in invalid:
                    broken = dict(images)
                    broken[names[0]] = bad
                    broken_payload = self.valid_payload(package, broken, Path(folder))
                    args_path.write_text(json.dumps(broken_payload), encoding="utf-8")
                    with self.subTest(package=package, reference=bad), self.assertRaises(DeploymentError):
                        load_and_validate(package, args_path)

    def test_launcher_rejects_unknown_args_and_missing_keys(self):
        digest = "1" * 64
        valid = f"registry.company.tld/zkdeal/artifact@sha256:{digest}"
        with tempfile.TemporaryDirectory() as folder:
            args_path = Path(folder) / "args.json"
            for package, names in REQUIRED_IMAGES.items():
                images = {name: valid for name in names}
                payload = self.valid_payload(package, images, Path(folder))
                payload["unexpected_extension"] = True
                args_path.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(package=package, case="extra"), self.assertRaises(DeploymentError):
                    load_and_validate(package, args_path)
                payload = self.valid_payload(package, images, Path(folder))
                payload["images"] = dict(images, hitchhiker=valid)
                args_path.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(package=package, case="image"), self.assertRaises(DeploymentError):
                    load_and_validate(package, args_path)

    def test_launcher_rejects_noop_or_shell_injected_assertion_commands(self):
        digest = "1" * 64
        valid = f"registry.company.tld/zkdeal/artifact@sha256:{digest}"
        with tempfile.TemporaryDirectory() as folder:
            args_path = Path(folder) / "args.json"
            for package in ("failover", "soak", "acceptance-matrix"):
                images = {name: valid for name in REQUIRED_IMAGES[package]}
                payload = self.valid_payload(package, images, Path(folder))
                if package == "acceptance-matrix":
                    payload["commands"]["reorg"] = "true"
                else:
                    payload[f"{package}_command"] = "true"
                args_path.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(package=package), self.assertRaises(DeploymentError):
                    load_and_validate(package, args_path)

                payload = self.valid_payload(package, images, Path(folder))
                if package == "acceptance-matrix":
                    payload["commands"]["reorg"] += "; true"
                else:
                    payload[f"{package}_command"] += "; true"
                args_path.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(package=package, injection=True), self.assertRaises(DeploymentError):
                    load_and_validate(package, args_path)

    def test_local_package_provisions_hosted_plane_without_standalone_queue(self):
        main = (ROOT / "kurtosis/local/main.star").read_text(encoding="utf-8")
        for service in (
            '"postgres"', '"minio"', '"coordinator"', '"indexer"',
            '"headless-room-node"', '"prover-agent"',
        ):
            self.assertIn("name=%s" % service, main)
        for marker in (
            "pg_isready", "/minio/health/live", "/hosting/v1/ready",
            "DATABASE_URL", "OBJECT_STORE_ENDPOINT", "dist/hosted-worker.js",
            '"QUEUE_ENABLED": "0"', '"DEMO_ENABLED": "0"',
            '"standalone_queue_started": False', '"release_evidence": False',
            "declared-fixture", "gpu-prover",
        ):
            self.assertIn(marker, main)
        self.assertNotIn("standalone.ts", main)
        # The hosted queue is the coordinator authority in the return value.
        self.assertIn('"queue": coordinator_url', main)
        self.assertIn('"coordinator": coordinator_url', main)
        example = json.loads((ROOT / "kurtosis/local/args.example.json").read_text(encoding="utf-8"))
        self.assertEqual(set(example["images"]), set(REQUIRED_IMAGES["local"]))
        self.assertNotIn("queue", example["images"])

    def test_failover_package_binds_verified_replicated_topology_and_runner(self):
        main = (ROOT / "kurtosis/failover/main.star").read_text(encoding="utf-8")
        for value in (
            "failover_runner", "failover_command", "candidate-topology-expanded-v1",
            "PROMOTION_CANDIDATE_ID", "ACTIVE_HEALTH_URLS", "STANDBY_HEALTH_URL",
            "PROMOTION_ENDPOINT", "FAILOVER_PROVIDER_OPERATION_URL",
            "PROMOTION_CONTROLLER_STATE_PATH", "CANDIDATE_TOPOLOGY_VERIFICATION_SHA256",
            '"ALL_FAILOVER_ASSERTIONS_PASSED"',
            '"candidate_stack_started_by_package": False',
            '"shared_single_postgres_accepted": False',
        ):
            self.assertIn(value, main)
        self.assertNotIn('"READY_FOR_FAILOVER_REHEARSAL"', main)
        # The candidate-topology contract itself must prove the replicated
        # primary/standby PostgreSQL pair with an observable replay LSN.
        self.assertIn("postgres-primary", candidate_topology.REQUIRED_WORKLOADS)
        self.assertIn("postgres-standby", candidate_topology.REQUIRED_WORKLOADS)
        topology_source = (ROOT / "scripts/candidate_topology.py").read_text(encoding="utf-8")
        for marker in ("targetLsn", "replayLsn", "standby replay is behind"):
            self.assertIn(marker, topology_source)

    def test_topology_contract_supplies_first_party_fault_and_backup_controllers(self):
        # The acceptance/failover/soak packages consume ACCEPTANCE_FAULT_URL
        # and ACCEPTANCE_BACKUP_URL; the candidate-topology contract must bind
        # those endpoints to the first-party adapter images and their stable
        # HTTP capability protocols.
        capabilities = candidate_topology.CAPABILITIES
        self.assertEqual(
            capabilities["faultControl"]["service"], "zkdeal-fault-control-adapter",
        )
        self.assertEqual(capabilities["faultControl"]["protocol"], "fault-control-v1")
        self.assertEqual(capabilities["faultControl"]["imageKey"], "faultController")
        self.assertEqual(
            capabilities["backupRestore"]["service"], "zkdeal-backup-restore-control-adapter",
        )
        self.assertEqual(capabilities["backupRestore"]["protocol"], "backup-restore-control-v1")
        self.assertEqual(capabilities["backupRestore"]["imageKey"], "backupRestoreController")
        expected_images = candidate_topology.EXPECTED_IMAGE_KEYS
        self.assertEqual(expected_images["fault"], "faultController")
        self.assertEqual(expected_images["backup"], "backupRestoreController")
        for workload in (
            "fault-control", "backup-restore-control", "failover-provider",
            "l1-rpc-a", "l1-rpc-b", "prover", "logs",
        ):
            self.assertIn(workload, candidate_topology.REQUIRED_WORKLOADS)
        self.assertTrue((ROOT / "fault-control/Dockerfile").is_file())
        self.assertTrue((ROOT / "backup-restore-control/Dockerfile").is_file())

    def test_star_endpoint_env_joins_runner_and_topology_contracts(self):
        verifier = acceptance_module()
        runner_env_names = set(verifier.ENDPOINT_ENV.values())
        reference = star_endpoint_env("failover")
        for package in ("failover", "soak", "acceptance-matrix"):
            with self.subTest(package=package):
                env = star_endpoint_env(package)
                self.assertEqual(env, reference)
                # Every endpoint the packages consume is provisioned by the
                # verified candidate topology, and every environment variable
                # they export is one the runner actually reads.
                self.assertEqual(set(env), set(candidate_topology.ENDPOINTS))
                self.assertEqual(set(env), ACCEPTANCE_ENDPOINTS)
                self.assertEqual(set(env.values()), runner_env_names)

    def test_example_runner_commands_parse_with_the_actual_runner_parsers(self):
        soak_example = json.loads((ROOT / "kurtosis/soak/args.example.json").read_text(encoding="utf-8"))
        soak_argv = soak_example["soak_command"].split()
        self.assertEqual(soak_argv[0], "/opt/zkdeal-soak")
        parsed = SOAK_RUNNER.parser().parse_args(soak_argv[1:])
        self.assertTrue(parsed.restart)
        self.assertTrue(parsed.assert_durable)

        failover_example = json.loads((ROOT / "kurtosis/failover/args.example.json").read_text(encoding="utf-8"))
        failover_argv = failover_example["failover_command"].split()
        self.assertEqual(failover_argv[0], "/opt/zkdeal-failover")
        parsed = FAILOVER_RUNNER.parser().parse_args(failover_argv[1:])
        self.assertEqual(parsed.assert_rto_seconds, 300)

        verifier = acceptance_module()
        acceptance_example = json.loads(
            (ROOT / "kurtosis/acceptance-matrix/args.example.json").read_text(encoding="utf-8"),
        )
        self.assertEqual(set(acceptance_example["commands"]), set(ACCEPTANCE_ASSERTIONS))
        for scenario, command in acceptance_example["commands"].items():
            argv = command.split()
            self.assertEqual(argv[0], "/opt/zkdeal-acceptance")
            with self.subTest(scenario=scenario):
                parsed = verifier.scenario_parser().parse_args(argv[1:])
                self.assertEqual(parsed.scenario, scenario)

    def test_acceptance_matrix_hard_gates_every_requested_fault(self):
        folder = ROOT / "kurtosis/acceptance-matrix"
        self.assertTrue((folder / "kurtosis.yml").is_file())
        self.assertTrue((folder / "args.example.json").is_file())
        main = (folder / "main.star").read_text(encoding="utf-8")
        for scenario in (
            "reorg", "rpc-disagreement", "split-brain-promotion",
            "database-object-restore", "sponsorship", "queue-congestion",
            "headless-restart", "blob", "partial-aggregate", "renewal", "withdrawal",
            "rpc-load-shadow", "sse-load-shadow", "indexer-load-shadow",
            "admission-load-shadow", "scheduler-load-shadow",
            "projection-parity-shadow", "prover-agent-trace-join",
        ):
            self.assertIn(f'"{scenario}"', main)
        self.assertIn("plan.run_sh(", main)
        self.assertIn("plan.render_templates(", main)
        self.assertIn("StoreSpec(src=\"/evidence\"", main)
        self.assertIn("candidate-topology-expanded-v1", main)
        self.assertIn("ACCEPTANCE_PLAN_SHA256", main)
        self.assertIn("ACCEPTANCE_ADMIN_TOKEN_FILE", main)
        self.assertIn("CANDIDATE_TOPOLOGY_VERIFICATION_SHA256", main)
        self.assertIn("@sha256:", main)
        self.assertIn("health_polling_only", main)
        self.assertIn("hosted coordinator queue", main)
        capability = json.loads((ROOT / "acceptance-runner/capability.json").read_text(encoding="utf-8"))
        self.assertEqual(set(capability["scenarios"]), set(ACCEPTANCE_ASSERTIONS))
        for scenario, markers in ACCEPTANCE_ASSERTIONS.items():
            self.assertEqual(capability["scenarios"][scenario], list(markers))
        dockerfile = (ROOT / "acceptance-runner/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("/opt/zkdeal/capability.json", dockerfile)

    def test_acceptance_launcher_binds_plan_role_tokens_and_verified_topology(self):
        digest = "1" * 64
        image = f"registry.company.tld/zkdeal/acceptance@sha256:{digest}"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args_path = root / "args.json"
            payload = self.valid_payload("acceptance-matrix", {"acceptance_runner": image}, root)
            args_path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_and_validate("acceptance-matrix", args_path)
            expanded = materialize_acceptance_payload(loaded, args_path)
            self.assertEqual(expanded["input_contract"], "candidate-topology-expanded-v1")
            self.assertEqual(set(expanded["scenario_auth"]), set(ACCEPTANCE_ASSERTIONS))
            self.assertEqual(
                expanded["auth"].keys(),
                {
                    "admin", "fault_control", "backup_restore", "failover_control",
                    "failover_approval",
                },
            )
            self.assertEqual(set(expanded["endpoints"]), ACCEPTANCE_ENDPOINTS)
            self.assertEqual(expanded["endpoints"]["queue"], expanded["endpoints"]["coordinator"])
            self.assertEqual(
                expanded["topology_verification_sha256"], payload["topology_verification_sha256"],
            )
            self.assertNotIn("endpoints", payload)

            broken = dict(payload)
            broken["plan_sha256"] = "0" * 64
            args_path.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaisesRegex(DeploymentError, "plan_sha256"):
                load_and_validate("acceptance-matrix", args_path)

            broken = json.loads(json.dumps(payload))
            receipt = topology_receipt(payload["candidate_id"], payload["hosted_integration_token"])
            receipt["endpoints"]["queue"] = "http://standalone-file-queue.internal:3005"
            broken_receipt = root / "broken-receipt.json"
            broken["topology_verification_file"] = str(broken_receipt)
            broken["topology_verification_sha256"] = write_json(broken_receipt, receipt)
            with self.assertRaisesRegex(DeploymentError, "standalone queue"):
                materialize_acceptance_payload(broken, args_path)

            broken = json.loads(json.dumps(payload))
            receipt = topology_receipt(payload["candidate_id"], payload["hosted_integration_token"])
            receipt["standaloneQueueAccepted"] = True
            broken_receipt = root / "accepting-receipt.json"
            broken["topology_verification_file"] = str(broken_receipt)
            broken["topology_verification_sha256"] = write_json(broken_receipt, receipt)
            with self.assertRaisesRegex(DeploymentError, "release-eligible"):
                materialize_acceptance_payload(broken, args_path)

            broken = json.loads(json.dumps(payload))
            receipt = topology_receipt(payload["candidate_id"], payload["hosted_integration_token"], schema=25)
            broken_receipt = root / "schema-receipt.json"
            broken["topology_verification_file"] = str(broken_receipt)
            broken["topology_verification_sha256"] = write_json(broken_receipt, receipt)
            with self.assertRaisesRegex(DeploymentError, "database schema"):
                materialize_acceptance_payload(broken, args_path)

            token_path = Path(payload["auth_files"]["admin"])
            token_path.write_text("eph_" + "b" * 40 + "\n", encoding="utf-8")
            args_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(DeploymentError, "credential binding"):
                load_and_validate("acceptance-matrix", args_path)

    def test_failover_launcher_expands_verified_topology_and_control_state(self):
        digest = "1" * 64
        image = f"registry.company.tld/zkdeal/failover-runner@sha256:{digest}"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            acceptance = self.valid_payload(
                "acceptance-matrix",
                {"acceptance_runner": f"registry.company.tld/zkdeal/acceptance@sha256:{digest}"},
                root,
            )
            principal = "eph_" + "f" * 40
            principal_path = root / "promotion-principal.token"
            principal_path.write_text(principal, encoding="utf-8")
            for alias, character in (
                ("admin", "a"), ("failover_control", "d"), ("failover_approval", "e"),
            ):
                (root / f"failover-{alias}.token").write_text(
                    "eph_" + character * 40, encoding="utf-8",
                )
            payload = json.loads(
                (ROOT / "kurtosis/failover/args.example.json").read_text(encoding="utf-8"),
            )
            payload.update({
                "images": {"failover_runner": image},
                "candidate_id": acceptance["candidate_id"],
                "hosted_integration_token": acceptance["hosted_integration_token"],
                "topology_verification_file": acceptance["topology_verification_file"],
                "topology_verification_sha256": acceptance["topology_verification_sha256"],
                "plan_file": acceptance["plan_file"],
                "plan_sha256": acceptance["plan_sha256"],
                # The failover/soak materializers require exact token bytes
                # (no trailing whitespace); write dedicated files.
                "auth_files": {
                    alias: str(root / f"failover-{alias}.token")
                    for alias in ("admin", "failover_control", "failover_approval")
                },
                "promotion_principal_file": str(principal_path),
                "promotion_principal_sha256": hashlib.sha256(principal.encode()).hexdigest(),
            })
            args_path = root / "failover-args.json"
            args_path.write_text(json.dumps(payload), encoding="utf-8")
            expanded = materialize_failover_payload(load_and_validate("failover", args_path), args_path)
            self.assertEqual(expanded["input_contract"], "candidate-topology-expanded-v1")
            self.assertEqual(expanded["endpoints"]["queue"], expanded["endpoints"]["coordinator"])
            control = expanded["failover_control"]
            self.assertNotEqual(
                control["active_coordinator_id"], control["standby_coordinator_id"],
            )
            self.assertEqual(len(set(control["active_witness_urls"])), 2)
            self.assertTrue(control["provider_operation_url"].endswith("/v1/failovers"))
            self.assertEqual(expanded["promotion_principal"], principal)
            command = expanded["failover_command"].split()
            parsed = FAILOVER_RUNNER.parser().parse_args(command[1:])
            self.assertLessEqual(parsed.assert_rto_seconds, 300)

    def test_soak_launcher_expands_manifest_owner_command_and_roles(self):
        digest = "1" * 64
        image = f"registry.company.tld/zkdeal/soak-runner@sha256:{digest}"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            candidate_id = "candidate-acceptance-test-0001"
            hosted_token = "sha256:" + "a" * 64
            receipt_path = root / "topology-verification.json"
            receipt_sha = write_json(receipt_path, topology_receipt(candidate_id, hosted_token))
            manifest_path = root / "release-soak-manifest.json"
            manifest_sha = write_json(manifest_path, release_soak_manifest(hosted_token))
            owner_command_path = root / "owner-command.json"
            command_raw = json.dumps(["/opt/zkdeal-owner-soak", "--manifest-bound"]).encode()
            owner_command_path.write_bytes(command_raw)
            auth_files = {}
            auth_hashes = {}
            for index, alias in enumerate(sorted(SOAK_AUTH)):
                token = "eph_" + chr(ord("a") + index) * 40
                token_path = root / f"soak-{alias}.token"
                token_path.write_text(token, encoding="utf-8")
                auth_files[alias] = str(token_path)
                auth_hashes[alias] = hashlib.sha256(token.encode()).hexdigest()
            payload = json.loads((ROOT / "kurtosis/soak/args.example.json").read_text(encoding="utf-8"))
            payload.update({
                "images": {"runner": image},
                "candidate_id": candidate_id,
                "hosted_integration_token": hosted_token,
                "topology_verification_file": str(receipt_path),
                "topology_verification_sha256": receipt_sha,
                "manifest_file": str(manifest_path),
                "manifest_sha256": manifest_sha,
                "owner_command_file": str(owner_command_path),
                "owner_command_sha256": hashlib.sha256(command_raw).hexdigest(),
                "owner_driver_source_sha256": "d" * 64,
                "auth_files": auth_files,
                "auth_token_sha256": auth_hashes,
                "deployment_addresses": {
                    "roomManager": "0x" + "11" * 20,
                    "operationsAccount": "0x" + "22" * 20,
                    "roomPool": "0x" + "33" * 20,
                    "sponsorAccount": "0x" + "44" * 20,
                },
                "duration_seconds": "43200",
            })
            args_path = root / "soak-args.json"
            args_path.write_text(json.dumps(payload), encoding="utf-8")
            expanded = materialize_soak_payload(load_and_validate("soak", args_path), args_path)
            self.assertEqual(expanded["input_contract"], "candidate-topology-expanded-v1")
            self.assertEqual(expanded["endpoints"]["queue"], expanded["endpoints"]["coordinator"])
            self.assertEqual(set(expanded["auth"]), SOAK_AUTH)
            self.assertEqual(
                expanded["owner_driver_image_label_sha256"],
                expanded["owner_driver_source_sha256"],
            )
            # The owner driver's coordinator-promotion fault needs the verified
            # topology identities the failover provider is configured with.
            self.assertEqual(expanded["active_coordinator_id"], "candidate-active")
            self.assertEqual(expanded["standby_coordinator_id"], "candidate-standby")
            self.assertEqual(expanded["failover_witness_count"], "2")
            self.assertEqual(
                expanded["deployment_addresses"],
                {
                    "roomManager": "0x" + "11" * 20,
                    "operationsAccount": "0x" + "22" * 20,
                    "roomPool": "0x" + "33" * 20,
                    "sponsorAccount": "0x" + "44" * 20,
                },
            )
            parsed = SOAK_RUNNER.parser().parse_args(expanded["soak_command"].split()[1:])
            self.assertTrue(parsed.assert_durable)

            broken = dict(payload)
            broken["duration_seconds"] = "3600"
            args_path.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaisesRegex(DeploymentError, "duration"):
                materialize_soak_payload(load_and_validate("soak", args_path), args_path)

    def test_soak_requires_real_jobs_restarts_and_durability(self):
        main = (ROOT / "kurtosis/soak/main.star").read_text(encoding="utf-8")
        for value in (
            "REQUIRE_REAL_PROOF_JOBS", "REQUIRE_INDUCED_RESTARTS",
            "REQUIRE_DURABLE_ASSERTIONS", "REQUIRE_FULL_LIFECYCLE",
            "REQUIRE_APPEND_ONLY_JOURNAL", "REQUIRE_RESTART_RESUME", "soak_command",
        ):
            self.assertIn(value, main)


if __name__ == "__main__":
    unittest.main()
