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


def generate_dmg_background(output_path: Path) -> None:
    """Generate custom dark-mode background image for the DMG installer."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return

    width, height = 660, 420
    im = Image.new("RGB", (width, height), "#0d1117")
    draw = ImageDraw.Draw(im)

    def load_font(size: int, bold: bool = False):
        try:
            return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size, index=1 if bold else 0)
        except Exception:
            return ImageFont.load_default()

    font_title = load_font(19, bold=True)
    font_sub = load_font(12, bold=False)
    font_arrow = load_font(13, bold=True)
    font_card_title = load_font(13, bold=True)
    font_card_bold = load_font(11, bold=True)
    font_card_body = load_font(11, bold=False)

    # Header
    draw.text((width // 2, 28), "🛡️  AntiAgent for macOS", font=font_title, fill="#f0f6fc", anchor="mm")
    draw.text((width // 2, 50), "Intelligent Safety Gatekeeper for Google Antigravity", font=font_sub, fill="#8b949e", anchor="mm")
    draw.line([(40, 68), (width - 40, 68)], fill="#21262d", width=1)

    # Subtle circular landing target zones
    draw.ellipse([(160 - 55, 145 - 55), (160 + 55, 145 + 55)], fill="#161b22", outline="#30363d", width=1)
    draw.ellipse([(500 - 55, 145 - 55), (500 + 55, 145 + 55)], fill="#161b22", outline="#30363d", width=1)

    # Arrow from left to right
    shaft_y = 142
    draw.line([(240, shaft_y), (405, shaft_y)], fill="#58a6ff", width=3)
    draw.polygon([(405, shaft_y - 8), (420, shaft_y), (405, shaft_y + 8)], fill="#58a6ff")
    draw.text((325, shaft_y - 18), "Drag into Applications", font=font_arrow, fill="#58a6ff", anchor="mm")

    # Bottom Notice Card for Gatekeeper
    card_x1, card_y1 = 30, 245
    card_x2, card_y2 = width - 30, 398
    draw.rounded_rectangle([(card_x1, card_y1), (card_x2, card_y2)], radius=10, fill="#161b22", outline="#30363d", width=1)

    # Card Content
    draw.text((card_x1 + 18, card_y1 + 18), "⚠️  macOS Gatekeeper / Apple Security Notice", font=font_card_title, fill="#d29922")
    draw.text((card_x1 + 18, card_y1 + 38), "Apple may display an 'unidentified developer' or 'damaged' warning on first launch for open-source apps.", font=font_card_body, fill="#8b949e")

    draw.text((card_x1 + 18, card_y1 + 62), "How to approve & open AntiAgent:", font=font_card_bold, fill="#f0f6fc")
    draw.text((card_x1 + 22, card_y1 + 82), "1. In Applications, Right-Click (or Control-Click) AntiAgent  ➜  click Open  ➜  click Open", font=font_card_body, fill="#c9d1d9")
    draw.text((card_x1 + 22, card_y1 + 102), "2. Or open System Settings  ➜  Privacy & Security  ➜  scroll down and click 'Open Anyway'", font=font_card_body, fill="#c9d1d9")
    draw.text((card_x1 + 22, card_y1 + 122), "3. Terminal 1-liner: xattr -cr /Applications/AntiAgent.app", font=font_card_body, fill="#58a6ff")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(str(output_path), "PNG")


def build_dmg(output_dir: Path = None) -> Path:
    """Package AntiAgent.app into a custom styled AntiAgent.dmg with custom background and instructions."""
    app_bundle = build_macos_app(output_dir)
    if output_dir is None:
        output_dir = Path(os.getcwd()) / "dist"

    output_dir.mkdir(parents=True, exist_ok=True)
    final_dmg = output_dir / "AntiAgent.dmg"
    rw_dmg = output_dir / "temp_rw.dmg"

    if rw_dmg.exists():
        rw_dmg.unlink()
    if final_dmg.exists():
        final_dmg.unlink()

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

    # 4. Generate & copy background image
    bg_dir = mount_point / ".background"
    bg_dir.mkdir(parents=True, exist_ok=True)
    bg_img = bg_dir / "background.png"
    generate_dmg_background(bg_img)

    # 5. Run AppleScript to layout Finder window and icons
    applescript = """
    tell application "Finder"
        tell disk "AntiAgent"
            open
            set current view of container window to icon view
            set toolbar visible of container window to false
            set statusbar visible of container window to false
            set the bounds of container window to {400, 120, 1060, 540}
            set viewOptions to the icon view options of container window
            set arrangement of viewOptions to not arranged
            set icon size of viewOptions to 88
            try
                set background picture of viewOptions to file ".background:background.png"
            end try
            set position of item "AntiAgent.app" of container window to {160, 145}
            set position of item "Applications" of container window to {500, 145}
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
