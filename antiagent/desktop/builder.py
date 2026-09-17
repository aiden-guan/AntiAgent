"""Builder and installer for native macOS AntiAgent desktop application."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

INFO_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
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
    <string>0.1.3</string>
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
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "desktop", ".DS_Store"),
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
    url = "http://127.0.0.1:4242/api/status"
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
title AntiAgent Guard
echo ============================================================
echo   Starting AntiAgent Guard for Antigravity...
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
  🛡️ AntiAgent Guard for Windows (Google Antigravity)
============================================================

Thank you for downloading AntiAgent!

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

For documentation and updates:
https://github.com/aiden-guan/AntiAgent
============================================================
"""
        zf.writestr("AntiAgent/README.txt", readme_content)

    print(f"📦 Created downloadable Windows release package: {zip_path}")
    return zip_path


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

    if dest_app is None or not dest_app.exists():
        dest_app = build_macos_app()
    subprocess.run(["open", str(dest_app)])
