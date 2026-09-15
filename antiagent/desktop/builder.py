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
    """Generate high-end dark-mode background image for the DMG installer."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return

    scale = 2
    w, h = 660 * scale, 420 * scale  # 1320 x 840 for supersampled anti-aliasing

    im = Image.new("RGB", (w, h), "#0d1117")
    draw = ImageDraw.Draw(im)

    def load_font(size_pt: int, mono: bool = False):
        try:
            font_path = "/System/Library/Fonts/SFNSMono.ttf" if mono else "/System/Library/Fonts/SFNS.ttf"
            return ImageFont.truetype(font_path, size_pt * scale)
        except Exception:
            try:
                fallback = "/System/Library/Fonts/Helvetica.ttc"
                return ImageFont.truetype(fallback, size_pt * scale, index=1 if mono else 0)
            except Exception:
                return ImageFont.load_default()

    font_eyebrow = load_font(10)
    font_title = load_font(22)
    font_sub = load_font(12)
    font_arrow_text = load_font(11)
    font_card_head = load_font(12)
    font_card_body = load_font(11)
    font_mono = load_font(10, mono=True)

    # 1. Header with Eyebrow Pill
    eyebrow_text = "ANTIGRAVITY SAFETY GATEKEEPER"
    eb_bbox = draw.textbbox((0, 0), eyebrow_text, font=font_eyebrow)
    eb_w = eb_bbox[2] - eb_bbox[0]
    eb_x = w // 2 - eb_w // 2
    eb_y = 20 * scale
    draw.rounded_rectangle(
        [(eb_x - 12 * scale, eb_y - 4 * scale), (eb_x + eb_w + 12 * scale, eb_y + 16 * scale)],
        radius=8 * scale,
        fill="#161b22",
        outline="#30363d",
        width=1 * scale,
    )
    draw.text((w // 2, eb_y + 6 * scale), eyebrow_text, font=font_eyebrow, fill="#58a6ff", anchor="mm")

    draw.text((w // 2, 52 * scale), "AntiAgent for macOS", font=font_title, fill="#f0f6fc", anchor="mm")
    draw.text((w // 2, 74 * scale), "Drag AntiAgent into Applications to complete installation", font=font_sub, fill="#8b949e", anchor="mm")

    # 2. Sleek Directional Indicator (Between icon positions)
    arrow_y = 145 * scale
    cx = w // 2

    # Central pill with label
    pill_w = 210 * scale
    pill_h = 32 * scale
    draw.rounded_rectangle(
        [(cx - pill_w // 2, arrow_y - pill_h // 2), (cx + pill_w // 2, arrow_y + pill_h // 2)],
        radius=16 * scale,
        fill="#161b22",
        outline="#30363d",
        width=1 * scale,
    )

    # Label text inside pill
    draw.text((cx - 14 * scale, arrow_y), "Drag into Applications", font=font_arrow_text, fill="#58a6ff", anchor="mm")

    # Crisp Vector Chevron Arrowhead
    ax = cx + 72 * scale
    ay = arrow_y
    draw.line([(ax - 18 * scale, ay), (ax, ay)], fill="#58a6ff", width=2 * scale)
    draw.line([(ax - 6 * scale, ay - 6 * scale), (ax, ay)], fill="#58a6ff", width=2 * scale)
    draw.line([(ax - 6 * scale, ay + 6 * scale), (ax, ay)], fill="#58a6ff", width=2 * scale)

    # 3. Gatekeeper Notice Card (Double-Bezel Architecture)
    card_x1 = 36 * scale
    card_x2 = w - 36 * scale
    card_y1 = 236 * scale
    card_y2 = 402 * scale

    # Outer shell
    draw.rounded_rectangle(
        [(card_x1, card_y1), (card_x2, card_y2)],
        radius=14 * scale,
        fill="#161b22",
        outline="#30363d",
        width=1 * scale,
    )

    # Inner core
    pad = 6 * scale
    draw.rounded_rectangle(
        [(card_x1 + pad, card_y1 + pad), (card_x2 - pad, card_y2 - pad)],
        radius=10 * scale,
        fill="#0d1117",
        outline="#21262d",
        width=1 * scale,
    )

    # Card text
    tx = card_x1 + 22 * scale
    ty = card_y1 + 18 * scale

    draw.text((tx, ty), "FIRST-TIME LAUNCH NOTICE", font=font_eyebrow, fill="#d29922")
    draw.text(
        (tx, ty + 18 * scale),
        'If macOS warns about "Malware", "Damaged", or "Unidentified Developer":',
        font=font_card_head,
        fill="#f0f6fc",
    )
    draw.text(
        (tx, ty + 38 * scale),
        "This is Apple's standard Gatekeeper warning for free open-source software downloaded from the web.",
        font=font_card_body,
        fill="#8b949e",
    )
    draw.text(
        (tx, ty + 60 * scale),
        "1. In Applications, Right-Click (or Control-Click) AntiAgent  ->  choose Open  ->  click Open",
        font=font_card_body,
        fill="#c9d1d9",
    )
    draw.text(
        (tx, ty + 78 * scale),
        "2. Or open System Settings  >  Privacy & Security  >  scroll down and click 'Open Anyway'",
        font=font_card_body,
        fill="#c9d1d9",
    )

    # Terminal code snippet pill
    code_text = "Terminal bypass: xattr -cr /Applications/AntiAgent.app"
    cb_bbox = draw.textbbox((0, 0), code_text, font=font_mono)
    cb_w = cb_bbox[2] - cb_bbox[0]
    code_y = ty + 98 * scale

    draw.rounded_rectangle(
        [(tx - 2 * scale, code_y - 2 * scale), (tx + cb_w + 14 * scale, code_y + 16 * scale)],
        radius=4 * scale,
        fill="#161b22",
        outline="#30363d",
        width=1 * scale,
    )
    draw.text((tx + 6 * scale, code_y + 7 * scale), code_text, font=font_mono, fill="#58a6ff", anchor="lm")

    # Downsample supersampled 2x image to 1x with LANCZOS for razor-sharp antialiasing
    im_1x = im.resize((660, 420), Image.Resampling.LANCZOS)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    im_1x.save(str(output_path), "PNG", dpi=(72, 72))


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
