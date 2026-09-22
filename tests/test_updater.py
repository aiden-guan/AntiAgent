"""Unit tests for AntiAgent in-app updater and downloader engine."""

import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from antiagent.updater import (
    InPlaceSelfUpdater,
    PipUpgradeManager,
    UpdateDownloader,
    check_for_updates,
    compare_versions,
    open_downloaded_file,
    parse_version,
    reveal_in_file_manager,
    select_recommended_asset,
)


class TestUpdaterEngine(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(parse_version("0.1.3"), (0, 1, 3))
        self.assertEqual(parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(parse_version("0.2"), (0, 2, 0))
        self.assertEqual(parse_version("v2"), (2, 0, 0))
        self.assertEqual(parse_version("0.1.4-beta.1"), (0, 1, 4))
        self.assertEqual(parse_version("1.0.0+build123"), (1, 0, 0))

    def test_compare_versions(self):
        self.assertEqual(compare_versions("0.1.3", "0.1.3"), 0)
        self.assertEqual(compare_versions("v0.1.3", "0.1.3"), 0)
        self.assertEqual(compare_versions("0.1.4", "0.1.3"), 1)
        self.assertEqual(compare_versions("0.1.3", "0.1.4"), -1)
        self.assertEqual(compare_versions("1.0.0", "0.9.9"), 1)
        self.assertEqual(compare_versions("0.2.0", "0.1.9"), 1)
        self.assertEqual(compare_versions("0.1.3", "0.2.0"), -1)

    def test_select_recommended_asset_macos(self):
        assets = [
            {"name": "AntiAgent.zip", "browser_download_url": "http://example.com/AntiAgent.zip"},
            {"name": "AntiAgent.dmg", "browser_download_url": "http://example.com/AntiAgent.dmg"},
            {"name": "AntiAgent.pkg", "browser_download_url": "http://example.com/AntiAgent.pkg"},
            {"name": "AntiAgent-Windows.zip", "browser_download_url": "http://example.com/win.zip"},
        ]
        with patch("sys.platform", "darwin"):
            rec = select_recommended_asset(assets)
            self.assertIsNotNone(rec)
            self.assertEqual(rec["name"], "AntiAgent.dmg")

    def test_select_recommended_asset_windows(self):
        assets = [
            {"name": "AntiAgent.dmg", "browser_download_url": "http://example.com/AntiAgent.dmg"},
            {"name": "AntiAgent-Windows.zip", "browser_download_url": "http://example.com/win.zip"},
            {"name": "AntiAgent.pkg", "browser_download_url": "http://example.com/AntiAgent.pkg"},
        ]
        with patch("sys.platform", "win32"):
            rec = select_recommended_asset(assets)
            self.assertIsNotNone(rec)
            self.assertEqual(rec["name"], "AntiAgent-Windows.zip")

    def test_check_for_updates_available(self):
        mock_github_release = {
            "tag_name": "v0.1.4",
            "name": "AntiAgent Release v0.1.4",
            "body": "### New Features\n- In-app update downloads!",
            "published_at": "2026-09-16T12:00:00Z",
            "html_url": "https://github.com/aiden-guan/AntiAgent/releases/tag/v0.1.4",
            "assets": [
                {
                    "name": "AntiAgent.dmg",
                    "size": 25000000,
                    "browser_download_url": "https://github.com/aiden-guan/AntiAgent/releases/download/v0.1.4/AntiAgent.dmg",
                    "content_type": "application/x-apple-diskimage",
                    "download_count": 42,
                }
            ],
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_github_release).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            info = check_for_updates(current_version="0.1.3")
            self.assertTrue(info["ok"])
            self.assertTrue(info["update_available"])
            self.assertEqual(info["latest_version"], "0.1.4")
            self.assertEqual(info["current_version"], "0.1.3")
            self.assertEqual(len(info["assets"]), 1)
            self.assertEqual(info["recommended_asset"]["name"], "AntiAgent.dmg")

    def test_check_for_updates_already_latest(self):
        mock_github_release = {
            "tag_name": "v0.1.3",
            "name": "AntiAgent Release v0.1.3",
            "body": "Bug fixes",
            "assets": [],
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_github_release).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            info = check_for_updates(current_version="0.1.3")
            self.assertTrue(info["ok"])
            self.assertFalse(info["update_available"])
            self.assertEqual(info["latest_version"], "0.1.3")

    def test_check_for_updates_rate_limit(self):
        http_err = urllib.error.HTTPError(
            url="https://api.github.com",
            code=403,
            msg="rate limit exceeded",
            hdrs={},
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            info = check_for_updates(current_version="0.1.3")
            self.assertFalse(info["ok"])
            self.assertFalse(info["update_available"])
            self.assertIn("rate limit", info["error"].lower())

    def test_downloader_lifecycle_and_completion(self):
        temp_dir = Path(tempfile.mkdtemp())
        downloader = UpdateDownloader()

        test_content = b"Mock AntiAgent binary content " * 1000  # 30 KB
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Length": str(len(test_content))}

        chunks = [test_content[:10000], test_content[10000:20000], test_content[20000:], b""]
        mock_resp.read.side_effect = chunks
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            started = downloader.start_download(
                "https://github.com/aiden-guan/AntiAgent/releases/download/v0.1.4/AntiAgent.dmg",
                filename="AntiAgent.dmg",
                dest_dir=temp_dir,
            )
            self.assertTrue(started)

            # Wait for completion
            for _ in range(30):
                if downloader.status == "completed":
                    break
                time.sleep(0.05)

            st = downloader.get_status()
            self.assertEqual(st["status"], "completed")
            self.assertEqual(st["progress"], 100)
            self.assertEqual(st["downloaded_bytes"], len(test_content))
            self.assertTrue(Path(st["dest_path"]).exists())
            self.assertEqual(Path(st["dest_path"]).read_bytes(), test_content)

    def test_downloader_cancellation(self):
        temp_dir = Path(tempfile.mkdtemp())
        downloader = UpdateDownloader()

        # Generator that simulates slow download
        def slow_read(chunk_size):
            time.sleep(0.05)
            return b"A" * 1024

        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Length": "1000000"}
        mock_resp.read.side_effect = slow_read
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            started = downloader.start_download(
                "https://github.com/aiden-guan/AntiAgent/releases/download/v0.1.4/large.dmg",
                filename="large.dmg",
                dest_dir=temp_dir,
            )
            self.assertTrue(started)
            time.sleep(0.1)
            self.assertEqual(downloader.status, "downloading")
            cancelled = downloader.cancel()
            self.assertTrue(cancelled)

            time.sleep(0.1)
            self.assertEqual(downloader.status, "cancelled")
            # Verify temp part file cleaned up
            self.assertFalse((temp_dir / "large.dmg.part").exists())

    def test_open_downloaded_file(self):
        downloads_dir = Path.home() / "Downloads"
        temp_file = downloads_dir / "test_fake.dmg"
        try:
            temp_file.write_text("test")
            with patch("subprocess.Popen") as mock_popen:
                res = open_downloaded_file(str(temp_file))
                self.assertTrue(res)
                mock_popen.assert_called_once()
        finally:
            if temp_file.exists():
                temp_file.unlink()

    def test_open_downloaded_file_security_rejection(self):
        # Disallowed extension
        temp_sh = Path.home() / "Downloads" / "malicious.sh"
        try:
            temp_sh.write_text("echo evil")
            self.assertFalse(open_downloaded_file(str(temp_sh)))
        finally:
            if temp_sh.exists():
                temp_sh.unlink()

        # Path outside downloads
        outside_file = Path("/etc/hosts")
        self.assertFalse(open_downloaded_file(str(outside_file)))

    def test_reveal_in_file_manager(self):
        downloads_dir = Path.home() / "Downloads"
        temp_file = downloads_dir / "test_fake.dmg"
        try:
            temp_file.write_text("test")
            with patch("subprocess.Popen") as mock_popen:
                res = reveal_in_file_manager(str(temp_file))
                self.assertTrue(res)
                mock_popen.assert_called_once()
        finally:
            if temp_file.exists():
                temp_file.unlink()

    def test_is_safe_download_url(self):
        from antiagent.updater import is_safe_download_url
        # Official releases
        self.assertTrue(is_safe_download_url("https://github.com/aiden-guan/AntiAgent/releases/download/v0.1.4/AntiAgent.dmg"))
        self.assertTrue(is_safe_download_url("https://objects.githubusercontent.com/github-production-release-asset-2e65be/12345/AntiAgent.dmg"))
        self.assertTrue(is_safe_download_url("https://raw.githubusercontent.com/aiden-guan/AntiAgent/main/install.sh"))

        # Insecure scheme
        self.assertFalse(is_safe_download_url("http://github.com/aiden-guan/AntiAgent/releases/download/v0.1.4/AntiAgent.dmg"))
        # Untrusted host
        self.assertFalse(is_safe_download_url("https://evil-hacker.com/malware.dmg"))
        self.assertFalse(is_safe_download_url("https://github.evil.com/fake.dmg"))
        # Untrusted repo on github
        self.assertFalse(is_safe_download_url("https://github.com/malicious-org/evil-repo/releases/download/v1/x.dmg"))

    def test_sanitize_download_filename(self):
        from antiagent.updater import sanitize_download_filename
        # Normal filename
        self.assertEqual(sanitize_download_filename("AntiAgent.dmg"), "AntiAgent.dmg")
        # Traversal attempt
        self.assertNotIn("/", sanitize_download_filename("../../etc/passwd"))
        self.assertNotIn("..", sanitize_download_filename("../../etc/passwd"))
        # Invalid extension gets converted to safe extension
        self.assertTrue(sanitize_download_filename("trojan.exe").endswith((".dmg", ".zip")))

    def test_downloader_rejects_untrusted_url(self):
        downloader = UpdateDownloader()
        res = downloader.start_download("https://evil-site.com/malware.dmg")
        self.assertFalse(res)
        self.assertEqual(downloader.status, "error")
        self.assertIn("Untrusted download URL", downloader.error_message)

    def test_pip_upgrade_manager(self):
        upgrader = PipUpgradeManager()
        mock_proc = MagicMock()
        mock_proc.stdout = io.StringIO("Successfully installed antiagent-0.1.4\n")
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_proc):
            started = upgrader.start_upgrade()
            self.assertTrue(started)
            for _ in range(20):
                if upgrader.status in ("success", "error"):
                    break
                time.sleep(0.05)
            st = upgrader.get_status()
            self.assertEqual(st["status"], "success")
            self.assertIn("Successfully installed", st["logs"])

    def test_in_place_self_updater_already_up_to_date(self):
        updater = InPlaceSelfUpdater()
        mock_check = {
            "ok": True,
            "latest_version": "0.1.0",
            "assets": [],
        }
        with patch("antiagent.updater.check_for_updates", return_value=mock_check):
            started = updater.start_update(force=False)
            self.assertTrue(started)
            for _ in range(20):
                if updater.status in ("success", "error"):
                    break
                time.sleep(0.05)
            st = updater.get_status()
            self.assertEqual(st["status"], "success")
            self.assertIn("already up to date", st["step"])
            self.assertTrue(st["is_up_to_date"])

    def test_in_place_self_updater_reset(self):
        updater = InPlaceSelfUpdater()
        updater.status = "success"
        updater.step_message = "Successfully updated to v0.1.6!"
        updater.progress = 100
        updater.target_version = "0.1.6"
        updater.logs = ["log 1", "log 2"]
        updater.is_up_to_date = True

        updater.reset()
        st = updater.get_status()
        self.assertEqual(st["status"], "idle")
        self.assertEqual(st["progress"], 0)
        self.assertEqual(st["step"], "")
        self.assertEqual(st["target_version"], "")
        self.assertEqual(st["logs"], "")
        self.assertFalse(st["is_up_to_date"])

    def test_in_place_self_updater_cancel(self):
        updater = InPlaceSelfUpdater()
        updater.status = "downloading"
        res = updater.cancel()
        self.assertTrue(res)
        self.assertEqual(updater.status, "cancelled")

    def test_zip_permission_preservation(self):
        """Verify zip extraction preserves POSIX executable bits."""
        import zipfile
        temp_dir = Path(tempfile.mkdtemp())
        zip_path = temp_dir / "test.zip"
        out_dir = temp_dir / "out"
        out_dir.mkdir(parents=True, exist_ok=True)

        # Create zip with an executable file
        with zipfile.ZipFile(zip_path, "w") as zf:
            info = zipfile.ZipInfo("bin/runner")
            info.external_attr = 0o755 << 16  # rwxr-xr-x
            zf.writestr(info, b"#!/bin/sh\necho ok\n")

        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                extracted_file = zf.extract(member, out_dir)
                mode = (member.external_attr >> 16) & 0o777
                if mode:
                    os.chmod(extracted_file, mode)

        extracted_bin = out_dir / "bin" / "runner"
        self.assertTrue(extracted_bin.exists())
        self.assertTrue(bool(extracted_bin.stat().st_mode & 0o111))


if __name__ == "__main__":
    unittest.main()
