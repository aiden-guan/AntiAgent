"""Lightweight local web server for the AntiAgent dashboard."""

import json
import os
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from antiagent import __version__
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
from antiagent.updater import (
    check_for_updates,
    global_downloader,
    global_pip_upgrader,
    is_safe_download_url,
    open_downloaded_file,
    reveal_in_file_manager,
)


def get_default_workspace_path() -> str:
    cwd = os.getcwd()
    if cwd and cwd != "/":
        return str(Path(cwd).resolve())
    return str(Path.home())


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests for the AntiAgent dashboard."""

    workspace_path: str = get_default_workspace_path()

    def log_message(self, format: str, *args: Any) -> None:
        # Keep terminal output clean unless debugging
        pass

    def _add_security_headers(self) -> None:
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self';")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

    def _validate_host(self) -> bool:
        host = self.headers.get("Host", "").split(":")[0].lower()
        if host in ("127.0.0.1", "localhost"):
            return True
        self.send_response(403)
        self.send_header("Content-Type", "text/plain")
        self._add_security_headers()
        self.end_headers()
        self.wfile.write(b"Forbidden: Invalid Host header.")
        return False

    def _validate_csrf(self) -> bool:
        fetch_site = self.headers.get("Sec-Fetch-Site", "").lower()
        if fetch_site == "cross-site":
            self._send_forbidden("Cross-site requests not allowed.")
            return False

        origin = self.headers.get("Origin", "")
        if origin:
            parsed_origin = urlparse(origin)
            origin_host = (parsed_origin.hostname or "").lower()
            if origin != "null" and origin_host not in ("127.0.0.1", "localhost"):
                self._send_forbidden("Cross-origin request blocked.")
                return False

        referer = self.headers.get("Referer", "")
        if referer:
            parsed_ref = urlparse(referer)
            ref_host = (parsed_ref.hostname or "").lower()
            if ref_host not in ("127.0.0.1", "localhost"):
                self._send_forbidden("Invalid request referer.")
                return False

        return True

    def _send_forbidden(self, msg: str) -> None:
        payload = json.dumps({"ok": False, "error": msg}).encode("utf-8")
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._add_security_headers()
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if not self._validate_host():
            return

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
        elif path == "/api/onboarding":
            cfg = load_config(self.workspace_path)
            self._send_json({"onboarding_completed": cfg.onboarding_completed})
        elif path == "/api/update/check":
            query = parse_qs(parsed_url.query)
            repo = query.get("repo", [None])[0]
            if repo:
                res = check_for_updates(repo=repo)
            else:
                res = check_for_updates()
            self._send_json(res)
        elif path == "/api/update/status":
            self._send_json(global_downloader.get_status())
        elif path == "/api/update/pip_status":
            self._send_json(global_pip_upgrader.get_status())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        if not self._validate_host():
            return
        if not self._validate_csrf():
            return

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
        elif path == "/api/onboarding":
            completed = body.get("completed", True)
            cfg = load_config(self.workspace_path)
            cfg.onboarding_completed = bool(completed)
            save_global_config(cfg)
            self._send_json({"ok": True, "onboarding_completed": cfg.onboarding_completed})
        elif path == "/api/choose_folder":
            self._handle_api_choose_folder()
        elif path == "/api/update/download":
            url = body.get("download_url")
            filename = body.get("filename")
            if not url:
                check_res = check_for_updates()
                rec = check_res.get("recommended_asset")
                if rec and rec.get("download_url"):
                    url = rec["download_url"]
                    filename = rec.get("name", filename)
                else:
                    if sys.platform == "darwin":
                        filename = filename or "AntiAgent.dmg"
                    elif sys.platform == "win32":
                        filename = filename or "AntiAgent-Windows.zip"
                    else:
                        filename = filename or "AntiAgent.zip"
                    url = f"https://github.com/aiden-guan/AntiAgent/releases/latest/download/{filename}"

            if not is_safe_download_url(url):
                self._send_json({"ok": False, "error": "Security: Untrusted download URL. Only official GitHub releases are allowed."}, status=400)
                return

            started = global_downloader.start_download(url, filename=filename)
            self._send_json({"ok": started, "status": global_downloader.get_status()})
        elif path == "/api/update/cancel":
            cancelled = global_downloader.cancel()
            self._send_json({"ok": cancelled, "status": global_downloader.get_status()})
        elif path == "/api/update/open":
            file_path = body.get("path") or global_downloader.dest_path
            opened = open_downloaded_file(file_path)
            self._send_json({"ok": opened, "path": file_path})
        elif path == "/api/update/reveal":
            file_path = body.get("path") or global_downloader.dest_path
            revealed = reveal_in_file_manager(file_path)
            self._send_json({"ok": revealed, "path": file_path})
        elif path == "/api/update/pip":
            started = global_pip_upgrader.start_upgrade()
            self._send_json({"ok": started, "status": global_pip_upgrader.get_status()})
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
        self._add_security_headers()
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
        raw_key = cfg.api_key or ""
        masked_key = ""
        if raw_key:
            masked_key = f"{raw_key[:4]}••••{raw_key[-4:]}" if len(raw_key) > 8 else "••••••••"

        resp = {
            "version": __version__,
            "workspace_path": str(Path(self.workspace_path).resolve()),
            "workspace_hook_active": ws_active,
            "global_hook_active": global_active,
            "profile": cfg.profile,
            "provider": cfg.provider,
            "model": cfg.model,
            "api_key": masked_key,
            "has_api_key": bool(cfg.api_key or os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")),
            "endpoint_url": cfg.endpoint_url or "",
            "auto_approve_reads": cfg.auto_approve_reads,
            "auto_approve_dev_commands": cfg.auto_approve_dev_commands,
            "auto_review": cfg.auto_review,
            "audit_enabled": cfg.audit_enabled,
            "custom_allow_patterns": cfg.custom_allow_patterns,
            "custom_deny_patterns": cfg.custom_deny_patterns,
            "audit_log_path": cfg.audit_log_path,
            "onboarding_completed": cfg.onboarding_completed,
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

        if "onboarding_completed" in body:
            cfg.onboarding_completed = bool(body["onboarding_completed"])
        if "profile" in body:
            profile = body["profile"]
            if profile in (PROFILE_BALANCED, PROFILE_PARANOID, PROFILE_AUTONOMOUS):
                cfg.profile = profile
        if "provider" in body:
            cfg.provider = body["provider"]
        if "model" in body:
            cfg.model = body["model"]
        if "api_key" in body:
            new_key = str(body["api_key"]).strip()
            if new_key and "•" not in new_key:
                cfg.api_key = new_key
            elif new_key == "":
                cfg.api_key = None
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

        actual_scope = scope
        if scope == "global":
            save_global_config(cfg)
        else:
            try:
                ws_p = Path(self.workspace_path).resolve()
                if str(ws_p) in ("/", str(Path.home())) or not os.access(str(ws_p), os.W_OK):
                    save_global_config(cfg)
                    actual_scope = "global"
                else:
                    save_workspace_config(cfg, self.workspace_path)
            except Exception:
                save_global_config(cfg)
                actual_scope = "global"

        self._send_json({"ok": True, "scope": actual_scope, "config": cfg.to_dict()})

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

    def _handle_api_choose_folder(self) -> None:
        """Open native folder chooser dialog (macOS Finder, Windows Explorer, or Tkinter)."""
        chosen_path = ""
        try:
            if sys.platform == "darwin":
                script = 'POSIX path of (choose folder with prompt "Select Project Folder for AntiAgent:")'
                res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
                if res.returncode == 0:
                    chosen_path = res.stdout.strip().rstrip("/")
                elif "User canceled" in res.stderr or "-128" in res.stderr:
                    self._send_json({"ok": False, "cancelled": True})
                    return
            elif sys.platform == "win32":
                ps_script = (
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                    "$f.Description = 'Select Project Folder for AntiAgent:'; "
                    "$f.ShowNewFolderButton = $true; "
                    "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $f.SelectedPath }"
                )
                res = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_script],
                    capture_output=True,
                    text=True,
                )
                if res.returncode == 0:
                    chosen_path = res.stdout.strip()

            if not chosen_path:
                # Universal fallback via tkinter if available
                try:
                    import tkinter as tk
                    from tkinter import filedialog

                    root = tk.Tk()
                    root.withdraw()
                    root.attributes("-topmost", True)
                    chosen_path = filedialog.askdirectory(title="Select Project Folder for AntiAgent:")
                    root.destroy()
                except Exception:
                    pass

            if chosen_path:
                p = Path(os.path.expanduser(chosen_path)).resolve()
                DashboardRequestHandler.workspace_path = str(p)
                self._send_json({"ok": True, "workspace_path": str(p)})
            else:
                self._send_json({"ok": False, "cancelled": True})
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
        self._add_security_headers()
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
