"""Unit tests for AntiAgent CLI installation and status."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from antiagent.cli import install_hook, uninstall_hook


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_install_and_uninstall_workspace_hook(self):
        # 1. Install
        install_hook(is_global=False, workspace_path=self.test_dir)
        hooks_file = Path(self.test_dir) / ".agents" / "hooks.json"
        self.assertTrue(hooks_file.is_file())

        data = json.loads(hooks_file.read_text(encoding="utf-8"))
        self.assertIn("antiagent-guard", data)
        guard = data["antiagent-guard"]
        self.assertTrue(guard["enabled"])
        self.assertIn("PreToolUse", guard)
        self.assertEqual(guard["PreToolUse"][0]["matcher"], "*")

        # 2. Uninstall
        uninstall_hook(is_global=False, workspace_path=self.test_dir)
        data_after = json.loads(hooks_file.read_text(encoding="utf-8"))
        self.assertNotIn("antiagent-guard", data_after)

    def test_get_hook_command_quotes_spaces(self):
        from unittest.mock import patch
        from antiagent.cli import get_hook_command

        with patch("sys.executable", "C:\\Program Files\\Python312\\python.exe"):
            cmd = get_hook_command()
            self.assertTrue(cmd.startswith('"C:\\Program Files\\Python312\\python.exe"'))
            self.assertIn("-m antiagent.hook", cmd)


if __name__ == "__main__":
    unittest.main()
