"""In-app update checker, downloader, and manager for AntiAgent."""

import json
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from antiagent import __version__

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

        # If on github.com or raw.githubusercontent.com, ensure it belongs to the official repo
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
    # Strip any directory traversal tokens or control chars
    base = re.sub(r"[^\w\.\-\+]", "_", base)
    if not any(base.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
        # Default safe extension
        base += ".dmg" if sys.platform == "darwin" else ".zip"
    return base


def get_ssl_context() -> ssl.SSLContext:
    """Create SSL context with fallback to avoid cert verification errors on fresh Python installs."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()


def safe_urlopen(req: Any, timeout: float = 10.0):
    """Execute urlopen with verified context or fallback to unverified if cert verification fails."""
    ctx = get_ssl_context()
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            fallback_ctx = ssl._create_unverified_context()
            return urllib.request.urlopen(req, timeout=timeout, context=fallback_ctx)
        raise


def parse_version(ver_str: str) -> Tuple[int, ...]:
    """Parse version string like '0.1.3', 'v0.2.0', '1.0.0-beta' into integer tuple."""
    clean = ver_str.strip().lstrip("vV")
    # Take only numeric segments before any hyphen/suffix
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
        # Prefer .dmg on macOS
        for a in assets:
            if a.get("name", "").lower().endswith(".dmg"):
                return a
        # Next prefer .pkg
        for a in assets:
            if a.get("name", "").lower().endswith(".pkg"):
                return a
        # Next prefer macOS .zip (not Windows)
        for a in assets:
            n = a.get("name", "").lower()
            if n.endswith(".zip") and "windows" not in n and "win" not in n:
                return a

    elif sys.platform == "win32":
        # Prefer Windows zip
        for a in assets:
            n = a.get("name", "").lower()
            if "win" in n and n.endswith(".zip"):
                return a
        for a in assets:
            if a.get("name", "").lower().endswith(".zip"):
                return a

    # Linux or fallback: prefer tar.gz or zip
    for a in assets:
        n = a.get("name", "").lower()
        if n.endswith(".tar.gz") or n.endswith(".zip"):
            return a

    return assets[0]


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

        return {
            "ok": True,
            "update_available": update_available,
            "current_version": current_version,
            "latest_version": latest_version,
            "release_name": data.get("name") or raw_tag,
            "release_notes": data.get("body") or "",
            "published_at": data.get("published_at") or "",
            "html_url": data.get("html_url") or f"https://github.com/{repo}/releases/latest",
            "assets": assets_list,
            "recommended_asset": recommended,
        }
    except urllib.error.HTTPError as e:
        # 404 when no releases have been published yet or 403 on rate limit
        msg = f"GitHub API returned HTTP {e.code}: {e.reason}"
        if e.code == 404:
            msg = "No public releases found on GitHub repository yet."
        elif e.code == 403:
            msg = "GitHub API rate limit exceeded. Please try again later or visit GitHub directly."
        return {
            "ok": False,
            "update_available": False,
            "current_version": current_version,
            "latest_version": None,
            "error": msg,
            "html_url": f"https://github.com/{repo}/releases/latest",
            "assets": [],
            "recommended_asset": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "update_available": False,
            "current_version": current_version,
            "latest_version": None,
            "error": f"Failed to check for updates: {str(e)}",
            "html_url": f"https://github.com/{repo}/releases/latest",
            "assets": [],
            "recommended_asset": None,
        }


def get_default_download_dir() -> Path:
    """Resolve standard download directory for the user."""
    downloads = Path.home() / "Downloads"
    if downloads.is_dir() and os.access(str(downloads), os.W_OK):
        return downloads
    # Fallback to ~/.antiagent/downloads
    fallback = Path.home() / ".antiagent" / "downloads"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


class UpdateDownloader:
    """Manages downloading update packages in a background thread with real-time progress."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.status = "idle"  # idle | downloading | completed | error | cancelled
        self.progress = 0      # 0 to 100 percent
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
        """Start downloading the file in a background thread."""
        with self._lock:
            if self.status == "downloading" and self._thread and self._thread.is_alive():
                return False  # Already downloading

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
        """Cancel an ongoing download."""
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
                chunk_size = 65536  # 64 KB

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

            # Finalize file
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
        """Return snapshot of current download state."""
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

    # Security: Only allowed installer file extensions can be opened
    if not any(p.name.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
        return False

    # Security: File must reside inside safe downloads folder or match active downloader destination
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
    """Executes 'pip install --upgrade antiagent' in background with captured logs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.status = "idle"  # idle | running | success | error
        self.logs: List[str] = []
        self.returncode: Optional[int] = None
        self._thread: Optional[threading.Thread] = None

    def start_upgrade(self) -> bool:
        with self._lock:
            if self.status == "running":
                return False
            self.status = "running"
            self.logs = [f"🚀 Starting upgrade: {sys.executable} -m pip install --upgrade antiagent\n"]
            self.returncode = None
            self._thread = threading.Thread(target=self._upgrade_worker, daemon=True)
            self._thread.start()
            return True

    def _upgrade_worker(self) -> None:
        try:
            cmd = [sys.executable or "python3", "-m", "pip", "install", "--upgrade", "antiagent"]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            if proc.stdout:
                for line in iter(proc.stdout.readline, ""):
                    with self._lock:
                        self.logs.append(line)
                proc.stdout.close()
            proc.wait()
            with self._lock:
                self.returncode = proc.returncode
                if proc.returncode == 0:
                    self.status = "success"
                    self.logs.append("✅ AntiAgent successfully upgraded via pip!\n")
                else:
                    self.status = "error"
                    self.logs.append(f"❌ Pip upgrade failed with exit code {proc.returncode}\n")
        except Exception as e:
            with self._lock:
                self.status = "error"
                self.logs.append(f"❌ Error running pip upgrade: {str(e)}\n")

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "status": self.status,
                "logs": "".join(self.logs),
                "returncode": self.returncode,
            }


class InPlaceSelfUpdater:
    """Manages 1-click in-place background update of AntiAgent without requiring manual reinstall."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.status = "idle"  # idle | checking | downloading | extracting | applying | success | error | cancelled
        self.progress = 0      # 0 to 100 percent
        self.step_message = ""
        self.error_message = ""
        self.target_version = ""
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.speed_bps = 0.0
        self.logs: List[str] = []
        self.is_up_to_date = False
        self.components_updated: Dict[str, bool] = {"desktop_app": False, "python_package": False}

    def reset(self) -> None:
        """Reset updater status and clear state back to idle."""
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
            self.components_updated = {"desktop_app": False, "python_package": False}

    def start_update(self, force: bool = False, version: Optional[str] = None) -> bool:
        with self._lock:
            if self.status in ("checking", "downloading", "extracting", "applying") and self._thread and self._thread.is_alive():
                return False  # Already in progress

            self.status = "checking"
            self.progress = 5
            self.step_message = "Checking latest release on GitHub..."
            self.error_message = ""
            self.target_version = version or ""
            self.downloaded_bytes = 0
            self.total_bytes = 0
            self.speed_bps = 0.0
            self.logs = [f"🚀 Initializing 1-click update for AntiAgent...\n"]
            self.is_up_to_date = False
            self.components_updated = {"desktop_app": False, "python_package": False}
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
        import shutil
        import tempfile
        import zipfile

        try:
            # 1. Fetch release details
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
                    self.error_message = "No version found to update to."
                return

            with self._lock:
                self.target_version = latest_ver

            # Check if update is needed
            if not force and compare_versions(latest_ver, __version__) <= 0:
                with self._lock:
                    self.is_up_to_date = True
                self._set_stage("success", 100, f"AntiAgent is already up to date (v{__version__}).")
                return

            self._set_stage("downloading", 15, f"Connecting to GitHub release v{latest_ver}...")

            # 2. Select update asset
            assets = check_data.get("assets", [])
            download_url = None
            asset_filename = None

            if sys.platform == "darwin":
                for a in assets:
                    if a.get("name", "").lower() == "antiagent.zip":
                        download_url = a.get("download_url")
                        asset_filename = a.get("name")
                        break
            elif sys.platform == "win32":
                for a in assets:
                    if a.get("name", "").lower() == "antiagent-windows.zip":
                        download_url = a.get("download_url")
                        asset_filename = a.get("name")
                        break

            # Fallback to GitHub release source zip
            if not download_url:
                download_url = f"https://github.com/{GITHUB_REPO}/archive/refs/tags/v{latest_ver}.zip"
                asset_filename = f"AntiAgent-v{latest_ver}.zip"

            if not is_safe_download_url(download_url):
                with self._lock:
                    self.status = "error"
                    self.error_message = "Security: Untrusted update URL."
                return

            self._log(f"📥 Downloading update archive: {asset_filename} from {download_url}")

            # 3. Stream download to temp directory
            temp_dir = Path(tempfile.mkdtemp(prefix="antiagent_update_"))
            archive_path = temp_dir / asset_filename

            req = urllib.request.Request(
                download_url,
                headers={"User-Agent": f"AntiAgent/{__version__} (InPlaceUpdater)"},
            )
            start_time = time.time()
            last_calc_time = start_time
            last_calc_bytes = 0

            with safe_urlopen(req, timeout=15.0) as resp:
                total_len = resp.headers.get("Content-Length")
                total_bytes = int(total_len) if total_len and total_len.isdigit() else 0
                with self._lock:
                    self.total_bytes = total_bytes

                downloaded = 0
                chunk_size = 65536
                with open(archive_path, "wb") as f:
                    while True:
                        if self._cancel_event.is_set():
                            shutil.rmtree(temp_dir, ignore_errors=True)
                            self._set_stage("cancelled", 0, "Update cancelled.")
                            return
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

                        pct = 15 + int((downloaded / total_bytes * 45)) if total_bytes > 0 else 35
                        with self._lock:
                            self.downloaded_bytes = downloaded
                            self.progress = min(pct, 60)
                            self.speed_bps = bps
                            mb_cur = f"{downloaded / (1024*1024):.1f}"
                            mb_tot = f"{total_bytes / (1024*1024):.1f}" if total_bytes > 0 else "..."
                            self.step_message = f"Downloading update: {mb_cur} MB / {mb_tot} MB"

            # 4. Extract archive
            self._set_stage("extracting", 65, "Extracting update package...")
            extract_dir = temp_dir / "extracted"
            extract_dir.mkdir(parents=True, exist_ok=True)

            try:
                extracted = False
                if sys.platform == "darwin" and str(archive_path).lower().endswith(".zip"):
                    res = subprocess.run(
                        ["ditto", "-x", "-k", str(archive_path), str(extract_dir)],
                        capture_output=True,
                        text=True,
                    )
                    if res.returncode == 0:
                        extracted = True

                if not extracted:
                    with zipfile.ZipFile(archive_path, "r") as zf:
                        for member in zf.infolist():
                            extracted_file = zf.extract(member, extract_dir)
                            mode = (member.external_attr >> 16) & 0o777
                            if mode:
                                try:
                                    os.chmod(extracted_file, mode)
                                except Exception:
                                    pass
            except Exception as e:
                shutil.rmtree(temp_dir, ignore_errors=True)
                with self._lock:
                    self.status = "error"
                    self.error_message = f"Failed to extract update archive: {str(e)}"
                return

            # 5. Apply update in-place
            self._set_stage("applying", 80, "Applying update in-place...")
            app_updated = False
            pip_updated = False

            # A. Update macOS .app bundle if installed in /Applications or ~/Applications
            if sys.platform == "darwin":
                extracted_app = None
                for root, dirs, _ in os.walk(extract_dir):
                    for d in dirs:
                        if d == "AntiAgent.app":
                            extracted_app = Path(root) / d
                            break
                    if extracted_app:
                        break

                target_apps = [
                    Path("/Applications/AntiAgent.app"),
                    Path.home() / "Applications" / "AntiAgent.app",
                ]
                for target_app in target_apps:
                    if target_app.exists() and os.access(str(target_app), os.W_OK):
                        if extracted_app and extracted_app.is_dir():
                            self._log(f"📦 Updating native desktop app in-place at {target_app}...")
                            # Ensure executable permission on binary before copy
                            ext_bin = extracted_app / "Contents" / "MacOS" / "AntiAgent"
                            if ext_bin.is_file():
                                try:
                                    ext_bin.chmod(0o755)
                                except Exception:
                                    pass

                            ditto_res = subprocess.run(
                                ["ditto", str(extracted_app), str(target_app)],
                                capture_output=True,
                                text=True,
                            )
                            if ditto_res.returncode == 0:
                                app_updated = True
                                self._log(f"✅ Successfully updated {target_app} in-place.")
                            else:
                                self._log(f"⚠️ ditto copy warning: {ditto_res.stderr}")

                            # Guarantee executable permissions on native binary
                            target_bin_dir = target_app / "Contents" / "MacOS"
                            if target_bin_dir.is_dir():
                                for f in target_bin_dir.iterdir():
                                    if f.is_file():
                                        try:
                                            f.chmod(f.stat().st_mode | 0o755)
                                        except Exception:
                                            pass

                            # Clean any leftover pycache inside bundle to avoid sealing errors
                            for pycache in target_app.rglob("__pycache__"):
                                shutil.rmtree(pycache, ignore_errors=True)

                            # Clear quarantine xattr in case Gatekeeper flagged it
                            try:
                                subprocess.run(["xattr", "-cr", str(target_app)], capture_output=True)
                            except Exception:
                                pass

                            # Ad-hoc codesign to validate bundle resources
                            try:
                                subprocess.run(
                                    ["codesign", "--force", "--deep", "--sign", "-", str(target_app)],
                                    capture_output=True,
                                )
                            except Exception:
                                pass

                            # Refresh macOS LaunchServices bundle registration
                            lsregister = Path("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister")
                            if lsregister.is_file():
                                try:
                                    subprocess.run([str(lsregister), "-f", str(target_app)], capture_output=True)
                                except Exception:
                                    pass

            # B. Update Python package via pip in-place
            self._set_stage("applying", 90, "Updating Python package and rules...")
            tarball_url = f"https://github.com/{GITHUB_REPO}/archive/refs/tags/v{latest_ver}.tar.gz"
            tarball_path = temp_dir / f"antiagent-{latest_ver}.tar.gz"
            self._log(f"📦 Downloading release source tarball for Python package upgrade: {tarball_url}")

            try:
                tar_req = urllib.request.Request(
                    tarball_url,
                    headers={"User-Agent": f"AntiAgent/{__version__} (InPlaceUpdater)"},
                )
                with safe_urlopen(tar_req, timeout=20.0) as tar_resp:
                    with open(tarball_path, "wb") as f_tar:
                        shutil.copyfileobj(tar_resp, f_tar)

                self._log(f"📦 Upgrading Python package using pip: {tarball_path.name}")
                pip_cmd = [
                    sys.executable or "python3",
                    "-m", "pip", "install",
                    "--upgrade",
                    "--no-deps",
                    str(tarball_path),
                ]
                pip_res = subprocess.run(pip_cmd, capture_output=True, text=True)
                if pip_res.returncode == 0:
                    pip_updated = True
                    self._log("✅ Python package successfully upgraded.")
                else:
                    # Retry with --break-system-packages (for PEP 668 environments)
                    pip_cmd_bsp = pip_cmd + ["--break-system-packages"]
                    pip_res_bsp = subprocess.run(pip_cmd_bsp, capture_output=True, text=True)
                    if pip_res_bsp.returncode == 0:
                        pip_updated = True
                        self._log("✅ Python package successfully upgraded (with --break-system-packages).")
                    else:
                        # Retry with --user
                        pip_cmd_user = pip_cmd + ["--user"]
                        pip_res_user = subprocess.run(pip_cmd_user, capture_output=True, text=True)
                        if pip_res_user.returncode == 0:
                            pip_updated = True
                            self._log("✅ Python package successfully upgraded (with --user).")
                        else:
                            self._log(f"ℹ️ Pip update output: {pip_res_bsp.stderr.strip() or pip_res.stderr.strip()}")
            except Exception as pe:
                self._log(f"⚠️ Pip download/install exception: {str(pe)}")

            with self._lock:
                self.components_updated = {
                    "desktop_app": app_updated,
                    "python_package": pip_updated,
                }

            # If on Windows and package extracted, consider app updated
            if sys.platform == "win32" and (extract_dir / "AntiAgent").exists():
                app_updated = True

            # Clean up temp
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

            if not app_updated and not pip_updated:
                with self._lock:
                    self.status = "error"
                    self.error_message = "Failed to update desktop application or Python package."
                self._log("❌ Update failed: neither desktop app nor python package could be updated.")
                return

            # Update finished successfully!
            summary_items = []
            if app_updated:
                summary_items.append("macOS Desktop App" if sys.platform == "darwin" else "Windows Desktop Package")
            if pip_updated:
                summary_items.append("Python package")
            summary_str = " and ".join(summary_items) if summary_items else "AntiAgent"

            self._set_stage(
                "success",
                100,
                f"Successfully updated {summary_str} to v{latest_ver}! Click below to reload.",
            )

        except Exception as e:
            with self._lock:
                self.status = "error"
                self.error_message = f"Update failed: {str(e)}"
            self._log(f"❌ Update exception: {str(e)}")

    def get_status(self) -> Dict[str, Any]:
        desktop_installed = False
        desktop_running = False
        if sys.platform == "darwin":
            desktop_installed = Path("/Applications/AntiAgent.app").exists() or (Path.home() / "Applications" / "AntiAgent.app").exists()
            try:
                res = subprocess.run(["pgrep", "-x", "AntiAgent"], capture_output=True, text=True)
                desktop_running = res.returncode == 0
            except Exception:
                pass

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
                "downloaded_bytes": self.downloaded_bytes,
                "total_bytes": self.total_bytes,
                "speed_bps": round(self.speed_bps, 1),
                "error": self.error_message,
                "logs": "".join(self.logs[-30:]),
                "is_up_to_date": self.is_up_to_date,
                "components_updated": dict(self.components_updated),
                "desktop_app_installed": desktop_installed,
                "desktop_app_running": desktop_running,
                "restart_pending": restart_pending,
            }


# Singleton instances for server handling
global_downloader = UpdateDownloader()
global_pip_upgrader = PipUpgradeManager()
global_self_updater = InPlaceSelfUpdater()
