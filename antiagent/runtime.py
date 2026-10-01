"""Runtime, installation mode, and environment detection layer for AntiAgent."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from antiagent import __version__


class InstallMode(str, Enum):
    """Runtime and installation mode under which AntiAgent is executing."""
    MACOS_BUNDLE = "macos_bundle"
    PIP = "pip"
    EDITABLE_SOURCE = "editable_source"
    WINDOWS_PORTABLE = "windows_portable"


@dataclass
class DashboardLaunchContext:
    """Normalized launch context for dashboard server processes."""
    host: str = "127.0.0.1"
    port: int = 4242
    workspace_path: str = "."
    open_browser: bool = False
    install_mode: str = "pip"
    python_executable: str = field(default_factory=lambda: sys.executable or "python3")
    macos_bundle_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_argv(self) -> List[str]:
        """Convert normalized launch context into safe command line arguments."""
        cmd = [self.python_executable, "-m", "antiagent", "dashboard"]
        if self.host:
            cmd.extend(["--host", self.host])
        if self.port:
            cmd.extend(["--port", str(self.port)])
        if self.workspace_path:
            cmd.extend(["--workspace", self.workspace_path])
        if not self.open_browser:
            cmd.append("--no-open")
        return cmd


_current_launch_context: Optional[DashboardLaunchContext] = None


def set_current_launch_context(ctx: DashboardLaunchContext) -> None:
    """Store the authoritative launch context for the running dashboard."""
    global _current_launch_context
    _current_launch_context = ctx


def get_current_launch_context() -> DashboardLaunchContext:
    """Retrieve the current launch context or a sensible default."""
    global _current_launch_context
    if _current_launch_context is None:
        _current_launch_context = DashboardLaunchContext(
            host="127.0.0.1",
            port=4242,
            workspace_path=os.getcwd(),
            open_browser=False,
            install_mode=detect_install_mode().value,
            python_executable=sys.executable or "python3",
            macos_bundle_path=str(find_active_macos_bundle()) if find_active_macos_bundle() else None,
        )
    return _current_launch_context


def find_enclosing_macos_bundle(path: Optional[Path] = None) -> Optional[Path]:
    """Derive enclosing macOS .app bundle from a file or package path."""
    if path is None:
        path = Path(__file__).resolve()
    else:
        path = Path(path).resolve()

    for parent in [path] + list(path.parents):
        if parent.name.endswith(".app"):
            contents_dir = parent / "Contents"
            if contents_dir.is_dir() and (contents_dir / "Info.plist").is_file():
                return parent
    return None


def find_active_macos_bundle(inspect_processes: bool = True) -> Optional[Path]:
    """Identify the exact running macOS AntiAgent.app bundle.

    Prefers deterministic process/bundle context over scanning hardcoded folders.
    Never assumes /Applications/AntiAgent.app merely because it exists.
    """
    # 1. Authoritative check: Is the running python code inside an .app bundle?
    pkg_dir = Path(__file__).resolve().parent
    enclosing = find_enclosing_macos_bundle(pkg_dir)
    if enclosing and enclosing.is_dir():
        return enclosing

    if sys.platform != "darwin" or not inspect_processes:
        return None

    # 2. Check parent process (PPID) if the backend was spawned by the Swift app
    try:
        ppid = os.getppid()
        pname = subprocess.check_output(["ps", "-p", str(ppid), "-o", "comm="], text=True).strip()
        if "AntiAgent.app" in pname:
            parts = pname.split(".app")
            candidate = Path(parts[0] + ".app")
            if candidate.is_dir() and (candidate / "Contents" / "Info.plist").is_file():
                return candidate
    except Exception:
        pass

    # 3. Check process table for a running AntiAgent binary and map to its .app
    try:
        out = subprocess.check_output(["ps", "-A", "-o", "comm"], text=True)
        for line in out.strip().splitlines():
            line_s = line.strip()
            if "AntiAgent.app/Contents/MacOS/AntiAgent" in line_s:
                parts = line_s.split(".app")
                candidate = Path(parts[0] + ".app")
                if candidate.is_dir() and (candidate / "Contents" / "Info.plist").is_file():
                    return candidate
    except Exception:
        pass

    try:
        out = subprocess.check_output(["pgrep", "-fl", "AntiAgent.app"], text=True)
        for line in out.strip().splitlines():
            if "Contents/MacOS/AntiAgent" in line:
                for token in line.split():
                    if ".app" in token:
                        candidate = Path(token.split(".app")[0] + ".app")
                        if candidate.is_dir() and (candidate / "Contents" / "Info.plist").is_file():
                            return candidate
    except Exception:
        pass

    return None


def find_installed_macos_bundles() -> List[Path]:
    """Find installed macOS AntiAgent.app bundles in standard directories."""
    candidates = [
        Path("/Applications/AntiAgent.app"),
        Path.home() / "Applications" / "AntiAgent.app",
    ]
    return [p for p in candidates if p.is_dir() and (p / "Contents" / "Info.plist").is_file()]


def is_desktop_app_running() -> bool:
    """Check if the native macOS desktop application is currently running."""
    pkg_dir = Path(__file__).resolve().parent
    if find_enclosing_macos_bundle(pkg_dir) is not None:
        return True

    if sys.platform != "darwin":
        return False

    if find_active_macos_bundle(inspect_processes=True) is not None:
        return True

    try:
        res = subprocess.run(["pgrep", "-x", "AntiAgent"], capture_output=True, text=True)
        return res.returncode == 0
    except Exception:
        return False


def detect_install_mode(
    pkg_path: Optional[Path] = None,
    executable: Optional[str] = None,
) -> InstallMode:
    """Reliably determine the installation/runtime mode of the current AntiAgent instance."""
    if pkg_path is None:
        pkg_path = Path(__file__).resolve().parent
    else:
        pkg_path = Path(pkg_path).resolve()

    # 1. macOS .app bundle
    if find_enclosing_macos_bundle(pkg_path) is not None:
        return InstallMode.MACOS_BUNDLE

    # 2. Explicit Windows Portable override
    if sys.platform == "win32" and os.environ.get("ANTIAGENT_PORTABLE") == "1":
        return InstallMode.WINDOWS_PORTABLE

    # 3. Editable / Source checkout
    repo_root = pkg_path.parent
    has_git = (repo_root / ".git").is_dir() or (repo_root / ".git").is_file()
    has_pyproject = (repo_root / "pyproject.toml").is_file()
    if has_git and has_pyproject:
        return InstallMode.EDITABLE_SOURCE

    try:
        for item in pkg_path.parent.glob("antiagent*.dist-info"):
            direct_url_file = item / "direct_url.json"
            if direct_url_file.is_file():
                data = json.loads(direct_url_file.read_text(encoding="utf-8"))
                if data.get("dir_info", {}).get("editable", False):
                    return InstallMode.EDITABLE_SOURCE
    except Exception:
        pass

    # 3. Windows Portable
    if sys.platform == "win32":
        is_in_site_packages = any(p in pkg_path.parts for p in ("site-packages", "dist-packages"))
        has_bat = (repo_root / "AntiAgent.bat").is_file() or (repo_root / "Install-Hook.bat").is_file()
        if not is_in_site_packages and has_bat:
            return InstallMode.WINDOWS_PORTABLE

    # 4. Standard pip / site-packages install
    return InstallMode.PIP


@dataclass
class RuntimeInstallInfo:
    """Comprehensive snapshot of the AntiAgent runtime and install state."""
    install_mode: InstallMode
    current_executable: str
    package_path: str
    macos_bundle_path: Optional[str]
    is_site_packages: bool
    current_version: str
    launch_context: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "install_mode": self.install_mode.value,
            "current_executable": self.current_executable,
            "package_path": self.package_path,
            "macos_bundle_path": self.macos_bundle_path,
            "is_site_packages": self.is_site_packages,
            "current_version": self.current_version,
            "launch_context": self.launch_context,
        }


def get_runtime_install_info() -> RuntimeInstallInfo:
    """Generate a complete runtime and installation diagnostics report."""
    pkg_path = Path(__file__).resolve().parent
    mode = detect_install_mode(pkg_path)
    bundle_path = find_active_macos_bundle()
    is_site_pkgs = any(p in pkg_path.parts for p in ("site-packages", "dist-packages"))
    ctx = get_current_launch_context()

    return RuntimeInstallInfo(
        install_mode=mode,
        current_executable=sys.executable or "python3",
        package_path=str(pkg_path),
        macos_bundle_path=str(bundle_path) if bundle_path else None,
        is_site_packages=is_site_pkgs,
        current_version=__version__,
        launch_context=ctx.to_dict(),
    )
