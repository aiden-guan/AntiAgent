"""AntiAgent Doctor diagnostic engine for Antigravity & AntiAgent environment."""

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict


def get_doctor_report(workspace_path: str = ".") -> Dict[str, Any]:
    """Inspect the environment and return structured diagnostic findings."""
    ws_resolved = Path(workspace_path).resolve()

    # 1. Python Runtime
    py_ver = sys.version.split()[0]
    python_info = {
        "version": py_ver,
        "executable": sys.executable,
        "ok": sys.version_info >= (3, 9),
    }

    # 2. Antigravity Global Hook
    global_hook_path = Path(os.path.expanduser("~/.gemini/config/hooks.json"))
    global_status = "not_installed"
    if global_hook_path.is_file():
        try:
            data = json.loads(global_hook_path.read_text(encoding="utf-8"))
            if "antiagent-guard" in data and data["antiagent-guard"].get("enabled", True):
                global_status = "active"
            else:
                global_status = "disabled"
        except Exception:
            global_status = "corrupt"

    # 3. Workspace Hook
    ws_hook_path = ws_resolved / ".agents" / "hooks.json"
    ws_status = "not_installed"
    if ws_hook_path.is_file():
        try:
            data = json.loads(ws_hook_path.read_text(encoding="utf-8"))
            if "antiagent-guard" in data and data["antiagent-guard"].get("enabled", True):
                ws_status = "active"
            else:
                ws_status = "disabled"
        except Exception:
            ws_status = "corrupt"

    # 4. Antigravity Sessions across Desktop, IDE, and CLI
    from antiagent.engine.conversations import get_conversation_store
    conv_store = get_conversation_store()
    sources_summary = conv_store.get_sources_summary()
    total_session_count = sources_summary.get("total", 0)
    primary_brain_dir = conv_store.get_primary_root("desktop") or Path(os.path.expanduser("~/.gemini/antigravity/brain"))

    # 5. Native Desktop App
    desktop_app_installed = False
    desktop_app_path = None

    if sys.platform == "darwin":
        user_app = Path(os.path.expanduser("~/Applications/AntiAgent.app"))
        sys_app = Path("/Applications/AntiAgent.app")
        desktop_app_installed = user_app.exists() or sys_app.exists()
        desktop_app_path = str(user_app if user_app.exists() else sys_app) if desktop_app_installed else None
    elif sys.platform == "win32":
        # Windows shortcuts & standalone launcher paths
        win_candidates = [
            Path(os.path.expanduser("~/Desktop/AntiAgent Guard.lnk")),
            Path(os.path.expanduser("~/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/AntiAgent Guard.lnk")),
            Path(os.path.expanduser("~/.antiagent/AntiAgent.bat")),
            Path(os.getcwd()) / "AntiAgent.bat",
            Path(os.path.expanduser("~/AppData/Local/Programs/AntiAgent/AntiAgent.exe")),
        ]
        for candidate in win_candidates:
            if candidate.exists():
                desktop_app_installed = True
                desktop_app_path = str(candidate)
                break
    else:
        user_app = Path(os.path.expanduser("~/.local/share/applications/antiagent.desktop"))
        if user_app.exists():
            desktop_app_installed = True
            desktop_app_path = str(user_app)

    # 6. Daemon Status
    daemon_running = False
    try:
        urllib.request.urlopen("http://127.0.0.1:4242/", timeout=0.5)
        daemon_running = True
    except Exception:
        daemon_running = False

    # 7. Antigravity Mode Compatibility info
    mode_recommendations = {
        "turbo_mode": {
            "name": "Turbo Mode (Always Proceed)",
            "supported": True,
            "recommended": True,
            "reason": (
                "AntiAgent intercepts tool calls BEFORE execution. Routine actions are approved automatically, "
                "while unsafe actions emit force_ask, which forces Antigravity to pause for your approval even in Turbo mode."
            ),
        },
        "request_review": {
            "name": "Request Review Mode",
            "supported": True,
            "recommended": False,
            "reason": "Standard interactive prompts; works seamlessly with AntiAgent's safety evaluations.",
        },
    }

    # 8. GitHub CLI & Auto-PR Monitoring
    from antiagent.engine.pr_monitor import check_gh_cli_status
    gh_info = check_gh_cli_status()

    return {
        "workspace_path": str(ws_resolved),
        "python": python_info,
        "global_hook": {
            "status": global_status,
            "path": str(global_hook_path),
            "active": global_status == "active",
        },
        "workspace_hook": {
            "status": ws_status,
            "path": str(ws_hook_path),
            "active": ws_status == "active",
        },
        "antigravity_sessions": {
            "count": total_session_count,
            "path": str(primary_brain_dir),
            "sources": {
                "desktop": {
                    "count": sources_summary.get("desktop", {}).get("count", 0) if isinstance(sources_summary.get("desktop"), dict) else (sources_summary.get("desktop") if isinstance(sources_summary.get("desktop"), int) else 0),
                    "available": sources_summary.get("desktop", {}).get("available", False) if isinstance(sources_summary.get("desktop"), dict) else bool(sources_summary.get("desktop")),
                },
                "ide": {
                    "count": sources_summary.get("ide", {}).get("count", 0) if isinstance(sources_summary.get("ide"), dict) else (sources_summary.get("ide") if isinstance(sources_summary.get("ide"), int) else 0),
                    "available": sources_summary.get("ide", {}).get("available", False) if isinstance(sources_summary.get("ide"), dict) else bool(sources_summary.get("ide")),
                },
                "cli": {
                    "count": sources_summary.get("cli", {}).get("count", 0) if isinstance(sources_summary.get("cli"), dict) else (sources_summary.get("cli") if isinstance(sources_summary.get("cli"), int) else 0),
                    "available": sources_summary.get("cli", {}).get("available", False) if isinstance(sources_summary.get("cli"), dict) else bool(sources_summary.get("cli")),
                },
            },
            "sources_detail": sources_summary,
        },
        "desktop_app": {
            "installed": desktop_app_installed,
            "path": desktop_app_path,
        },
        "daemon": {
            "running": daemon_running,
            "url": "http://127.0.0.1:4242",
        },
        "modes": mode_recommendations,
        "github_cli": gh_info,
    }


run_doctor_check = get_doctor_report

