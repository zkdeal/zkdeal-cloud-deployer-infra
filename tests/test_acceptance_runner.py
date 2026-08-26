from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "acceptance-runner" / "zkdeal_acceptance.py"
SPEC = importlib.util.spec_from_file_location("zkdeal_acceptance", MODULE_PATH)
assert SPEC and SPEC.loader
acceptance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = acceptance
SPEC.loader.exec_module(acceptance)

TEST_TOKEN = "eph_tenant_acceptance_token_00000001"


class FixtureHandler(BaseHTTPRequestHandler):
    responses: dict[str, tuple[int, object]] = {}
    authorization: list[str | None] = []
    approvals: list[str | None] = []

    def _handle(self):
        FixtureHandler.authorization.append(self.headers.get("authorization"))
        FixtureHandler.approvals.append(self.headers.get("x-zkdeal-failover-approval"))
        status, body = self.responses.get(self.path, (404, {"error": "absent"}))
        raw = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle

    def log_message(self, *_args):
        return


@contextmanager
def fixture_server(responses):
    FixtureHandler.responses = responses
    FixtureHandler.authorization = []
    FixtureHandler.approvals = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def aggregate_plan():
    return {
        "schemaVersion": 1,
        "candidateId": "candidate-acceptance-001",
        "hostedIntegrationToken": "sha256:" + "a" * 64,
        "ownerDatabaseSchema": 26,
        "authTokenSha256": {"tenant_a": hashlib.sha256(TEST_TOKEN.encode()).hexdigest()},
        "scenarios": {
            "partial-aggregate": {
                "steps": [
                    {
                        "id": "aggregate-operation", "endpoint": "coordinator", "method": "POST",
                        "path": "/operation", "auth": "tenant_a", "body": {"run": True},
                        "expectStatus": [202],
                    },
                    {
                        "id": "aggregate-outcomes", "endpoint": "indexer", "method": "GET",
                        "path": "/outcomes", "expectStatus": [200],
                    },
                ],
                "evidence": {
                    "memberCount": {"step": "aggregate-operation", "pointer": "/body/memberCount"},
                    "uniqueRooms": {"step": "aggregate-operation", "pointer": "/body/uniqueRooms"},
                    "operationId": {"step": "aggregate-operation", "pointer": "/body/operationId"},
                    "appliedMembers": {"step": "aggregate-outcomes", "pointer": "/body/appliedMembers"},
                    "failedMembers": {"step": "aggregate-outcomes", "pointer": "/body/failedMembers"},
                    "chargedMembers": {"step": "aggregate-outcomes", "pointer": "/body/chargedMembers"},
                    "failedMemberCharges": {"step": "aggregate-outcomes", "pointer": "/body/failedMemberCharges"},
                    "retryApplied": {"step": "aggregate-outcomes", "pointer": "/body/retryApplied"},
                    "finalized": {"step": "aggregate-outcomes", "pointer": "/body/finalized"},
                },
            }
        },
    }


class AcceptanceRunnerTests(unittest.TestCase):
    def test_real_http_steps_join_independent_sources_and_write_once(self):
        responses = {
            "/coordinator/hosting/v1/capabilities": (200, {
                "databaseSchema": 26,
                "managedL1Operations": {
                    "roomBatch": {
                        "enabled": True,
                        "hostedIntegration": {
                            "enabled": True,
                            "acceptanceToken": "sha256:" + "a" * 64,
                        },
                    },
                    "roomAggregate": {"enabled": True},
                    "poolSponsorMutation": {"enabled": True},
                },
            }),
            "/coordinator/operation": (202, {"memberCount": "8", "uniqueRooms": 8, "operationId": "aggregate-operation-001"}),
            "/indexer/outcomes": (200, {
                "appliedMembers": 7, "failedMembers": 1, "chargedMembers": 7,
                "failedMemberCharges": 0, "retryApplied": True, "finalized": True,
            }),
        }
        with fixture_server(responses) as base, tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = aggregate_plan()
            raw = (json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n").encode()
            plan_path = root / "plan.json"
            plan_path.write_bytes(raw)
            token_path = root / "tenant-a.token"
            token_path.write_text(TEST_TOKEN + "\n", encoding="utf-8")
            token_path.chmod(0o600)
            evidence = root / "evidence"
            evidence.mkdir()
            env = {
                "ACCEPTANCE_PLAN_FILE": str(plan_path),
                "ACCEPTANCE_PLAN_SHA256": hashlib.sha256(raw).hexdigest(),
                "ACCEPTANCE_CANDIDATE_ID": plan["candidateId"],
                "HOSTED_INTEGRATION_TOKEN": plan["hostedIntegrationToken"],
                "COORDINATOR_URL": base + "/coordinator",
                "INDEXER_URL": base + "/indexer",
                "ACCEPTANCE_TENANT_A_TOKEN_FILE": str(token_path),
                "ACCEPTANCE_EVIDENCE_DIR": str(evidence),
            }
            with patch.dict(os.environ, env, clear=True):
                result = acceptance.execute(argparse.Namespace(scenario="partial-aggregate"))
                self.assertTrue(result["passed"])
                record = json.loads(Path(result["record"]).read_text(encoding="utf-8"))
                self.assertEqual(record["evidence"]["appliedMembers"], 7)
                self.assertEqual(record["evidence"]["failedMemberCharges"], 0)
                self.assertEqual(
                    record["endpointBindingsSha256"]["coordinator"],
                    hashlib.sha256((base + "/coordinator").encode()).hexdigest(),
                )
                serialized = json.dumps(record)
                self.assertNotIn("eph_tenant_acceptance_token", serialized)
                self.assertIn(f"Bearer {TEST_TOKEN}", FixtureHandler.authorization)
                with self.assertRaisesRegex(acceptance.AcceptanceError, "write-once"):
                    acceptance.execute(argparse.Namespace(scenario="partial-aggregate"))

    def test_plan_hash_and_source_separation_fail_closed(self):
        plan = aggregate_plan()
        plan["scenarios"]["partial-aggregate"]["evidence"]["appliedMembers"] = {
            "step": "aggregate-operation", "pointer": "/body/memberCount",
        }
        with self.assertRaisesRegex(acceptance.AcceptanceError, "cannot trust endpoint coordinator"):
            acceptance.validate_scenario_shape("partial-aggregate", plan["scenarios"]["partial-aggregate"])
        raw = json.dumps(plan).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_bytes(raw)
            with patch.dict(os.environ, {
                "ACCEPTANCE_PLAN_FILE": str(path), "ACCEPTANCE_PLAN_SHA256": "0" * 64,
            }, clear=True):
                with self.assertRaisesRegex(acceptance.AcceptanceError, "SHA-256"):
                    acceptance.load_plan()

    def test_control_endpoints_require_distinct_scoped_authorities(self):
        expected = {
            "fault": "fault_control",
            "backup": "backup_restore",
            "failover": "failover_control",
        }
        for endpoint, authority in expected.items():
            plan = aggregate_plan()
            plan["scenarios"]["partial-aggregate"]["steps"].append({
                "id": f"{endpoint}-control",
                "endpoint": endpoint,
                "method": "POST",
                "path": "/v1/test",
                "auth": "admin",
                "expectStatus": [200],
            })
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(
                acceptance.AcceptanceError, f"scoped {endpoint} authority",
            ):
                acceptance.validate_scenario_shape(
                    "partial-aggregate", plan["scenarios"]["partial-aggregate"],
                )
            plan["scenarios"]["partial-aggregate"]["steps"][-1]["auth"] = authority
            if endpoint == "failover":
                with self.assertRaisesRegex(
                    acceptance.AcceptanceError, "separate failover approval authority",
                ):
                    acceptance.validate_scenario_shape(
                        "partial-aggregate", plan["scenarios"]["partial-aggregate"],
                    )
                plan["scenarios"]["partial-aggregate"]["steps"][-1][
                    "approvalAuth"
                ] = "failover_approval"
            acceptance.validate_scenario_shape(
                "partial-aggregate", plan["scenarios"]["partial-aggregate"],
            )

        schema = json.loads((ROOT / "acceptance-runner" / "scenario-plan.schema.json").read_text())
        aliases = set(schema["properties"]["authTokenSha256"]["propertyNames"]["enum"])
        self.assertTrue({
            "fault_control", "backup_restore", "failover_control", "failover_approval",
        }.issubset(aliases))

        plan = aggregate_plan()
        plan["scenarios"]["partial-aggregate"]["steps"][0]["auth"] = "fault_control"
        with self.assertRaisesRegex(acceptance.AcceptanceError, "cannot send fault_control"):
            acceptance.validate_scenario_shape(
                "partial-aggregate", plan["scenarios"]["partial-aggregate"],
            )

    def test_live_owner_capability_preflight_rejects_stale_source_token(self):
        responses = {
            "/hosting/v1/capabilities": (200, {
                "databaseSchema": 26,
                "managedL1Operations": {
                    "roomBatch": {
                        "enabled": True,
                        "hostedIntegration": {
                            "enabled": True,
                            "acceptanceToken": "sha256:" + "b" * 64,
                        },
                    },
                    "roomAggregate": {"enabled": True},
                    "poolSponsorMutation": {"enabled": True},
                },
            }),
        }
        with fixture_server(responses) as base, patch.dict(os.environ, {"COORDINATOR_URL": base}, clear=True):
            executor = acceptance.HttpExecutor({"coordinator"}, {})
            with self.assertRaisesRegex(acceptance.AcceptanceError, "token differs"):
                executor.verify_candidate(26, "sha256:" + "a" * 64)

    def test_failover_mutation_uses_separate_mounted_control_and_approval_tokens(self):
        control = "eph_failover_control_000000000001"
        approval = "eph_failover_approval_0000000001"
        with fixture_server({"/v1/failovers": (200, {"prepared": True})}) as base:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                control_path = root / "control.token"
                approval_path = root / "approval.token"
                control_path.write_text(control + "\n", encoding="utf-8")
                approval_path.write_text(approval + "\n", encoding="utf-8")
                control_path.chmod(0o600)
                approval_path.chmod(0o600)
                env = {
                    "FAILOVER_PROVIDER_URL": base,
                    "ACCEPTANCE_FAILOVER_CONTROL_TOKEN_FILE": str(control_path),
                    "ACCEPTANCE_FAILOVER_APPROVAL_TOKEN_FILE": str(approval_path),
                }
                hashes = {
                    "failover_control": hashlib.sha256(control.encode()).hexdigest(),
                    "failover_approval": hashlib.sha256(approval.encode()).hexdigest(),
                }
                with patch.dict(os.environ, env, clear=True):
                    executor = acceptance.HttpExecutor({"failover"}, hashes)
                    result = executor.run({
                        "id": "prepare-failover",
                        "endpoint": "failover",
                        "method": "POST",
                        "path": "/v1/failovers",
                        "auth": "failover_control",
                        "approvalAuth": "failover_approval",
                        "body": {"candidateId": "candidate-acceptance-001"},
                        "expectStatus": [200],
                    }, ["/body/prepared"])
                self.assertEqual(result.status, 200)
                self.assertEqual(FixtureHandler.authorization[-1], f"Bearer {control}")
                self.assertEqual(FixtureHandler.approvals[-1], approval)

    def test_direct_runner_rejects_non_ephemeral_or_group_readable_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            non_ephemeral = "persistent-acceptance-token-000001"
            path.write_text(non_ephemeral + "\n", encoding="utf-8")
            path.chmod(0o600)
            env = {
                "COORDINATOR_URL": "http://coordinator.invalid",
                "ACCEPTANCE_ADMIN_TOKEN_FILE": str(path),
            }
            with patch.dict(os.environ, env, clear=True):
                executor = acceptance.HttpExecutor(
                    {"coordinator"},
                    {"admin": hashlib.sha256(non_ephemeral.encode()).hexdigest()},
                )
                with self.assertRaisesRegex(acceptance.AcceptanceError, "not enclave-scoped"):
                    executor.token("admin")

            # The group-readable rejection is a POSIX permission boundary; on
            # platforms that do not honor st_mode the enforced Linux runner keeps
            # it and this local assertion is skipped.
            if os.name == "posix":
                ephemeral = "eph_private_acceptance_token_000001"
                path.write_text(ephemeral + "\n", encoding="utf-8")
                path.chmod(0o640)
                with patch.dict(os.environ, env, clear=True):
                    executor = acceptance.HttpExecutor(
                        {"coordinator"},
                        {"admin": hashlib.sha256(ephemeral.encode()).hexdigest()},
                    )
                    with self.assertRaisesRegex(acceptance.AcceptanceError, "private"):
                        executor.token("admin")

    def test_all_code_owned_scenario_invariants_have_adversarial_negatives(self):
        good = {
            "reorg": {
                "previousBlockHash": "0x" + "1" * 64, "canonicalBlockHash": "0x" + "2" * 64,
                "rpcABlockHash": "0x" + "2" * 64, "rpcBBlockHash": "0x" + "2" * 64,
                "rollbackDepth": 3, "retractionPreviousState": "PROVISIONAL",
                "retractionReason": "canonical reorg", "reconciliationReady": True,
            },
            "rpc-disagreement": {
                "agreedBeforeA": "head-a", "agreedBeforeB": "head-a", "disagreeA": "fork-a",
                "disagreeB": "fork-b", "criticalStatus": 503, "restoredA": "head-b",
                "restoredB": "head-b", "readyAfterRestore": True,
            },
            "database-object-restore": {
                "sourceDatabaseDigest": "a" * 64, "restoredDatabaseDigest": "a" * 64,
                "sourceObjectDigest": "b" * 64, "restoredObjectDigest": "b" * 64,
                "freshDatabase": True, "freshObjectStore": True, "serviceReady": True,
            },
            "sponsorship": {
                "tenantIsolationDenied": True, "firstOperationId": "sponsor-operation-001",
                "replayOperationId": "sponsor-operation-001", "conflictingReplayDenied": True,
                "reservedQuantity": "100", "chargedQuantity": "60", "refundedQuantity": "40",
                "doubleBilled": False,
            },
            "queue-congestion": {
                "tenantACompleted": 4, "tenantBCompleted": 4, "starvedJobs": 0,
                "leaseRecovered": True, "backoffObserved": True, "withinCaps": True, "edfViolations": 0,
            },
            "headless-restart": {
                "stateDigestBefore": "a" * 64, "stateDigestAfter": "a" * 64,
                "admissionJournalBefore": "b" * 64, "admissionJournalAfter": "b" * 64,
                "restartCount": 1, "lockRecovered": True, "roomReady": True,
                "queueJobPreserved": True, "duplicateSequences": 0,
            },
            "blob": {
                "manifestDigest": "a" * 64, "archiveDigest": "a" * 64,
                "rawTransactionHash": "0x" + "b" * 64, "rebroadcastRawTransactionHash": "0x" + "b" * 64,
                "signerCalls": 1, "finalized": True, "sidecarVerified": True,
            },
            "partial-aggregate": {
                "memberCount": 8, "uniqueRooms": 8, "appliedMembers": 7, "failedMembers": 1,
                "chargedMembers": 7, "failedMemberCharges": 0, "retryApplied": True,
                "operationId": "aggregate-operation-001", "finalized": True,
            },
            "renewal": {
                "checkpointFinalized": True, "freshPriceBound": True, "maximumChargeEnforced": True,
                "refundQuantity": "5", "duplicateCharge": 0, "handoffNoOverlap": True,
            },
            "withdrawal": {
                "proofRootValid": True, "operationId": "withdrawal-operation-001", "finalized": True,
                "replayDenied": True, "externalRaceCanonical": True, "sponsoredGas": True,
            },
            "load-shadow": {
                "requests": 1000, "durationSeconds": 60, "rpcMismatches": 0, "sseLostEvents": 0,
                "indexerMismatches": 0, "admissionFailOpen": 0, "schedulerStarvation": 0,
                "projectionMismatches": 0, "capsEnforced": True, "backpressureObserved": True,
            },
            "rpc-load-shadow": {
                "requests": 1000, "durationSeconds": 60, "p99LatencyMs": 240,
                "rpcAMatchedResponses": 1000, "rpcBMatchedResponses": 1000,
                "mismatches": 0, "criticalFailOpen": 0,
            },
            "sse-load-shadow": {
                "eventsPublished": 1000, "eventsObserved": 1000, "lostEvents": 0,
                "duplicateEventIds": 0, "reconnectResumed": True, "backpressureObserved": True,
                "subscriberCapEnforced": True, "eventIdsMonotonic": True,
            },
            "indexer-load-shadow": {
                "indexerHeadHash": "0x" + "1" * 64, "rpcAHeadHash": "0x" + "1" * 64,
                "rpcBHeadHash": "0x" + "1" * 64, "lagBlocks": 3, "comparedBlocks": 1000,
                "retractionsObserved": 1, "canonicalMismatches": 0,
                "rollbackApplied": True, "reconciliationReady": True,
            },
            "admission-load-shadow": {
                "submittedAdmissions": 1000, "walCommittedAdmissions": 1000,
                "recoveredAdmissions": 1, "lostAdmissions": 0, "failOpenAdmissions": 0,
                "backpressureObserved": True, "tenantCapEnforced": True,
            },
            "scheduler-load-shadow": {
                "tenantACompleted": 500, "tenantBCompleted": 500,
                "minimumTenantServiceShareBps": 4000, "starvedJobs": 0,
                "deadlineMisses": 0, "edfViolations": 0, "canonicalUrgentPreemptions": 1,
                "agingRecoveries": 1, "capacityCapsEnforced": True,
            },
            "projection-parity-shadow": {
                "primaryProjectionDigest": "a" * 64, "shadowProjectionDigest": "a" * 64,
                "comparedRooms": 100, "comparedEvents": 1000, "mismatchCount": 0,
                "reorgCompared": True, "finalityFloorMatched": True,
            },
            "prover-agent-trace-join": {
                "queueCorrelationId": "corr-acceptance-0001", "queueTenantId": "tenant-a",
                "queueRoomId": "42", "queueJobId": "pj-0123456789ab",
                "agentCorrelationId": "corr-acceptance-0001", "agentTenantId": "tenant-a",
                "agentRoomId": "42", "agentJobId": "pj-0123456789ab",
                "proverCorrelationId": "corr-acceptance-0001", "proverJobId": "pj-0123456789ab",
                "heartbeatCorrelationId": "corr-acceptance-0001", "heartbeatFinalized": True,
                "completionCorrelationId": "corr-acceptance-0001", "finalJobId": "pj-0123456789ab",
                "finalStatus": "DONE", "directSignerConfigured": False, "directRpcConfigured": False,
                "traceRecordCount": 3,
            },
        }
        for name, evidence in good.items():
            with self.subTest(scenario=name):
                if name == "rpc-load-shadow":
                    normalized = acceptance.validate_rpc_load_shadow(evidence, 500, 0)
                elif name == "indexer-load-shadow":
                    normalized = acceptance.validate_indexer_load_shadow(evidence, 8)
                elif name == "projection-parity-shadow":
                    normalized = acceptance.validate_projection_parity_shadow(evidence, 0)
                else:
                    normalized = acceptance.VALIDATORS[name](evidence)
                self.assertTrue(normalized)
                broken = dict(evidence)
                if name == "reorg": broken["rpcBBlockHash"] = "0x" + "3" * 64
                elif name == "rpc-disagreement": broken["disagreeB"] = broken["disagreeA"]
                elif name == "database-object-restore": broken["restoredObjectDigest"] = "c" * 64
                elif name == "sponsorship": broken["doubleBilled"] = True
                elif name == "queue-congestion": broken["edfViolations"] = 1
                elif name == "headless-restart": broken["stateDigestAfter"] = "c" * 64
                elif name == "blob": broken["signerCalls"] = 2
                elif name == "partial-aggregate": broken["chargedMembers"] = 8
                elif name == "renewal": broken["duplicateCharge"] = 1
                elif name == "withdrawal": broken["replayDenied"] = False
                elif name == "load-shadow": broken["projectionMismatches"] = 1
                elif name == "rpc-load-shadow": broken["mismatches"] = 1
                elif name == "sse-load-shadow": broken["lostEvents"] = 1
                elif name == "indexer-load-shadow": broken["canonicalMismatches"] = 1
                elif name == "admission-load-shadow": broken["walCommittedAdmissions"] = 999
                elif name == "scheduler-load-shadow": broken["edfViolations"] = 1
                elif name == "projection-parity-shadow": broken["mismatchCount"] = 1
                else: broken["agentJobId"] = "pj-ffffffffffff"
                with self.assertRaises(acceptance.AcceptanceError):
                    if name == "rpc-load-shadow": acceptance.validate_rpc_load_shadow(broken, 500, 0)
                    elif name == "indexer-load-shadow": acceptance.validate_indexer_load_shadow(broken, 8)
                    elif name == "projection-parity-shadow": acceptance.validate_projection_parity_shadow(broken, 0)
                    else: acceptance.VALIDATORS[name](broken)

    def test_postgresql_lsn_and_cli_contract(self):
        evidence = {
            "activeTerminated": True, "staleWriterRejected": True, "promotedReady": True,
            "signerAfterFence": True, "targetLsn": "0/16B6C50", "replayLsn": "0/16B6C51",
            "rtoSeconds": 42,
        }
        result = acceptance.validate_split_brain(evidence, 300)
        self.assertEqual(result["targetLsn"], "0/16B6C50")
        parser = acceptance.scenario_parser()
        args = parser.parse_args([
            "prover-agent-trace-join", "--assert-coordinator-agent-prover-heartbeat",
        ])
        self.assertEqual(args.scenario, "prover-agent-trace-join")
        args = parser.parse_args([
            "rpc-load-shadow", "--assert-latency-budget-ms", "500", "--assert-mismatch-rate", "0",
        ])
        self.assertEqual(args.scenario, "rpc-load-shadow")
        args = parser.parse_args([
            "headless-restart", "--assert-state-continuity", "--assert-single-writer",
        ])
        self.assertEqual(args.scenario, "headless-restart")


if __name__ == "__main__":
    unittest.main()
