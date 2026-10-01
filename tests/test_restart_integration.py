"""Integration tests for real AntiAgent dashboard process restart and launch context preservation."""

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


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def query_json(url: str, timeout: float = 3.0):
    req = urllib.request.Request(url, headers={"User-Agent": "AntiAgent-Test"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_json(url: str, data: dict, timeout: float = 5.0):
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "AntiAgent-Test", "Origin": "http://127.0.0.1"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class TestDashboardRestartIntegration(unittest.TestCase):
    def setUp(self):
        self.port = find_free_port()
        self.host = "127.0.0.1"
        self.proc = None

    def tearDown(self):
        if self.proc:
            if self.proc.stdout:
                try:
                    self.proc.stdout.close()
                except Exception:
                    pass
            if self.proc.stderr:
                try:
                    self.proc.stderr.close()
                except Exception:
                    pass
            if self.proc.poll() is None:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=2.0)
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass

    def test_restart_endpoint_returns_structured_plan(self):
        """Verify POST /api/update/restart and /api/update/relaunch_app return unified structured plan."""
        from antiagent.dashboard.server import DashboardRequestHandler, SERVER_INSTANCE_ID
        from io import BytesIO

        class DummyHandler(DashboardRequestHandler):
            def __init__(self):
                self.headers = {"Host": "127.0.0.1"}
                self.workspace_path = "/tmp"
                self.launch_context = None
                self.wfile = BytesIO()

            def send_response(self, code, message=None):
                self.status_code = code

            def send_header(self, keyword, value):
                pass

            def end_headers(self):
                pass

            def _add_security_headers(self):
                pass

        # Set testing mode so background threads don't trigger os._exit
        old_val = os.environ.get("ANTIAGENT_TESTING")
        os.environ["ANTIAGENT_TESTING"] = "1"
        try:
            handler = DummyHandler()
            handler._handle_api_restart(force_desktop=False)
            res = json.loads(handler.wfile.getvalue().decode("utf-8"))

            self.assertTrue(res["ok"])
            self.assertIn("mode", res)
            self.assertIn("target_version", res)
            self.assertEqual(res["server_instance_id"], SERVER_INSTANCE_ID)
            self.assertIn("desktop_relaunch", res)
            self.assertIn("message", res)

            # Test force_desktop=True
            handler_desktop = DummyHandler()
            handler_desktop._handle_api_restart(force_desktop=True)
            res_desktop = json.loads(handler_desktop.wfile.getvalue().decode("utf-8"))
            self.assertTrue(res_desktop["ok"])
            self.assertTrue(res_desktop["desktop_relaunch"])
            self.assertEqual(res_desktop["mode"], "macos_bundle")
        finally:
            if old_val is not None:
                os.environ["ANTIAGENT_TESTING"] = old_val
            else:
                os.environ.pop("ANTIAGENT_TESTING", None)

    def test_real_server_startup_and_status(self):
        """Start real dashboard server, verify server_instance_id and install_mode in /api/status."""
        py = sys.executable or "python3"
        env = os.environ.copy()
        env["ANTIAGENT_TESTING"] = "1"

        cmd = [
            py, "-m", "antiagent.dashboard",
            "--host", self.host,
            "--port", str(self.port),
            "--no-open",
        ]
        self.proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        base_url = f"http://{self.host}:{self.port}"
        status_url = f"{base_url}/api/status"

        # Wait for server to become responsive
        initial_status = None
        for _ in range(30):
            try:
                initial_status = query_json(status_url, timeout=1.0)
                if initial_status and initial_status.get("version"):
                    break
            except Exception:
                time.sleep(0.1)

        self.assertIsNotNone(initial_status, "Server failed to start within timeout.")
        self.assertIn("server_instance_id", initial_status)
        self.assertTrue(len(initial_status["server_instance_id"]) > 10)
        self.assertIn("install_mode", initial_status)
        self.assertIn("current_executable", initial_status)
        self.assertIn("package_path", initial_status)

        # Verify POST /api/update/restart responds with structured plan
        restart_url = f"{base_url}/api/update/restart"
        restart_resp = post_json(restart_url, {})
        self.assertTrue(restart_resp["ok"])
        self.assertEqual(restart_resp["server_instance_id"], initial_status["server_instance_id"])


if __name__ == "__main__":
    unittest.main()
