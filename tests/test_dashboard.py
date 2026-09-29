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
        os.environ["ANTIAGENT_TESTING"] = "1"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("ANTIAGENT_TESTING", None)
        cls.server.shutdown()
        cls.server.server_close()

    def test_get_index_html(self):
        url = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            content = resp.read().decode("utf-8")
            self.assertIn("AntiAgent Dashboard", content)

    def test_revamped_ui_structure(self):
        url = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            content = resp.read().decode("utf-8")
            # 1. Telemetry HUD Strip
            self.assertIn("telemetryHud", content)
            self.assertIn("hudStanceVal", content)
            self.assertIn("hudAuditCount", content)
            self.assertIn("hudConversationsCount", content)
            self.assertIn("hudPRVal", content)
            self.assertIn("hudRemotesCount", content)

            # 2. View Mode Navigation Bar & Tabs
            self.assertIn("viewTabsContainer", content)
            self.assertIn('data-view="overview"', content)
            self.assertIn('data-view="audit"', content)
            self.assertIn('data-view="conversations"', content)
            self.assertIn('data-view="security"', content)
            self.assertIn('data-view="sandbox"', content)
            self.assertIn('data-view="fleet"', content)
            self.assertIn('data-view="all"', content)

            # 3. Layout Toolbar & Search
            self.assertIn("dashboardSearchInput", content)
            self.assertIn("btnToggleAllCards", content)
            self.assertIn("btnDensityToggle", content)
            self.assertIn("openCustomizeModal", content)

            # 4. Standardized Cards with Collapsible and Focus Capabilities
            for card_id in [
                "hooksCard", "engineCard", "rulesCard", "customRegexCard",
                "prMonitorCard", "conversationsCard", "remoteSessionsCard",
                "threatSimulatorCard", "auditSectionCard"
            ]:
                self.assertIn(f'id="{card_id}"', content)
                self.assertIn(f"toggleFocusCard('{card_id}')", content)
                self.assertIn(f"toggleCardCollapse('{card_id}')", content)

            # 5. Focus Backdrop & Customization Modal
            self.assertIn("focusBackdrop", content)
            self.assertIn("customizeLayoutModal", content)
            self.assertIn("selectDefaultView", content)

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

        # Test restart endpoint
        url_restart = f"http://127.0.0.1:{self.port}/api/update/restart"
        req_restart = urllib.request.Request(url_restart, data=b"{}", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req_restart) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data.get("ok"))
            self.assertIn("desktop_relaunch", data)

    def test_changelog_readability_assets(self):
        """Verify that index.html contains the rich markdown parser and styled changelog elements."""
        url = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            content = resp.read().decode("utf-8")
            self.assertIn("renderMarkdown", content)
            self.assertIn("release-notes-box", content)
            self.assertIn("changelog-table-wrap", content)
            self.assertIn("releaseNotesExternalLink", content)

    def test_api_remotes_endpoints(self):
        from unittest.mock import patch
        from antiagent.dashboard.server import global_remote_manager
        from antiagent.engine.remote_sessions import RemoteHost, RemoteProbeResult, RemoteControlStatus, AntiAgentRemoteStatus, AntigravityStatus

        # 1. Add remote host via POST /api/remotes/add
        add_url = f"http://127.0.0.1:{self.port}/api/remotes/add"
        payload = json.dumps({
            "name": "dash-box",
            "ssh_host": "dash-box.corp",
            "workspace": "~/Developer/PigeonBox",
            "antigravity_name": "Dash Box",
        }).encode("utf-8")
        req = urllib.request.Request(add_url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertEqual(data["host"]["name"], "dash-box")

        # 2. List remote hosts via GET /api/remotes
        list_url = f"http://127.0.0.1:{self.port}/api/remotes"
        mock_probe = RemoteProbeResult(
            ok=True,
            name="dash-box",
            ssh_connected=True,
            latency_ms=20,
            remote_os="Linux",
            hostname="dash-box",
            antigravity=AntigravityStatus(installed=True, version="1.15.0"),
            remote_control=RemoteControlStatus(supported=True, running=True, instance_name="Dash Box"),
            antiagent=AntiAgentRemoteStatus(installed=True, global_hook_active=True, protection_status="Protected"),
        )
        with patch.object(global_remote_manager, "probe_all", return_value=[mock_probe]):
            with urllib.request.urlopen(list_url) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])
                self.assertTrue(any(h["name"] == "dash-box" for h in data["hosts"]))

        # 3. Status via GET /api/remotes/status
        status_url = f"http://127.0.0.1:{self.port}/api/remotes/status?name=dash-box"
        with patch.object(global_remote_manager, "probe", return_value=mock_probe):
            with urllib.request.urlopen(status_url) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])
                self.assertEqual(data["probe"]["name"], "dash-box")

        # 4. Doctor via GET /api/remotes/doctor
        doc_url = f"http://127.0.0.1:{self.port}/api/remotes/doctor?name=dash-box"
        doc_result = {"ok": True, "host": {"name": "dash-box"}, "probe": mock_probe.to_dict()}
        with patch.object(global_remote_manager, "doctor", return_value=doc_result):
            with urllib.request.urlopen(doc_url) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])

        # 5. Test connection via POST /api/remotes/test
        test_url = f"http://127.0.0.1:{self.port}/api/remotes/test"
        with patch.object(global_remote_manager, "test_connection", return_value={"ok": True, "latency_ms": 15, "remote_os": "Linux"}):
            req = urllib.request.Request(test_url, data=json.dumps({"name": "dash-box"}).encode("utf-8"), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])
                self.assertEqual(data["latency_ms"], 15)

        # 6. Start daemon via POST /api/remotes/start
        start_url = f"http://127.0.0.1:{self.port}/api/remotes/start"
        with patch.object(global_remote_manager, "start_remote_control", return_value={"ok": True, "running": True, "instance_name": "Dash Box", "url": "https://antigravity.google.com/"}):
            req = urllib.request.Request(start_url, data=json.dumps({"name": "dash-box"}).encode("utf-8"), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])
                self.assertTrue(data["running"])

        # 7. Stop daemon via POST /api/remotes/stop
        stop_url = f"http://127.0.0.1:{self.port}/api/remotes/stop"
        with patch.object(global_remote_manager, "stop_remote_control", return_value={"ok": True, "running": False}):
            req = urllib.request.Request(stop_url, data=json.dumps({"name": "dash-box"}).encode("utf-8"), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])

        # 8. Protect remote via POST /api/remotes/protect
        protect_url = f"http://127.0.0.1:{self.port}/api/remotes/protect"
        with patch.object(global_remote_manager, "protect_remote", return_value={"ok": True, "protection_status": "Protected"}):
            req = urllib.request.Request(protect_url, data=json.dumps({"name": "dash-box"}).encode("utf-8"), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])

        # 9. Remove remote host via POST /api/remotes/remove
        remove_url = f"http://127.0.0.1:{self.port}/api/remotes/remove"
        req = urllib.request.Request(remove_url, data=json.dumps({"name": "dash-box"}).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])

    def test_api_open_url(self):
        from unittest.mock import patch
        url = f"http://127.0.0.1:{self.port}/api/open-url"

        # Valid HTTPS URL
        payload = json.dumps({"url": "https://github.com/aiden-guan/AntiAgent/releases"}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with patch("antiagent.dashboard.server.webbrowser.open") as mock_open:
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])
                mock_open.assert_called_once_with("https://github.com/aiden-guan/AntiAgent/releases")

        # Invalid scheme
        bad_payload = json.dumps({"url": "javascript:alert(1)"}).encode("utf-8")
        bad_req = urllib.request.Request(url, data=bad_payload, headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(bad_req)
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()


