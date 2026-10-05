"""Regression coverage for user homes beneath /var (issue #7)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from antiagent.config import AntiAgentConfig
from antiagent.constants import DECISION_ALLOW, DECISION_ASK
from antiagent.engine.evaluator import AntiAgentEvaluator
from antiagent.engine.heuristics.fs_guard import FSGuard


class TestLinuxHomePaths(unittest.TestCase):
    def setUp(self):
        self.home = Path("/var/home/antiagent-issue7-test-user")
        self.home_patch = patch.object(Path, "home", return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.workspace = self.home / "project"
        self.guard = FSGuard(workspace_paths=[str(self.workspace)])

    def test_home_files_are_not_system_targets(self):
        for home in (self.home, Path("/var/lib/home/issue7-user"), Path("/private/var/home/issue7-user")):
            with self.subTest(home=home), patch.object(Path, "home", return_value=home):
                guard = FSGuard(workspace_paths=[str(home / "project")])
                self.assertFalse(guard.is_sensitive_target(str(home / "project/app.py"))[0])

    def test_project_reads_and_writes_follow_normal_policy(self):
        for provider in ("native", "offline"):
            evaluator = AntiAgentEvaluator(
                AntiAgentConfig(provider=provider), workspace_paths=[str(self.workspace)]
            )
            for tool, key in (("view_file", "AbsolutePath"), ("list_dir", "DirectoryPath"),
                              ("write_to_file", "TargetFile"), ("replace_file_content", "TargetFile")):
                with self.subTest(provider=provider, tool=tool):
                    result = evaluator.evaluate(tool, {key: str(self.workspace / "app.py")})
                    self.assertEqual(result.decision, DECISION_ALLOW, result.reason)

    def test_credentials_in_home_remain_sensitive(self):
        for name in (".env", ".ssh/id_ed25519", ".aws/credentials", "server.key", ".git/config"):
            with self.subTest(name=name):
                target = str(self.workspace / name)
                self.assertTrue(self.guard.is_sensitive_target(target)[0])
                evaluator = AntiAgentEvaluator(
                    AntiAgentConfig(provider="native"), workspace_paths=[str(self.workspace)]
                )
                for tool, key in (("view_file", "AbsolutePath"), ("write_to_file", "TargetFile")):
                    self.assertEqual(evaluator.evaluate(tool, {key: target}).decision, DECISION_ASK)

    def test_home_writes_outside_workspace_still_ask(self):
        target = str(self.home / "other-project/app.py")
        result = self.guard.evaluate_file_tool("write_to_file", {"TargetFile": target})
        self.assertEqual(result[0], DECISION_ASK)
        self.assertIn("outside active workspace", result[1])

    def test_other_users_and_system_paths_remain_protected(self):
        for target in ("/var", "/var/log/app.log", "/var/lib/app/data", "/private/var/log/app.log",
                       "/var/home/another-user/project/app.py",
                       str(self.home) + "-other/project/app.py", "/etc/hosts", "/root/app.py"):
            with self.subTest(target=target):
                self.assertTrue(self.guard.is_sensitive_target(target)[0])

    def test_traversal_out_of_home_remains_protected(self):
        target = str(self.home / ".." / ".." / "log/app.log")
        self.assertTrue(self.guard.is_sensitive_target(target)[0])

    def test_paranoid_mode_still_asks_for_home_edits(self):
        evaluator = AntiAgentEvaluator(
            AntiAgentConfig(profile="paranoid", provider="native"),
            workspace_paths=[str(self.workspace)],
        )
        result = evaluator.evaluate("write_to_file", {"TargetFile": str(self.workspace / "app.py")})
        self.assertEqual(result.decision, DECISION_ASK)
        self.assertIn("Paranoid mode", result.reason)

    def test_home_cannot_exempt_entire_system_root(self):
        for home in (Path("/"), Path("/var"), Path("/private/var"), Path("/etc"), Path("/root")):
            with self.subTest(home=home), patch.object(Path, "home", return_value=home):
                guard = FSGuard(workspace_paths=[str(home)])
                target = "/var/log/app.log" if home == Path("/") else str(home / "app.py")
                self.assertTrue(guard.is_sensitive_target(target)[0])

    def test_symlink_from_home_to_system_path_remains_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            link = home / "system-link"
            try:
                link.symlink_to("/var/log", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks are unavailable")
            with patch.object(Path, "home", return_value=home):
                guard = FSGuard(workspace_paths=[str(home)])
                self.assertTrue(guard.is_sensitive_target(str(link / "app.log"))[0])

    def test_home_symlink_resolves_to_var_home(self):
        with tempfile.TemporaryDirectory() as directory:
            alias = Path(directory) / "home-alias"
            try:
                alias.symlink_to(self.home, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks are unavailable")
            with patch.object(Path, "home", return_value=alias):
                guard = FSGuard(workspace_paths=[str(alias / "project")])
                self.assertFalse(guard.is_sensitive_target(str(alias / "project/app.py"))[0])


if __name__ == "__main__":
    unittest.main()
