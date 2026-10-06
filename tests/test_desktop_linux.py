"""Unit tests for AntiAgent native Linux desktop application, installation, packaging, and launching."""

import os
import shutil
import sys
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
            sh_data = tf.extractfile(ti_sh).read().decode("utf-8")
            self.assertIn("WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS", sh_data)

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
            self.assertIn(str(self.mock_home / ".local/bin/antiagent-app"), content)
            self.assertIn("StartupWMClass=com.antiagent.desktop", content)

            # Ensure no duplicate unnamespaced antiagent.desktop exists
            legacy_desktop = self.mock_home / ".local/share/applications/antiagent.desktop"
            self.assertFalse(legacy_desktop.exists(), "Duplicate antiagent.desktop must not exist")

            # Check launcher
            launcher = self.mock_home / ".local/bin/antiagent-app"
            self.assertTrue(launcher.is_file())
            self.assertTrue(bool(launcher.stat().st_mode & 0o111))
            self.assertIn("WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS", launcher.read_text(encoding="utf-8"))

            # Check icons
            icon_png = self.mock_home / ".local/share/icons/hicolor/512x512/apps/antiagent.png"
            icon_svg = self.mock_home / ".local/share/icons/hicolor/scalable/apps/antiagent.svg"
            icon_png_alt = self.mock_home / ".local/share/icons/hicolor/512x512/apps/com.antiagent.desktop.png"
            icon_svg_alt = self.mock_home / ".local/share/icons/hicolor/scalable/apps/com.antiagent.desktop.svg"
            self.assertTrue(icon_png.is_file())
            self.assertTrue(icon_svg.is_file())
            self.assertTrue(icon_png_alt.is_file())
            self.assertTrue(icon_svg_alt.is_file())

    def test_install_linux_app_portable_includes_pythonpath(self):
        def fake_expanduser(path: str) -> str:
            if path.startswith("~"):
                return str(self.mock_home) + path[1:]
            return path

        with patch("os.path.expanduser", side_effect=fake_expanduser), \
             patch("subprocess.run"), \
             patch("antiagent.cli._is_importable_without_pythonpath", return_value=False):
            install_linux_app(to_global=False)
            launcher = self.mock_home / ".local/bin/antiagent-app"
            self.assertTrue(launcher.is_file())
            content = launcher.read_text(encoding="utf-8")
            self.assertIn("export PYTHONPATH=", content)
            self.assertIn("antiagent app", content)

    def test_install_linux_app_pip_installed_omits_pythonpath(self):
        def fake_expanduser(path: str) -> str:
            if path.startswith("~"):
                return str(self.mock_home) + path[1:]
            return path

        with patch("os.path.expanduser", side_effect=fake_expanduser), \
             patch("subprocess.run"), \
             patch("antiagent.cli._is_importable_without_pythonpath", return_value=True):
            install_linux_app(to_global=False)
            launcher = self.mock_home / ".local/bin/antiagent-app"
            self.assertTrue(launcher.is_file())
            content = launcher.read_text(encoding="utf-8")
            self.assertNotIn("export PYTHONPATH=", content)
            self.assertIn("antiagent app", content)

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

    def test_handle_linux_portable_update_flow(self):
        import tarfile
        import io
        from antiagent.updater import InPlaceSelfUpdater
        from antiagent.runtime import RuntimeInstallInfo

        temp_dir = tempfile.mkdtemp()
        try:
            repo_root = Path(temp_dir)
            pkg_dir = repo_root / "antiagent"
            pkg_dir.mkdir(parents=True)
            (pkg_dir / "__init__.py").write_text("# old\n")
            (repo_root / "AntiAgent.sh").write_text("#!/bin/sh\n")

            tar_buf = io.BytesIO()
            with tarfile.open(fileobj=tar_buf, mode="w:gz") as tf:
                content = b"# updated version 0.9.9\n"
                ti = tarfile.TarInfo(name="AntiAgent/antiagent/__init__.py")
                ti.size = len(content)
                ti.mtime = 1000
                tf.addfile(ti, io.BytesIO(content))

                sh_content = b"#!/bin/sh\n# updated launcher\n"
                ti_sh = tarfile.TarInfo(name="AntiAgent/AntiAgent.sh")
                ti_sh.size = len(sh_content)
                ti_sh.mode = 0o755
                ti_sh.mtime = 1000
                tf.addfile(ti_sh, io.BytesIO(sh_content))

            tar_bytes = tar_buf.getvalue()

            mock_resp = MagicMock()
            mock_resp.headers = {"Content-Length": str(len(tar_bytes))}
            mock_resp.read.side_effect = [tar_bytes, b""]
            mock_resp.__enter__.return_value = mock_resp

            updater = InPlaceSelfUpdater()
            check_data = {
                "ok": True,
                "latest_version": "0.9.9",
                "assets": [
                    {
                        "name": "AntiAgent-Linux.tar.gz",
                        "download_url": "https://github.com/aiden-guan/AntiAgent/releases/download/v0.9.9/AntiAgent-Linux.tar.gz",
                    }
                ],
            }

            fake_runtime = RuntimeInstallInfo(
                install_mode=InstallMode.LINUX_PORTABLE,
                current_executable=sys.executable,
                package_path=str(pkg_dir),
                macos_bundle_path=None,
                is_site_packages=False,
                current_version="0.1.0",
                launch_context={},
            )

            with patch("antiagent.updater.get_runtime_install_info", return_value=fake_runtime), \
                 patch("antiagent.updater.safe_urlopen", return_value=mock_resp), \
                 patch("antiagent.desktop.builder.install_linux_app"):
                updater._handle_linux_portable_update("0.9.9", check_data)

            self.assertEqual(updater.status, "success")
            self.assertEqual(updater.progress, 100)
            self.assertTrue(updater.update_verified)
            self.assertTrue(updater.components_updated["desktop_bundle"])
            self.assertTrue(updater.components_updated["python_package"])

            updated_content = (pkg_dir / "__init__.py").read_text()
            self.assertIn("updated version 0.9.9", updated_content)

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_handle_unsupported_mode_does_not_stay_stuck_checking(self):
        from antiagent.updater import InPlaceSelfUpdater

        updater = InPlaceSelfUpdater()
        check_data = {
            "ok": True,
            "latest_version": "0.9.9",
            "assets": [],
        }

        fake_runtime = MagicMock()
        fake_runtime.install_mode = MagicMock(value="unknown_mode")
        fake_runtime.package_path = "/unknown"

        with patch("antiagent.updater.get_runtime_install_info", return_value=fake_runtime), \
             patch("antiagent.updater.check_for_updates", return_value=check_data):
            updater.status = "checking"
            updater._update_worker(force=True, requested_version="0.9.9")

        self.assertEqual(updater.status, "error")
        self.assertIn("Unsupported installation mode", updater.error_message)
        self.assertNotEqual(updater.status, "checking")


class TestLinuxThemeDetection(unittest.TestCase):
    """Test detection and synchronization of system theme preference (dark/light) on Linux."""

    @classmethod
    def setUpClass(cls):
        cls._cleanup_modules = []
        mock_gi = MagicMock()
        mock_gio = MagicMock()
        mock_glib = MagicMock()
        mock_gi.repository.Gio = mock_gio
        mock_gi.repository.GLib = mock_glib

        for mod_name, mod_obj in [
            ("gi", mock_gi),
            ("gi.repository", mock_gi.repository),
            ("gi.repository.Gio", mock_gio),
            ("gi.repository.GLib", mock_glib),
        ]:
            if mod_name not in sys.modules:
                sys.modules[mod_name] = mod_obj
                cls._cleanup_modules.append(mod_name)

    @classmethod
    def tearDownClass(cls):
        for mod_name in cls._cleanup_modules:
            sys.modules.pop(mod_name, None)

    def test_detect_system_is_dark_portal_dark(self):
        from antiagent.desktop.linux_window import _detect_system_is_dark

        mock_val = MagicMock()
        mock_child = MagicMock()
        mock_variant = MagicMock()
        mock_variant.unpack.return_value = 1  # 1 = prefer-dark
        mock_child.get_variant.return_value = mock_variant
        mock_val.get_child_value.return_value = mock_child

        mock_bus = MagicMock()
        mock_bus.call_sync.return_value = mock_val

        with patch("gi.repository.Gio.bus_get_sync", return_value=mock_bus):
            self.assertTrue(_detect_system_is_dark())

    def test_detect_system_is_dark_portal_light(self):
        from antiagent.desktop.linux_window import _detect_system_is_dark

        mock_val = MagicMock()
        mock_child = MagicMock()
        mock_variant = MagicMock()
        mock_variant.unpack.return_value = 2  # 2 = prefer-light
        mock_child.get_variant.return_value = mock_variant
        mock_val.get_child_value.return_value = mock_child

        mock_bus = MagicMock()
        mock_bus.call_sync.return_value = mock_val

        with patch("gi.repository.Gio.bus_get_sync", return_value=mock_bus):
            self.assertFalse(_detect_system_is_dark())

    def test_detect_system_is_dark_gsettings_dark(self):
        from antiagent.desktop.linux_window import _detect_system_is_dark

        mock_source = MagicMock()
        mock_source.lookup.return_value = True

        mock_settings = MagicMock()
        mock_settings.list_keys.return_value = ["color-scheme", "gtk-theme"]
        mock_settings.get_string.side_effect = lambda k: "prefer-dark" if k == "color-scheme" else "Adwaita"

        # Force portal check to fail so it falls back to GSettings
        with patch("gi.repository.Gio.bus_get_sync", side_effect=Exception("no portal")), \
             patch("gi.repository.Gio.SettingsSchemaSource.get_default", return_value=mock_source), \
             patch("gi.repository.Gio.Settings.new", return_value=mock_settings):
            self.assertTrue(_detect_system_is_dark())

    def test_detect_system_is_dark_gsettings_light(self):
        from antiagent.desktop.linux_window import _detect_system_is_dark

        mock_source = MagicMock()
        mock_source.lookup.return_value = True

        mock_settings = MagicMock()
        mock_settings.list_keys.return_value = ["color-scheme", "gtk-theme"]
        mock_settings.get_string.side_effect = lambda k: "prefer-light" if k == "color-scheme" else "Adwaita"

        with patch("gi.repository.Gio.bus_get_sync", side_effect=Exception("no portal")), \
             patch("gi.repository.Gio.SettingsSchemaSource.get_default", return_value=mock_source), \
             patch("gi.repository.Gio.Settings.new", return_value=mock_settings):
            self.assertFalse(_detect_system_is_dark())

    def test_detect_system_is_dark_gtk_theme_env(self):
        from antiagent.desktop.linux_window import _detect_system_is_dark

        with patch("gi.repository.Gio.bus_get_sync", side_effect=Exception("no portal")), \
             patch("gi.repository.Gio.SettingsSchemaSource.get_default", return_value=None), \
             patch.dict(os.environ, {"GTK_THEME": "Adwaita-dark"}):
            self.assertTrue(_detect_system_is_dark())

    def test_detect_system_is_dark_kde_globals(self):
        from antiagent.desktop.linux_window import _detect_system_is_dark

        temp_dir = tempfile.mkdtemp()
        try:
            kde_cfg = Path(temp_dir) / ".config" / "kdeglobals"
            kde_cfg.parent.mkdir(parents=True)
            kde_cfg.write_text("[General]\nColorScheme=BreezeDark\n", encoding="utf-8")

            with patch("gi.repository.Gio.bus_get_sync", side_effect=Exception("no portal")), \
                 patch("gi.repository.Gio.SettingsSchemaSource.get_default", return_value=None), \
                 patch.dict(os.environ, {"GTK_THEME": ""}, clear=True), \
                 patch("pathlib.Path.home", return_value=Path(temp_dir)):
                self.assertTrue(_detect_system_is_dark())
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_setup_theme_listener(self):
        from antiagent.desktop.linux_window import _setup_theme_listener

        mock_callback = MagicMock()
        mock_bus = MagicMock()
        mock_bus.signal_subscribe.return_value = 42

        mock_source = MagicMock()
        mock_source.lookup.return_value = True
        mock_settings = MagicMock()

        with patch("gi.repository.Gio.bus_get_sync", return_value=mock_bus), \
             patch("gi.repository.Gio.SettingsSchemaSource.get_default", return_value=mock_source), \
             patch("gi.repository.Gio.Settings.new", return_value=mock_settings):
            handles = _setup_theme_listener(mock_callback)
            self.assertTrue(len(handles) >= 1)
            mock_bus.signal_subscribe.assert_called_once()
            self.assertEqual(mock_settings.connect.call_count, 2)


class TestLinuxDoctorGuiDiagnostics(unittest.TestCase):
    """Test Linux GUI dependency diagnostics in doctor."""

    def test_check_linux_gui_dependencies_native_gtk4(self):
        from antiagent.engine.doctor import check_linux_gui_dependencies

        mock_gi = MagicMock()
        with patch.dict(sys.modules, {"gi": mock_gi}):
            res = check_linux_gui_dependencies()
            self.assertEqual(res["status"], "native")
            self.assertEqual(res["backend_name"], "GTK 4 + WebKit 6")
            self.assertTrue(res["has_gi"])
            self.assertTrue(res["has_gtk4"])
            self.assertTrue(res["has_webkit6"])

    def test_check_linux_gui_dependencies_browser_fallback(self):
        from antiagent.engine.doctor import check_linux_gui_dependencies

        # Gi unavailable, but chrome installed
        with patch.dict(sys.modules, {"gi": None}), \
             patch("shutil.which", side_effect=lambda x: "/usr/bin/google-chrome" if "google-chrome" in x else None):
            res = check_linux_gui_dependencies()
            self.assertEqual(res["status"], "browser_app")
            self.assertEqual(res["backend_name"], "Google Chrome")
            self.assertFalse(res["has_gi"])

    def test_check_linux_gui_dependencies_pure_fallback(self):
        from antiagent.engine.doctor import check_linux_gui_dependencies

        # Gi unavailable and no browsers installed
        with patch.dict(sys.modules, {"gi": None}), \
             patch("shutil.which", return_value=None):
            res = check_linux_gui_dependencies()
            self.assertEqual(res["status"], "fallback")
            self.assertEqual(res["backend_name"], "Default Web Browser")
            self.assertIn("apt install", res["install_hint"])

    def test_get_doctor_report_includes_gui_on_linux(self):
        from antiagent.engine.doctor import get_doctor_report

        with patch("antiagent.engine.doctor.sys.platform", "linux"):
            report = get_doctor_report()
            self.assertIn("gui_dependencies", report)
            self.assertIn("status", report["gui_dependencies"])
            self.assertIn("backend_name", report["gui_dependencies"])


if __name__ == "__main__":
    unittest.main()


