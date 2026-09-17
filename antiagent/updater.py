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


# Singleton instances for server handling
global_downloader = UpdateDownloader()
global_pip_upgrader = PipUpgradeManager()
