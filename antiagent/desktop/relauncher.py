"""Detached helper script to safely perform atomic staged replacement and relaunch of AntiAgent.app."""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional


def _log(msg: str, log_file: Optional[Path] = None) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] [Relauncher] {msg}\n"
    sys.stdout.write(entry)
    sys.stdout.flush()
    if log_file:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception:
            pass


def _wait_pid(pid: int, timeout: float = 8.0, term_after: float = 2.0, log_file: Optional[Path] = None) -> bool:
    """Wait for a process to terminate, escalating from waiting to SIGTERM to SIGKILL."""
    if pid <= 0:
        return True

    start = time.time()
    signaled_term = False

    while time.time() - start < timeout:
        try:
            os.kill(pid, 0)
        except OSError:
            # Process does not exist anymore
            return True

        elapsed = time.time() - start
        if elapsed >= term_after and not signaled_term:
            _log(f"Process {pid} still running after {term_after:.1f}s, sending SIGTERM...", log_file)
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                return True
            signaled_term = True

        time.sleep(0.1)

    # Force kill if still alive
    try:
        os.kill(pid, 0)
        _log(f"Process {pid} did not exit within {timeout}s, sending SIGKILL...", log_file)
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)
        return True
    except OSError:
        return True


def perform_staged_swap_and_relaunch(
    target_app: Path,
    staged_app: Path,
    backup_app: Path,
    old_pid: int = 0,
    parent_pid: int = 0,
    target_version: Optional[str] = None,
    log_file: Optional[Path] = None,
    no_relaunch: bool = False,
    timeout: float = 8.0,
) -> bool:
    """Safely terminate old app/backend, atomically swap staged bundle, validate, and relaunch."""
    target_app = target_app.resolve()
    staged_app = staged_app.resolve()
    backup_app = backup_app.resolve()

    _log(f"Starting staged update for: {target_app}", log_file)
    _log(f"Staged app source: {staged_app}", log_file)
    _log(f"Backup destination: {backup_app}", log_file)

    # 1. Wait for old backend PID to terminate
    if old_pid > 0 and old_pid != os.getpid():
        _log(f"Waiting for old backend process {old_pid} to terminate...", log_file)
        _wait_pid(old_pid, timeout=timeout, term_after=2.0, log_file=log_file)

    # 2. Wait for parent Swift app process if applicable
    if parent_pid > 0 and parent_pid != os.getpid():
        _log(f"Requesting desktop app {parent_pid} to gracefully terminate...", log_file)
        try:
            subprocess.run(["osascript", "-e", 'tell application "AntiAgent" to quit'], capture_output=True, timeout=3.0)
        except Exception:
            pass
        _wait_pid(parent_pid, timeout=timeout, term_after=2.0, log_file=log_file)

    # Give OS a brief moment to release file locks
    time.sleep(0.3)

    if not staged_app.is_dir():
        _log(f"❌ Staged application bundle not found at {staged_app}", log_file)
        return False

    # 3. Clean up any leftover backup
    if backup_app.exists():
        _log(f"Cleaning up prior backup at {backup_app}...", log_file)
        if backup_app.is_dir():
            shutil.rmtree(backup_app, ignore_errors=True)
        else:
            backup_app.unlink(missing_ok=True)

    # 4. Atomic swap: Move target -> backup, staged -> target
    _log(f"Moving active app {target_app} to backup {backup_app}...", log_file)
    try:
        if target_app.exists():
            shutil.move(str(target_app), str(backup_app))
        shutil.move(str(staged_app), str(target_app))
    except Exception as e:
        _log(f"❌ Failed during bundle swap: {e}", log_file)
        # Attempt recovery if target was moved to backup but staged failed to move
        if not target_app.exists() and backup_app.exists():
            try:
                shutil.move(str(backup_app), str(target_app))
            except Exception:
                pass
        return False

    # 5. Validate the installed bundle
    _log(f"Validating newly swapped bundle at {target_app}...", log_file)
    validation_ok = True
    info_plist = target_app / "Contents" / "Info.plist"
    binary_path = target_app / "Contents" / "MacOS" / "AntiAgent"

    if not info_plist.is_file():
        _log(f"❌ Validation error: Info.plist missing at {info_plist}", log_file)
        validation_ok = False
    else:
        try:
            with open(info_plist, "rb") as f:
                plist_data = plistlib.load(f)
            installed_ver = plist_data.get("CFBundleShortVersionString")
            bundle_id = plist_data.get("CFBundleIdentifier")

            if bundle_id != "com.antiagent.desktop":
                _log(f"❌ Validation error: unexpected bundle identifier {bundle_id}", log_file)
                validation_ok = False

            if target_version and installed_ver != target_version:
                _log(f"❌ Validation error: version mismatch (expected {target_version}, got {installed_ver})", log_file)
                validation_ok = False
        except Exception as pe:
            _log(f"❌ Validation error parsing Info.plist: {pe}", log_file)
            validation_ok = False

    if not binary_path.is_file():
        _log(f"❌ Validation error: native binary missing at {binary_path}", log_file)
        validation_ok = False

    # 6. Roll back if validation failed
    if not validation_ok:
        _log("⚠️ Replacement validation failed! Rolling back to previous version...", log_file)
        try:
            if target_app.exists():
                shutil.rmtree(target_app, ignore_errors=True)
            if backup_app.exists():
                shutil.move(str(backup_app), str(target_app))
                _log("✅ Rollback to backup bundle succeeded.", log_file)
        except Exception as re:
            _log(f"❌ Rollback failed: {re}", log_file)
        return False

    # 7. Post-install fixups: permissions, quarantine, codesign, launchservices
    try:
        binary_path.chmod(binary_path.stat().st_mode | 0o755)
    except Exception:
        pass

    try:
        subprocess.run(["xattr", "-cr", str(target_app)], capture_output=True)
    except Exception:
        pass

    try:
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(target_app)], capture_output=True)
    except Exception:
        pass

    lsregister = Path("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister")
    if lsregister.is_file():
        try:
            subprocess.run([str(lsregister), "-f", str(target_app)], capture_output=True)
        except Exception:
            pass

    # Clean up backup now that replacement is verified
    if backup_app.exists():
        shutil.rmtree(backup_app, ignore_errors=True)

    _log(f"✅ Successfully installed and verified update at {target_app}!", log_file)

    # 8. Relaunch
    if not no_relaunch:
        _log(f"🚀 Relaunching {target_app}...", log_file)
        try:
            subprocess.Popen(["open", str(target_app)])
        except Exception as e:
            _log(f"❌ Failed to relaunch application: {e}", log_file)
            return False

    return True


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description="Detached helper to swap and relaunch AntiAgent.app")
    parser.add_argument("--target-app", required=True, help="Path to active installed .app bundle")
    parser.add_argument("--staged-app", required=True, help="Path to staged update .app bundle")
    parser.add_argument("--backup-app", required=True, help="Path for temporary backup")
    parser.add_argument("--old-pid", type=int, default=0, help="PID of backend process to wait for")
    parser.add_argument("--parent-pid", type=int, default=0, help="PID of parent desktop app to wait for")
    parser.add_argument("--target-version", help="Expected version of target bundle")
    parser.add_argument("--log-file", help="Path to write relauncher log")
    parser.add_argument("--no-relaunch", action="store_true", help="Do not launch app after update (test mode)")
    parser.add_argument("--timeout", type=float, default=8.0, help="Timeout waiting for processes")

    args = parser.parse_args(argv)

    log_p = Path(args.log_file).resolve() if args.log_file else Path.home() / ".antiagent" / "update_relaunch.log"

    success = perform_staged_swap_and_relaunch(
        target_app=Path(args.target_app),
        staged_app=Path(args.staged_app),
        backup_app=Path(args.backup_app),
        old_pid=args.old_pid,
        parent_pid=args.parent_pid,
        target_version=args.target_version,
        log_file=log_p,
        no_relaunch=args.no_relaunch,
        timeout=args.timeout,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
