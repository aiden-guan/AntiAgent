"""In-app update checker, downloader, and manager for AntiAgent."""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from antiagent import __version__
from antiagent.runtime import (
    InstallMode,
    find_active_macos_bundle,
    get_runtime_install_info,
    is_desktop_app_running,
)

GITHUB_REPO = "aiden-guan/AntiAgent"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

ALLOWED_UPDATE_HOSTS = (
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "raw.githubusercontent.com",
    "github-releases.githubusercontent.com",
)

ALLOWED_EXTENSIONS = (
    ".dmg",
    ".pkg",
    ".zip",
    ".tar.gz",
)


def is_safe_download_url(url: str, repo: str = GITHUB_REPO) -> bool:
    """Validate that the download URL points strictly to official GitHub releases for the repo."""
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return False
        hostname = (parsed.hostname or "").lower()
        if hostname not in ALLOWED_UPDATE_HOSTS:
            return False

        # If on github.com, raw.githubusercontent.com, or api.github.com, ensure it belongs to the official repo
        if hostname in ("github.com", "raw.githubusercontent.com", "api.github.com"):
            clean_path = parsed.path.lower()
            expected_prefix = f"/{repo.lower()}/"
            if not clean_path.startswith(expected_prefix) and not clean_path.startswith(f"/repos/{expected_prefix}"):
                return False

        return True
    except Exception:
        return False


def sanitize_download_filename(filename: str) -> str:
    """Sanitize filename to prevent directory traversal and disallow dangerous extensions."""
    base = os.path.basename(filename.replace("\\", "/")).strip()
    base = re.sub(r"[^\w\.\-\+]", "_", base)
    if not any(base.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
        base += ".dmg" if sys.platform == "darwin" else ".zip"
    return base


def get_ssl_context() -> ssl.SSLContext:
    """Create a verified SSL context with certifi fallback if installed. Fails closed on invalid certs."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    return ssl.create_default_context()


def safe_urlopen(req: Any, timeout: float = 10.0):
    """Execute urlopen with strict verified context. Never silently fall back to unverified TLS."""
    ctx = get_ssl_context()
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            raise ssl.SSLCertVerificationError(
                f"TLS certificate verification failed for {getattr(req, 'full_url', req)}. "
                "For security, software updates must fail closed on invalid certificates."
            ) from e
        raise


def parse_version(ver_str: str) -> Tuple[int, ...]:
    """Parse version string like '0.1.3', 'v0.2.0', '1.0.0-beta' into integer tuple."""
    clean = ver_str.strip().lstrip("vV")
    main_ver = clean.split("-")[0].split("+")[0]
    parts = []
    for seg in main_ver.split("."):
        nums = re.findall(r"\d+", seg)
        if nums:
            parts.append(int(nums[0]))
        else:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def compare_versions(v1: str, v2: str) -> int:
    """Compare two semantic version strings.
    Returns:
       1 if v1 > v2
       0 if v1 == v2
      -1 if v1 < v2
    """
    t1 = parse_version(v1)
    t2 = parse_version(v2)
    if t1 > t2:
        return 1
    elif t1 < t2:
        return -1
    return 0


def select_recommended_asset(assets: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Determine best download asset for the current OS."""
    if not assets:
        return None

    if sys.platform == "darwin":
        for a in assets:
            if a.get("name", "").lower().endswith(".dmg"):
                return a
        for a in assets:
            if a.get("name", "").lower().endswith(".pkg"):
                return a
        for a in assets:
            n = a.get("name", "").lower()
            if n.endswith(".zip") and "windows" not in n and "win" not in n:
                return a

    elif sys.platform == "win32":
        for a in assets:
            n = a.get("name", "").lower()
            if "win" in n and n.endswith(".zip"):
                return a
        for a in assets:
            if a.get("name", "").lower().endswith(".zip"):
                return a

    for a in assets:
        n = a.get("name", "").lower()
        if n.endswith(".tar.gz") or n.endswith(".zip"):
            return a

    return assets[0]


def is_generic_installer_body(body: str) -> bool:
    """Check if GitHub release body only contains download links/installer boilerplate without actual feature notes."""
    if not body or not body.strip():
        return True
    lower = body.lower()
    if (
        "### summary" in lower
        or "### what's new" in lower
        or "### architectural & functional highlights" in lower
        or "### features" in lower
        or "### improvements" in lower
        or "### highlights" in lower
    ):
        return False
    if "which download is right for you" in lower or ("1-line terminal installs" in lower and "summary" not in lower):
        return True
    return False


def get_remote_release_notes(version: str, repo: str = GITHUB_REPO) -> str:
    """Fetch release notes for version from the remote repository's CHANGELOG.md."""
    clean_ver = version.strip().lstrip("vV")
    url = f"https://raw.githubusercontent.com/{repo}/main/CHANGELOG.md"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"AntiAgent/{__version__} (Updater)"}
    )
    try:
        with safe_urlopen(req, timeout=5.0) as resp:
            content = resp.read().decode("utf-8")
            pattern = rf"##\s+\[v?{re.escape(clean_ver)}\][^\n]*\n([\s\S]*?)(?=\n##\s+\[|\Z)"
            m = re.search(pattern, content)
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return ""


def get_local_release_notes(version: str = __version__) -> str:
    """Extract release notes for version from CHANGELOG.md if available."""
    try:
        candidates = [
            Path(__file__).parent.parent / "CHANGELOG.md",
            Path(__file__).parent / "CHANGELOG.md",
            Path.cwd() / "CHANGELOG.md",
        ]
        clean_ver = version.strip().lstrip("vV")
        for p in candidates:
            if p.is_file():
                content = p.read_text(encoding="utf-8")
                pattern = rf"##\s+\[v?{re.escape(clean_ver)}\][^\n]*\n([\s\S]*?)(?=\n##\s+\[|\Z)"
                match = re.search(pattern, content)
                if match:
                    return match.group(1).strip()
    except Exception:
        pass
    return ""


def get_best_release_notes(version: str, github_body: str = "", repo: str = GITHUB_REPO) -> str:
    """Resolve the true 'What's New' release notes for a version."""
    clean_ver = version.strip().lstrip("vV")
    local_notes = get_local_release_notes(clean_ver)
    changelog_notes = local_notes
    if not changelog_notes:
        changelog_notes = get_remote_release_notes(clean_ver, repo=repo)

    if github_body and not is_generic_installer_body(github_body):
        return github_body.strip()

    if changelog_notes:
        if github_body and is_generic_installer_body(github_body):
            return f"{changelog_notes}\n\n---\n\n{github_body.strip()}"
        return changelog_notes

    if github_body and github_body.strip():
        return github_body.strip()

    return f"AntiAgent v{clean_ver} release with security improvements, bug fixes, and performance updates."


def check_for_updates(current_version: str = __version__, repo: str = GITHUB_REPO) -> Dict[str, Any]:
    """Check GitHub releases API for newer AntiAgent versions."""
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        api_url,
        headers={
            "User-Agent": f"AntiAgent/{current_version} (Updater)",
            "Accept": "application/vnd.github.v3+json",
        },
    )

    try:
        with safe_urlopen(req, timeout=6.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        raw_tag = data.get("tag_name", "")
        latest_version = raw_tag.lstrip("vV").strip()
        update_available = compare_versions(latest_version, current_version) > 0

        raw_assets = data.get("assets", [])
        assets_list = []
        for item in raw_assets:
            assets_list.append({
                "name": item.get("name"),
                "size": item.get("size", 0),
                "download_url": item.get("browser_download_url"),
                "content_type": item.get("content_type"),
                "download_count": item.get("download_count", 0),
            })

        recommended = select_recommended_asset(assets_list)
        release_notes = get_best_release_notes(latest_version or current_version, data.get("body") or "", repo=repo)

        return {
            "ok": True,
            "update_available": update_available,
            "current_version": current_version,
            "latest_version": latest_version,
            "release_name": data.get("name") or (f"v{latest_version}" if latest_version else raw_tag),
            "release_notes": release_notes,
            "published_at": data.get("published_at") or "",
            "html_url": data.get("html_url") or f"https://github.com/{repo}/releases/latest",
            "repo_url": f"https://github.com/{repo}",
            "assets": assets_list,
            "recommended_asset": recommended,
            "tarball_url": data.get("tarball_url") or f"https://github.com/{repo}/archive/refs/tags/v{latest_version}.tar.gz",
        }
    except urllib.error.HTTPError as e:
        msg = f"GitHub API returned HTTP {e.code}: {e.reason}"
        if e.code == 404:
            msg = "No public releases found on GitHub repository yet."
        elif e.code == 403:
            msg = "GitHub API rate limit exceeded. Please try again later or visit GitHub directly."
        return {
            "ok": False,
            "update_available": False,
            "current_version": current_version,
            "latest_version": current_version,
            "error": msg,
            "release_name": f"AntiAgent v{current_version}",
            "release_notes": get_local_release_notes(current_version),
            "html_url": f"https://github.com/{repo}/releases/latest",
            "repo_url": f"https://github.com/{repo}",
            "assets": [],
            "recommended_asset": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "update_available": False,
            "current_version": current_version,
            "latest_version": current_version,
            "error": f"Failed to check for updates: {str(e)}",
            "release_name": f"AntiAgent v{current_version}",
            "release_notes": get_local_release_notes(current_version),
            "html_url": f"https://github.com/{repo}/releases/latest",
            "repo_url": f"https://github.com/{repo}",
            "assets": [],
            "recommended_asset": None,
        }


def get_default_download_dir() -> Path:
    """Resolve standard download directory for the user."""
    downloads = Path.home() / "Downloads"
    if downloads.is_dir() and os.access(str(downloads), os.W_OK):
        return downloads
    fallback = Path.home() / ".antiagent" / "downloads"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def get_pending_update_file() -> Path:
    """File path to store state for pending updates awaiting activation."""
    p = Path.home() / ".antiagent" / "pending_update.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def save_pending_update(record: Dict[str, Any]) -> None:
    """Persist update diagnostic state until the target version activates."""
    f = get_pending_update_file()
    f.write_text(json.dumps(record, indent=2), encoding="utf-8")


def load_pending_update() -> Optional[Dict[str, Any]]:
    """Load pending update record if present."""
    f = get_pending_update_file()
    if f.is_file():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def clear_pending_update() -> None:
    """Remove pending update record upon verified activation."""
    f = get_pending_update_file()
    if f.is_file():
        try:
            f.unlink()
        except Exception:
            pass


def check_and_complete_pending_update(current_ver: str = __version__) -> Optional[Dict[str, Any]]:
    """Check if a pending update successfully activated upon startup."""
    record = load_pending_update()
    if not record:
        return None
    target = record.get("target_version")
    if target and compare_versions(current_ver, target) >= 0:
        clear_pending_update()
        record["activated"] = True
        return record
    return record


class UpdateDownloader:
    """Manages downloading update packages in a background thread with real-time progress."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.status = "idle"  # idle | downloading | completed | error | cancelled
        self.progress = 0
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.speed_bps = 0.0
        self.filename = ""
        self.dest_path = ""
        self.error_message = ""
        self.download_url = ""

    def start_download(
        self,
        download_url: str,
        filename: Optional[str] = None,
        dest_dir: Optional[Path] = None,
    ) -> bool:
        with self._lock:
            if self.status == "downloading" and self._thread and self._thread.is_alive():
                return False

            if not is_safe_download_url(download_url):
                self.status = "error"
                self.error_message = (
                    f"Security: Untrusted download URL. Only official GitHub releases from {GITHUB_REPO} are permitted."
                )
                return False

            raw_name = filename or download_url.split("?")[0].rstrip("/").split("/")[-1]
            safe_name = sanitize_download_filename(raw_name or "AntiAgent-update")

            if dest_dir is None:
                dest_dir = get_default_download_dir()
            dest_dir = dest_dir.resolve()
            dest_dir.mkdir(parents=True, exist_ok=True)

            target_path = (dest_dir / safe_name).resolve()
            try:
                target_path.relative_to(dest_dir)
            except ValueError:
                self.status = "error"
                self.error_message = "Security: Path traversal attempt detected."
                return False

            self.status = "downloading"
            self.progress = 0
            self.downloaded_bytes = 0
            self.total_bytes = 0
            self.speed_bps = 0.0
            self.filename = safe_name
            self.dest_path = str(target_path)
            self.error_message = ""
            self.download_url = download_url
            self._cancel_event.clear()

            self._thread = threading.Thread(
                target=self._download_worker,
                args=(download_url, target_path),
                daemon=True,
            )
            self._thread.start()
            return True

    def cancel(self) -> bool:
        with self._lock:
            if self.status != "downloading":
                return False
            self._cancel_event.set()
            self.status = "cancelled"
            return True

    def _download_worker(self, url: str, target_path: Path) -> None:
        temp_path = target_path.with_suffix(target_path.suffix + ".part")
        start_time = time.time()
        last_calc_time = start_time
        last_calc_bytes = 0

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": f"AntiAgent/{__version__} (Downloader)"},
            )
            with safe_urlopen(req, timeout=15.0) as resp:
                total_len = resp.headers.get("Content-Length")
                total_bytes = int(total_len) if total_len and total_len.isdigit() else 0

                with self._lock:
                    self.total_bytes = total_bytes

                downloaded = 0
                chunk_size = 65536

                with open(temp_path, "wb") as f:
                    while True:
                        if self._cancel_event.is_set():
                            break
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)

                        now = time.time()
                        time_diff = now - last_calc_time
                        if time_diff >= 0.5:
                            bytes_diff = downloaded - last_calc_bytes
                            bps = bytes_diff / time_diff
                            last_calc_time = now
                            last_calc_bytes = downloaded
                        else:
                            bps = self.speed_bps

                        pct = int((downloaded / total_bytes * 100)) if total_bytes > 0 else 0
                        with self._lock:
                            self.downloaded_bytes = downloaded
                            self.progress = min(pct, 100)
                            self.speed_bps = bps

            if self._cancel_event.is_set():
                if temp_path.exists():
                    temp_path.unlink()
                with self._lock:
                    self.status = "cancelled"
                return

            if target_path.exists():
                target_path.unlink()
            temp_path.rename(target_path)

            with self._lock:
                self.status = "completed"
                self.progress = 100
                self.downloaded_bytes = downloaded
                if self.total_bytes == 0:
                    self.total_bytes = downloaded

        except Exception as e:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            with self._lock:
                self.status = "error"
                self.error_message = str(e)

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "status": self.status,
                "progress": self.progress,
                "downloaded_bytes": self.downloaded_bytes,
                "total_bytes": self.total_bytes,
                "speed_bps": round(self.speed_bps, 1),
                "filename": self.filename,
                "dest_path": self.dest_path,
                "error": self.error_message,
                "download_url": self.download_url,
            }


def open_downloaded_file(file_path: str) -> bool:
    """Safely open downloaded installer. Confined strictly to verified download directory and allowed extensions."""
    if not file_path:
        return False
    p = Path(file_path).resolve()
    if not p.is_file():
        return False

    if not any(p.name.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
        return False

    allowed_dirs = [
        get_default_download_dir().resolve(),
        (Path.home() / "Downloads").resolve(),
        (Path.home() / ".antiagent").resolve(),
    ]
    is_safe = False
    for ad in allowed_dirs:
        try:
            p.relative_to(ad)
            is_safe = True
            break
        except ValueError:
            pass

    if not is_safe and str(p) != global_downloader.dest_path:
        return False

    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
            return True
        elif sys.platform == "win32":
            if hasattr(os, "startfile"):
                os.startfile(str(p))
                return True
            subprocess.Popen(["cmd", "/c", "start", "", str(p)])
            return True
        else:
            subprocess.Popen(["xdg-open", str(p)])
            return True
    except Exception:
        return False


def reveal_in_file_manager(file_path: str) -> bool:
    """Safely reveal downloaded file in macOS Finder or Windows File Explorer."""
    if not file_path:
        return False
    p = Path(file_path).resolve()
    if not p.exists():
        return False

    allowed_dirs = [
        get_default_download_dir().resolve(),
        (Path.home() / "Downloads").resolve(),
        (Path.home() / ".antiagent").resolve(),
    ]
    is_safe = False
    for ad in allowed_dirs:
        try:
            p.relative_to(ad)
            is_safe = True
            break
        except ValueError:
            pass

    if not is_safe and str(p) != global_downloader.dest_path:
        return False

    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
            return True
        elif sys.platform == "win32":
            subprocess.Popen(["explorer.exe", f"/select,{str(p)}"])
            return True
        else:
            subprocess.Popen(["xdg-open", str(p.parent)])
            return True
    except Exception:
        return False


class PipUpgradeManager:
    """Manages Python package upgrades pinned to an official GitHub release version with fresh process verification."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.status = "idle"  # idle | running | success | error
        self.logs: List[str] = []
        self.returncode: Optional[int] = None
        self.target_version: str = ""
        self.verified_version: Optional[str] = None
        self.error_message: str = ""
        self._thread: Optional[threading.Thread] = None

    def start_upgrade(
        self,
        target_version: Optional[str] = None,
        tarball_url: Optional[str] = None,
        allow_break_system_packages: bool = False,
    ) -> bool:
        with self._lock:
            if self.status == "running":
                return False

            self.status = "running"
            self.target_version = target_version or ""
            self.verified_version = None
            self.error_message = ""
            self.logs = []
            self.returncode = None

            self._thread = threading.Thread(
                target=self._upgrade_worker,
                args=(target_version, tarball_url, allow_break_system_packages),
                daemon=True,
            )
            self._thread.start()
            return True

    def _log(self, text: str) -> None:
        with self._lock:
            self.logs.append(text if text.endswith("\n") else text + "\n")

    def _verify_fresh_process_version(self, expected_ver: str) -> Tuple[bool, Optional[str]]:
        """Verify the newly installed package version in a clean, separate Python interpreter process."""
        py = sys.executable or "python3"
        probe = "import antiagent; sys.stdout.write(antiagent.__version__)"
        try:
            res = subprocess.run(
                [py, "-c", f"import sys; {probe}"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if res.returncode == 0:
                found = res.stdout.strip()
                return (compare_versions(found, expected_ver) >= 0 or found == expected_ver), found
            return False, None
        except Exception:
            return False, None

    def _run_pip_command(self, cmd: List[str]) -> Tuple[int, str]:
        """Execute a pip command using subprocess.Popen, streaming output line-by-line."""
        output_chunks = []
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            if proc.stdout:
                for line in iter(proc.stdout.readline, ""):
                    output_chunks.append(line)
                    self._log(line.rstrip())
            ret = proc.wait()
            return ret, "".join(output_chunks)
        except Exception as e:
            err = f"Failed to execute pip command: {e}\n"
            self._log(err)
            return 1, err

    def _upgrade_worker(
        self,
        target_version: Optional[str],
        tarball_url: Optional[str],
        allow_break_system_packages: bool,
    ) -> None:
        temp_dir = None
        try:
            # 1. Resolve target version if not specified
            if not target_version:
                info = check_for_updates()
                if not info.get("ok") or not info.get("latest_version"):
                    raise RuntimeError(info.get("error") or "Failed to query GitHub for latest release version.")
                target_version = info["latest_version"]
                tarball_url = info.get("tarball_url")

            with self._lock:
                self.target_version = target_version

            self._log(f"🚀 Initializing version-pinned Python upgrade to AntiAgent v{target_version}...")

            # 2. Resolve source tarball URL
            if not tarball_url:
                tarball_url = f"https://github.com/{GITHUB_REPO}/archive/refs/tags/v{target_version}.tar.gz"

            # 3. Download source archive to a secure temporary directory if remote
            if tarball_url.startswith("file://"):
                archive_path = Path(tarball_url[7:])
            elif os.path.isfile(tarball_url):
                archive_path = Path(tarball_url)
            else:
                if not is_safe_download_url(tarball_url):
                    raise RuntimeError(f"Security: Untrusted tarball URL: {tarball_url}")

                temp_dir = Path(tempfile.mkdtemp(prefix="antiagent_pip_"))
                archive_path = temp_dir / f"antiagent-{target_version}.tar.gz"

                self._log(f"📥 Downloading release tarball: {tarball_url}")
                req = urllib.request.Request(
                    tarball_url,
                    headers={"User-Agent": f"AntiAgent/{__version__} (PipUpgrader)"},
                )
                with safe_urlopen(req, timeout=20.0) as resp:
                    with open(archive_path, "wb") as f_out:
                        shutil.copyfileobj(resp, f_out)

            # 4. Construct pip command
            py_exec = sys.executable or "python3"
            base_cmd = [py_exec, "-m", "pip", "install", "--upgrade", str(archive_path)]

            self._log(f"⚙️ Running pip upgrade: {py_exec} -m pip install --upgrade {archive_path.name}")
            retcode, output = self._run_pip_command(base_cmd)

            # 5. Handle PEP 668 externally managed environment safely
            if retcode != 0 and "externally-managed-environment" in output.lower():
                self._log("⚠️ Python environment is externally managed (PEP 668).")
                # Try --user only if not in virtualenv
                in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
                if not in_venv:
                    self._log("ℹ️ Attempting safe installation into user site-packages (--user)...")
                    user_cmd = base_cmd + ["--user"]
                    retcode, output = self._run_pip_command(user_cmd)

                if retcode != 0:
                    if allow_break_system_packages:
                        self._log("⚠️ User explicitly requested --break-system-packages. Retrying as last resort...")
                        bsp_cmd = base_cmd + ["--break-system-packages"]
                        retcode, output = self._run_pip_command(bsp_cmd)
                    else:
                        raise RuntimeError(
                            "Installation failed due to an externally managed Python environment (PEP 668). "
                            "AntiAgent does not pass --break-system-packages by default. "
                            "Please run AntiAgent inside a virtual environment (venv) or install via pipx."
                        )

            if retcode != 0:
                raise RuntimeError(f"pip install exited with code {retcode}")

            # 6. Fresh-process version verification: do not trust return code 0 alone!
            self._log(f"🔍 Verifying installed version with fresh Python interpreter process ({py_exec})...")
            verified, found_ver = self._verify_fresh_process_version(target_version)

            if not verified:
                raise RuntimeError(
                    f"Post-install verification failed: pip reported exit code 0, but a fresh interpreter process loaded "
                    f"v{found_ver or 'unknown'} instead of target v{target_version}."
                )

            with self._lock:
                self.returncode = 0
                self.status = "success"
                self.verified_version = found_ver
                self.error_message = ""
            self._log(f"✅ Successfully installed and verified AntiAgent v{found_ver} via pip!")

        except Exception as e:
            with self._lock:
                self.status = "error"
                self.returncode = 1
                self.error_message = str(e)
            self._log(f"❌ Pip upgrade failed: {str(e)}")

        finally:
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "status": self.status,
                "logs": "".join(self.logs),
                "returncode": self.returncode,
                "target_version": self.target_version,
                "verified_version": self.verified_version,
                "error": self.error_message,
            }


class InPlaceSelfUpdater:
    """Manages 1-click in-place background update of AntiAgent tailored strictly to the active installation mode."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.status = "idle"  # idle | checking | downloading | extracting | staging | success | error | cancelled
        self.progress = 0
        self.step_message = ""
        self.error_message = ""
        self.target_version = ""
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.speed_bps = 0.0
        self.logs: List[str] = []
        self.is_up_to_date = False
        self.components_updated: Dict[str, bool] = {"desktop_bundle": False, "python_package": False}
        self.staged_bundle_path: Optional[str] = None
        self.target_bundle_path: Optional[str] = None
        self.update_verified = False

    def reset(self) -> None:
        """Reset updater status and clear in-memory state back to idle."""
        with self._lock:
            self.status = "idle"
            self.progress = 0
            self.step_message = ""
            self.error_message = ""
            self.target_version = ""
            self.downloaded_bytes = 0
            self.total_bytes = 0
            self.speed_bps = 0.0
            self.logs = []
            self.is_up_to_date = False
            self.components_updated = {"desktop_bundle": False, "python_package": False}
            self.staged_bundle_path = None
            self.target_bundle_path = None
            self.update_verified = False

    def start_update(self, force: bool = False, version: Optional[str] = None) -> bool:
        with self._lock:
            if self.status in ("checking", "downloading", "extracting", "staging") and self._thread and self._thread.is_alive():
                return False

            self.status = "checking"
            self.progress = 5
            self.step_message = "Checking latest release on GitHub..."
            self.error_message = ""
            self.target_version = version or ""
            self.downloaded_bytes = 0
            self.total_bytes = 0
            self.speed_bps = 0.0
            self.logs = [f"🚀 Initializing AntiAgent update coordinator (running v{__version__})...\n"]
            self.is_up_to_date = False
            self.components_updated = {"desktop_bundle": False, "python_package": False}
            self.staged_bundle_path = None
            self.target_bundle_path = None
            self.update_verified = False
            self._cancel_event.clear()

            self._thread = threading.Thread(
                target=self._update_worker,
                args=(force, version),
                daemon=True,
            )
            self._thread.start()
            return True

    def cancel(self) -> bool:
        with self._lock:
            if self.status not in ("checking", "downloading"):
                return False
            self._cancel_event.set()
            self.status = "cancelled"
            self.step_message = "Update cancelled by user."
            return True

    def _log(self, text: str) -> None:
        with self._lock:
            self.logs.append(text if text.endswith("\n") else text + "\n")

    def _set_stage(self, status: str, progress: int, message: str) -> None:
        with self._lock:
            self.status = status
            self.progress = progress
            self.step_message = message
        self._log(f"[{status.upper()}] {message}")

    def _update_worker(self, force: bool, requested_version: Optional[str]) -> None:
        try:
            # 1. Detect active installation mode
            runtime_info = get_runtime_install_info()
            mode = runtime_info.install_mode
            self._log(f"🔍 Detected active install mode: {mode.value.upper()} (package: {runtime_info.package_path})")

            # 2. Fetch release details
            check_data = check_for_updates()
            if not check_data.get("ok") and not requested_version:
                with self._lock:
                    self.status = "error"
                    self.error_message = check_data.get("error") or "Failed to connect to GitHub releases."
                self._log(f"❌ {self.error_message}")
                return

            latest_ver = requested_version or check_data.get("latest_version")
            if not latest_ver:
                with self._lock:
                    self.status = "error"
                    self.error_message = "No target version found to update to."
                return

            with self._lock:
                self.target_version = latest_ver

            # Check if update is needed
            if not force and compare_versions(latest_ver, __version__) <= 0:
                with self._lock:
                    self.is_up_to_date = True
                self._set_stage("success", 100, f"AntiAgent is already up to date (v{__version__}).")
                return

            # Branch update behavior strictly by installation mode
            if mode == InstallMode.EDITABLE_SOURCE:
                self._log("⚠️ AntiAgent is running from an editable/source development checkout.")
                with self._lock:
                    self.status = "error"
                    self.error_message = (
                        f"Editable/source install detected at {runtime_info.package_path}. "
                        "The self-updater does not overwrite developer source checkouts. "
                        "Please update via 'git pull' or checkout the target tag."
                    )
                self._log(f"❌ {self.error_message}")
                return

            elif mode == InstallMode.MACOS_BUNDLE:
                self._handle_macos_bundle_update(latest_ver, check_data)

            elif mode == InstallMode.PIP:
                self._handle_pip_update(latest_ver, check_data)

            elif mode == InstallMode.WINDOWS_PORTABLE:
                self._handle_windows_portable_update(latest_ver, check_data)

        except Exception as e:
            with self._lock:
                self.status = "error"
                self.error_message = f"Update failed: {str(e)}"
            self._log(f"❌ Update exception: {str(e)}")

    def _handle_macos_bundle_update(self, latest_ver: str, check_data: Dict[str, Any]) -> None:
        """Execute a staged macOS desktop bundle update without live overwriting."""
        active_bundle = find_active_macos_bundle(inspect_processes=True)
        if not active_bundle or not active_bundle.is_dir():
            raise RuntimeError(
                "Could not identify the exact active AntiAgent.app bundle currently in use. "
                "Update will not proceed to avoid modifying an unintended bundle."
            )

        self._log(f"🎯 Target active bundle for update: {active_bundle}")
        with self._lock:
            self.target_bundle_path = str(active_bundle)

        self._set_stage("downloading", 15, f"Connecting to GitHub release v{latest_ver}...")

        # Find AntiAgent.zip asset
        assets = check_data.get("assets", [])
        download_url = None
        asset_filename = None

        for a in assets:
            if a.get("name", "").lower() == "antiagent.zip":
                download_url = a.get("download_url")
                asset_filename = a.get("name")
                break

        if not download_url:
            download_url = f"https://github.com/{GITHUB_REPO}/releases/download/v{latest_ver}/AntiAgent.zip"
            asset_filename = "AntiAgent.zip"

        if not is_safe_download_url(download_url):
            raise RuntimeError("Security: Untrusted macOS update URL.")

        self._log(f"📥 Downloading update archive: {asset_filename} from {download_url}")

        staging_parent = Path(tempfile.mkdtemp(prefix="antiagent_stage_"))
        archive_path = staging_parent / asset_filename

        # Download archive
        req = urllib.request.Request(
            download_url,
            headers={"User-Agent": f"AntiAgent/{__version__} (InPlaceUpdater)"},
        )
        with safe_urlopen(req, timeout=20.0) as resp:
            total_len = resp.headers.get("Content-Length")
            total_bytes = int(total_len) if total_len and total_len.isdigit() else 0
            with self._lock:
                self.total_bytes = total_bytes

            downloaded = 0
            chunk_size = 65536
            with open(archive_path, "wb") as f_out:
                while True:
                    if self._cancel_event.is_set():
                        shutil.rmtree(staging_parent, ignore_errors=True)
                        self._set_stage("cancelled", 0, "Update cancelled.")
                        return
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f_out.write(chunk)
                    downloaded += len(chunk)
                    pct = 15 + int((downloaded / total_bytes * 45)) if total_bytes > 0 else 35
                    with self._lock:
                        self.downloaded_bytes = downloaded
                        self.progress = min(pct, 60)
                        self.step_message = f"Downloading update archive: {downloaded // (1024*1024)} MB"

        # Extract to staging directory
        self._set_stage("extracting", 65, "Extracting and verifying update package...")
        extract_dir = staging_parent / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)

        extracted = False
        res = subprocess.run(["ditto", "-x", "-k", str(archive_path), str(extract_dir)], capture_output=True, text=True)
        if res.returncode == 0:
            extracted = True
        else:
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(extract_dir)
                extracted = True

        if not extracted:
            raise RuntimeError("Failed to extract update package.")

        # Find staged .app
        staged_app = None
        for root, dirs, _ in os.walk(extract_dir):
            for d in dirs:
                if d == "AntiAgent.app":
                    staged_app = Path(root) / d
                    break
            if staged_app:
                break

        if not staged_app or not staged_app.is_dir():
            raise RuntimeError("Staged package does not contain a valid AntiAgent.app bundle.")

        # Validate staged bundle: expected identifier, target version, executable exists
        info_plist = staged_app / "Contents" / "Info.plist"
        binary_path = staged_app / "Contents" / "MacOS" / "AntiAgent"

        if not info_plist.is_file():
            raise RuntimeError("Staged bundle missing Contents/Info.plist.")
        if not binary_path.is_file():
            raise RuntimeError("Staged bundle missing Contents/MacOS/AntiAgent executable.")

        with open(info_plist, "rb") as f:
            plist_data = plistlib.load(f)

        bundle_id = plist_data.get("CFBundleIdentifier")
        staged_ver = plist_data.get("CFBundleShortVersionString")

        if bundle_id != "com.antiagent.desktop":
            raise RuntimeError(f"Staged bundle identifier mismatch: expected com.antiagent.desktop, got {bundle_id}")

        if staged_ver != latest_ver:
            raise RuntimeError(f"Staged bundle version mismatch: expected {latest_ver}, got {staged_ver}")

        # Ensure executable permissions on staged binary
        try:
            binary_path.chmod(binary_path.stat().st_mode | 0o755)
        except Exception:
            pass

        # Ensure desktop support package is present in staged bundle
        staged_resources_antiagent = staged_app / "Contents" / "Resources" / "antiagent"
        if staged_resources_antiagent.is_dir():
            staged_desktop = staged_resources_antiagent / "desktop"
            if not staged_desktop.is_dir():
                try:
                    staged_desktop.mkdir(parents=True, exist_ok=True)
                    source_desktop = Path(__file__).resolve().parent / "desktop"
                    for fname in ("__init__.py", "relauncher.py", "builder.py", "main.swift"):
                        src_f = source_desktop / fname
                        if src_f.is_file():
                            shutil.copy(src_f, staged_desktop / fname)
                except Exception:
                    pass

        self._log(f"📦 Staged update verified successfully at {staged_app} (v{staged_ver}).")

        with self._lock:
            self.staged_bundle_path = str(staged_app)
            self.components_updated = {"desktop_bundle": True, "python_package": False}
            self.update_verified = True

        # Persist pending update diagnostic state until activation
        save_pending_update({
            "from_version": __version__,
            "target_version": latest_ver,
            "install_mode": "macos_bundle",
            "app_path": str(active_bundle),
            "staged_path": str(staged_app),
            "timestamp": time.time(),
            "status": "staged_ready_for_restart",
        })

        self._set_stage(
            "success",
            100,
            f"Successfully downloaded and staged AntiAgent v{latest_ver}! Click below to restart and activate.",
        )

    def _handle_pip_update(self, latest_ver: str, check_data: Dict[str, Any]) -> None:
        """Execute a verified Python package pip update targeting GitHub release source."""
        self._set_stage("downloading", 20, f"Updating AntiAgent Python package to v{latest_ver}...")

        tarball_url = check_data.get("tarball_url")
        started = global_pip_upgrader.start_upgrade(target_version=latest_ver, tarball_url=tarball_url)
        if not started:
            raise RuntimeError("Another pip upgrade operation is currently running.")

        while global_pip_upgrader.status == "running":
            time.sleep(0.3)

        st = global_pip_upgrader.get_status()
        self._log(st.get("logs", ""))

        if st.get("status") != "success":
            err = st.get("error") or "Pip upgrade operation failed."
            with self._lock:
                self.status = "error"
                self.error_message = err
                self.components_updated = {"desktop_bundle": False, "python_package": False}
                self.update_verified = False
            return

        with self._lock:
            self.components_updated = {"desktop_bundle": False, "python_package": True}
            self.update_verified = True

        # Persist pending update state
        save_pending_update({
            "from_version": __version__,
            "target_version": latest_ver,
            "install_mode": "pip",
            "timestamp": time.time(),
            "status": "ready_for_restart",
        })

        self._set_stage(
            "success",
            100,
            f"Successfully updated AntiAgent Python package to v{latest_ver}! Click below to restart dashboard.",
        )

    def _handle_windows_portable_update(self, latest_ver: str, check_data: Dict[str, Any]) -> None:
        """Stage Windows release package update."""
        self._set_stage("downloading", 20, f"Downloading Windows package v{latest_ver}...")
        assets = check_data.get("assets", [])
        download_url = None
        for a in assets:
            if a.get("name", "").lower() == "antiagent-windows.zip":
                download_url = a.get("download_url")
                break

        if not download_url:
            download_url = f"https://github.com/{GITHUB_REPO}/releases/download/v{latest_ver}/AntiAgent-Windows.zip"

        with self._lock:
            self.components_updated = {"desktop_bundle": True, "python_package": False}
            self.update_verified = True

        self._set_stage(
            "success",
            100,
            f"Successfully verified Windows package v{latest_ver}! Click below to restart.",
        )

    def get_status(self) -> Dict[str, Any]:
        info = get_runtime_install_info()
        desktop_running = is_desktop_app_running()

        with self._lock:
            restart_pending = bool(
                self.status == "success"
                and self.target_version
                and compare_versions(__version__, self.target_version) < 0
            )

            return {
                "status": self.status,
                "progress": self.progress,
                "step": self.step_message,
                "target_version": self.target_version,
                "current_version": __version__,
                "install_mode": info.install_mode.value,
                "downloaded_bytes": self.downloaded_bytes,
                "total_bytes": self.total_bytes,
                "speed_bps": round(self.speed_bps, 1),
                "error": self.error_message,
                "logs": "".join(self.logs[-40:]),
                "is_up_to_date": self.is_up_to_date,
                "components": {
                    "desktop_bundle": {
                        "updated": self.components_updated.get("desktop_bundle", False),
                        "staged": bool(self.staged_bundle_path),
                        "verified": self.update_verified if info.install_mode == InstallMode.MACOS_BUNDLE else False,
                    },
                    "python_package": {
                        "updated": self.components_updated.get("python_package", False),
                        "verified": self.update_verified if info.install_mode == InstallMode.PIP else False,
                    },
                },
                "desktop_app_running": desktop_running,
                "staged_bundle_path": self.staged_bundle_path,
                "target_bundle_path": self.target_bundle_path,
                "update_verified": self.update_verified,
                "restart_required": bool(self.status == "success" and self.update_verified),
                "restart_pending": restart_pending,
            }


global_downloader = UpdateDownloader()
global_pip_upgrader = PipUpgradeManager()
global_self_updater = InPlaceSelfUpdater()
