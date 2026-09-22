"""Unit tests for Auto-PR monitoring, CI tracking, and GitHub CLI integration."""

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from antiagent.config import AntiAgentConfig, load_config
from antiagent.constants import DECISION_ALLOW
from antiagent.engine.heuristics.command_guard import CommandGuard
from antiagent.engine.pr_monitor import (
    PRMonitor,
    PRMonitorManager,
    TupleResult,
    check_gh_cli_status,
    get_pr_checks,
    get_pr_details,
    get_pr_failure_logs,
    list_pull_requests,
    merge_pr,
)


class TestPRMonitor(unittest.TestCase):
    """Test suite for pull request and CI monitoring."""

    def test_gh_cli_status_installed(self):
        """Test check_gh_cli_status when gh is present."""
        with patch("shutil.which", return_value="/usr/local/bin/gh"):
            with patch("subprocess.run") as mock_run:
                # Mock version
                v_res = MagicMock()
                v_res.returncode = 0
                v_res.stdout = "gh version 2.96.0 (2026-07-02)\n"
                v_res.stderr = ""

                # Mock auth status
                a_res = MagicMock()
                a_res.returncode = 0
                a_res.stdout = "Logged in to github.com account testuser (keyring)\n"
                a_res.stderr = ""

                mock_run.side_effect = [v_res, a_res]
                st = check_gh_cli_status()
                self.assertTrue(st["installed"])
                self.assertTrue(st["authenticated"])
                self.assertEqual(st["user"], "testuser")
                self.assertEqual(st["version"], "2.96.0")

    def test_gh_cli_status_not_installed(self):
        """Test check_gh_cli_status when gh is missing."""
        with patch("shutil.which", return_value=None):
            st = check_gh_cli_status()
            self.assertFalse(st["installed"])
            self.assertFalse(st["authenticated"])
            self.assertIn("not installed", st["error"])

    @patch("antiagent.engine.pr_monitor._run_gh_command")
    def test_get_pr_details_success(self, mock_cmd):
        """Test get_pr_details with successful response."""
        mock_payload = {
            "number": 42,
            "title": "feat: add auto-pr monitoring",
            "url": "https://github.com/aiden-guan/AntiAgent/pull/42",
            "state": "OPEN",
            "author": {"login": "aiden-guan"},
            "headRefName": "feat/pr-monitor",
            "baseRefName": "main",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "reviewDecision": "APPROVED",
        }
        mock_cmd.return_value = TupleResult(0, json.dumps(mock_payload), "")
        res = get_pr_details(pr_identifier=42)
        self.assertTrue(res["ok"])
        self.assertEqual(res["number"], 42)
        self.assertEqual(res["author"], "aiden-guan")
        self.assertEqual(res["headRefName"], "feat/pr-monitor")

    @patch("antiagent.engine.pr_monitor._run_gh_command")
    def test_get_pr_details_no_pr(self, mock_cmd):
        """Test get_pr_details when no PR is associated with current branch."""
        mock_cmd.return_value = TupleResult(1, "", "no pull requests found for branch 'main'")
        res = get_pr_details()
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("no_pr"))

    @patch("antiagent.engine.pr_monitor._run_gh_command")
    def test_get_pr_checks_all_passed(self, mock_cmd):
        """Test rollup calculations when all CI checks pass."""
        mock_payload = {
            "number": 10,
            "title": "Add feature",
            "url": "https://github.com/foo/bar/pull/10",
            "state": "OPEN",
            "headRefName": "feat",
            "baseRefName": "main",
            "mergeable": "MERGEABLE",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "Unit Tests",
                    "workflowName": "CI",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                    "detailsUrl": "https://ci/tests",
                },
                {
                    "__typename": "CheckRun",
                    "name": "Linter",
                    "workflowName": "CI",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                    "detailsUrl": "https://ci/lint",
                },
                {
                    "__typename": "StatusContext",
                    "context": "coverage/codecov",
                    "state": "SUCCESS",
                    "targetUrl": "https://codecov.io/test",
                },
            ],
        }
        mock_cmd.return_value = TupleResult(0, json.dumps(mock_payload), "")
        data = get_pr_checks(10)
        self.assertTrue(data["ok"])
        self.assertEqual(data["overall_status"], "SUCCESS")
        self.assertTrue(data["is_all_passed"])
        self.assertFalse(data["has_failures"])
        self.assertEqual(data["summary"]["total"], 3)
        self.assertEqual(data["summary"]["passed"], 3)
        self.assertEqual(data["summary"]["failed"], 0)
        self.assertEqual(data["summary"]["pending"], 0)

    @patch("antiagent.engine.pr_monitor._run_gh_command")
    def test_get_pr_checks_with_failures(self, mock_cmd):
        """Test rollup calculations when one or more checks fail."""
        mock_payload = {
            "number": 11,
            "title": "Fix bug",
            "url": "https://github.com/foo/bar/pull/11",
            "state": "OPEN",
            "headRefName": "fix",
            "baseRefName": "main",
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "Build",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                },
                {
                    "__typename": "CheckRun",
                    "name": "Integration Tests",
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                    "detailsUrl": "https://ci/fail",
                },
                {
                    "__typename": "StatusContext",
                    "context": "security-scan",
                    "state": "ERROR",
                },
            ],
        }
        mock_cmd.return_value = TupleResult(0, json.dumps(mock_payload), "")
        data = get_pr_checks(11)
        self.assertTrue(data["ok"])
        self.assertEqual(data["overall_status"], "FAILURE")
        self.assertTrue(data["has_failures"])
        self.assertFalse(data["is_all_passed"])
        self.assertEqual(data["summary"]["total"], 3)
        self.assertEqual(data["summary"]["passed"], 1)
        self.assertEqual(data["summary"]["failed"], 2)

    @patch("antiagent.engine.pr_monitor._run_gh_command")
    def test_get_pr_checks_pending(self, mock_cmd):
        """Test rollup calculations when checks are in progress."""
        mock_payload = {
            "number": 12,
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "Build",
                    "status": "COMPLETED",
                    "conclusion": "SUCCESS",
                },
                {
                    "__typename": "CheckRun",
                    "name": "Test Suite",
                    "status": "IN_PROGRESS",
                    "conclusion": "",
                },
            ],
        }
        mock_cmd.return_value = TupleResult(0, json.dumps(mock_payload), "")
        data = get_pr_checks(12)
        self.assertTrue(data["ok"])
        self.assertEqual(data["overall_status"], "PENDING")
        self.assertTrue(data["is_pending"])
        self.assertEqual(data["summary"]["pending"], 1)

    @patch("antiagent.engine.pr_monitor._run_gh_command")
    def test_get_pr_failure_logs_extraction(self, mock_cmd):
        """Test extraction of failed run logs."""
        pr_payload = {
            "number": 15,
            "title": "Broken feature",
            "branch": "feat-broken",
            "headRefName": "feat-broken",
            "statusCheckRollup": [
                {"__typename": "CheckRun", "name": "Pytest", "status": "COMPLETED", "conclusion": "FAILURE"}
            ],
        }
        runs_payload = [
            {"databaseId": 12345, "name": "CI", "status": "completed", "conclusion": "failure"}
        ]
        log_output = "FAIL: test_something_broken (tests.test_core)\nAssertionError: expected True but got False"

        # Mock sequence: 1. get_pr_checks -> 2. run list -> 3. run view --log-failed
        mock_cmd.side_effect = [
            TupleResult(0, json.dumps(pr_payload), ""),
            TupleResult(0, json.dumps(runs_payload), ""),
            TupleResult(0, log_output, ""),
        ]

        res = get_pr_failure_logs(15)
        self.assertTrue(res["ok"])
        self.assertTrue(res["has_failures"])
        self.assertEqual(len(res["failure_logs"]), 1)
        self.assertEqual(res["failure_logs"][0]["run_id"], 12345)
        self.assertIn("AssertionError", res["failure_logs"][0]["log"])

    @patch("antiagent.engine.pr_monitor._run_gh_command")
    def test_merge_pr(self, mock_cmd):
        """Test merge_pr command generation."""
        mock_cmd.return_value = TupleResult(0, "Auto-merge set up for pull request #20", "")
        res = merge_pr(pr_identifier=20, auto=True, method="squash")
        self.assertTrue(res["ok"])
        # Check arguments sent to _run_gh_command
        args = mock_cmd.call_args[0][0]
        self.assertIn("pr", args)
        self.assertIn("merge", args)
        self.assertIn("20", args)
        self.assertIn("--auto", args)
        self.assertIn("--squash", args)

    @patch("antiagent.engine.pr_monitor._run_gh_command")
    def test_list_pull_requests(self, mock_cmd):
        """Test listing open PRs."""
        mock_prs = [
            {"number": 1, "title": "PR 1", "state": "OPEN", "headRefName": "patch-1", "author": {"login": "alice"}},
            {"number": 2, "title": "PR 2", "state": "OPEN", "headRefName": "patch-2", "author": {"login": "bob"}},
        ]
        mock_cmd.return_value = TupleResult(0, json.dumps(mock_prs), "")
        res = list_pull_requests()
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["pull_requests"]), 2)

    def test_pr_monitor_manager(self):
        """Test PRMonitorManager registry."""
        mgr = PRMonitorManager()
        with patch.object(PRMonitor, "start_background", return_value=True):
            with patch.object(PRMonitor, "poll_once") as mock_poll:
                mock_poll.return_value = {"ok": True, "overall_status": "PENDING", "summary": {}}
                m = mgr.start_monitor(pr_identifier="99", workspace_dir="/tmp/test_ws")
                self.assertIsNotNone(m)
                self.assertEqual(m.pr_identifier, "99")

                # Retrieve monitor
                m2 = mgr.get_monitor(pr_identifier="99", workspace_dir="/tmp/test_ws")
                self.assertEqual(m, m2)

                # Stop monitor
                stopped = mgr.stop_monitor(pr_identifier="99", workspace_dir="/tmp/test_ws")
                self.assertTrue(stopped)

    def test_command_guard_gh_safe_commands(self):
        """Test that CommandGuard auto-approves safe read-only gh and antiagent pr commands."""
        guard = CommandGuard()

        safe_cases = [
            "gh pr status",
            "gh pr checks",
            "gh pr view",
            "gh pr view 42",
            "gh pr list",
            "gh pr diff",
            "gh run view 123",
            "gh auth status",
            "gh pr create --title 'feat' --body 'test'",
            "antiagent pr status",
            "antiagent pr list",
            "antiagent pr autofix",
        ]

        for cmd in safe_cases:
            res = guard.evaluate(cmd)
            self.assertIsNotNone(res, f"Expected verdict for: {cmd}")
            decision, reason = res
            self.assertEqual(decision, DECISION_ALLOW, f"Command '{cmd}' should be ALLOW, got {decision} ({reason})")

    def test_config_pr_monitoring_options(self):
        """Test AntiAgentConfig PR monitoring attributes and env overrides."""
        cfg = AntiAgentConfig()
        self.assertFalse(cfg.auto_pr_monitor)
        self.assertFalse(cfg.pr_monitor_auto_merge)
        self.assertEqual(cfg.pr_monitor_interval, 15)

        # Serialization
        d = cfg.to_dict()
        self.assertIn("auto_pr_monitor", d)
        self.assertIn("pr_monitor_auto_merge", d)
        self.assertIn("pr_monitor_interval", d)

        # Deserialization
        loaded = AntiAgentConfig.from_dict({"auto_pr_monitor": True, "pr_monitor_auto_merge": True, "pr_monitor_interval": 30})
        self.assertTrue(loaded.auto_pr_monitor)
        self.assertTrue(loaded.pr_monitor_auto_merge)
        self.assertEqual(loaded.pr_monitor_interval, 30)

        # Environment variable overrides
        with patch.dict(os.environ, {
            "ANTIAGENT_AUTO_PR_MONITOR": "true",
            "ANTIAGENT_PR_AUTO_MERGE": "1",
            "ANTIAGENT_PR_INTERVAL": "25",
        }):
            cfg_env = load_config()
            self.assertTrue(cfg_env.auto_pr_monitor)
            self.assertTrue(cfg_env.pr_monitor_auto_merge)
            self.assertEqual(cfg_env.pr_monitor_interval, 25)


if __name__ == "__main__":
    unittest.main()
