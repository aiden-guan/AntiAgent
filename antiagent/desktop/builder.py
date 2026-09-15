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
    <string>0.1.0</string>
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

    print(f"✅ Successfully built native app: {app_bundle}")
    return app_bundle


def install_app(to_global: bool = False) -> Path:
    """Install AntiAgent.app to ~/Applications or /Applications."""
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
    if dest_app is None or not dest_app.exists():
        dest_app = build_macos_app()
    subprocess.run(["open", str(dest_app)])
