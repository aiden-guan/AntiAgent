"""Unit tests for AntiAgent local dashboard server and API endpoints."""

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from antiagent.dashboard.server import DashboardRequestHandler


class TestDashboardServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()
        DashboardRequestHandler.workspace_path = cls.test_dir
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        cls.port = cls.server.server_address[1]
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

    def test_security_headers_present(self):
        url = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(url) as resp:
            headers = dict(resp.headers)
            self.assertIn("Content-Security-Policy", headers)
            self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
            self.assertEqual(headers.get("X-Frame-Options"), "DENY")
            self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")

    def test_invalid_host_header_blocked(self):
        import urllib.error
        url = f"http://127.0.0.1:{self.port}/api/status"
        req = urllib.request.Request(url, headers={"Host": "attacker.evil.com"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 403)

    def test_cross_site_post_blocked(self):
        import urllib.error
        url = f"http://127.0.0.1:{self.port}/api/config"
        payload = json.dumps({"profile": "autonomous"}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 403)

    def test_cross_origin_post_blocked(self):
        import urllib.error
        url = f"http://127.0.0.1:{self.port}/api/config"
        payload = json.dumps({"profile": "autonomous"}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Origin": "https://malicious-site.com",
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 403)

    def test_api_status_masks_api_key(self):
        from unittest.mock import patch
        from antiagent.config import AntiAgentConfig
        test_cfg = AntiAgentConfig(api_key="AIzaSySecretLongKey123456789")
        with patch("antiagent.dashboard.server.load_config", return_value=test_cfg):
            url = f"http://127.0.0.1:{self.port}/api/status"
            with urllib.request.urlopen(url) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["has_api_key"])
                self.assertNotIn("AIzaSySecretLongKey123456789", data["api_key"])
                self.assertIn("••••", data["api_key"])

    def test_api_update_check(self):
        from unittest.mock import patch
        mock_info = {
            "ok": True,
            "update_available": True,
            "current_version": "0.1.3",
            "latest_version": "0.1.4",
            "release_name": "AntiAgent v0.1.4",
            "release_notes": "In-app updates",
            "published_at": "2026-09-16T12:00:00Z",
            "html_url": "https://github.com/aiden-guan/AntiAgent/releases",
            "assets": [{"name": "AntiAgent.dmg", "download_url": "http://example.com/AntiAgent.dmg", "size": 1000}],
            "recommended_asset": {"name": "AntiAgent.dmg", "download_url": "http://example.com/AntiAgent.dmg", "size": 1000},
        }
        with patch("antiagent.dashboard.server.check_for_updates", return_value=mock_info):
            url = f"http://127.0.0.1:{self.port}/api/update/check"
            with urllib.request.urlopen(url) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])
                self.assertTrue(data["update_available"])
                self.assertEqual(data["latest_version"], "0.1.4")

    def test_api_update_status_and_cancel(self):
        url_status = f"http://127.0.0.1:{self.port}/api/update/status"
        with urllib.request.urlopen(url_status) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("status", data)
            self.assertIn("progress", data)

        url_cancel = f"http://127.0.0.1:{self.port}/api/update/cancel"
        req = urllib.request.Request(url_cancel, data=b"{}", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("ok", data)

    def test_api_update_download_trigger(self):
        from unittest.mock import patch
        with patch("antiagent.dashboard.server.global_downloader.start_download", return_value=True):
            url = f"http://127.0.0.1:{self.port}/api/update/download"
            # 1. Valid GitHub release URL succeeds
            payload = json.dumps({
                "download_url": "https://github.com/aiden-guan/AntiAgent/releases/download/v0.1.4/AntiAgent.dmg",
                "filename": "AntiAgent.dmg"
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])

            # 2. Untrusted URL is rejected with HTTP 400
            bad_payload = json.dumps({
                "download_url": "https://malicious.com/virus.exe",
                "filename": "virus.exe"
            }).encode("utf-8")
            bad_req = urllib.request.Request(url, data=bad_payload, headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(bad_req)
            self.assertEqual(ctx.exception.code, 400)

    def test_api_update_open_and_reveal(self):
        from unittest.mock import patch
        with patch("antiagent.dashboard.server.open_downloaded_file", return_value=True):
            url = f"http://127.0.0.1:{self.port}/api/update/open"
            payload = json.dumps({"path": "/tmp/fake.dmg"}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])

        with patch("antiagent.dashboard.server.reveal_in_file_manager", return_value=True):
            url = f"http://127.0.0.1:{self.port}/api/update/reveal"
            payload = json.dumps({"path": "/tmp/fake.dmg"}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])

    def test_api_update_self_update_endpoints(self):
        from unittest.mock import patch
        # Test status endpoint
        url_status = f"http://127.0.0.1:{self.port}/api/update/self_update_status"
        with urllib.request.urlopen(url_status) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("status", data)

        # Test trigger endpoint
        with patch("antiagent.dashboard.server.global_self_updater.start_update", return_value=True):
            url_trigger = f"http://127.0.0.1:{self.port}/api/update/self_update"
            payload = json.dumps({"force": True}).encode("utf-8")
            req = urllib.request.Request(url_trigger, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])

        # Test reset endpoint
        url_reset = f"http://127.0.0.1:{self.port}/api/update/self_update_reset"
        req_reset = urllib.request.Request(url_reset, data=b"{}", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req_reset) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertEqual(data["status"]["status"], "idle")

        # Test relaunch endpoint
        url_relaunch = f"http://127.0.0.1:{self.port}/api/update/relaunch_app"
        req_relaunch = urllib.request.Request(url_relaunch, data=b"{}", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req_relaunch) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("ok", data)

        # Test restart endpoint (mocking Thread to prevent killing test process)
        with patch("antiagent.dashboard.server.threading.Thread") as mock_thread:
            url_restart = f"http://127.0.0.1:{self.port}/api/update/restart"
            req_restart = urllib.request.Request(url_restart, data=b"{}", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req_restart) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data.get("ok"))
                mock_thread.assert_called_once()


if __name__ == "__main__":
    unittest.main()


