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

    def test_pre_tool_use_with_transcript_path(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp_dir:
            tfile = Path(temp_dir) / "custom_transcript.jsonl"
            tfile.write_text(
                json.dumps({"type": "USER_INPUT", "content": "<USER_REQUEST>Run tests</USER_REQUEST>"}) + "\n",
                encoding="utf-8"
            )
            payload = {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "pytest"},
                },
                "stepIdx": 5,
                "conversationId": "test-uuid-5678",
                "workspacePaths": [temp_dir],
                "transcriptPath": str(tfile),
            }
            resp = handle_pre_tool_use(payload)
            self.assertIn("decision", resp)
            self.assertEqual(resp["decision"], DECISION_ALLOW)

    def test_pre_tool_use_with_full_metadata_contract(self):
        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git diff"},
            },
            "stepIdx": 42,
            "conversationId": "full-meta-uuid-9999",
            "workspacePaths": ["/Users/fakeuser/project"],
            "transcriptPath": "/Users/fakeuser/.gemini/antigravity/brain/full-meta-uuid-9999/.system_generated/logs/transcript.jsonl",
            "artifactDirectoryPath": "/Users/fakeuser/.gemini/antigravity/brain/full-meta-uuid-9999",
            "modelName": "gemini-2.5-pro",
        }
        resp = handle_pre_tool_use(payload)
        self.assertIn("decision", resp)
        self.assertIn("reason", resp)
        self.assertEqual(resp["decision"], DECISION_ALLOW)


if __name__ == "__main__":
    unittest.main()

