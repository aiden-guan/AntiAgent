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
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        body = self._read_json_body()

        if path == "/api/install":
            scope = body.get("scope", "workspace")
            is_global = scope == "global"
            install_hook(is_global=is_global, workspace_path=self.workspace_path)
            self._send_json({"ok": True, "scope": scope})
        elif path == "/api/uninstall":
            scope = body.get("scope", "workspace")
            is_global = scope == "global"
            uninstall_hook(is_global=is_global, workspace_path=self.workspace_path)
            self._send_json({"ok": True, "scope": scope})
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
            "auto_approve_reads": cfg.auto_approve_reads,
            "audit_log_path": cfg.audit_log_path,
        }
        self._send_json(resp)

    def _handle_api_audit(self, limit: int) -> None:
        cfg = load_config(self.workspace_path)
        logger = AuditLogger(cfg.audit_log_path)
        entries = logger.read_recent(limit=limit)
        self._send_json(entries)

    def _handle_api_config(self, body: Dict[str, Any]) -> None:
        cfg = load_config(self.workspace_path)
        if "profile" in body:
            profile = body["profile"]
            if profile in (PROFILE_BALANCED, PROFILE_PARANOID, PROFILE_AUTONOMOUS):
                cfg.profile = profile
        if "provider" in body:
            cfg.provider = body["provider"]
        if "model" in body:
            cfg.model = body["model"]

        save_workspace_config(cfg, self.workspace_path)
        self._send_json({"ok": True, "config": cfg.to_dict()})

    def _handle_api_simulate(self, body: Dict[str, Any]) -> None:
        tool = body.get("tool", "run_command")
        args = body.get("args", {})
        cfg = load_config(self.workspace_path)
        evaluator = AntiAgentEvaluator(cfg, workspace_paths=[str(Path(self.workspace_path).resolve())])
        result = evaluator.evaluate(tool, args)
        self._send_json({"decision": result.decision, "reason": result.reason})

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
