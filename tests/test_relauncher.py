"""Unit tests for the detached desktop updater/relauncher helper."""

import plistlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from antiagent.desktop.relauncher import perform_staged_swap_and_relaunch


class TestRelauncherHelper(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp_dir.name)

        self.target_app = self.base / "Applications" / "AntiAgent.app"
        self.staged_app = self.base / "staging" / "AntiAgent.app"
        self.backup_app = self.base / "AntiAgent.app.backup"

        self._create_mock_bundle(self.target_app, version="0.4.3")
        self._create_mock_bundle(self.staged_app, version="0.4.4")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _create_mock_bundle(self, path: Path, version: str, bundle_id: str = "com.antiagent.desktop", binary: bool = True):
        macos_dir = path / "Contents" / "MacOS"
        macos_dir.mkdir(parents=True, exist_ok=True)
        plist_path = path / "Contents" / "Info.plist"

        plist_data = {
            "CFBundleIdentifier": bundle_id,
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "CFBundleExecutable": "AntiAgent",
        }
        with open(plist_path, "wb") as f:
            plistlib.dump(plist_data, f)

        if binary:
            bin_path = macos_dir / "AntiAgent"
            bin_path.write_bytes(b"#!/bin/sh\necho running\n")
            bin_path.chmod(0o755)

    def test_successful_staged_swap(self):
        """Valid staged bundle replaces target and backup is cleaned up."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            res = perform_staged_swap_and_relaunch(
                target_app=self.target_app,
                staged_app=self.staged_app,
                backup_app=self.backup_app,
                old_pid=0,
                parent_pid=0,
                target_version="0.4.4",
                no_relaunch=True,
            )
            self.assertTrue(res)
            self.assertTrue(self.target_app.is_dir())
            # Target should now have the new version (0.4.4)
            with open(self.target_app / "Contents" / "Info.plist", "rb") as f:
                data = plistlib.load(f)
            self.assertEqual(data["CFBundleShortVersionString"], "0.4.4")
            # Staged app moved away
            self.assertFalse(self.staged_app.exists())
            # Backup cleaned up on success
            self.assertFalse(self.backup_app.exists())

    def test_validation_failure_rolls_back_to_backup(self):
        """When staged app has wrong version or bundle ID, rollback restores old bundle."""
        # Corrupt staged app with wrong bundle id
        self._create_mock_bundle(self.staged_app, version="0.4.4", bundle_id="com.malicious.fake")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            res = perform_staged_swap_and_relaunch(
                target_app=self.target_app,
                staged_app=self.staged_app,
                backup_app=self.backup_app,
                old_pid=0,
                parent_pid=0,
                target_version="0.4.4",
                no_relaunch=True,
            )
            self.assertFalse(res)
            # Old target should have been restored from backup
            self.assertTrue(self.target_app.is_dir())
            with open(self.target_app / "Contents" / "Info.plist", "rb") as f:
                data = plistlib.load(f)
            self.assertEqual(data["CFBundleShortVersionString"], "0.4.3")
            self.assertEqual(data["CFBundleIdentifier"], "com.antiagent.desktop")

    def test_missing_binary_rolls_back(self):
        """When staged app is missing the native executable, rollback occurs."""
        # Create staged bundle without binary
        shutil.rmtree(self.staged_app)
        self._create_mock_bundle(self.staged_app, version="0.4.4", binary=False)

        with patch("subprocess.run"):
            res = perform_staged_swap_and_relaunch(
                target_app=self.target_app,
                staged_app=self.staged_app,
                backup_app=self.backup_app,
                old_pid=0,
                parent_pid=0,
                target_version="0.4.4",
                no_relaunch=True,
            )
            self.assertFalse(res)
            self.assertTrue(self.target_app.is_dir())
            # Old binary must still be there
            self.assertTrue((self.target_app / "Contents" / "MacOS" / "AntiAgent").is_file())


if __name__ == "__main__":
    unittest.main()
