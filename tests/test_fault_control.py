from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fault-control"))

import fault_control as fc  # noqa: E402


TOKEN = "eph_fault_control_fixture_token_00000001"
CANDIDATE = "fixture-candidate-20260821"
PLAN = "a" * 64
HOSTED = "sha256:" + "b" * 64
RECEIPT = {
    "candidateDescriptorSha256": "d" * 64,
    "topologyReceiptSha256": "e" * 64,
    "platform": "docker",
    "adapterImage": "registry.local/zkdeal-fault-control@sha256:" + "f" * 64,
    "adapterSourceSha256": "1" * 64,
}


class ProbeHandler(BaseHTTPRequestHandler):
    calls = 0

    def log_message(self, _format, *_args):
        return

    def do_GET(self):  # noqa: N802
        type(self).calls += 1
        payload = b'{"status":"ok"}\n'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class FaultControlTests(unittest.TestCase):
    def topology(self, base: str) -> fc.Topology:
        endpoint = fc.LoadEndpoint(base, "/health", "GET", None, None)
        return fc.Topology(
            "non-release-fixture",
            CANDIDATE,
            PLAN,
            HOSTED,
            hashlib.sha256(TOKEN.encode()).hexdigest(),
            frozenset({"127.0.0.1", "rpc-a", "rpc-b"}),
            "http://rpc-a:8545",
            "http://rpc-b:8545",
            None,
            {},
            None,
            (),
            {"rpc": (endpoint,)},
            2,
            RECEIPT["topologyReceiptSha256"],
        )

    def body(self):
        return {
            "schemaVersion": 1,
            "binding": {
                "candidateId": CANDIDATE,
                "planSha256": PLAN,
                "hostedIntegrationToken": HOSTED,
            },
            "profile": "rpc",
            "requests": 3,
            "concurrency": 2,
            "durationSeconds": 1,
        }

    def test_scoped_candidate_bound_load_is_durable_and_idempotent(self):
        ProbeHandler.calls = 0
        probe = ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
        thread = threading.Thread(target=probe.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{probe.server_address[1]}"
            with tempfile.TemporaryDirectory() as directory:
                topology = self.topology(base)
                journal = fc.Journal(Path(directory), "2" * 64)
                controller = fc.Controller(topology, TOKEN, journal, RECEIPT)
                first, replay = controller.execute(
                    "load", self.body(), "fault-load-key-0001", "fault-load-correlation-0001",
                )
                second, replay_second = controller.execute(
                    "load", self.body(), "fault-load-key-0001", "fault-load-correlation-0001",
                )
                self.assertFalse(replay)
                self.assertTrue(replay_second)
                self.assertEqual(first["operationId"], second["operationId"])
                self.assertEqual(first["requests"], 3)
                self.assertEqual(ProbeHandler.calls, 3)
                self.assertEqual(first["adapterImage"], RECEIPT["adapterImage"])
                serialized = json.dumps([
                    path.read_text(encoding="utf-8")
                    for path in Path(directory).glob("*.json")
                ])
                self.assertNotIn(TOKEN, serialized)
                changed = dict(self.body(), requests=4)
                with self.assertRaisesRegex(fc.ControlError, "changed input"):
                    controller.execute(
                        "load", changed, "fault-load-key-0001", "fault-load-correlation-0001",
                    )
        finally:
            probe.shutdown()
            probe.server_close()
            thread.join(2)

    def test_auth_binding_and_request_surface_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = fc.Controller(
                self.topology("http://127.0.0.1:1"), TOKEN,
                fc.Journal(Path(directory), "3" * 64), RECEIPT,
            )
            controller.authenticate("Bearer " + TOKEN)
            with self.assertRaisesRegex(fc.ControlError, "scoped bearer"):
                controller.authenticate("Bearer owner-admin-token-must-not-work")
            broken = self.body()
            broken["binding"] = dict(broken["binding"], candidateId="another-candidate")
            with self.assertRaisesRegex(fc.ControlError, "candidate plan"):
                controller.execute(
                    "load", broken, "fault-load-key-0002", "fault-load-correlation-0002",
                )
            injected = dict(self.body(), url="https://attacker.invalid", command="sh")
            with self.assertRaisesRegex(fc.ControlError, "keys differ"):
                controller.execute(
                    "load", injected, "fault-load-key-0003", "fault-load-correlation-0003",
                )

    def test_scoped_token_file_is_ephemeral_and_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "fault-token"
            token_path.write_text(TOKEN + "\n", encoding="utf-8")
            # The 0600 rejection is a POSIX permission boundary enforced in the
            # Linux runtime; it is only assertable where st_mode is honored.
            if os.name == "posix":
                token_path.chmod(0o640)
                with self.assertRaisesRegex(fc.ControlError, "mode 0600"):
                    fc.read_secret(
                        str(token_path), hashlib.sha256(TOKEN.encode()).hexdigest(),
                        "fault token", require_ephemeral=True,
                    )
            malformed = "owner_admin_token_that_is_long_enough_0001"
            token_path.write_text(malformed + "\n", encoding="utf-8")
            token_path.chmod(0o600)
            with self.assertRaisesRegex(fc.ControlError, "enclave-scoped eph_"):
                fc.read_secret(
                    str(token_path), hashlib.sha256(malformed.encode()).hexdigest(),
                    "fault token", require_ephemeral=True,
                )

    def test_exact_fault_and_load_contract_is_published(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = fc.Controller(
                self.topology("http://127.0.0.1:1"), TOKEN,
                fc.Journal(Path(directory), "4" * 64), RECEIPT,
            )
            capability = controller.capabilities()
            self.assertEqual(capability["scopedBearer"], "fault_control")
            self.assertEqual(set(capability["faultActions"]), {
                "l1-reorg", "rpc-disagreement", "rpc-provider-control",
                "coordinator-terminate", "service-pause",
                "headless-restart", "prover-restart", "indexer-rollback",
                "object-store-restart", "database-restart", "network-partition",
                "sse-disconnect",
            })
            self.assertEqual(
                set(capability["loadProfiles"]),
                {"rpc", "sse", "indexer", "admission", "scheduler", "projection"},
            )
            self.assertFalse(capability["requestControlledUrls"])
            source = (ROOT / "fault-control/fault_control.py").read_text(encoding="utf-8")
            self.assertIn('"/v1/faults": "fault"', source)
            self.assertIn(r'/v1/(?:faults|load-runs)/', source)

    def docker_topology(self, restart_targets):
        return fc.Topology(
            "non-release-fixture", CANDIDATE, PLAN, HOSTED,
            hashlib.sha256(TOKEN.encode()).hexdigest(),
            frozenset({"rpc-a", "rpc-b"}),
            "http://rpc-a:8545", "http://rpc-b:8545",
            "/var/run/docker.sock", dict(restart_targets),
            None, (), {}, 2, RECEIPT["topologyReceiptSha256"],
        )

    def fault_body(self, action, parameters):
        return {
            "schemaVersion": 1,
            "binding": {
                "candidateId": CANDIDATE,
                "planSha256": PLAN,
                "hostedIntegrationToken": HOSTED,
            },
            "action": action,
            "parameters": parameters,
        }

    def test_new_fault_actions_validate_allowlist_and_fail_closed_without_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = fc.Controller(
                self.topology("http://127.0.0.1:1"), TOKEN,
                fc.Journal(Path(directory), "9" * 64), RECEIPT,
            )
            with self.assertRaisesRegex(fc.ControlError, "rpc-a or rpc-b"):
                controller._fault_callback("rpc-provider-control", {"provider": "rpc-c", "phase": "stop"})
            with self.assertRaisesRegex(fc.ControlError, "stop or start"):
                controller._fault_callback("rpc-provider-control", {"provider": "rpc-a", "phase": "kill"})
            with self.assertRaisesRegex(fc.ControlError, "keys differ"):
                controller._fault_callback(
                    "rpc-provider-control", {"provider": "rpc-a", "phase": "stop", "url": "x"},
                )
            with self.assertRaisesRegex(fc.ControlError, "reviewed pausable"):
                controller._fault_callback("service-pause", {"target": "postgres-primary", "phase": "pause"})
            with self.assertRaisesRegex(fc.ControlError, "pause or unpause"):
                controller._fault_callback("service-pause", {"target": "indexer", "phase": "halt"})
            # Without a Docker socket the actuation fails closed instead of
            # silently no-op'ing.
            callback = controller._fault_callback("rpc-provider-control", {"provider": "rpc-a", "phase": "stop"})
            with self.assertRaisesRegex(fc.ControlError, "disabled"):
                callback("fc-" + "0" * 40)

    def test_rpc_provider_and_service_pause_actuate_allowlisted_containers(self):
        with tempfile.TemporaryDirectory() as directory:
            topology = self.docker_topology({
                "rpc-a": "cand-rpc-a",
                "rpc-b": "cand-rpc-b",
                "indexer": "cand-indexer",
            })
            controller = fc.Controller(
                topology, TOKEN, fc.Journal(Path(directory), "a" * 64), RECEIPT,
            )
            calls: list[tuple[str, str]] = []

            def fake_action(socket_path, container, action, timeout):
                calls.append((container, action))

            with patch.object(fc, "docker_container_action", fake_action):
                stop_result, replay = controller.execute(
                    "fault", self.fault_body("rpc-provider-control", {"provider": "rpc-b", "phase": "stop"}),
                    "fault-rpc-key-0001", "fault-rpc-correlation-0001",
                )
                self.assertFalse(replay)
                pause_result, _ = controller.execute(
                    "fault", self.fault_body("service-pause", {"target": "indexer", "phase": "pause"}),
                    "fault-pause-key-0001", "fault-pause-correlation-0001",
                )
            self.assertEqual(calls, [("cand-rpc-b", "stop"), ("cand-indexer", "pause")])
            self.assertEqual(stop_result["logicalTarget"], "rpc-b")
            self.assertEqual(stop_result["dockerAction"], "stop")
            self.assertEqual(stop_result["containerId"], "cand-rpc-b")
            self.assertTrue(stop_result["applied"])
            self.assertEqual(pause_result["action"], "service-pause")
            self.assertEqual(pause_result["dockerAction"], "pause")
            # The receipt binds the exact adapter image and is durable.
            self.assertEqual(stop_result["adapterImage"], RECEIPT["adapterImage"])
            closure = controller.journal.closure(stop_result["operationId"])
            self.assertEqual(closure["logicalTarget"], "rpc-b")

    def test_reorg_actuates_both_providers_symmetrically(self):
        class FakeProvider:
            def __init__(self):
                self.chain: list[tuple[int, int]] = []
                self.snapshots: dict[str, list[tuple[int, int]]] = {}
                self.next_fee = 0
                self.next_timestamp = 1_700_000_000
                self.mined = 0

            def head(self):
                digest = hashlib.sha256(json.dumps(self.chain).encode()).hexdigest()
                timestamp = self.chain[-1][1] if self.chain else 1_699_999_999
                return {"hash": "0x" + digest, "timestamp": hex(timestamp)}

        providers = {
            "http://rpc-a:8545": FakeProvider(),
            "http://rpc-b:8545": FakeProvider(),
        }

        def fake_rpc(base_url, method, params, _timeout, _request_id):
            provider = providers[base_url]
            if method == "evm_snapshot":
                name = f"0x{len(provider.snapshots)}"
                provider.snapshots[name] = list(provider.chain)
                return name
            if method == "eth_getBlockByNumber":
                return provider.head()
            if method == "anvil_setNextBlockBaseFeePerGas":
                provider.next_fee = int(params[0], 16)
                return None
            if method == "evm_setNextBlockTimestamp":
                provider.next_timestamp = int(params[0])
                return None
            if method == "evm_mine":
                provider.chain.append((provider.next_fee, provider.next_timestamp))
                provider.mined += 1
                return "0x0"
            if method == "evm_revert":
                provider.chain = list(provider.snapshots[params[0]])
                return True
            raise AssertionError(f"unexpected RPC method {method}")

        with tempfile.TemporaryDirectory() as directory:
            controller = fc.Controller(
                self.topology("http://127.0.0.1:1"), TOKEN,
                fc.Journal(Path(directory), "c" * 64), RECEIPT,
            )
            with patch.object(fc, "rpc_json", fake_rpc):
                prepared, _ = controller.execute(
                    "fault",
                    self.fault_body("l1-reorg", {"phase": "prepare", "depth": 3, "preparedOperationId": None}),
                    "fault-reorg-key-0001", "fault-reorg-correlation-0001",
                )
                self.assertEqual(prepared["phase"], "PREPARED")
                replaced, _ = controller.execute(
                    "fault",
                    self.fault_body("l1-reorg", {
                        "phase": "replace", "depth": 3,
                        "preparedOperationId": prepared["operationId"],
                    }),
                    "fault-reorg-key-0002", "fault-reorg-correlation-0002",
                )
        provider_a = providers["http://rpc-a:8545"]
        provider_b = providers["http://rpc-b:8545"]
        self.assertEqual(replaced["phase"], "REPLACED")
        # Both independent providers mined the branch and its replacement.
        self.assertEqual(provider_a.mined, 6)
        self.assertEqual(provider_b.mined, 6)
        # Both providers converge on one replacement canonical hash, which is
        # what the acceptance runner's dual-provider agreement check requires.
        self.assertEqual(provider_a.head()["hash"], provider_b.head()["hash"])
        self.assertEqual(replaced["canonicalBlockHash"], provider_a.head()["hash"])
        self.assertNotEqual(replaced["canonicalBlockHash"], replaced["previousBlockHash"])

    def test_docker_action_allowlist_is_narrow(self):
        self.assertEqual(
            set(fc.DOCKER_CONTAINER_ACTIONS),
            {"restart", "stop", "start", "pause", "unpause"},
        )
        with tempfile.TemporaryDirectory() as directory:
            controller = fc.Controller(
                self.docker_topology({"indexer": "cand-indexer"}), TOKEN,
                fc.Journal(Path(directory), "b" * 64), RECEIPT,
            )
            # An unreviewed Docker verb can never be reached through a fault.
            with self.assertRaisesRegex(fc.ControlError, "unreviewed Docker container action"):
                fc.docker_container_action("/var/run/docker.sock", "cand-indexer", "exec", 2)
            del controller

    def test_topology_rejects_unallowlisted_url_and_container(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "fault.token"
            token.write_text(TOKEN + "\n", encoding="utf-8")
            token.chmod(0o640)
            config = json.loads((ROOT / "fault-control/topology.example.json").read_text(encoding="utf-8"))
            config["faultControlTokenSha256"] = hashlib.sha256(TOKEN.encode()).hexdigest()
            config["rpcProviders"]["rpc-a"] = "https://attacker.invalid"
            raw = fc.canonical_bytes(config)
            path = root / "topology.json"
            path.write_bytes(raw)
            with self.assertRaisesRegex(fc.ControlError, "fixed target topology"):
                fc.load_topology(str(path), hashlib.sha256(raw).hexdigest())
            config["rpcProviders"]["rpc-a"] = "http://rpc-a:8545"
            config["docker"]["restartTargets"]["indexer"] = "different-candidate-indexer"
            raw = fc.canonical_bytes(config)
            path.write_bytes(raw)
            with self.assertRaisesRegex(fc.ControlError, "candidate prefix"):
                fc.load_topology(str(path), hashlib.sha256(raw).hexdigest())

    def test_image_is_nonroot_source_bound_and_read_only_compatible(self):
        dockerfile = (ROOT / "fault-control/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("python:3.13-alpine@sha256:", dockerfile)
        self.assertIn("ARG FAULT_CONTROL_SOURCE_SHA256", dockerfile)
        self.assertIn("USER 65532:65532", dockerfile)
        self.assertNotIn("VOLUME", dockerfile)
        readme = (ROOT / "fault-control/README.md").read_text(encoding="utf-8")
        self.assertIn("read-only root filesystem", readme)
        self.assertIn("not an owner administrator", readme)


if __name__ == "__main__":
    unittest.main()
