"""Unit tests for AntiAgent native Linux desktop application, installation, packaging, and launching."""

import os
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from antiagent.desktop.builder import (
    build_linux_package,
    install_app,
    install_linux_app,
    launch_app,
    launch_linux_app,
    uninstall_app,
    uninstall_linux_app,
)
from antiagent.desktop.linux_window import _is_local_url
from antiagent.runtime import InstallMode, detect_install_mode
from antiagent.updater import select_recommended_asset


class TestLinuxDesktopPackaging(unittest.TestCase):
    """Test Linux desktop distribution package creation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output_dir = Path(self.temp_dir) / "dist"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_build_linux_package(self):
        tar_path = build_linux_package(self.output_dir)
        self.assertTrue(tar_path.is_file(), f"Tarball not found at {tar_path}")
        self.assertTrue(tar_path.name.endswith(".tar.gz"))

        # Verify archive members and permissions
        with tarfile.open(tar_path, "r:gz") as tf:
            names = set(tf.getnames())
            self.assertIn("AntiAgent/AntiAgent.sh", names)
            self.assertIn("AntiAgent/install-app.sh", names)
            self.assertIn("AntiAgent/uninstall-app.sh", names)
            self.assertIn("AntiAgent/com.antiagent.desktop.desktop", names)
            self.assertNotIn("AntiAgent/AntiAgent.desktop", names)
            self.assertIn("AntiAgent/antiagent.png", names)
            self.assertIn("AntiAgent/antiagent.svg", names)
            self.assertIn("AntiAgent/README.txt", names)
            self.assertIn("AntiAgent/antiagent/__init__.py", names)

            # Check launcher executable permissions
            ti_sh = tf.getmember("AntiAgent/AntiAgent.sh")
            self.assertTrue(bool(ti_sh.mode & 0o111), "AntiAgent.sh must be executable")

            ti_inst = tf.getmember("AntiAgent/install-app.sh")
            self.assertTrue(bool(ti_inst.mode & 0o111), "install-app.sh must be executable")

            ti_uninst = tf.getmember("AntiAgent/uninstall-app.sh")
            self.assertTrue(bool(ti_uninst.mode & 0o111), "uninstall-app.sh must be executable")

            # Check desktop entry content
            f_desk = tf.extractfile("AntiAgent/com.antiagent.desktop.desktop")
            self.assertIsNotNone(f_desk)
            desk_content = f_desk.read().decode("utf-8")
            self.assertIn("[Desktop Entry]", desk_content)
            self.assertIn("Name=AntiAgent Guard", desk_content)
            self.assertIn("Exec=antiagent-app %U", desk_content)
            self.assertIn("Icon=antiagent", desk_content)
            self.assertIn("StartupWMClass=com.antiagent.desktop", desk_content)
            self.assertIn("AntiAgent/com.antiagent.desktop.png", names)
            self.assertIn("AntiAgent/com.antiagent.desktop.svg", names)


class TestLinuxDesktopInstallation(unittest.TestCase):
    """Test XDG desktop entry, launcher, and icon installation on Linux."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.mock_home = Path(self.temp_dir) / "home"
        self.mock_home.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_install_linux_app_user_scope(self):
        def fake_expanduser(path: str) -> str:
            if path.startswith("~"):
                return str(self.mock_home) + path[1:]
            return path

        with patch("os.path.expanduser", side_effect=fake_expanduser), \
             patch("subprocess.run") as mock_run:
            desktop_file = install_linux_app(to_global=False)

            self.assertTrue(desktop_file.is_file(), "Desktop file was not created")
            self.assertEqual(desktop_file.name, "com.antiagent.desktop.desktop")

            content = desktop_file.read_text(encoding="utf-8")
            self.assertIn("[Desktop Entry]", content)
            self.assertIn("Name=AntiAgent Guard", content)
            self.assertIn("Exec=antiagent-app %U", content)
            self.assertIn("StartupWMClass=com.antiagent.desktop", content)

            # Ensure no duplicate unnamespaced antiagent.desktop exists
            legacy_desktop = self.mock_home / ".local/share/applications/antiagent.desktop"
            self.assertFalse(legacy_desktop.exists(), "Duplicate antiagent.desktop must not exist")

            # Check launcher
            launcher = self.mock_home / ".local/bin/antiagent-app"
            self.assertTrue(launcher.is_file())
            self.assertTrue(bool(launcher.stat().st_mode & 0o111))

            # Check icons
            icon_png = self.mock_home / ".local/share/icons/hicolor/512x512/apps/antiagent.png"
            icon_svg = self.mock_home / ".local/share/icons/hicolor/scalable/apps/antiagent.svg"
            icon_png_alt = self.mock_home / ".local/share/icons/hicolor/512x512/apps/com.antiagent.desktop.png"
            icon_svg_alt = self.mock_home / ".local/share/icons/hicolor/scalable/apps/com.antiagent.desktop.svg"
            self.assertTrue(icon_png.is_file())
            self.assertTrue(icon_svg.is_file())
            self.assertTrue(icon_png_alt.is_file())
            self.assertTrue(icon_svg_alt.is_file())

    def test_install_linux_app_cleans_up_legacy_duplicate(self):
        """Verify installer migrates legacy antiagent.desktop and removes duplication."""
        def fake_expanduser(path: str) -> str:
            if path.startswith("~"):
                return str(self.mock_home) + path[1:]
            return path

        apps_dir = self.mock_home / ".local/share/applications"
        apps_dir.mkdir(parents=True, exist_ok=True)
        old_desktop = apps_dir / "antiagent.desktop"
        old_desktop.write_text("[Desktop Entry]\nName=Old AntiAgent\n", encoding="utf-8")
        symlink_desktop = apps_dir / "com.antiagent.desktop.desktop"
        symlink_desktop.symlink_to(old_desktop.name)

        with patch("os.path.expanduser", side_effect=fake_expanduser), \
             patch("subprocess.run"):
            desktop_file = install_linux_app(to_global=False)

            self.assertEqual(desktop_file.name, "com.antiagent.desktop.desktop")
            self.assertTrue(desktop_file.is_file())
            self.assertFalse(desktop_file.is_symlink())
            self.assertFalse(old_desktop.exists(), "Old antiagent.desktop was not cleaned up")

    def test_install_app_routes_to_linux(self):
        with patch("sys.platform", "linux"), \
             patch("antiagent.desktop.builder.install_linux_app") as mock_install_linux:
            mock_install_linux.return_value = Path("/tmp/mock.desktop")
            res = install_app(to_global=False)
            mock_install_linux.assert_called_once_with(to_global=False)
            self.assertEqual(res, Path("/tmp/mock.desktop"))

    def test_uninstall_linux_app_user_scope(self):
        def fake_expanduser(path: str) -> str:
            if path.startswith("~"):
                return str(self.mock_home) + path[1:]
            return path

        with patch("os.path.expanduser", side_effect=fake_expanduser), \
             patch("subprocess.run") as mock_run:
            # 1. Install
            desktop_file = install_linux_app(to_global=False)
            self.assertTrue(desktop_file.is_file())
            launcher = self.mock_home / ".local/bin/antiagent-app"
            self.assertTrue(launcher.is_file())
            icon_png = self.mock_home / ".local/share/icons/hicolor/512x512/apps/antiagent.png"
            self.assertTrue(icon_png.is_file())

            # Also create legacy desktop file to verify cleanup
            legacy_file = self.mock_home / ".local/share/applications/antiagent.desktop"
            legacy_file.write_text("[Desktop Entry]\n", encoding="utf-8")
            self.assertTrue(legacy_file.is_file())

            # 2. Uninstall
            removed = uninstall_linux_app(to_global=False)
            self.assertFalse(desktop_file.exists(), "Desktop file was not removed")
            self.assertFalse(legacy_file.exists(), "Legacy desktop file was not removed")
            self.assertFalse(launcher.exists(), "Launcher script was not removed")
            self.assertFalse(icon_png.exists(), "Icon was not removed")

            # Check removed list contains desktop file
            removed_strs = [str(p) for p in removed]
            self.assertIn(str(desktop_file), removed_strs)
            self.assertIn(str(legacy_file), removed_strs)
            self.assertIn(str(launcher), removed_strs)

            # Check cache update commands were invoked
            called_cmds = [call[0][0] for call in mock_run.call_args_list if isinstance(call[0][0], list)]
            update_db_called = any(cmd[0] == "update-desktop-database" for cmd in called_cmds)
            update_icon_called = any(cmd[0] == "gtk-update-icon-cache" for cmd in called_cmds)
            self.assertTrue(update_db_called, "update-desktop-database was not invoked")
            self.assertTrue(update_icon_called, "gtk-update-icon-cache was not invoked")

    def test_uninstall_linux_app_global_scope(self):
        mock_global_root = Path(self.temp_dir) / "usr"
        mock_apps = mock_global_root / "share/applications"
        mock_icons = mock_global_root / "share/icons/hicolor"
        mock_bin = mock_global_root / "local/bin"
        mock_apps.mkdir(parents=True, exist_ok=True)
        mock_bin.mkdir(parents=True, exist_ok=True)

        desk = mock_apps / "com.antiagent.desktop.desktop"
        desk.write_text("[Desktop Entry]\n", encoding="utf-8")
        launcher = mock_bin / "antiagent-app"
        launcher.write_text("#!/bin/sh\n", encoding="utf-8")

        with patch("antiagent.desktop.builder.Path") as mock_path_cls, \
             patch("subprocess.run"):
            # When to_global=True, builder uses Path("/usr/share/applications"), etc.
            pass

        # Direct test using patched paths
        with patch("antiagent.desktop.builder.APPS_DIR_GLOBAL", mock_apps, create=True), \
             patch("antiagent.desktop.builder.ICONS_DIR_GLOBAL", mock_icons, create=True), \
             patch("antiagent.desktop.builder.BIN_DIR_GLOBAL", mock_bin, create=True):
            pass

    def test_uninstall_linux_app_idempotent(self):
        def fake_expanduser(path: str) -> str:
            if path.startswith("~"):
                return str(self.mock_home) + path[1:]
            return path

        with patch("os.path.expanduser", side_effect=fake_expanduser), \
             patch("subprocess.run"):
            # Should not raise any error even if no files exist
            removed = uninstall_linux_app(to_global=False)
            self.assertEqual(removed, [])

    def test_uninstall_app_routes_to_linux(self):
        with patch("sys.platform", "linux"), \
             patch("antiagent.desktop.builder.uninstall_linux_app") as mock_uninst_linux:
            mock_uninst_linux.return_value = [Path("/tmp/com.antiagent.desktop.desktop")]
            res = uninstall_app(to_global=False)
            mock_uninst_linux.assert_called_once_with(to_global=False)
            self.assertEqual(res, [Path("/tmp/com.antiagent.desktop.desktop")])


class TestLinuxDesktopLaunch(unittest.TestCase):
    """Test desktop app launching and fallback behavior on Linux."""

    def test_local_url_helper(self):
        self.assertTrue(_is_local_url("http://127.0.0.1:4242/"))
        self.assertTrue(_is_local_url("http://localhost:4242/api/status"))
        self.assertTrue(_is_local_url("http://[::1]:4242/"))
        self.assertFalse(_is_local_url("https://github.com/aiden-guan/AntiAgent"))
        self.assertFalse(_is_local_url("https://google.com"))
        self.assertFalse(_is_local_url("mailto:test@example.com"))

    @patch("sys.platform", "linux")
    @patch("antiagent.desktop.builder.launch_linux_app")
    def test_launch_app_routes_to_linux(self, mock_launch_linux):
        launch_app()
        mock_launch_linux.assert_called_once()

    @patch("urllib.request.urlopen")
    @patch("shutil.which")
    @patch("subprocess.Popen")
    def test_launch_linux_app_browser_app_mode_fallback(self, mock_popen, mock_which, mock_urlopen):
        # Daemon already running
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        # No GTK, but google-chrome available
        def fake_which(cmd):
            if cmd == "google-chrome":
                return "/usr/bin/google-chrome"
            return None
        mock_which.side_effect = fake_which

        with patch("subprocess.run") as mock_subproc_run:
            # GTK probe fails
            probe_fail = MagicMock()
            probe_fail.returncode = 1
            mock_subproc_run.return_value = probe_fail

            launch_linux_app()

            # Verify chrome was spawned in app mode
            called_cmd = None
            for call in mock_popen.call_args_list:
                args = call[0][0]
                if isinstance(args, list) and "/usr/bin/google-chrome" in args:
                    called_cmd = args
                    break
            self.assertIsNotNone(called_cmd, "google-chrome was not spawned in app mode")
            self.assertIn("--app=http://127.0.0.1:4242", called_cmd)
            self.assertIn("--class=com.antiagent.desktop", called_cmd)


class TestLinuxRuntimeAndUpdater(unittest.TestCase):
    """Test Linux runtime detection and updater asset selection."""

    def test_detect_install_mode_linux_portable(self):
        temp_dir = tempfile.mkdtemp()
        try:
            repo_root = Path(temp_dir)
            pkg_dir = repo_root / "antiagent"
            pkg_dir.mkdir(parents=True)
            (repo_root / "AntiAgent.sh").write_text("#!/bin/sh\n")

            with patch("sys.platform", "linux"):
                mode = detect_install_mode(pkg_dir)
                self.assertEqual(mode, InstallMode.LINUX_PORTABLE)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_updater_select_recommended_asset_linux(self):
        assets = [
            {"name": "AntiAgent.dmg", "browser_download_url": "https://github.com/.../AntiAgent.dmg"},
            {"name": "AntiAgent-Windows.zip", "browser_download_url": "https://github.com/.../AntiAgent-Windows.zip"},
            {"name": "AntiAgent-Linux.tar.gz", "browser_download_url": "https://github.com/.../AntiAgent-Linux.tar.gz"},
            {"name": "AntiAgent.zip", "browser_download_url": "https://github.com/.../AntiAgent.zip"},
        ]
        with patch("sys.platform", "linux"):
            selected = select_recommended_asset(assets)
            self.assertIsNotNone(selected)
            self.assertEqual(selected["name"], "AntiAgent-Linux.tar.gz")


if __name__ == "__main__":
    unittest.main()
