"""Unit tests for Antigravity PreToolUse hook contract."""

import json
import unittest

from antiagent.constants import (
    DECISION_ALLOW,
    DECISION_ASK,
    DECISION_DENY,
    DECISION_FORCE_ASK,
)
from antiagent.hook import handle_pre_tool_use


class TestHookContract(unittest.TestCase):
    def test_pre_tool_use_allow_contract(self):
        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "ls -la"},
            },
            "stepIdx": 10,
            "conversationId": "test-uuid-1234",
            "workspacePaths": ["/Users/fakeuser/myproject"],
        }
        resp = handle_pre_tool_use(payload)
        self.assertIn("decision", resp)
        self.assertIn("reason", resp)
        self.assertEqual(resp["decision"], DECISION_ALLOW)

    def test_pre_tool_use_deny_contract(self):
        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "rm -rf /"},
            },
            "stepIdx": 11,
            "conversationId": "test-uuid-1234",
            "workspacePaths": ["/Users/fakeuser/myproject"],
        }
        resp = handle_pre_tool_use(payload)
        self.assertEqual(resp["decision"], DECISION_DENY)
        self.assertIn("reason", resp)

    def test_pre_tool_use_ask_contract(self):
        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git push --force"},
            },
            "stepIdx": 12,
            "conversationId": "test-uuid-1234",
            "workspacePaths": ["/Users/fakeuser/myproject"],
        }
        resp = handle_pre_tool_use(payload)
        self.assertEqual(resp["decision"], DECISION_FORCE_ASK)
        self.assertIn("reason", resp)


if __name__ == "__main__":
    unittest.main()
