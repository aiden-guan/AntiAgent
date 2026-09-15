"""Unit tests for AntiAgent local dashboard server and API endpoints."""

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from antiagent.dashboard.server import DashboardRequestHandler


class TestDashboardServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()
        DashboardRequestHandler.workspace_path = cls.test_dir
        cls.port = 42429
        cls.server = ThreadingHTTPServer(("127.0.0.1", cls.port), DashboardRequestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_get_index_html(self):
        url = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            content = resp.read().decode("utf-8")
            self.assertIn("AntiAgent Dashboard", content)

    def test_api_status(self):
        url = f"http://127.0.0.1:{self.port}/api/status"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("profile", data)
            self.assertIn("provider", data)
            self.assertEqual(data["provider"], "native")

    def test_api_simulate_safe(self):
        url = f"http://127.0.0.1:{self.port}/api/simulate"
        payload = json.dumps({"tool": "run_command", "args": {"CommandLine": "ls -la"}}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["decision"], "allow")

    def test_api_simulate_dangerous(self):
        url = f"http://127.0.0.1:{self.port}/api/simulate"
        payload = json.dumps({"tool": "run_command", "args": {"CommandLine": "rm -rf /"}}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["decision"], "deny")

    def test_api_config_update(self):
        url = f"http://127.0.0.1:{self.port}/api/config"
        payload = json.dumps({"profile": "paranoid"}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertEqual(data["config"]["profile"], "paranoid")


if __name__ == "__main__":
    unittest.main()
