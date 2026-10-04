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
            self.assertIn("AntiAgent/AntiAgent.desktop", names)
            self.assertIn("AntiAgent/antiagent.png", names)
            self.assertIn("AntiAgent/antiagent.svg", names)
            self.assertIn("AntiAgent/README.txt", names)
            self.assertIn("AntiAgent/antiagent/__init__.py", names)

            # Check launcher executable permissions
            ti_sh = tf.getmember("AntiAgent/AntiAgent.sh")
            self.assertTrue(bool(ti_sh.mode & 0o111), "AntiAgent.sh must be executable")

            ti_inst = tf.getmember("AntiAgent/install-app.sh")
            self.assertTrue(bool(ti_inst.mode & 0o111), "install-app.sh must be executable")

            # Check desktop entry content
            f_desk = tf.extractfile("AntiAgent/AntiAgent.desktop")
            self.assertIsNotNone(f_desk)
            desk_content = f_desk.read().decode("utf-8")
            self.assertIn("[Desktop Entry]", desk_content)
            self.assertIn("Name=AntiAgent Guard", desk_content)
            self.assertIn("Exec=antiagent-app %U", desk_content)
            self.assertIn("Icon=antiagent", desk_content)
            self.assertIn("StartupWMClass=com.antiagent.desktop", desk_content)
            self.assertIn("AntiAgent/com.antiagent.desktop.desktop", names)
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
            self.assertEqual(desktop_file.name, "antiagent.desktop")

            content = desktop_file.read_text(encoding="utf-8")
            self.assertIn("[Desktop Entry]", content)
            self.assertIn("Name=AntiAgent Guard", content)
            self.assertIn("Exec=antiagent-app %U", content)
            self.assertIn("StartupWMClass=com.antiagent.desktop", content)

            # Check reverse-DNS alias desktop file
            alt_desktop = self.mock_home / ".local/share/applications/com.antiagent.desktop.desktop"
            self.assertTrue(alt_desktop.exists(), "com.antiagent.desktop.desktop was not created")

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

    def test_install_app_routes_to_linux(self):
        with patch("sys.platform", "linux"), \
             patch("antiagent.desktop.builder.install_linux_app") as mock_install_linux:
            mock_install_linux.return_value = Path("/tmp/mock.desktop")
            res = install_app(to_global=False)
            mock_install_linux.assert_called_once_with(to_global=False)
            self.assertEqual(res, Path("/tmp/mock.desktop"))


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
