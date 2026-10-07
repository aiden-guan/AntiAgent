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
    def setUp(self):
        # Interaction-state updates only happen while an `antiagent agy` bridge is active.
        from unittest.mock import patch

        self._bridge = patch("antiagent.engine.flow_presence.any_bridge_active", return_value=True)
        self._bridge.start()
        self.addCleanup(self._bridge.stop)

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

    def test_pre_tool_use_artifact_write_auto_approved(self):
        """Verify file mutation inside current conversation artifactDirectoryPath is auto-approved."""
        from antiagent.engine.interaction_state import InteractionState, InteractionStateStore

        store = InteractionStateStore.default()
        cid = "d0d717e3-97f7-49ee-a0b1-5ce92363f6ba"
        artifact_root = "/Users/fakeuser/.gemini/antigravity/brain/d0d717e3-97f7-49ee-a0b1-5ce92363f6ba"
        payload = {
            "toolCall": {
                "name": "write_to_file",
                "args": {
                    "TargetFile": f"{artifact_root}/scratch/test_fix_generator.py",
                    "CodeContent": "def test(): pass",
                },
            },
            "conversationId": cid,
            "workspacePaths": ["/Users/fakeuser/myproject"],
            "artifactDirectoryPath": artifact_root,
        }
        resp = handle_pre_tool_use(payload)
        self.assertEqual(resp["decision"], DECISION_ALLOW)
        self.assertIn("Auto-approved agent artifact mutation", resp.get("reason", ""))
        self.assertNotIn(resp["decision"], [DECISION_ASK, DECISION_FORCE_ASK, DECISION_DENY])

        state = store.get_state(cid)
        self.assertFalse(state.pending_approval)
        self.assertNotEqual(state.state, InteractionState.AWAITING_APPROVAL)

    def test_pre_tool_use_missing_artifact_directory_forces_ask(self):
        """When artifactDirectoryPath is missing from payload, external file write requires FORCE_ASK."""
        cid = "conv-missing-artifact-dir"
        target = "/Users/fakeuser/.gemini/antigravity/brain/conv-other/scratch/test.py"
        payload = {
            "toolCall": {
                "name": "write_to_file",
                "args": {"TargetFile": target, "CodeContent": "print('hello')"},
            },
            "conversationId": cid,
            "workspacePaths": ["/Users/fakeuser/myproject"],
        }
        resp = handle_pre_tool_use(payload)
        self.assertEqual(resp["decision"], DECISION_FORCE_ASK)
        self.assertIn("outside active workspace", resp.get("reason", ""))

    def test_pre_tool_use_different_conversation_artifact_forces_ask(self):
        """When target is in a different conversation artifact directory, it requires confirmation."""
        cid = "conv-current-1111"
        current_artifact = "/Users/fakeuser/.gemini/antigravity/brain/conv-current-1111"
        other_artifact = "/Users/fakeuser/.gemini/antigravity/brain/conv-other-2222"
        payload = {
            "toolCall": {
                "name": "write_to_file",
                "args": {
                    "TargetFile": f"{other_artifact}/scratch/test.py",
                    "CodeContent": "print('hello')",
                },
            },
            "conversationId": cid,
            "workspacePaths": ["/Users/fakeuser/myproject"],
            "artifactDirectoryPath": current_artifact,
        }
        resp = handle_pre_tool_use(payload)
        self.assertEqual(resp["decision"], DECISION_FORCE_ASK)
        self.assertIn("outside active workspace", resp.get("reason", ""))

    def test_pre_tool_use_artifact_write_disabled_by_config(self):
        """When auto_approve_artifact_writes=False, artifact write requires FORCE_ASK."""
        from unittest.mock import patch
        import os

        cid = "conv-disabled-cfg"
        artifact_root = "/Users/fakeuser/.gemini/antigravity/brain/conv-disabled-cfg"
        payload = {
            "toolCall": {
                "name": "write_to_file",
                "args": {
                    "TargetFile": f"{artifact_root}/scratch/test.py",
                    "CodeContent": "print('hello')",
                },
            },
            "conversationId": cid,
            "workspacePaths": ["/Users/fakeuser/myproject"],
            "artifactDirectoryPath": artifact_root,
        }
        with patch.dict(os.environ, {"ANTIAGENT_AUTO_APPROVE_ARTIFACT_WRITES": "false"}):
            resp = handle_pre_tool_use(payload)
            self.assertEqual(resp["decision"], DECISION_FORCE_ASK)
            self.assertIn("outside active workspace", resp.get("reason", ""))

    def test_read_json_payload_without_eof(self):
        """read_json_payload must return parsed JSON immediately without waiting for EOF."""
        import io
        from antiagent.hook import read_json_payload

        # Single line JSON
        stream = io.StringIO('{"toolCall": {"name": "view_file"}, "stepIdx": 70}\n')
        payload = read_json_payload(stream)
        self.assertEqual(payload.get("stepIdx"), 70)
        self.assertEqual(payload.get("toolCall", {}).get("name"), "view_file")

        # Multi-line formatted JSON
        stream = io.StringIO('{\n  "toolCall": {\n    "name": "view_file"\n  },\n  "stepIdx": 71\n}\n')
        payload = read_json_payload(stream)
        self.assertEqual(payload.get("stepIdx"), 71)

        # Leading blank lines and whitespace
        stream = io.StringIO('\n\n  \n{"toolCall": {"name": "run_command"}}\n')
        payload = read_json_payload(stream)
        self.assertEqual(payload.get("toolCall", {}).get("name"), "run_command")

        # Empty input
        stream = io.StringIO('')
        payload = read_json_payload(stream)
        self.assertEqual(payload, {})

    def test_parallel_hooks_with_unclosed_pipes(self):
        """Concurrent hook invocations with open write handles (simulating Windows leaked pipe handles) must not deadlock or time out."""
        import os
        import subprocess
        import sys
        import time

        pipes = [os.pipe() for _ in range(3)]
        procs = []

        try:
            for i in range(3):
                r, w = pipes[i]
                p = subprocess.Popen(
                    [sys.executable, "-m", "antiagent.hook"],
                    stdin=r,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=dict(os.environ, PYTHONPATH=".")
                )
                procs.append(p)

            payloads = [
                {"toolCall": {"name": "view_file", "args": {"AbsolutePath": "file1.txt"}}, "stepIdx": 37, "conversationId": "cid-parallel"},
                {"toolCall": {"name": "view_file", "args": {"AbsolutePath": "file2.txt"}}, "stepIdx": 38, "conversationId": "cid-parallel"},
                {"toolCall": {"name": "view_file", "args": {"AbsolutePath": "file3.txt"}}, "stepIdx": 39, "conversationId": "cid-parallel"},
            ]

            # Write data without closing the write descriptors
            for i in range(3):
                _, w = pipes[i]
                os.write(w, (json.dumps(payloads[i]) + "\n").encode())

            # All processes must finish promptly (within 3 seconds) instead of waiting for 10s timeout
            for i, p in enumerate(procs):
                stdout, stderr = p.communicate(timeout=4)
                self.assertEqual(p.returncode, 0, f"Process {i} failed: {stderr}")
                res = json.loads(stdout.strip())
                self.assertIn("decision", res)
                self.assertEqual(res["decision"], DECISION_ALLOW)
        finally:
            for r, w in pipes:
                try:
                    os.close(w)
                except OSError:
                    pass
                try:
                    os.close(r)
                except OSError:
                    pass

    def test_context_extractor_transcript_full_jsonl(self):
        """TranscriptContextExtractor should resolve transcript_full.jsonl when transcript.jsonl is absent."""
        import tempfile
        from pathlib import Path
        from antiagent.engine.context_extractor import TranscriptContextExtractor

        with tempfile.TemporaryDirectory() as temp_dir:
            brain_dir = Path(temp_dir)
            cid = "test-conv-full-only"
            logs_dir = brain_dir / cid / ".system_generated" / "logs"
            logs_dir.mkdir(parents=True)
            full_file = logs_dir / "transcript_full.jsonl"
            full_file.write_text(
                json.dumps({"type": "USER_INPUT", "content": "<USER_REQUEST>Build docs</USER_REQUEST>"}) + "\n",
                encoding="utf-8"
            )

            extractor = TranscriptContextExtractor(base_brain_dir=str(brain_dir))
            ctx = extractor.extract_context(cid)
            self.assertEqual(ctx.user_prompt, "Build docs")
            self.assertEqual(ctx.primary_goal, "Build docs")


if __name__ == "__main__":
    unittest.main()


