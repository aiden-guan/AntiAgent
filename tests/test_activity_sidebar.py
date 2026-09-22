"""Unit tests for Activity Sidebar, tool calls audit filtering, and settings toggle."""

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from antiagent.audit.logger import AuditLogger
from antiagent.config import AntiAgentConfig, load_config
from antiagent.constants import DECISION_ALLOW
from antiagent.dashboard.server import DashboardRequestHandler
from antiagent.hook import handle_pre_tool_use


class TestActivitySidebarAndToolCalls(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.audit_log = os.path.join(self.test_dir, "test_audit.log")

    def test_config_default_and_env(self):
        # Default should be False (command log only)
        cfg = AntiAgentConfig()
        self.assertFalse(cfg.audit_include_tool_calls)
        data = cfg.to_dict()
        self.assertIn("audit_include_tool_calls", data)
        self.assertFalse(data["audit_include_tool_calls"])

        # Dict roundtrip
        cfg2 = AntiAgentConfig.from_dict({"audit_include_tool_calls": True})
        self.assertTrue(cfg2.audit_include_tool_calls)

        # Environment variable override
        os.environ["ANTIAGENT_AUDIT_INCLUDE_TOOL_CALLS"] = "true"
        try:
            loaded = load_config(self.test_dir)
            self.assertTrue(loaded.audit_include_tool_calls)
        finally:
            os.environ.pop("ANTIAGENT_AUDIT_INCLUDE_TOOL_CALLS", None)

    def test_hook_logs_commands_only_when_disabled(self):
        from unittest.mock import patch
        with patch("antiagent.config.get_global_config_dir", return_value=Path(self.test_dir)):
            ws_config_file = Path(self.test_dir) / ".antiagent.json"
            ws_config_file.write_text(json.dumps({
                "audit_enabled": True,
                "audit_include_tool_calls": False,
            }), encoding="utf-8")

            # Command tool call should be logged
            cmd_payload = {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "ls -la"},
                },
                "conversationId": "test-conv-1",
                "workspacePaths": [self.test_dir],
            }
            resp = handle_pre_tool_use(cmd_payload)
            self.assertEqual(resp["decision"], DECISION_ALLOW)

            logger = AuditLogger(os.path.join(self.test_dir, "audit.log"))
            entries = logger.read_recent(limit=10, include_tool_calls=True)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["tool"], "run_command")

            # Non-command tool call (e.g. view_file) should NOT be logged when audit_include_tool_calls is False
            file_payload = {
                "toolCall": {
                    "name": "view_file",
                    "args": {"AbsolutePath": "/Users/fake/file.txt"},
                },
                "conversationId": "test-conv-1",
                "workspacePaths": [self.test_dir],
            }
            resp = handle_pre_tool_use(file_payload)
            self.assertEqual(resp["decision"], DECISION_ALLOW)

            entries_after = logger.read_recent(limit=10, include_tool_calls=True)
            self.assertEqual(len(entries_after), 1)
            self.assertFalse(any(e.get("tool") == "view_file" for e in entries_after))

    def test_hook_logs_all_tools_when_enabled(self):
        from unittest.mock import patch
        with patch("antiagent.config.get_global_config_dir", return_value=Path(self.test_dir)):
            ws_config_file = Path(self.test_dir) / ".antiagent.json"
            ws_config_file.write_text(json.dumps({
                "audit_enabled": True,
                "audit_include_tool_calls": True,
            }), encoding="utf-8")

            file_payload = {
                "toolCall": {
                    "name": "view_file",
                    "args": {"AbsolutePath": "/Users/fake/file.txt"},
                },
                "conversationId": "test-conv-2",
                "workspacePaths": [self.test_dir],
            }
            resp = handle_pre_tool_use(file_payload)
            self.assertEqual(resp["decision"], DECISION_ALLOW)

            logger = AuditLogger(os.path.join(self.test_dir, "audit.log"))
            entries = logger.read_recent(limit=10, include_tool_calls=True)
            self.assertTrue(any(e.get("tool") == "view_file" for e in entries))

    def test_audit_logger_filtering(self):
        logger = AuditLogger(self.audit_log)
        logger.log_event("run_command", {"CommandLine": "git status"}, "allow", "Safe git")
        logger.log_event("view_file", {"AbsolutePath": "foo.py"}, "allow", "Safe read")
        logger.log_event("grep_search", {"Query": "test"}, "allow", "Safe search")
        logger.log_event("run_command", {"CommandLine": "pytest"}, "allow", "Safe test")

        # Commands only
        cmd_only = logger.read_recent(limit=10, include_tool_calls=False)
        self.assertEqual(len(cmd_only), 2)
        self.assertTrue(all(e["tool"] == "run_command" for e in cmd_only))

        # All tool calls
        all_entries = logger.read_recent(limit=10, include_tool_calls=True)
        self.assertEqual(len(all_entries), 4)


class TestDashboardSidebarEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()
        DashboardRequestHandler.workspace_path = cls.test_dir
        cls.port = 42431
        cls.server = ThreadingHTTPServer(("127.0.0.1", cls.port), DashboardRequestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_status_has_audit_include_tool_calls(self):
        url = f"http://127.0.0.1:{self.port}/api/status"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("audit_include_tool_calls", data)

    def test_config_updates_audit_include_tool_calls(self):
        url = f"http://127.0.0.1:{self.port}/api/config"
        payload = json.dumps({
            "audit_include_tool_calls": True,
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertTrue(data["config"]["audit_include_tool_calls"])

        # Check status reflects the change
        status_url = f"http://127.0.0.1:{self.port}/api/status"
        with urllib.request.urlopen(status_url) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["audit_include_tool_calls"])

    def test_api_audit_query_parameter(self):
        cfg = load_config(self.test_dir)
        logger = AuditLogger(cfg.audit_log_path)
        logger.log_event("run_command", {"CommandLine": "make test"}, "allow", "Safe make")
        logger.log_event("replace_file_content", {"TargetFile": "a.txt"}, "allow", "Safe edit")

        # Query with include_tool_calls=false
        url_cmds = f"http://127.0.0.1:{self.port}/api/audit?limit=10&include_tool_calls=false"
        with urllib.request.urlopen(url_cmds) as resp:
            self.assertEqual(resp.status, 200)
            events = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(all(e["tool"] == "run_command" for e in events))

        # Query with include_tool_calls=true
        url_all = f"http://127.0.0.1:{self.port}/api/audit?limit=10&include_tool_calls=true"
        with urllib.request.urlopen(url_all) as resp:
            self.assertEqual(resp.status, 200)
            events = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(any(e["tool"] == "replace_file_content" for e in events))

    def test_index_html_contains_sidebar_and_toggle(self):
        url = f"http://127.0.0.1:{self.port}/"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode("utf-8")
            self.assertIn("activitySidebar", html)
            self.assertIn("sidebarToggleBtn", html)
            self.assertIn("toggleAuditIncludeToolCalls", html)
            self.assertIn("activityDetailModal", html)


class TestCLIAuditFlags(unittest.TestCase):
    def test_cli_config_set_audit_include_tool_calls(self):
        import argparse
        from antiagent.cli import configure_cli
        from unittest.mock import patch

        temp_dir = tempfile.mkdtemp()
        with patch("antiagent.cli.save_workspace_config") as mock_save, \
             patch("antiagent.cli.load_config", return_value=AntiAgentConfig()):
            args = argparse.Namespace(
                set_profile=None,
                set_provider=None,
                set_model=None,
                set_audit_include_tool_calls="true",
                set_auto_pr_monitor=None,
                set_pr_auto_merge=None,
                set_pr_interval=None,
                global_config=False,
            )
            configure_cli(args)
            self.assertTrue(mock_save.called)
            saved_cfg = mock_save.call_args[0][0]
            self.assertTrue(saved_cfg.audit_include_tool_calls)


if __name__ == "__main__":
    unittest.main()
