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

    # 4. Antigravity Brain Sessions
    brain_dir = Path(os.path.expanduser("~/.gemini/antigravity/brain"))
    session_count = 0
    if brain_dir.is_dir():
        session_count = len([d for d in brain_dir.iterdir() if d.is_dir()])

    # 5. Native Desktop App
    user_app = Path(os.path.expanduser("~/Applications/AntiAgent.app"))
    sys_app = Path("/Applications/AntiAgent.app")
    desktop_app_installed = user_app.exists() or sys_app.exists()
    desktop_app_path = str(user_app if user_app.exists() else sys_app) if desktop_app_installed else None

    # 6. Daemon Status
    daemon_running = False
    try:
        urllib.request.urlopen("http://127.0.0.1:4242/api/status", timeout=0.5)
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
            "count": session_count,
            "path": str(brain_dir),
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
    }
