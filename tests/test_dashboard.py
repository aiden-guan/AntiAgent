"""Unit tests for AntiAgent local dashboard server and API endpoints."""

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

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

    def test_api_config_extended_fields(self):
        url = f"http://127.0.0.1:{self.port}/api/config"
        payload = json.dumps({
            "profile": "balanced",
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "api_key": "test-key-123",
            "endpoint_url": "http://localhost:11434",
            "auto_approve_reads": True,
            "auto_approve_dev_commands": True,
            "audit_enabled": True,
            "custom_allow_patterns": ["^npm run lint"],
            "custom_deny_patterns": ["^rm -rf /tmp"],
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            cfg = data["config"]
            self.assertEqual(cfg["profile"], "balanced")
            self.assertEqual(cfg["provider"], "gemini")
            self.assertEqual(cfg["model"], "gemini-2.5-flash")
            self.assertEqual(cfg["api_key"], "test-key-123")
            self.assertEqual(cfg["endpoint_url"], "http://localhost:11434")
            self.assertTrue(cfg["auto_approve_dev_commands"])
            self.assertEqual(cfg["custom_allow_patterns"], ["^npm run lint"])
            self.assertEqual(cfg["custom_deny_patterns"], ["^rm -rf /tmp"])

    def test_api_workspace_switch(self):
        from pathlib import Path
        url = f"http://127.0.0.1:{self.port}/api/workspace"
        new_dir = tempfile.mkdtemp()
        payload = json.dumps({"path": new_dir}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertEqual(data["workspace_path"], str(Path(new_dir).resolve()))

    def test_api_create_project(self):
        url = f"http://127.0.0.1:{self.port}/api/create_project"
        new_proj = os.path.join(tempfile.mkdtemp(), "test-app")
        payload = json.dumps({"path": new_proj}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertTrue(os.path.isdir(new_proj))
            # Verify hook was installed
    def test_api_doctor(self):
        url = f"http://127.0.0.1:{self.port}/api/doctor"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("python", data)
            self.assertIn("global_hook", data)
            self.assertIn("workspace_hook", data)
            self.assertIn("modes", data)
            self.assertTrue(data["modes"]["turbo_mode"]["supported"])
            self.assertTrue(data["modes"]["turbo_mode"]["recommended"])

    def test_api_onboarding(self):
        url = f"http://127.0.0.1:{self.port}/api/onboarding"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("onboarding_completed", data)

        # Post update
        payload = json.dumps({"completed": True}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertTrue(data["onboarding_completed"])

    def test_api_choose_folder_cancelled(self):
        from unittest.mock import patch, MagicMock
        url = f"http://127.0.0.1:{self.port}/api/choose_folder"
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_res.stderr = "User canceled. (-128)"
        with patch("subprocess.run", return_value=mock_res):
            req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertFalse(data["ok"])
                self.assertTrue(data["cancelled"])

    def test_api_choose_folder_success(self):
        from unittest.mock import patch, MagicMock
        url = f"http://127.0.0.1:{self.port}/api/choose_folder"
        target_dir = tempfile.mkdtemp()
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = f"{target_dir}\n"
        with patch("subprocess.run", return_value=mock_res):
            req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])
                self.assertEqual(data["workspace_path"], str(Path(target_dir).resolve()))


if __name__ == "__main__":
    unittest.main()

