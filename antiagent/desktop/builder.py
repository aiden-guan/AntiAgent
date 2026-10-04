"""Builder and installer for native macOS AntiAgent desktop application."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from antiagent import __version__

# Ensure UTF-8 output streams on Windows to prevent charmap/CP1252 emoji encoding errors
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

INFO_PLIST_TEMPLATE = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>AntiAgent</string>
    <key>CFBundleIdentifier</key>
    <string>com.antiagent.desktop</string>
    <key>CFBundleName</key>
    <string>AntiAgent</string>
    <key>CFBundleDisplayName</key>
    <string>AntiAgent Guard</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>{__version__}</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsArbitraryLoads</key>
        <true/>
        <key>NSAllowsLocalNetworking</key>
        <true/>
    </dict>
</dict>
</plist>
"""


def build_macos_app(output_dir: Path = None) -> Path:
    """Compile and package AntiAgent.app bundle."""
    desktop_dir = Path(__file__).parent.resolve()
    swift_source = desktop_dir / "main.swift"

    if not swift_source.is_file():
        raise FileNotFoundError(f"Swift source not found at {swift_source}")

    if output_dir is None:
        output_dir = Path(os.getcwd()) / "dist"

    output_dir.mkdir(parents=True, exist_ok=True)
    app_bundle = output_dir / "AntiAgent.app"

    contents_dir = app_bundle / "Contents"
    macos_dir = contents_dir / "MacOS"
    resources_dir = contents_dir / "Resources"

    macos_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)

    # 1. Compile Swift binary with optimizations
    binary_path = macos_dir / "AntiAgent"
    cmd = [
        "swiftc",
        "-O",
        "-framework", "Cocoa",
        "-framework", "WebKit",
        str(swift_source),
        "-o", str(binary_path)
    ]

    print(f"🔨 Compiling native macOS desktop binary for AntiAgent...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Failed to compile Swift app: {res.stderr}")

    binary_path.chmod(0o755)

    # 2. Write Info.plist
    plist_path = contents_dir / "Info.plist"
    plist_path.write_text(INFO_PLIST_TEMPLATE, encoding="utf-8")

    # 3. Copy AppIcon.icns if available
    source_icon = desktop_dir / "AppIcon.icns"
    if source_icon.is_file():
        shutil.copy(source_icon, resources_dir / "AppIcon.icns")

    # 4. Bundle self-contained antiagent python package into Contents/Resources
    repo_antiagent = desktop_dir.parent.resolve()  # Path to antiagent package root
    target_antiagent = resources_dir / "antiagent"
    if target_antiagent.exists():
        shutil.rmtree(target_antiagent)

    shutil.copytree(
        repo_antiagent,
        target_antiagent,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.png", "*.tiff", "*.o", ".DS_Store"),
    )

    # 5. Ad-hoc codesign the completed bundle so macOS recognizes valid resources
    sign_cmd = ["codesign", "--force", "--deep", "--sign", "-", str(app_bundle)]
    sign_res = subprocess.run(sign_cmd, capture_output=True, text=True)
    if sign_res.returncode != 0:
        print(f"⚠️ Codesign warning: {sign_res.stderr}")
    else:
        print(f"🔏 Successfully ad-hoc codesigned {app_bundle.name}")

    print(f"✅ Successfully built standalone native app: {app_bundle}")
    return app_bundle


def setup_dmg_background(mount_point: Path) -> None:
    """Copy pre-rendered Retina minimalist SaaS background assets to DMG .background directory."""
    bg_dir = mount_point / ".background"
    bg_dir.mkdir(parents=True, exist_ok=True)

    desktop_dir = Path(__file__).parent.resolve()
    bg_tiff = desktop_dir / "dmg_background.tiff"
    bg_png = desktop_dir / "dmg_background.png"
    bg_2x = desktop_dir / "dmg_background@2x.png"

    if bg_tiff.is_file():
        shutil.copy(bg_tiff, bg_dir / "background.tiff")
    if bg_png.is_file():
        shutil.copy(bg_png, bg_dir / "background.png")
    elif bg_2x.is_file():
        shutil.copy(bg_2x, bg_dir / "background.png")
    return bg_dir


def build_dmg(output_dir: Path = None) -> Path:
    """Package AntiAgent.app into a custom styled AntiAgent.dmg with custom background and instructions."""
    app_bundle = build_macos_app(output_dir)
    if output_dir is None:
        output_dir = Path(os.getcwd()) / "dist"

    output_dir.mkdir(parents=True, exist_ok=True)
    final_dmg = output_dir / "AntiAgent.dmg"

    if final_dmg.exists():
        final_dmg.unlink()

    desktop_dir = Path(__file__).parent.resolve()
    bg_img = desktop_dir / "dmg_background.png"

    # Prefer dmgbuild for exact, native DS_Store Retina generation without flaky Finder GUI timing
    try:
        import dmgbuild
        import ds_store

        # Ensure Finder's underlying canvas background color is dark obsidian (#08090b) instead of default white
        orig_partial_setitem = ds_store.DSStore.Partial.__setitem__

        def _dark_icvp_setitem(self, key, value):
            if key == "icvp" and isinstance(value, dict):
                value["backgroundColorRed"] = 8.0 / 255.0
                value["backgroundColorGreen"] = 9.0 / 255.0
                value["backgroundColorBlue"] = 11.0 / 255.0
            return orig_partial_setitem(self, key, value)

        ds_store.DSStore.Partial.__setitem__ = _dark_icvp_setitem

        print("📀 Building pixel-perfect Retina DMG with dmgbuild...")
        settings = {
            "files": [str(app_bundle)],
            "symlinks": {"Applications": "/Applications"},
            "background": str(bg_img),
            "icon_size": 80.0,
            "icon_locations": {
                "AntiAgent.app": (160, 160),
                "Applications": (520, 160),
            },
            "window_rect": ((200, 100), (680, 540)),
            "default_view": "icon-view",
            "show_toolbar": False,
            "show_status_bar": False,
            "show_pathbar": False,
            "show_sidebar": False,
            "scroll_position": (0.0, 0.0),
            "format": "UDZO",
        }
        dmgbuild.build_dmg(str(final_dmg), "AntiAgent", settings=settings)
        print(f"🎉 Created styled installer DMG: {final_dmg}")
        return final_dmg
    except ImportError:
        print("⚠️ dmgbuild not installed, falling back to hdiutil and AppleScript...")

    rw_dmg = output_dir / "temp_rw.dmg"
    if rw_dmg.exists():
        rw_dmg.unlink()

    # Detach any existing mount
    subprocess.run(["hdiutil", "detach", "/Volumes/AntiAgent"], capture_output=True)

    # 1. Create writable disk image
    print("📀 Creating writable staging image for DMG layout...")
    cmd = ["hdiutil", "create", "-size", "45m", "-volname", "AntiAgent", "-fs", "HFS+", "-ov", str(rw_dmg)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Failed to create staging DMG: {res.stderr}")

    # 2. Mount it
    subprocess.run(
        ["hdiutil", "attach", "-readwrite", "-noverify", "-noautoopen", str(rw_dmg)],
        capture_output=True, text=True, check=True
    )
    mount_point = Path("/Volumes/AntiAgent")

    # 3. Copy app & create symlink
    shutil.copytree(app_bundle, mount_point / "AntiAgent.app", symlinks=True)
    apps_link = mount_point / "Applications"
    if not apps_link.exists():
        os.symlink("/Applications", apps_link)

    # 4. Copy background assets
    bg_dir = setup_dmg_background(mount_point)

    # 5. Run AppleScript to layout Finder window and icons
    applescript = """
    tell application "Finder"
        tell disk "AntiAgent"
            open
            set current view of container window to icon view
            set toolbar visible of container window to false
            set statusbar visible of container window to false
            set the bounds of container window to {200, 120, 880, 600}
            set viewOptions to the icon view options of container window
            set arrangement of viewOptions to not arranged
            set icon size of viewOptions to 80
            try
                set background picture of viewOptions to file ".background:background.tiff"
            end try
            set position of item "AntiAgent.app" of container window to {160, 161}
            set position of item "Applications" of container window to {520, 161}
            close
            open
            update without registering applications
            delay 1
        end tell
    end tell
    """
    subprocess.run(["osascript", "-e", applescript], capture_output=True, text=True)

    # 6. Hide background directory and sync
    subprocess.run(["SetFile", "-a", "V", str(bg_dir)], capture_output=True)
    subprocess.run(["sync"])
    import time
    time.sleep(0.5)

    # 7. Detach and convert to final UDZO
    subprocess.run(["hdiutil", "detach", str(mount_point)], check=True)
    print("🗜️  Compressing into finalized AntiAgent.dmg...")
    subprocess.run(
        ["hdiutil", "convert", str(rw_dmg), "-format", "UDZO", "-imagekey", "zlib-level=9", "-o", str(final_dmg)],
        check=True
    )
    rw_dmg.unlink(missing_ok=True)

    print(f"🎉 Created styled installer DMG: {final_dmg}")
    return final_dmg


def build_zip(output_dir: Path = None) -> Path:
    """Package AntiAgent.app into a standalone AntiAgent.zip."""
    app_bundle = build_macos_app(output_dir)
    if output_dir is None:
        output_dir = Path(os.getcwd()) / "dist"

    zip_path = output_dir / "AntiAgent.zip"
    if zip_path.exists():
        zip_path.unlink()

    cmd = ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app_bundle), str(zip_path)]
    subprocess.run(cmd, check=True)
    print(f"📦 Created downloadable ZIP archive: {zip_path}")
    return zip_path


def build_pkg(output_dir: Path = None) -> Path:
    """Package AntiAgent.app into an auto-opening macOS installer package (.pkg)."""
    import tempfile
    app_bundle = build_macos_app(output_dir)
    if output_dir is None:
        output_dir = Path(os.getcwd()) / "dist"

    output_dir.mkdir(parents=True, exist_ok=True)
    pkg_path = output_dir / "AntiAgent.pkg"
    if pkg_path.exists():
        pkg_path.unlink()

    scripts_dir = Path(tempfile.mkdtemp())
    postinstall = scripts_dir / "postinstall"
    postinstall.write_text("""#!/bin/bash
# Auto-open AntiAgent as the current desktop console user immediately after install
CONSOLE_USER=$(stat -f "%Su" /dev/console)
if [ -n "$CONSOLE_USER" ] && [ "$CONSOLE_USER" != "root" ]; then
    sudo -u "$CONSOLE_USER" open "/Applications/AntiAgent.app"
else
    open "/Applications/AntiAgent.app"
fi
exit 0
""")
    postinstall.chmod(0o755)

    from antiagent import __version__
    cmd = [
        "pkgbuild",
        "--component", str(app_bundle),
        "--install-location", "/Applications",
        "--scripts", str(scripts_dir),
        "--identifier", "com.antiagent.desktop.pkg",
        "--version", __version__,
        str(pkg_path)
    ]
    subprocess.run(cmd, check=True)
    print(f"📦 Created auto-launching installer package: {pkg_path}")
    return pkg_path


def launch_windows_app() -> None:
    """Launch the AntiAgent desktop application on Windows using Edge/Chrome app mode or default browser."""
    import time
    import urllib.request
    import webbrowser

    # 1. Ensure backend daemon is running
    url = "http://127.0.0.1:4242/"
    is_up = False
    try:
        with urllib.request.urlopen(url, timeout=0.5) as resp:
            if resp.status == 200:
                is_up = True
    except Exception:
        is_up = False

    if not is_up:
        print("🚀 Starting AntiAgent background daemon...")
        py_exec = sys.executable or "python"
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(
            [py_exec, "-m", "antiagent.dashboard", "--no-open"],
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        for _ in range(25):
            time.sleep(0.2)
            try:
                with urllib.request.urlopen(url, timeout=0.3) as resp:
                    if resp.status == 200:
                        is_up = True
                        break
            except Exception:
                continue

    if not is_up:
        print(
            "⚠️  The AntiAgent dashboard did not respond on http://127.0.0.1:4242. "
            "A stale AntiAgent process may be holding the port; close it (or reboot) and retry."
        )

    # 2. Launch standalone window via Edge app mode, Chrome app mode, or default browser
    app_url = "http://127.0.0.1:4242"
    edge_candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        shutil.which("msedge"),
        shutil.which("msedge.exe"),
    ]
    edge_bin = next((p for p in edge_candidates if p and os.path.isfile(p)), None)

    if edge_bin:
        print("🖥️  Launching AntiAgent Guard window via Microsoft Edge App Mode...")
        subprocess.Popen([edge_bin, f"--app={app_url}", "--window-size=1160,800"])
        return

    chrome_candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
    ]
    chrome_bin = next((p for p in chrome_candidates if p and os.path.isfile(p)), None)

    if chrome_bin:
        print("🖥️  Launching AntiAgent Guard window via Chrome App Mode...")
        subprocess.Popen([chrome_bin, f"--app={app_url}", "--window-size=1160,800"])
        return

    print("🌐 Opening AntiAgent in default web browser...")
    webbrowser.open(app_url)


def build_windows_package(output_dir: Path = None) -> Path:
    """Package AntiAgent into a standalone AntiAgent-Windows.zip for Windows 10/11."""
    import zipfile

    if output_dir is None:
        output_dir = Path(os.getcwd()) / "dist"

    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / "AntiAgent-Windows.zip"
    if zip_path.exists():
        zip_path.unlink()

    repo_root = Path(__file__).resolve().parent.parent.parent
    antiagent_src = repo_root / "antiagent"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Add package files
        for root, dirs, files in os.walk(antiagent_src):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".pytest_cache")]
            for file in files:
                if file.endswith((".pyc", ".DS_Store", ".o")):
                    continue
                file_path = Path(root) / file
                rel_path = file_path.relative_to(repo_root)
                zf.write(file_path, f"AntiAgent/{rel_path}")

        # 2. Add AntiAgent.bat launcher
        bat_content = """@echo off
title AntiAgent Guard (Public Beta)
echo ============================================================
echo   Starting AntiAgent Guard for Antigravity (Public Beta)...
echo ============================================================
set PYTHONPATH=%~dp0;%PYTHONPATH%
where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    py -m antiagent app
    exit /b %ERRORLEVEL%
)
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python -m antiagent app
    exit /b %ERRORLEVEL%
)
echo [ERROR] Python 3.9+ was not found on your system!
echo Please install Python from https://www.python.org/ or run:
echo   winget install Python.Python.3.12
pause
exit /b 1
"""
        zf.writestr("AntiAgent/AntiAgent.bat", bat_content)

        # 3. Add Install-Hook.bat
        install_bat = """@echo off
title AntiAgent - Register Global Hook
echo ============================================================
echo   Installing AntiAgent Global Protection for Antigravity...
echo ============================================================
set PYTHONPATH=%~dp0;%PYTHONPATH%
where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    py -m antiagent install --global
    pause
    exit /b 0
)
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python -m antiagent install --global
    pause
    exit /b 0
)
echo [ERROR] Python 3.9+ was not found on your system!
pause
exit /b 1
"""
        zf.writestr("AntiAgent/Install-Hook.bat", install_bat)

        # 4. Add README.txt
        readme_content = """============================================================
  🛡️ AntiAgent Guard for Windows (Public Beta)
============================================================

Thank you for downloading AntiAgent!

NOTE: Windows support is currently in Public Beta. The core safety
engine and hooks are fully functional. If you notice any UI or
platform-specific quirks, please file an issue on GitHub!

QUICKSTART:
1. Double-click "AntiAgent.bat" to launch the native desktop application.
2. In the top bar, click "🚀 Guide & Doctor" and click "Enable Global Hook".
3. That's it! Google Antigravity is now automatically protected across all your projects.

OR REGISTER HOOK DIRECTLY:
- Double-click "Install-Hook.bat" to protect all Antigravity projects globally.

COMMAND LINE USAGE:
Open PowerShell or CMD in this directory:
  python -m antiagent status      (Check active protection status)
  python -m antiagent test        (Run safety simulation test suite)
  python -m antiagent doctor      (Run environment diagnostic check)

REQUIREMENTS:
- Windows 10 or 11
- Python 3.9 or newer (Install via https://www.python.org/ or: winget install Python.Python.3.12)
- Google Antigravity

For documentation, bug reports, and updates:
https://github.com/aiden-guan/AntiAgent/issues
============================================================
"""
        zf.writestr("AntiAgent/README.txt", readme_content)

    print(f"📦 Created downloadable Windows release package: {zip_path}")
    return zip_path


def launch_linux_app() -> None:
    """Launch the AntiAgent desktop application on Linux via native GTK WebKit, Chromium app mode, or default browser."""
    import time
    import urllib.request
    import webbrowser

    # 1. Ensure backend daemon is running
    url = "http://127.0.0.1:4242/"
    is_up = False
    try:
        with urllib.request.urlopen(url + "api/status", timeout=0.5) as resp:
            if resp.status == 200:
                is_up = True
    except Exception:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                if resp.status == 200:
                    is_up = True
        except Exception:
            is_up = False

    if not is_up:
        print("🚀 Starting AntiAgent background daemon...")
        py_exec = sys.executable or "python3"
        subprocess.Popen(
            [py_exec, "-m", "antiagent.dashboard", "--no-open"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        for _ in range(25):
            time.sleep(0.2)
            try:
                with urllib.request.urlopen(url + "api/status", timeout=0.3) as resp:
                    if resp.status == 200:
                        is_up = True
                        break
            except Exception:
                try:
                    with urllib.request.urlopen(url, timeout=0.3) as resp:
                        if resp.status == 200:
                            is_up = True
                            break
                except Exception:
                    continue

    if not is_up:
        print(
            "⚠️  The AntiAgent dashboard did not respond on http://127.0.0.1:4242. "
            "A stale AntiAgent process may be holding the port; close it and retry."
        )

    app_url = "http://127.0.0.1:4242"
    repo_root = Path(__file__).resolve().parent.parent.parent

    # 2. Try Native GTK WebKit window
    python_candidates = [sys.executable or "python3", "/usr/bin/python3", "/usr/local/bin/python3"]
    seen_py = set()
    unique_py = []
    for p in python_candidates:
        if p and p not in seen_py:
            seen_py.add(p)
            unique_py.append(p)

    for py in unique_py:
        if not shutil.which(py) and not os.path.isfile(py):
            continue
        probe_cmd = [
            py, "-c",
            "import gi; "
            "(gi.require_version('Gtk', '4.0'), gi.require_version('WebKit', '6.0')) if hasattr(gi, 'require_version') else None"
        ]
        probe_gtk3_cmd = [
            py, "-c",
            "import gi; "
            "gi.require_version('Gtk', '3.0')"
        ]
        has_gtk = False
        try:
            r = subprocess.run(probe_cmd, capture_output=True, timeout=1.5)
            if r.returncode == 0:
                has_gtk = True
            else:
                r3 = subprocess.run(probe_gtk3_cmd, capture_output=True, timeout=1.5)
                if r3.returncode == 0:
                    has_gtk = True
        except Exception:
            has_gtk = False

        if has_gtk:
            print("🖥️  Launching AntiAgent Guard native GTK window...")
            env = dict(os.environ)
            existing_pp = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{repo_root}:{existing_pp}" if existing_pp else str(repo_root)
            try:
                proc = subprocess.Popen([py, "-m", "antiagent.desktop.linux_window", app_url], env=env)
                try:
                    exit_code = proc.wait(timeout=0.6)
                    if exit_code != 0:
                        pass
                    else:
                        return
                except subprocess.TimeoutExpired:
                    return
            except Exception:
                pass

    # 3. Fallback: Browser App Mode (Chrome, Chromium, Brave, Edge)
    browser_bins = [
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "brave-browser",
        "microsoft-edge",
        "microsoft-edge-stable",
    ]
    for b in browser_bins:
        bin_path = shutil.which(b)
        if bin_path:
            print(f"🖥️  Launching AntiAgent Guard window via {b} App Mode...")
            subprocess.Popen([bin_path, f"--app={app_url}", "--window-size=1160,800"])
            return

    flatpak_candidates = [
        ("com.google.Chrome", "/var/lib/flatpak/exports/bin/com.google.Chrome"),
        ("org.chromium.Chromium", "/var/lib/flatpak/exports/bin/org.chromium.Chromium"),
        ("com.brave.Browser", "/var/lib/flatpak/exports/bin/com.brave.Browser"),
        ("com.microsoft.Edge", "/var/lib/flatpak/exports/bin/com.microsoft.Edge"),
    ]
    for app_id, exp_bin in flatpak_candidates:
        if os.path.isfile(exp_bin):
            print(f"🖥️  Launching AntiAgent Guard window via Flatpak {app_id} App Mode...")
            subprocess.Popen([exp_bin, f"--app={app_url}", "--window-size=1160,800"])
            return
        if shutil.which("flatpak"):
            try:
                res = subprocess.run(["flatpak", "info", app_id], capture_output=True, timeout=1.0)
                if res.returncode == 0:
                    print(f"🖥️  Launching AntiAgent Guard window via Flatpak {app_id} App Mode...")
                    subprocess.Popen(["flatpak", "run", app_id, f"--app={app_url}", "--window-size=1160,800"])
                    return
            except Exception:
                pass

    # 4. Fallback: Default web browser
    print("🌐 Opening AntiAgent in default web browser...")
    webbrowser.open(app_url)


def install_linux_app(to_global: bool = False) -> Path:
    """Install AntiAgent XDG desktop entry, icons, and launcher on Linux."""
    if to_global:
        apps_dir = Path("/usr/share/applications")
        icons_root = Path("/usr/share/icons/hicolor")
        bin_dir = Path("/usr/local/bin")
    else:
        apps_dir = Path(os.path.expanduser("~/.local/share/applications"))
        icons_root = Path(os.path.expanduser("~/.local/share/icons/hicolor"))
        bin_dir = Path(os.path.expanduser("~/.local/bin"))

    apps_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)

    # 1. Install icons
    desktop_dir = Path(__file__).resolve().parent
    png_src = desktop_dir / "antiagent.png"
    svg_src = desktop_dir / "antiagent.svg"

    icon_png_dest = icons_root / "512x512/apps/antiagent.png"
    icon_svg_dest = icons_root / "scalable/apps/antiagent.svg"

    if png_src.is_file():
        icon_png_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(png_src, icon_png_dest)

    if svg_src.is_file():
        icon_svg_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(svg_src, icon_svg_dest)

    # 2. Write launcher script
    launcher_file = bin_dir / "antiagent-app"
    py_exec = sys.executable or "python3"
    launcher_content = f"""#!/bin/sh
# AntiAgent desktop launcher
exec {py_exec} -m antiagent app "$@"
"""
    launcher_file.write_text(launcher_content, encoding="utf-8")
    launcher_file.chmod(0o755)

    # 3. Write XDG .desktop entry
    desktop_file = apps_dir / "antiagent.desktop"
    desktop_content = """[Desktop Entry]
Version=1.0
Type=Application
Name=AntiAgent Guard
GenericName=AI Agent Safety Guard
Comment=Interactive security dashboard and real-time safety guard for Google Antigravity
Exec=antiagent-app %U
Icon=antiagent
Terminal=false
Categories=Development;Security;System;Utility;
StartupWMClass=antiagent
Keywords=antigravity;agent;security;guard;ai;safety;
"""
    desktop_file.write_text(desktop_content, encoding="utf-8")
    desktop_file.chmod(0o644)

    # 4. Update desktop and icon caches if available
    try:
        subprocess.run(["update-desktop-database", str(apps_dir)], capture_output=True, timeout=2.0)
    except Exception:
        pass
    try:
        subprocess.run(["gtk-update-icon-cache", "-q", "-t", str(icons_root)], capture_output=True, timeout=2.0)
    except Exception:
        pass

    print(f"🎉 Installed AntiAgent desktop application to {desktop_file}")
    print(f"   • Application launcher: {launcher_file}")
    print(f"   • Application entry: {desktop_file}")
    if png_src.is_file():
        print(f"   • Application icon: {icon_png_dest}")
    print("   You can now launch 'AntiAgent Guard' from your application menu or run 'antiagent app'!")
    return desktop_file


def build_linux_package(output_dir: Path = None) -> Path:
    """Package AntiAgent into a standalone AntiAgent-Linux.tar.gz for Linux."""
    import tarfile
    import time
    import io

    if output_dir is None:
        output_dir = Path(os.getcwd()) / "dist"

    output_dir.mkdir(parents=True, exist_ok=True)
    tar_path = output_dir / "AntiAgent-Linux.tar.gz"
    if tar_path.exists():
        tar_path.unlink()

    repo_root = Path(__file__).resolve().parent.parent.parent
    antiagent_src = repo_root / "antiagent"
    desktop_dir = Path(__file__).resolve().parent

    with tarfile.open(tar_path, "w:gz") as tf:
        # 1. Add antiagent package
        for root, dirs, files in os.walk(antiagent_src):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".pytest_cache")]
            for file in files:
                if file.endswith((".pyc", ".DS_Store", ".o")):
                    continue
                file_path = Path(root) / file
                rel_path = file_path.relative_to(repo_root)
                tf.add(file_path, arcname=f"AntiAgent/{rel_path}")

        # 2. Add AntiAgent.sh
        sh_content = """#!/usr/bin/env bash
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$DIR:$PYTHONPATH"
if [ "$#" -eq 0 ]; then
    exec python3 -m antiagent app
else
    exec python3 -m antiagent "$@"
fi
"""
        sh_bytes = sh_content.encode("utf-8")
        ti = tarfile.TarInfo(name="AntiAgent/AntiAgent.sh")
        ti.size = len(sh_bytes)
        ti.mode = 0o755
        ti.mtime = int(time.time())
        tf.addfile(ti, io.BytesIO(sh_bytes))

        # 3. Add install-app.sh
        install_sh = """#!/usr/bin/env bash
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$DIR:$PYTHONPATH"
echo "============================================================"
echo "  Installing AntiAgent Guard for Linux..."
echo "============================================================"
python3 -m antiagent install-app
echo ""
echo "🔒 Enabling AntiAgent global protection hook for Antigravity..."
python3 -m antiagent install --global
echo ""
echo "============================================================"
echo "🎉 Installation complete!"
echo "You can now launch 'AntiAgent Guard' from your app launcher,"
echo "or run: ./AntiAgent.sh"
echo "============================================================"
"""
        inst_bytes = install_sh.encode("utf-8")
        ti_inst = tarfile.TarInfo(name="AntiAgent/install-app.sh")
        ti_inst.size = len(inst_bytes)
        ti_inst.mode = 0o755
        ti_inst.mtime = int(time.time())
        tf.addfile(ti_inst, io.BytesIO(inst_bytes))

        # 4. Add AntiAgent.desktop
        desktop_content = """[Desktop Entry]
Version=1.0
Type=Application
Name=AntiAgent Guard
GenericName=AI Agent Safety Guard
Comment=Interactive security dashboard and real-time safety guard for Google Antigravity
Exec=antiagent-app %U
Icon=antiagent
Terminal=false
Categories=Development;Security;System;Utility;
StartupWMClass=antiagent
Keywords=antigravity;agent;security;guard;ai;safety;
"""
        desk_bytes = desktop_content.encode("utf-8")
        ti_desk = tarfile.TarInfo(name="AntiAgent/AntiAgent.desktop")
        ti_desk.size = len(desk_bytes)
        ti_desk.mode = 0o644
        ti_desk.mtime = int(time.time())
        tf.addfile(ti_desk, io.BytesIO(desk_bytes))

        # 5. Add icons if available
        png_path = desktop_dir / "antiagent.png"
        if png_path.is_file():
            tf.add(png_path, arcname="AntiAgent/antiagent.png")
        svg_path = desktop_dir / "antiagent.svg"
        if svg_path.is_file():
            tf.add(svg_path, arcname="AntiAgent/antiagent.svg")

        # 6. Add README.txt
        readme_content = f"""============================================================
  🛡️ AntiAgent Guard for Linux (v{__version__})
============================================================

Thank you for downloading AntiAgent!

QUICKSTART:
1. Run './AntiAgent.sh' to launch the desktop application.
2. In the top bar, click '🚀 Guide & Doctor' and click 'Enable Global Hook'.
   That's it! Google Antigravity is now protected across all your projects.

INSTALL SYSTEM SHORTCUT & GLOBAL HOOK:
Run:
  ./install-app.sh

This automatically installs the desktop application menu entry
('AntiAgent Guard') and enables the Antigravity PreToolUse hook.

COMMAND LINE USAGE:
  ./AntiAgent.sh status      (Check active protection status)
  ./AntiAgent.sh doctor      (Run environment diagnostic health check)
  ./AntiAgent.sh test        (Run safety simulation test suite)
  ./AntiAgent.sh app         (Launch the desktop window)

REQUIREMENTS:
- Linux (x86_64 or aarch64) with Wayland or X11
- Python 3.9 or newer
- Google Antigravity

For documentation, bug reports, and updates:
https://github.com/aiden-guan/AntiAgent/issues
============================================================
"""
        readme_bytes = readme_content.encode("utf-8")
        ti_readme = tarfile.TarInfo(name="AntiAgent/README.txt")
        ti_readme.size = len(readme_bytes)
        ti_readme.mode = 0o644
        ti_readme.mtime = int(time.time())
        tf.addfile(ti_readme, io.BytesIO(readme_bytes))

    print(f"📦 Created downloadable Linux release package: {tar_path}")
    return tar_path


def install_app(to_global: bool = False) -> Path:
    """Install AntiAgent desktop launcher."""
    if sys.platform == "win32":
        antiagent_dir = Path(os.path.expanduser("~/.antiagent"))
        antiagent_dir.mkdir(parents=True, exist_ok=True)
        bat_file = antiagent_dir / "AntiAgent.bat"
        bat_file.write_text(
            "@echo off\ntitle AntiAgent Guard\npython -m antiagent app\n",
            encoding="utf-8",
        )
        desktop = Path(os.path.expanduser("~/Desktop"))
        if desktop.is_dir():
            shortcut_ps = f'''$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut("{desktop}\\AntiAgent Guard.lnk"); $s.TargetPath = "{bat_file}"; $s.Save()'''
            try:
                subprocess.run(["powershell", "-Command", shortcut_ps], capture_output=True)
            except Exception:
                pass
        print(f"🎉 Installed AntiAgent Windows launcher to {bat_file}")
        return bat_file

    if sys.platform.startswith("linux"):
        return install_linux_app(to_global=to_global)

    app_bundle = build_macos_app()

    if to_global:
        target_dir = Path("/Applications")
    else:
        target_dir = Path(os.path.expanduser("~/Applications"))

    target_dir.mkdir(parents=True, exist_ok=True)
    dest_app = target_dir / "AntiAgent.app"

    if dest_app.exists():
        if dest_app.is_dir():
            shutil.rmtree(dest_app)
        else:
            dest_app.unlink()

    shutil.copytree(app_bundle, dest_app)
    print(f"🎉 Installed AntiAgent to {dest_app}")
    print("   You can now launch it from Spotlight (Cmd+Space -> AntiAgent), Launchpad, or the Dock!")
    return dest_app


def launch_app(dest_app: Path = None) -> None:
    """Open the native desktop app."""
    if sys.platform == "win32":
        launch_windows_app()
        return

    if sys.platform.startswith("linux"):
        launch_linux_app()
        return

    if dest_app is None or not dest_app.exists():
        dest_app = build_macos_app()
    subprocess.run(["open", str(dest_app)])
