"""Desktop restarts must launch independently before the old backend exits."""

import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from antiagent.dashboard import server
from antiagent.desktop import relauncher
from antiagent.runtime import InstallMode


class TestDesktopRestart(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "AntiAgent.app"
        self.binary = self.app / "Contents" / "MacOS" / "AntiAgent"
        self.binary.parent.mkdir(parents=True)
        self.binary.write_text("placeholder")

    def test_helper_runs_by_file_outside_package_for_restart_and_update(self):
        """Installed releases may omit desktop; the helper needs no package imports."""
        staged = self.root / "staging" / "AntiAgent.app"
        for bundle, version in ((self.app, "0.4.4"), (staged, "0.4.6")):
            executable = bundle / "Contents" / "MacOS" / "AntiAgent"
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("placeholder")
            with (bundle / "Contents" / "Info.plist").open("wb") as file:
                plistlib.dump({"CFBundleIdentifier": "com.antiagent.desktop",
                              "CFBundleShortVersionString": version}, file)
        base = [sys.executable, str(Path(relauncher.__file__).resolve()),
                "--target-app", str(self.app), "--no-relaunch",
                "--log-file", str(self.root / "relaunch.log")]
        environment = {**os.environ, "PYTHONPATH": ""}
        for extra in ([], ["--staged-app", str(staged),
                           "--backup-app", str(self.root / "backup.app"),
                           "--target-version", "0.4.6"]):
            result = subprocess.run(base + extra, cwd=self.root, env=environment,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        with (self.app / "Contents" / "Info.plist").open("rb") as file:
            self.assertEqual(plistlib.load(file)["CFBundleShortVersionString"], "0.4.6")

    def test_spawn_is_detached_and_logs_failures_for_both_modes(self):
        with patch.object(Path, "home", return_value=self.root), \
                patch.object(server, "_find_desktop_app_pid", return_value=123), \
                patch.object(server.subprocess, "Popen") as popen:
            for staged in (None, self.root):
                server._spawn_desktop_relauncher(self.app, staged, "0.4.6")
                args, kwargs = popen.call_args
                command = args[0]
                self.assertEqual(command[1], str(Path(relauncher.__file__).resolve()))
                self.assertNotIn("-m", command)
                self.assertEqual("--staged-app" in command, staged is not None)
                self.assertTrue(kwargs["start_new_session"])
                self.assertTrue(kwargs["close_fds"])
                self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
                self.assertIs(kwargs["stdout"], kwargs["stderr"])

    def test_missing_helper_or_stage_keeps_server_alive(self):
        with patch.object(server, "_find_relauncher_script", return_value=None), \
                patch.object(server.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "helper is missing"):
                server._spawn_desktop_relauncher(self.app)
            with self.assertRaisesRegex(RuntimeError, "staged update is missing"):
                server._spawn_desktop_relauncher(self.app, self.root / "missing")
            popen.assert_not_called()

    def test_pid_selection_matches_exact_bundle_and_never_falls_back_to_shell(self):
        processes = f"44 /Other/AntiAgent.app/Contents/MacOS/AntiAgent\n55 {self.binary.resolve()}\n"
        with patch.object(server.subprocess, "check_output", return_value=processes):
            self.assertEqual(server._find_desktop_app_pid(self.app), 55)
        with patch.object(server.subprocess, "check_output", side_effect=OSError):
            self.assertEqual(server._find_desktop_app_pid(self.app), 0)

    def test_endpoint_acknowledges_only_after_helper_is_scheduled(self):
        handler = object.__new__(server.DashboardRequestHandler)
        events = []
        handler._send_json = lambda data, status=200: events.append((status, data))
        runtime = SimpleNamespace(install_mode=InstallMode.MACOS_BUNDLE)
        with patch.dict(os.environ, {"ANTIAGENT_TESTING": "0"}), \
                patch.object(server, "get_runtime_install_info", return_value=runtime), \
                patch.object(server, "find_active_macos_bundle", return_value=self.app), \
                patch.object(server, "load_pending_update", return_value=None), \
                patch.object(server, "global_self_updater", SimpleNamespace(staged_bundle_path=None, target_version=None)), \
                patch.object(server.threading, "Thread") as thread, \
                patch.object(server, "_spawn_desktop_relauncher") as spawn:
            spawn.side_effect = OSError("cannot start helper")
            handler._handle_api_restart()
            self.assertEqual(events[0][0], 500)
            self.assertFalse(events[0][1]["ok"])
            thread.assert_not_called()
            events.clear()
            spawn.side_effect = lambda *args: events.append("helper_started")
            handler._handle_api_restart()
            self.assertEqual(events[0], "helper_started")
            self.assertTrue(events[1][1]["ok"])
            self.assertIs(thread.call_args.kwargs["target"], server._exit_dashboard_worker)
            thread.return_value.start.assert_called_once()

    def test_restart_waits_for_only_its_backend_and_desktop(self):
        with patch.object(relauncher, "_wait_pid") as wait, \
                patch.object(relauncher.subprocess, "run") as run:
            self.assertTrue(relauncher.perform_relaunch(self.app, old_pid=11, parent_pid=22))
            self.assertEqual([call.args[0] for call in wait.call_args_list], [11, 22])
            self.assertIn(json.dumps(str(self.app.resolve())), run.call_args_list[0].args[0][2])
            self.assertEqual(run.call_args_list[1].args[0], ["open", str(self.app.resolve())])
        with patch.object(relauncher.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "open")):
            self.assertFalse(relauncher.perform_relaunch(self.app))


if __name__ == "__main__":
    unittest.main()
