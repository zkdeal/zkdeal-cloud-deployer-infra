from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ConformanceServiceTests(unittest.TestCase):
    def test_health_ready_and_mutations_are_disabled(self):
        port = free_port()
        env = {**os.environ, "PORT": str(port), "SERVICE_NAME": "test-boundary"}
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "tests/fixtures/conformance_service.py")],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(50):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2) as response:
                        health = json.load(response)
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                self.fail("conformance service did not start")
            self.assertTrue(health["conformanceOnly"])
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/ready", timeout=1) as response:
                self.assertTrue(json.load(response)["ready"])
            request = urllib.request.Request(f"http://127.0.0.1:{port}/mutate", data=b"{}", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=1)
            self.assertEqual(caught.exception.code, 501)
            self.assertIn("CONFORMANCE_STUB_NO_BUSINESS_LOGIC", caught.exception.read().decode())
        finally:
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
