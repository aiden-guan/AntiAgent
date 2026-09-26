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

    def test_get_hook_command_uses_launcher_when_not_pip_installed(self):
        """Bundle installs (PYTHONPATH-only) must not register a bare `-m antiagent.hook`."""
        import os
        import subprocess
        import sys
        from unittest.mock import patch
        from antiagent import cli

        with patch.object(cli, "_is_importable_without_pythonpath", return_value=False), patch.object(
            cli, "get_global_config_dir", return_value=Path(self.test_dir)
        ):
            cmd = cli.get_hook_command()

        launcher = Path(self.test_dir) / "hook_launcher.py"
        self.assertTrue(launcher.is_file())
        self.assertNotIn("-m antiagent.hook", cmd)
        self.assertIn(str(launcher), cmd)

        # The launcher must run the hook with no PYTHONPATH and an unrelated cwd.
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        result = subprocess.run(
            [sys.executable, str(launcher)],
            input="",
            env=env,
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["decision"], "ask")

    def test_is_importable_without_pythonpath_detects_missing_package(self):
        import sys
        from unittest.mock import patch
        from antiagent import cli

        root = Path(cli.__file__).resolve().parent.parent
        with patch("subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            self.assertFalse(cli._is_importable_without_pythonpath(sys.executable, root))
            run.return_value.returncode = 0
            run.return_value.stdout = str(root)
            self.assertTrue(cli._is_importable_without_pythonpath(sys.executable, root))

    def test_cli_update_check(self):
        import argparse
        from unittest.mock import patch
        from antiagent.cli import handle_update_cli

        args = argparse.Namespace(check=True, download=False, pip=False, asset=None)
        mock_info = {
            "ok": True,
            "update_available": True,
            "current_version": "0.1.3",
            "latest_version": "0.1.4",
            "release_name": "v0.1.4",
            "html_url": "https://github.com",
            "recommended_asset": {"name": "AntiAgent.dmg", "size": 1000, "download_url": "http://example.com"},
            "assets": [],
        }
        with patch("antiagent.updater.check_for_updates", return_value=mock_info):
            # Verify runs without exception
            handle_update_cli(args)

    def test_cli_update_pip(self):
        import argparse
        from unittest.mock import patch, MagicMock
        from antiagent.cli import handle_update_cli

        args = argparse.Namespace(check=False, download=False, pip=True, asset=None)
        mock_mgr = MagicMock()
        mock_mgr.status = "success"
        mock_mgr.logs = ["Success!"]
        with patch("antiagent.updater.PipUpgradeManager", return_value=mock_mgr):
            handle_update_cli(args)

    def test_cli_update_download(self):
        import argparse
        from unittest.mock import patch, MagicMock
        from antiagent.cli import handle_update_cli

        args = argparse.Namespace(check=False, download=True, pip=False, asset=None)
        mock_info = {
            "ok": True,
            "update_available": True,
            "current_version": "0.1.3",
            "latest_version": "0.1.4",
            "release_name": "v0.1.4",
            "html_url": "https://github.com",
            "recommended_asset": {"name": "AntiAgent.dmg", "size": 1000, "download_url": "http://example.com/AntiAgent.dmg"},
            "assets": [{"name": "AntiAgent.dmg", "download_url": "http://example.com/AntiAgent.dmg"}],
        }
        mock_dl = MagicMock()
        mock_dl.status = "completed"
        mock_dl.get_status.return_value = {
            "status": "completed",
            "progress": 100,
            "downloaded_bytes": 1000,
            "total_bytes": 1000,
            "dest_path": "/tmp/AntiAgent.dmg",
        }
        with patch("antiagent.updater.check_for_updates", return_value=mock_info), \
             patch("antiagent.updater.UpdateDownloader", return_value=mock_dl), \
             patch("antiagent.updater.open_downloaded_file", return_value=True):
            handle_update_cli(args)


if __name__ == "__main__":
    unittest.main()
