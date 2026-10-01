"""Unit tests for AntiAgent CLI installation and status."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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

        # Verify antiagent-flow-state hook is also installed with all 4 lifecycle events
        self.assertIn("antiagent-flow-state", data)
        flow = data["antiagent-flow-state"]
        self.assertTrue(flow["enabled"])
        self.assertIn("PreInvocation", flow)
        self.assertIn("PostInvocation", flow)
        self.assertIn("PostToolUse", flow)
        self.assertIn("Stop", flow)

        # 2. Uninstall
        uninstall_hook(is_global=False, workspace_path=self.test_dir)
        data_after = json.loads(hooks_file.read_text(encoding="utf-8"))
        self.assertNotIn("antiagent-guard", data_after)
        self.assertNotIn("antiagent-flow-state", data_after)

    def test_install_hook_non_destructive_merge_and_uninstall(self):
        """Verify install_hook merges non-destructively with existing user hooks in hooks.json."""
        agents_dir = Path(self.test_dir) / ".agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        hooks_file = agents_dir / "hooks.json"
        initial_data = {
            "my-custom-linter": {
                "enabled": True,
                "PreToolUse": [{"matcher": "run_command", "hooks": [{"type": "command", "command": "echo linter"}]}],
            }
        }
        hooks_file.write_text(json.dumps(initial_data, indent=2), encoding="utf-8")

        # Install antiagent hooks
        install_hook(is_global=False, workspace_path=self.test_dir)
        merged = json.loads(hooks_file.read_text(encoding="utf-8"))

        # Pre-existing hook must be preserved!
        self.assertIn("my-custom-linter", merged)
        self.assertEqual(merged["my-custom-linter"]["enabled"], True)
        self.assertIn("antiagent-guard", merged)
        self.assertIn("antiagent-flow-state", merged)

        # Uninstall only removes AntiAgent hooks
        uninstall_hook(is_global=False, workspace_path=self.test_dir)
        after_uninstall = json.loads(hooks_file.read_text(encoding="utf-8"))
        self.assertIn("my-custom-linter", after_uninstall)
        self.assertNotIn("antiagent-guard", after_uninstall)
        self.assertNotIn("antiagent-flow-state", after_uninstall)

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

        args = argparse.Namespace(check=False, download=False, pip=True, asset=None, version=None, break_system_packages=False)
        mock_mgr = MagicMock()
        mock_mgr.status = "success"
        mock_mgr.verified_version = "0.1.4"
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


class TestRemoteCLI(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.remotes_file = Path(self.test_dir) / "remotes.json"
        self.registry_patch = patch(
            "antiagent.engine.remote_sessions.get_global_config_dir",
            return_value=Path(self.test_dir),
        )
        self.registry_patch.start()

    def tearDown(self):
        self.registry_patch.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_remote_cli_lifecycle(self):
        import argparse
        from io import StringIO
        from unittest.mock import patch
        from antiagent.cli import handle_remote_cli

        # 1. remote list (empty)
        with patch("sys.stdout", new_callable=StringIO) as out:
            handle_remote_cli(argparse.Namespace(remote_command="list"))
            self.assertIn("No remote machines configured yet", out.getvalue())

        # 2. remote add
        add_args = argparse.Namespace(
            remote_command="add",
            name="mac-mini",
            ssh_host="mac-mini.local",
            hostname=None,
            user="aiden",
            port=22,
            identity_file="~/.ssh/id_ed25519",
            workspace="~/Developer/PigeonBox",
            remote_os="auto",
            antigravity_name="Aiden Mac Mini",
            agy_path=None,
        )
        with patch("sys.stdout", new_callable=StringIO) as out:
            handle_remote_cli(add_args)
            self.assertIn("successfully added", out.getvalue())

        # 3. remote list (populated)
        with patch("sys.stdout", new_callable=StringIO) as out:
            handle_remote_cli(argparse.Namespace(remote_command="list"))
            self.assertIn("mac-mini", out.getvalue())
            self.assertIn("aiden@mac-mini.local", out.getvalue())

        # 4. remote show
        with patch("sys.stdout", new_callable=StringIO) as out:
            handle_remote_cli(argparse.Namespace(remote_command="show", name="mac-mini"))
            val = out.getvalue()
            self.assertIn("mac-mini", val)
            self.assertIn("~/Developer/PigeonBox", val)
            self.assertIn("Aiden Mac Mini", val)

        # 5. remote test (mocked)
        with patch("antiagent.engine.remote_sessions.RemoteSessionManager.test_connection", return_value={"ok": True, "latency_ms": 35, "remote_os": "macOS"}):
            with patch("sys.stdout", new_callable=StringIO) as out:
                handle_remote_cli(argparse.Namespace(remote_command="test", name="mac-mini"))
                self.assertIn("reachability verified", out.getvalue())
                self.assertIn("35ms", out.getvalue())

        # 6. remote doctor (mocked)
        doc_res = {
            "ok": True,
            "host": {"name": "mac-mini", "ssh_host": "mac-mini.local"},
            "probe": {
                "ssh_connected": True,
                "latency_ms": 40,
                "remote_os": "macOS",
                "hostname": "mac-mini",
                "workspace_path": "/Users/aiden/Developer",
                "workspace_exists": True,
                "antigravity": {"installed": True, "version": "1.15.0", "authenticated": True, "path": "/bin/agy"},
                "remote_control": {"running": True, "instance_name": "Aiden Mac Mini"},
                "antiagent": {"installed": True, "version": "0.2.0", "global_hook_active": True, "protection_status": "Protected"},
            }
        }
        with patch("antiagent.engine.remote_sessions.RemoteSessionManager.doctor", return_value=doc_res):
            with patch("sys.stdout", new_callable=StringIO) as out:
                handle_remote_cli(argparse.Namespace(remote_command="doctor", name="mac-mini"))
                val = out.getvalue()
                self.assertIn("SSH Reachability:      🟢 Connected", val)
                self.assertIn("Antigravity CLI:       🟢 Installed", val)
                self.assertIn("Remote Control:        🟢 Running", val)
                self.assertIn("Protection:            🟢 Protected", val)

        # 7. remote status (mocked)
        from antiagent.engine.remote_sessions import RemoteProbeResult, RemoteControlStatus, AntiAgentRemoteStatus, AntigravityStatus
        probe_res = RemoteProbeResult(
            ok=True,
            name="mac-mini",
            ssh_connected=True,
            remote_control=RemoteControlStatus(supported=True, running=True, instance_name="Aiden Mac Mini", url="https://antigravity.google.com/"),
            antiagent=AntiAgentRemoteStatus(installed=True, global_hook_active=True, protection_status="Protected"),
        )
        with patch("antiagent.engine.remote_sessions.RemoteSessionManager.probe", return_value=probe_res):
            with patch("sys.stdout", new_callable=StringIO) as out:
                handle_remote_cli(argparse.Namespace(remote_command="status", name="mac-mini"))
                val = out.getvalue()
                self.assertIn("🟢 Running", val)
                self.assertIn("Aiden Mac Mini", val)

        # 8. remote start (mocked)
        start_res = {"ok": True, "running": True, "instance_name": "Aiden Mac Mini", "url": "https://antigravity.google.com/"}
        with patch("antiagent.engine.remote_sessions.RemoteSessionManager.start_remote_control", return_value=start_res), \
             patch("antiagent.engine.remote_sessions.RemoteSessionManager.probe", return_value=probe_res):
            with patch("sys.stdout", new_callable=StringIO) as out:
                handle_remote_cli(argparse.Namespace(remote_command="start", name="mac-mini", name_override=None))
                self.assertIn("Remote Control started on 'mac-mini'", out.getvalue())

        # 9. remote stop (mocked)
        with patch("antiagent.engine.remote_sessions.RemoteSessionManager.stop_remote_control", return_value={"ok": True}):
            with patch("sys.stdout", new_callable=StringIO) as out:
                handle_remote_cli(argparse.Namespace(remote_command="stop", name="mac-mini"))
                self.assertIn("Remote Control stopped on 'mac-mini'", out.getvalue())

        # 10. remote protect (mocked)
        with patch("antiagent.engine.remote_sessions.RemoteSessionManager.protect_remote", return_value={"ok": True, "message": "Hook enabled."}):
            with patch("sys.stdout", new_callable=StringIO) as out:
                handle_remote_cli(argparse.Namespace(remote_command="protect", name="mac-mini"))
                self.assertIn("Hook enabled", out.getvalue())

        # 11. remote remove
        with patch("sys.stdout", new_callable=StringIO) as out:
            handle_remote_cli(argparse.Namespace(remote_command="remove", name="mac-mini"))
            self.assertIn("removed", out.getvalue())

    def test_agy_args_transparent_forwarding(self):
        """Verify antiagent agy transparently forwards flags like --continue, --model, --help."""
        import sys
        from unittest.mock import patch
        from antiagent.cli import main

        test_args = ["antiagent", "agy", "--model", "gemini-2.5-pro", "--continue", "--mode=plan"]
        with patch.object(sys, "argv", test_args):
            with patch("antiagent.cli.launch_agy_cli", return_value=0) as mock_launch:
                with self.assertRaises(SystemExit) as cm:
                    main()
                self.assertEqual(cm.exception.code, 0)
                mock_launch.assert_called_once_with(["--model", "gemini-2.5-pro", "--continue", "--mode=plan"])

    def test_queue_cli_status_clear_resume(self):
        """Verify queue status, clear, and resume subcommands execute cleanly."""
        import argparse
        from io import StringIO
        from unittest.mock import patch
        from antiagent.cli import handle_queue_command
        from antiagent.engine.prompt_queue import PromptQueue

        cid = "queue-cli-test"
        q = PromptQueue(cid)
        q.enqueue("hello from test")

        # 1. status
        with patch("sys.stdout", new_callable=StringIO) as out:
            args = argparse.Namespace(queue_command="status", conversation_id=cid)
            res = handle_queue_command(args)
            self.assertEqual(res, 0)
            self.assertIn("hello from test", out.getvalue())

        # 2. clear
        with patch("sys.stdout", new_callable=StringIO) as out:
            args = argparse.Namespace(queue_command="clear", conversation_id=cid)
            res = handle_queue_command(args)
            self.assertEqual(res, 0)
            self.assertIn("Cleared 1 prompt(s)", out.getvalue())

        # 3. resume
        with patch("sys.stdout", new_callable=StringIO) as out:
            args = argparse.Namespace(queue_command="resume", conversation_id=cid)
            res = handle_queue_command(args)
            self.assertEqual(res, 0)
            self.assertIn("Resumed", out.getvalue())

    def test_resolve_real_agy_executable_env_var(self):
        """Verify AGY_PATH and ANTIGRAVITY_PATH override resolution."""
        import os
        from unittest.mock import patch
        from antiagent.cli import resolve_real_agy_executable

        with patch.dict(os.environ, {"AGY_PATH": "/custom/bin/agy"}):
            with patch("os.path.isfile", return_value=True), patch("os.access", return_value=True):
                self.assertEqual(resolve_real_agy_executable(), "/custom/bin/agy")

    def test_install_hook_quiet_mode(self):
        """Verify install_hook(quiet=True) does not write output to stdout."""
        from io import StringIO
        from antiagent.cli import install_hook

        with patch("sys.stdout", new_callable=StringIO) as out:
            install_hook(is_global=False, workspace_path=self.test_dir, quiet=True)
            self.assertEqual(out.getvalue(), "")

    def test_launch_agy_cli_forwards_workspace_path(self):
        """Verify launch_agy_cli loads configuration from specified workspace_path."""
        from unittest.mock import MagicMock, patch
        from antiagent.cli import launch_agy_cli

        with patch("antiagent.cli.resolve_real_agy_executable", return_value="/bin/echo"):
            with patch("antiagent.cli.load_config") as mock_load_cfg:
                with patch("antiagent.engine.pty_bridge.PTYBridge") as mock_bridge:
                    mock_instance = MagicMock()
                    mock_instance.run.return_value = 0
                    mock_bridge.return_value = mock_instance

                    code = launch_agy_cli(["--continue"], workspace_path="/custom/ws")
                    self.assertEqual(code, 0)
                    mock_load_cfg.assert_called_once_with("/custom/ws")


if __name__ == "__main__":
    unittest.main()
