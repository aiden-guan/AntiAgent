"""Unit tests for multi-tier evaluator."""

import os
import unittest

from antiagent.config import AntiAgentConfig
from antiagent.constants import (
    DECISION_ALLOW,
    DECISION_ASK,
    DECISION_DENY,
    PROFILE_AUTONOMOUS,
    PROFILE_BALANCED,
    PROFILE_PARANOID,
)
from antiagent.engine.evaluator import AntiAgentEvaluator


class TestEvaluator(unittest.TestCase):
    def setUp(self):
        self.workspace = os.path.abspath("/tmp/test_workspace")
        self.config = AntiAgentConfig(
            profile=PROFILE_BALANCED,
            provider="offline",
            auto_approve_reads=True,
        )
        self.evaluator = AntiAgentEvaluator(self.config, workspace_paths=[self.workspace])

    def test_safe_read_tool_auto_approved(self):
        res = self.evaluator.evaluate("view_file", {"AbsolutePath": f"{self.workspace}/index.js"})
        self.assertEqual(res.decision, DECISION_ALLOW)

        res = self.evaluator.evaluate("list_dir", {"DirectoryPath": self.workspace})
        self.assertEqual(res.decision, DECISION_ALLOW)

    def test_safe_read_tool_on_sensitive_target_asks(self):
        res = self.evaluator.evaluate("view_file", {"AbsolutePath": f"{self.workspace}/.env"})
        self.assertEqual(res.decision, DECISION_ASK)

    def test_hard_deny_command(self):
        res = self.evaluator.evaluate("run_command", {"CommandLine": "rm -rf /"})
        self.assertEqual(res.decision, DECISION_DENY)

    def test_safe_command_cached(self):
        args = {"CommandLine": "ls -la"}
        res1 = self.evaluator.evaluate("run_command", args)
        self.assertEqual(res1.decision, DECISION_ALLOW)

        # Second call should be served from cache
        res2 = self.evaluator.evaluate("run_command", args)
        self.assertEqual(res2.decision, DECISION_ALLOW)
        self.assertIn("cached", res2.reason)

    def test_paranoid_profile(self):
        paranoid_cfg = AntiAgentConfig(profile=PROFILE_PARANOID, provider="offline")
        evaluator = AntiAgentEvaluator(paranoid_cfg, workspace_paths=[self.workspace])

        # Read is allowed
        res = evaluator.evaluate("run_command", {"CommandLine": "ls"})
        self.assertEqual(res.decision, DECISION_ALLOW)

        # File modification asks in paranoid
        res = evaluator.evaluate(
            "write_to_file",
            {"TargetFile": f"{self.workspace}/test.txt", "CodeContent": "hi"},
        )
        self.assertEqual(res.decision, DECISION_ASK)

    def test_autonomous_profile(self):
        auto_cfg = AntiAgentConfig(profile=PROFILE_AUTONOMOUS, provider="offline")
        evaluator = AntiAgentEvaluator(auto_cfg, workspace_paths=[self.workspace])

        # File modification allows in autonomous
        res = evaluator.evaluate(
            "write_to_file",
            {"TargetFile": f"{self.workspace}/test.txt", "CodeContent": "hi"},
        )
        self.assertEqual(res.decision, DECISION_ALLOW)

        # Hard-denied root wipe still denies in autonomous
        res = evaluator.evaluate("run_command", {"CommandLine": "rm -rf /"})
        self.assertEqual(res.decision, DECISION_DENY)


if __name__ == "__main__":
    unittest.main()
