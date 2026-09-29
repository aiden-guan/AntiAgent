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

    def test_pre_tool_use_updates_interaction_state_awaiting_approval(self):
        """Verify PreToolUse DECISION_FORCE_ASK updates InteractionStateStore to AWAITING_APPROVAL."""
        from antiagent.engine.interaction_state import ActiveSurface, InteractionState, InteractionStateStore

        store = InteractionStateStore.default()
        cid = "conv-hook-ask-state"
        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git push --force"},
            },
            "stepIdx": 20,
            "conversationId": cid,
            "workspacePaths": ["/Users/fakeuser/myproject"],
        }
        resp = handle_pre_tool_use(payload)
        self.assertEqual(resp["decision"], DECISION_FORCE_ASK)

        state = store.get_state(cid)
        self.assertEqual(state.state, InteractionState.AWAITING_APPROVAL)
        self.assertEqual(state.active_surface, ActiveSurface.APPROVAL)
        self.assertTrue(state.pending_approval)
        self.assertEqual(state.active_tool, "run_command")
        self.assertFalse(state.fully_idle)

    def test_pre_tool_use_snake_case_conversation_id(self):
        """Verify PreToolUse parses snake_case conversation_id."""
        from antiagent.engine.interaction_state import InteractionState, InteractionStateStore

        store = InteractionStateStore.default()
        cid = "conv-snake-cid"
        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git push --force"},
            },
            "conversation_id": cid,
        }
        resp = handle_pre_tool_use(payload)
        self.assertEqual(resp["decision"], DECISION_FORCE_ASK)

        state = store.get_state(cid)
        self.assertEqual(state.state, InteractionState.AWAITING_APPROVAL)
        self.assertTrue(state.pending_approval)

    def test_pre_tool_use_fallback_to_active_conversation_id(self):
        """Verify PreToolUse falls back to store's active_conversation_id when not in payload."""
        from antiagent.engine.interaction_state import InteractionState, InteractionStateStore

        store = InteractionStateStore.default()
        cid = "conv-active-fallback"
        store.set_active_conversation_id(cid)

        payload = {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git push --force"},
            },
        }
        resp = handle_pre_tool_use(payload)
        self.assertEqual(resp["decision"], DECISION_FORCE_ASK)

        state = store.get_state(cid)
        self.assertEqual(state.state, InteractionState.AWAITING_APPROVAL)
        self.assertTrue(state.pending_approval)

    def test_pre_tool_use_denied_records_deny_last_event(self):
        """Verify PreToolUse on DENY records last_event='PreToolUse:deny'."""
        from antiagent.constants import DECISION_DENY
        from antiagent.engine.interaction_state import InteractionStateStore
        from unittest.mock import patch, MagicMock

        cid = "conv-deny-event"
        store = InteractionStateStore.default()

        with patch("antiagent.hook.AntiAgentEvaluator") as mock_eval:
            instance = MagicMock()
            instance.evaluate.return_value = MagicMock(
                decision=DECISION_DENY,
                reason="Hard blocked",
                to_antigravity_dict=lambda: {"decision": DECISION_DENY, "reason": "Hard blocked"},
            )
            mock_eval.return_value = instance

            payload = {
                "toolCall": {"name": "test_cmd", "args": {}},
                "conversationId": cid,
            }
            resp = handle_pre_tool_use(payload)
            self.assertEqual(resp["decision"], DECISION_DENY)

            st = store.get_state(cid)
            self.assertEqual(st.last_event, "PreToolUse:deny")

    def test_pre_tool_use_ask_question_sets_question_state(self):
        """Verify PreToolUse for ask_question transitions to AWAITING_QUESTION and QUESTION surface."""
        from antiagent.engine.interaction_state import ActiveSurface, InteractionState, InteractionStateStore

        cid = "conv-question-hook"
        store = InteractionStateStore.default()

        payload = {
            "toolCall": {
                "name": "ask_question",
                "args": {
                    "question": "Which backend?",
                    "options": ["FastAPI", "Flask"],
                },
            },
            "conversationId": cid,
        }
        resp = handle_pre_tool_use(payload)
        self.assertEqual(resp["decision"], DECISION_ALLOW)

        st = store.get_state(cid)
        self.assertEqual(st.state, InteractionState.AWAITING_QUESTION)
        self.assertEqual(st.active_surface, ActiveSurface.QUESTION)
        self.assertTrue(st.pending_question)
        self.assertEqual(st.active_tool, "ask_question")


if __name__ == "__main__":
    unittest.main()


