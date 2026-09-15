"""Lightweight local web server for the AntiAgent dashboard."""

import json
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from antiagent.audit.logger import AuditLogger
from antiagent.cli import install_hook, uninstall_hook
from antiagent.config import (
    load_config,
    save_global_config,
    save_workspace_config,
)
from antiagent.constants import (
    PROFILE_AUTONOMOUS,
    PROFILE_BALANCED,
    PROFILE_PARANOID,
)
from antiagent.engine.evaluator import AntiAgentEvaluator


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests for the AntiAgent dashboard."""

    workspace_path: str = "."

    def log_message(self, format: str, *args: Any) -> None:
        # Keep terminal output clean unless debugging
        pass

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path

        if path in ("/", "/index.html"):
            self._serve_static_html()
        elif path == "/api/status":
            self._handle_api_status()
        elif path == "/api/audit":
            query = parse_qs(parsed_url.query)
            limit = int(query.get("limit", ["25"])[0])
            self._handle_api_audit(limit)
        elif path == "/api/doctor":
            self._handle_api_doctor()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        body = self._read_json_body()

        if path == "/api/install":
            scope = body.get("scope", "workspace")
            ws = body.get("workspace_path", self.workspace_path)
            is_global = scope == "global"
            install_hook(is_global=is_global, workspace_path=ws)
            self._send_json({"ok": True, "scope": scope})
        elif path == "/api/uninstall":
            scope = body.get("scope", "workspace")
            ws = body.get("workspace_path", self.workspace_path)
            is_global = scope == "global"
            uninstall_hook(is_global=is_global, workspace_path=ws)
            self._send_json({"ok": True, "scope": scope})
        elif path == "/api/install_app":
            self._handle_api_install_app(body)
        elif path == "/api/workspace":
            new_ws = body.get("path")
            if new_ws:
                p = Path(os.path.expanduser(new_ws)).resolve()
                p.mkdir(parents=True, exist_ok=True)
                DashboardRequestHandler.workspace_path = str(p)
                self._send_json({"ok": True, "workspace_path": str(p)})
            else:
                self._send_json({"ok": False, "error": "Path required"}, status=400)
        elif path == "/api/create_project":
            project_path = body.get("path")
            if not project_path:
                self._send_json({"ok": False, "error": "Project path is required"}, status=400)
                return
            p = Path(os.path.expanduser(project_path)).resolve()
            p.mkdir(parents=True, exist_ok=True)
            import subprocess
            if not (p / ".git").is_dir():
                try:
                    subprocess.run(["git", "init"], cwd=str(p), capture_output=True)
                except Exception:
                    pass
            install_hook(is_global=False, workspace_path=str(p))
            DashboardRequestHandler.workspace_path = str(p)
            self._send_json({"ok": True, "workspace_path": str(p)})
        elif path == "/api/config":
            self._handle_api_config(body)
        elif path == "/api/simulate":
            self._handle_api_simulate(body)
        elif path == "/api/clear_audit":
            cfg = load_config(self.workspace_path)
            logger = AuditLogger(cfg.audit_log_path)
            if logger.log_path.is_file():
                logger.log_path.write_text("", encoding="utf-8")
            self._send_json({"ok": True})
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_static_html(self) -> None:
        html_file = Path(__file__).parent / "assets" / "index.html"
        if not html_file.is_file():
            self.send_response(404)
            self.end_headers()
            return

        content = html_file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _handle_api_status(self) -> None:
        ws_hook = Path(self.workspace_path).resolve() / ".agents" / "hooks.json"
        global_hook = Path(os.path.expanduser("~/.gemini/config/hooks.json"))

        ws_active = False
        if ws_hook.is_file():
            try:
                data = json.loads(ws_hook.read_text(encoding="utf-8"))
                ws_active = "antiagent-guard" in data and data["antiagent-guard"].get("enabled", True)
            except Exception:
                pass

        global_active = False
        if global_hook.is_file():
            try:
                data = json.loads(global_hook.read_text(encoding="utf-8"))
                global_active = "antiagent-guard" in data and data["antiagent-guard"].get("enabled", True)
            except Exception:
                pass

        cfg = load_config(self.workspace_path)
        resp = {
            "workspace_path": str(Path(self.workspace_path).resolve()),
            "workspace_hook_active": ws_active,
            "global_hook_active": global_active,
            "profile": cfg.profile,
            "provider": cfg.provider,
            "model": cfg.model,
            "api_key": cfg.api_key or "",
            "has_api_key": bool(cfg.api_key or os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")),
            "endpoint_url": cfg.endpoint_url or "",
            "auto_approve_reads": cfg.auto_approve_reads,
            "auto_approve_dev_commands": cfg.auto_approve_dev_commands,
            "auto_review": cfg.auto_review,
            "audit_enabled": cfg.audit_enabled,
            "custom_allow_patterns": cfg.custom_allow_patterns,
            "custom_deny_patterns": cfg.custom_deny_patterns,
            "audit_log_path": cfg.audit_log_path,
        }
        self._send_json(resp)

    def _handle_api_audit(self, limit: int) -> None:
        cfg = load_config(self.workspace_path)
        logger = AuditLogger(cfg.audit_log_path)
        entries = logger.read_recent(limit=limit)
        self._send_json(entries)

    def _handle_api_config(self, body: Dict[str, Any]) -> None:
        scope = body.get("scope", "workspace")
        cfg = load_config(self.workspace_path)

        if "profile" in body:
            profile = body["profile"]
            if profile in (PROFILE_BALANCED, PROFILE_PARANOID, PROFILE_AUTONOMOUS):
                cfg.profile = profile
        if "provider" in body:
            cfg.provider = body["provider"]
        if "model" in body:
            cfg.model = body["model"]
        if "api_key" in body:
            cfg.api_key = body["api_key"]
        if "endpoint_url" in body:
            cfg.endpoint_url = body["endpoint_url"]
        if "auto_approve_reads" in body:
            cfg.auto_approve_reads = bool(body["auto_approve_reads"])
        if "auto_approve_dev_commands" in body:
            cfg.auto_approve_dev_commands = bool(body["auto_approve_dev_commands"])
        if "auto_review" in body:
            cfg.auto_review = bool(body["auto_review"])
        if "audit_enabled" in body:
            cfg.audit_enabled = bool(body["audit_enabled"])
        if "custom_allow_patterns" in body:
            patterns = body["custom_allow_patterns"]
            if isinstance(patterns, str):
                cfg.custom_allow_patterns = [p.strip() for p in patterns.splitlines() if p.strip()]
            elif isinstance(patterns, list):
                cfg.custom_allow_patterns = [str(p).strip() for p in patterns if str(p).strip()]
        if "custom_deny_patterns" in body:
            patterns = body["custom_deny_patterns"]
            if isinstance(patterns, str):
                cfg.custom_deny_patterns = [p.strip() for p in patterns.splitlines() if p.strip()]
            elif isinstance(patterns, list):
                cfg.custom_deny_patterns = [str(p).strip() for p in patterns if str(p).strip()]

        if scope == "global":
            save_global_config(cfg)
        else:
            save_workspace_config(cfg, self.workspace_path)

        self._send_json({"ok": True, "scope": scope, "config": cfg.to_dict()})

    def _handle_api_simulate(self, body: Dict[str, Any]) -> None:
        tool = body.get("tool", "run_command")
        args = body.get("args", {})
        user_prompt = body.get("user_prompt", "")
        goal = body.get("goal", "")

        from antiagent.engine.context_extractor import TaskContext
        context = None
        if user_prompt or goal:
            context = TaskContext(user_prompt=user_prompt, primary_goal=goal or user_prompt)

        cfg = load_config(self.workspace_path)
        evaluator = AntiAgentEvaluator(cfg, workspace_paths=[str(Path(self.workspace_path).resolve())])
        result = evaluator.evaluate(tool, args, context=context)
        self._send_json({"decision": result.decision, "reason": result.reason})

    def _handle_api_doctor(self) -> None:
        from antiagent.engine.doctor import get_doctor_report
        report = get_doctor_report(self.workspace_path)
        self._send_json(report)

    def _handle_api_install_app(self, body: Dict[str, Any]) -> None:
        to_global = bool(body.get("global", False))
        try:
            from antiagent.desktop.builder import install_app
            app_path = install_app(to_global=to_global)
            self._send_json({"ok": True, "app_path": str(app_path)})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def _read_json_body(self) -> Dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            return {}
        try:
            raw = self.rfile.read(content_length).decode("utf-8")
            return json.loads(raw)
        except Exception:
            return {}

    def _send_json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run_dashboard(
    host: str = "127.0.0.1",
    port: int = 4242,
    open_browser: bool = True,
    workspace_path: str = ".",
) -> None:
    """Launch the AntiAgent local dashboard server."""
    DashboardRequestHandler.workspace_path = workspace_path
    server = ThreadingHTTPServer((host, port), DashboardRequestHandler)
    url = f"http://{host}:{port}"
    print(f"🛡️  AntiAgent Dashboard running at: {url}")
    print("   Press Ctrl+C to stop the dashboard.")

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Stopping AntiAgent Dashboard.")
        server.server_close()
