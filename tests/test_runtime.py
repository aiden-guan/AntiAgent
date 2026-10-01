"""Unit tests for AntiAgent runtime detection, bundle isolation, and launch context."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from antiagent.runtime import (
    DashboardLaunchContext,
    InstallMode,
    RuntimeInstallInfo,
    detect_install_mode,
    find_active_macos_bundle,
    find_enclosing_macos_bundle,
    get_runtime_install_info,
)


class TestRuntimeDetection(unittest.TestCase):
    def test_find_enclosing_macos_bundle(self):
        fake_bundle = Path("/Applications/AntiAgent.app")
        fake_pkg = fake_bundle / "Contents" / "Resources" / "antiagent"
        enclosing = find_enclosing_macos_bundle(fake_pkg)
        self.assertEqual(enclosing, fake_bundle)

        # Negative case: normal path outside any .app bundle
        self.assertIsNone(find_enclosing_macos_bundle(Path("/usr/local/lib/python3.12/site-packages/antiagent")))

    def test_find_active_macos_bundle_isolation(self):
        """When both /Applications/AntiAgent.app and ~/Applications/AntiAgent.app exist,
        only the active running bundle is targeted, never both or the wrong one."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            sys_app = Path(tmp_dir) / "Applications" / "AntiAgent.app"
            user_app = Path(tmp_dir) / "UserApplications" / "AntiAgent.app"
            sys_app.mkdir(parents=True)
            user_app.mkdir(parents=True)

            # Create valid Info.plist inside bundles
            (sys_app / "Contents").mkdir(parents=True)
            (sys_app / "Contents" / "Info.plist").write_text("<plist></plist>", encoding="utf-8")
            (user_app / "Contents").mkdir(parents=True)
            (user_app / "Contents" / "Info.plist").write_text("<plist></plist>", encoding="utf-8")

            # 1. When the running Python package is enclosed inside user_app
            with patch("antiagent.runtime.find_enclosing_macos_bundle", return_value=user_app):
                active = find_active_macos_bundle(inspect_processes=False)
                self.assertEqual(active, user_app)

            # 2. When running process maps to sys_app executable
            with patch("antiagent.runtime.find_enclosing_macos_bundle", return_value=None), \
                 patch("sys.platform", "darwin"), \
                 patch("subprocess.check_output", return_value=f"{sys_app}/Contents/MacOS/AntiAgent\n"):
                active = find_active_macos_bundle(inspect_processes=True)
                self.assertEqual(active, sys_app)

    def test_detect_install_mode(self):
        # 1. macOS Bundle mode
        with patch("antiagent.runtime.find_enclosing_macos_bundle", return_value=Path("/Applications/AntiAgent.app")):
            mode = detect_install_mode()
            self.assertEqual(mode, InstallMode.MACOS_BUNDLE)

        # 2. Editable source mode (has .git in parent hierarchy)
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir) / "AntiAgent"
            git_dir = repo_root / ".git"
            git_dir.mkdir(parents=True)
            (repo_root / "pyproject.toml").write_text("[project]\nname='antiagent'\n", encoding="utf-8")
            pkg_dir = repo_root / "antiagent"
            pkg_dir.mkdir()

            with patch("antiagent.runtime.find_enclosing_macos_bundle", return_value=None), \
                 patch("antiagent.runtime.is_desktop_app_running", return_value=False):
                mode = detect_install_mode(pkg_dir)
                self.assertEqual(mode, InstallMode.EDITABLE_SOURCE)

        # 3. Pip mode (site-packages)
        fake_site = Path("/env/lib/python3.12/site-packages/antiagent")
        with patch("antiagent.runtime.find_enclosing_macos_bundle", return_value=None), \
             patch("antiagent.runtime.is_desktop_app_running", return_value=False):
            mode = detect_install_mode(fake_site)
            self.assertEqual(mode, InstallMode.PIP)

        # 4. Windows Portable mode
        with patch("sys.platform", "win32"), \
             patch.dict(os.environ, {"ANTIAGENT_PORTABLE": "1"}):
            mode = detect_install_mode()
            self.assertEqual(mode, InstallMode.WINDOWS_PORTABLE)

    def test_get_runtime_install_info(self):
        info = get_runtime_install_info()
        self.assertIsInstance(info, RuntimeInstallInfo)
        self.assertIsInstance(info.install_mode, InstallMode)
        self.assertTrue(os.path.exists(info.package_path))
        self.assertEqual(info.current_version, info.current_version)

    def test_dashboard_launch_context_normalization(self):
        """Verify launch context serializes to argv without duplicate 'dashboard'."""
        ctx = DashboardLaunchContext(
            host="127.0.0.1",
            port=5000,
            workspace_path="/path/to/project with spaces",
            open_browser=False,
            install_mode="pip",
            python_executable="/usr/bin/python3",
            macos_bundle_path=None,
        )

        argv = ctx.to_argv()
        self.assertEqual(argv[:3], ["/usr/bin/python3", "-m", "antiagent"])
        # Exactly one 'dashboard' subcommand
        self.assertEqual(argv.count("dashboard"), 1)
        self.assertEqual(argv[3], "dashboard")

        # Arguments preserved
        self.assertIn("--host", argv)
        self.assertEqual(argv[argv.index("--host") + 1], "127.0.0.1")
        self.assertIn("--port", argv)
        self.assertEqual(argv[argv.index("--port") + 1], "5000")
        self.assertIn("--workspace", argv)
        self.assertEqual(argv[argv.index("--workspace") + 1], "/path/to/project with spaces")
        self.assertIn("--no-open", argv)


if __name__ == "__main__":
    unittest.main()
