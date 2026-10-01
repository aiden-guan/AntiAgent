"""Comprehensive test suite for Antigravity conversation artifact/brain directory auto-approval.

Verifies:
1. FSGuard unit tests (workspace write, artifact write, replace, delete, sibling conv isolation,
   outside write, sensitive target precedence, path traversal rejection, prefix collision rejection,
   POSIX symlink escape rejection).
2. AntiAgentEvaluator test verifying deterministic ALLOW without invoking LLMSupervisor.
3. Hook contract tests for PreToolUse with artifactDirectoryPath (ALLOW, no pending approval).
4. Missing artifactDirectoryPath handling (FORCE_ASK).
5. Sibling conversation isolation at hook level (FORCE_ASK).
6. Config toggle (auto_approve_artifact_writes=False) restoring outside-workspace behavior.
7. Windows case-insensitivity and path containment.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from antiagent.config import AntiAgentConfig
from antiagent.constants import (
    DECISION_ALLOW,
    DECISION_ASK,
    DECISION_FORCE_ASK,
    PROFILE_BALANCED,
    PROFILE_PARANOID,
)
from antiagent.engine.evaluator import AntiAgentEvaluator
from antiagent.engine.heuristics.fs_guard import FSGuard
from antiagent.engine.interaction_state import (
    ActiveSurface,
    InteractionState,
    InteractionStateStore,
)
from antiagent.hook import handle_pre_tool_use


class TestArtifactDirectoryFSGuard(unittest.TestCase):
    """Unit tests for FSGuard with trusted_artifact_paths."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(dir=os.getcwd())
        self.workspace = os.path.join(self.test_dir, "workspace")
        self.conv_a = os.path.join(self.test_dir, "brain", "conv-a")
        self.conv_b = os.path.join(self.test_dir, "brain", "conv-b")
        self.outside = os.path.join(self.test_dir, "outside")

        os.makedirs(self.workspace, exist_ok=True)
        os.makedirs(self.conv_a, exist_ok=True)
        os.makedirs(self.conv_b, exist_ok=True)
        os.makedirs(self.outside, exist_ok=True)

        self.guard = FSGuard(
            workspace_paths=[self.workspace],
            trusted_artifact_paths=[self.conv_a],
        )

    def tearDown(self):
        if os.path.isdir(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_1_write_inside_workspace_preserved(self):
        """1. write_to_file inside workspace -> None (passed to next evaluation tier)."""
        target = os.path.join(self.workspace, "src", "index.py")
        res = self.guard.evaluate_file_tool("write_to_file", {"TargetFile": target})
        self.assertIsNone(res)

    def test_2_write_in_trusted_artifact_scratch(self):
        """2. write_to_file in brain/conv-a/scratch/test.py -> DECISION_ALLOW."""
        target = os.path.join(self.conv_a, "scratch", "test.py")
        res = self.guard.evaluate_file_tool("write_to_file", {"TargetFile": target})
        self.assertIsNotNone(res)
        self.assertEqual(res[0], DECISION_ALLOW)
        self.assertIn("Auto-approved agent artifact mutation", res[1])

    def test_3_replace_file_content_in_trusted_artifact(self):
        """3. replace_file_content in brain/conv-a/artifacts/result.md -> DECISION_ALLOW."""
        target = os.path.join(self.conv_a, "artifacts", "result.md")
        res = self.guard.evaluate_file_tool("replace_file_content", {"TargetFile": target})
        self.assertIsNotNone(res)
        self.assertEqual(res[0], DECISION_ALLOW)
        self.assertIn("Auto-approved agent artifact mutation", res[1])

    def test_4_delete_file_in_trusted_artifact(self):
        """4. delete_file inside brain/conv-a -> DECISION_ALLOW."""
        target = os.path.join(self.conv_a, "scratch", "temp.txt")
        res = self.guard.evaluate_file_tool("delete_file", {"TargetFile": target})
        self.assertIsNotNone(res)
        self.assertEqual(res[0], DECISION_ALLOW)
        self.assertIn("Auto-approved agent artifact mutation", res[1])

    def test_5_write_in_sibling_conversation_requires_ask(self):
        """5. write_to_file in brain/conv-b/file.py -> ASK (outside workspace)."""
        target = os.path.join(self.conv_b, "file.py")
        res = self.guard.evaluate_file_tool("write_to_file", {"TargetFile": target})
        self.assertIsNotNone(res)
        self.assertEqual(res[0], DECISION_ASK)
        self.assertIn("outside active workspace", res[1])

    def test_6_write_outside_workspace_requires_ask(self):
        """6. write_to_file in outside/file.py -> ASK."""
        target = os.path.join(self.outside, "file.py")
        res = self.guard.evaluate_file_tool("write_to_file", {"TargetFile": target})
        self.assertIsNotNone(res)
        self.assertEqual(res[0], DECISION_ASK)
        self.assertIn("outside active workspace", res[1])

    def test_7_sensitive_target_in_artifact_requires_ask(self):
        """7. Sensitive target (.env, id_rsa, etc.) inside artifact directory still requires ASK."""
        target_env = os.path.join(self.conv_a, ".env")
        res = self.guard.evaluate_file_tool("write_to_file", {"TargetFile": target_env})
        self.assertIsNotNone(res)
        self.assertEqual(res[0], DECISION_ASK)
        self.assertIn("Mutating sensitive target", res[1])

        target_ssh = os.path.join(self.conv_a, "id_rsa")
        res_ssh = self.guard.evaluate_file_tool("write_to_file", {"TargetFile": target_ssh})
        self.assertIsNotNone(res_ssh)
        self.assertEqual(res_ssh[0], DECISION_ASK)
        self.assertIn("Mutating sensitive target", res_ssh[1])

    def test_8_path_traversal_out_of_artifact_rejected(self):
        """8. Path traversal (conv-a/../conv-b/file.py) must not be treated as trusted."""
        target = os.path.join(self.conv_a, "..", "conv-b", "file.py")
        res = self.guard.evaluate_file_tool("write_to_file", {"TargetFile": target})
        self.assertIsNotNone(res)
        self.assertEqual(res[0], DECISION_ASK)
        self.assertIn("outside active workspace", res[1])

    def test_9_prefix_collision_rejected(self):
        """9. Prefix collision (conv-a vs conv-a-malicious) must not be treated as trusted."""
        target = self.conv_a + "-malicious" + os.path.sep + "file.py"
        res = self.guard.evaluate_file_tool("write_to_file", {"TargetFile": target})
        self.assertIsNotNone(res)
        self.assertEqual(res[0], DECISION_ASK)
        self.assertIn("outside active workspace", res[1])

    def test_10_posix_symlink_escape_rejected(self):
        """10. Symlink inside conv-a pointing outside must not escape to allow writes outside."""
        link_path = os.path.join(self.conv_a, "escape_link")
        try:
            os.symlink(self.outside, link_path)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks not supported on this platform/filesystem.")

        target = os.path.join(link_path, "escaped_file.py")
        res = self.guard.evaluate_file_tool("write_to_file", {"TargetFile": target})
        self.assertIsNotNone(res)
        self.assertEqual(res[0], DECISION_ASK)
        self.assertIn("outside active workspace", res[1])


class TestArtifactDirectoryEvaluator(unittest.TestCase):
    """Evaluator-level tests verifying bypass of supervisor and deterministic ALLOW."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(dir=os.getcwd())
        self.workspace = os.path.join(self.test_dir, "workspace")
        self.artifact_root = os.path.join(self.test_dir, "brain", "conv-eval")
        os.makedirs(self.workspace, exist_ok=True)
        os.makedirs(self.artifact_root, exist_ok=True)

        self.config = AntiAgentConfig(profile=PROFILE_BALANCED)

    def tearDown(self):
        if os.path.isdir(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_evaluator_deterministic_allow_bypasses_supervisor(self):
        """AntiAgentEvaluator auto-approves artifact writes without calling LLMSupervisor."""
        evaluator = AntiAgentEvaluator(
            self.config,
            workspace_paths=[self.workspace],
            trusted_artifact_paths=[self.artifact_root],
        )
        # Mock LLMSupervisor to ensure it is NEVER called
        evaluator.supervisor.review_tool_call = MagicMock()

        target = os.path.join(self.artifact_root, "scratch", "test_fix_generator.py")
        result = evaluator.evaluate(
            "write_to_file",
            {"TargetFile": target, "CodeContent": "def solve(): pass"},
        )
        self.assertEqual(result.decision, DECISION_ALLOW)
        self.assertIn("Auto-approved agent artifact mutation", result.reason)
        evaluator.supervisor.review_tool_call.assert_not_called()

    def test_evaluator_paranoid_profile_preserves_artifact_auto_approval(self):
        """Even under paranoid profile, routine artifact writes in the agent's scratch dir are allowed."""
        paranoid_cfg = AntiAgentConfig(profile=PROFILE_PARANOID)
        evaluator = AntiAgentEvaluator(
            paranoid_cfg,
            workspace_paths=[self.workspace],
            trusted_artifact_paths=[self.artifact_root],
        )
        target = os.path.join(self.artifact_root, "scratch", "note.md")
        result = evaluator.evaluate(
            "write_to_file",
            {"TargetFile": target, "CodeContent": "# Notes"},
        )
        self.assertEqual(result.decision, DECISION_ALLOW)

    def test_evaluator_disabled_config_forces_ask(self):
        """When auto_approve_artifact_writes=False, evaluator routes artifact write to ASK."""
        disabled_cfg = AntiAgentConfig(auto_approve_artifact_writes=False)
        evaluator = AntiAgentEvaluator(
            disabled_cfg,
            workspace_paths=[self.workspace],
            trusted_artifact_paths=[self.artifact_root],
        )
        target = os.path.join(self.artifact_root, "scratch", "test.py")
        result = evaluator.evaluate(
            "write_to_file",
            {"TargetFile": target, "CodeContent": "pass"},
        )
        self.assertEqual(result.decision, DECISION_ASK)
        self.assertIn("outside active workspace", result.reason)


class TestArtifactDirectoryHookContract(unittest.TestCase):
    """End-to-end hook contract tests with realistic Antigravity PreToolUse payloads."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(dir=os.getcwd())
        self.workspace = os.path.join(self.test_dir, "myproject")
        self.artifact_root = os.path.join(self.test_dir, "brain", "d0d717e3-97f7-49ee-a0b1-5ce92363f6ba")
        os.makedirs(self.workspace, exist_ok=True)
        os.makedirs(self.artifact_root, exist_ok=True)

    def tearDown(self):
        if os.path.isdir(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_hook_auto_approves_artifact_mutation(self):
        """PreToolUse with valid artifactDirectoryPath auto-approves mutation without confirmation."""
        cid = "d0d717e3-97f7-49ee-a0b1-5ce92363f6ba"
        payload = {
            "toolCall": {
                "name": "write_to_file",
                "args": {
                    "TargetFile": os.path.join(self.artifact_root, "scratch", "test_fix_generator.py"),
                    "CodeContent": "import sys\nprint('fixed')",
                },
            },
            "conversationId": cid,
            "workspacePaths": [self.workspace],
            "artifactDirectoryPath": self.artifact_root,
        }
        response = handle_pre_tool_use(payload)
        self.assertEqual(response.get("decision"), DECISION_ALLOW)
        self.assertIn("Auto-approved agent artifact mutation", response.get("reason", ""))

        store = InteractionStateStore.default()
        state = store.get_state(cid)
        self.assertFalse(state.pending_approval)
        self.assertNotEqual(state.state, InteractionState.AWAITING_APPROVAL)

    def test_hook_missing_artifact_directory_path(self):
        """PreToolUse payload without artifactDirectoryPath returns DECISION_FORCE_ASK for external write."""
        cid = "conv-no-artifact-dir"
        target = os.path.join(self.test_dir, "brain", "conv-no-artifact-dir", "scratch", "file.py")
        payload = {
            "toolCall": {
                "name": "write_to_file",
                "args": {"TargetFile": target, "CodeContent": "print('hello')"},
            },
            "conversationId": cid,
            "workspacePaths": [self.workspace],
        }
        response = handle_pre_tool_use(payload)
        self.assertEqual(response.get("decision"), DECISION_FORCE_ASK)
        self.assertIn("outside active workspace", response.get("reason", ""))

    def test_hook_different_conversation_artifact_path(self):
        """PreToolUse targeting a sibling conversation directory returns DECISION_FORCE_ASK."""
        cid = "conv-current-aaa"
        current_artifact = os.path.join(self.test_dir, "brain", "conv-current-aaa")
        sibling_artifact = os.path.join(self.test_dir, "brain", "conv-sibling-bbb")
        os.makedirs(current_artifact, exist_ok=True)
        os.makedirs(sibling_artifact, exist_ok=True)

        payload = {
            "toolCall": {
                "name": "write_to_file",
                "args": {
                    "TargetFile": os.path.join(sibling_artifact, "scratch", "test.py"),
                    "CodeContent": "print('hello')",
                },
            },
            "conversationId": cid,
            "workspacePaths": [self.workspace],
            "artifactDirectoryPath": current_artifact,
        }
        response = handle_pre_tool_use(payload)
        self.assertEqual(response.get("decision"), DECISION_FORCE_ASK)
        self.assertIn("outside active workspace", response.get("reason", ""))

    def test_hook_disabled_via_environment_override(self):
        """Setting ANTIAGENT_AUTO_APPROVE_ARTIFACT_WRITES=false disables auto-approval."""
        cid = "conv-env-disabled"
        artifact_path = os.path.join(self.test_dir, "brain", "conv-env-disabled")
        os.makedirs(artifact_path, exist_ok=True)

        payload = {
            "toolCall": {
                "name": "write_to_file",
                "args": {
                    "TargetFile": os.path.join(artifact_path, "scratch", "test.py"),
                    "CodeContent": "print('hello')",
                },
            },
            "conversationId": cid,
            "workspacePaths": [self.workspace],
            "artifactDirectoryPath": artifact_path,
        }
        with patch.dict(os.environ, {"ANTIAGENT_AUTO_APPROVE_ARTIFACT_WRITES": "false"}):
            response = handle_pre_tool_use(payload)
            self.assertEqual(response.get("decision"), DECISION_FORCE_ASK)
            self.assertIn("outside active workspace", response.get("reason", ""))


class TestWindowsArtifactPathContainment(unittest.TestCase):
    """Platform-independent validation of Windows path containment and case-insensitivity."""

    def test_windows_case_insensitive_containment(self):
        """FSGuard._is_path_contained handles Windows case-insensitivity correctly."""
        root = Path("C:/Users/Admin/.gemini/antigravity/brain/d0d717e3-97f7-49ee-a0b1-5ce92363f6ba")
        # Target with different casing
        target_lower = Path("c:/users/admin/.gemini/antigravity/brain/d0d717e3-97f7-49ee-a0b1-5ce92363f6ba/scratch/test_fix_generator.py")
        self.assertTrue(FSGuard._is_path_contained(target_lower, root, is_windows=True))

        # Target in different conversation
        target_other = Path("c:/users/admin/.gemini/antigravity/brain/other-conv/scratch/test.py")
        self.assertFalse(FSGuard._is_path_contained(target_other, root, is_windows=True))

        # Target with prefix collision
        target_prefix = Path("c:/users/admin/.gemini/antigravity/brain/d0d717e3-97f7-49ee-a0b1-5ce92363f6ba-evil/scratch/test.py")
        self.assertFalse(FSGuard._is_path_contained(target_prefix, root, is_windows=True))


if __name__ == "__main__":
    unittest.main()
