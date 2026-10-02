from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
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
from antiagent.engine.conversations import (
    AmbiguousConversationError,
    ConversationStore,
    VALID_SOURCES,
    detect_agy_capabilities,
    get_conversation_store,
    validate_conversation_id,
)
from antiagent.engine.evaluator import AntiAgentEvaluator
from antiagent.engine.pr_monitor import (
    check_gh_cli_status,
    get_pr_checks,
    get_pr_details,
    get_pr_failure_logs,
    global_pr_manager,
    list_pull_requests,
    merge_pr,
)
from antiagent.engine.remote_sessions import (
    RemoteHost,
    RemoteSessionManager,
)
import argparse
import uuid

from antiagent.runtime import (
    DashboardLaunchContext,
    InstallMode,
    find_active_macos_bundle,
    find_installed_macos_bundles,
    get_current_launch_context,
    get_runtime_install_info,
    is_desktop_app_running,
    set_current_launch_context,
)
from antiagent.updater import (
    check_and_complete_pending_update,
    check_for_updates,
    global_downloader,
    global_pip_upgrader,
    global_self_updater,
    is_safe_download_url,
    load_pending_update,
    open_downloaded_file,
    reveal_in_file_manager,
)

SERVER_INSTANCE_ID = uuid.uuid4().hex

# Automatically complete pending update diagnostics upon process startup
check_and_complete_pending_update(__version__)

global_remote_manager = RemoteSessionManager()

# Backward compatibility aliases
find_running_or_installed_desktop_app = find_active_macos_bundle
is_desktop_app_currently_running = is_desktop_app_running


def get_default_workspace_path() -> str:
    cwd = os.getcwd()
    if cwd and cwd != "/":
        return str(Path(cwd).resolve())
    return str(Path.home())


def parse_dashboard_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse dashboard command line arguments with consistent defaults."""
    parser = argparse.ArgumentParser(description="Launch AntiAgent visual dashboard")
    parser.add_argument("--port", type=int, default=4242, help="Port to listen on (default: 4242)")
    parser.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    parser.add_argument("--no-open", action="store_true", help="Do not automatically open browser")
    parser.add_argument("--workspace", default=".", help="Workspace path")
    return parser.parse_args(argv)


def _relaunch_desktop_worker(
    app_path: Path,
    staged_path: Optional[Path] = None,
    target_version: Optional[str] = None,
) -> None:
    time.sleep(0.3)
    if DashboardRequestHandler.server_instance:
        try:
            DashboardRequestHandler.server_instance.server_close()
        except Exception:
            pass

    if staged_path and staged_path.exists():
        backup_app = app_path.parent / f"{app_path.name}.backup"
        cmd = [
            sys.executable or "python3",
            "-m", "antiagent.desktop.relauncher",
            "--target-app", str(app_path),
            "--staged-app", str(staged_path),
            "--backup-app", str(backup_app),
            "--old-pid", str(os.getpid()),
            "--parent-pid", str(os.getppid()),
            "--target-version", str(target_version or __version__),
        ]
        try:
            subprocess.Popen(cmd, start_new_session=True, close_fds=True)
        except Exception:
            pass
    else:
        try:
            subprocess.run(["osascript", "-e", 'quit app "AntiAgent"'], capture_output=True, timeout=3.0)
        except Exception:
            pass
        time.sleep(0.5)
        try:
            subprocess.Popen(["open", str(app_path)], start_new_session=True)
        except Exception:
            pass
    os._exit(0)


def _restart_cli_worker(ctx: Optional[DashboardLaunchContext] = None) -> None:
    time.sleep(0.3)
    if DashboardRequestHandler.server_instance:
        try:
            DashboardRequestHandler.server_instance.server_close()
        except Exception:
            pass

    launch_ctx = ctx or get_current_launch_context()
    cmd = launch_ctx.to_argv()
    try:
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
            subprocess.Popen(cmd, creationflags=creationflags, close_fds=True)
        else:
            subprocess.Popen(cmd, start_new_session=True, close_fds=True)
    except Exception:
        pass
    os._exit(0)


class DashboardHTTPServer(ThreadingHTTPServer):
    """Threading server that refuses to share a port with another listener.

    On Windows SO_REUSEADDR lets several processes bind the same port, so a stale
    daemon from an older install would silently keep answering requests. Use
    SO_EXCLUSIVEADDRUSE there so the second bind fails with "address in use".
    """

    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if sys.platform == "win32":
            import socket

            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if exclusive is not None:
                self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests for the AntiAgent dashboard."""

    workspace_path: str = get_default_workspace_path()
    server_instance: Optional[DashboardHTTPServer] = None
    launch_context: Optional[DashboardLaunchContext] = None

    def log_message(self, format: str, *args: Any) -> None:
        # Keep terminal output clean unless debugging
        pass

    def _add_security_headers(self) -> None:
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self';")
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
            include_tool_calls_param = query.get("include_tool_calls", [None])[0]
            self._handle_api_audit(limit, include_tool_calls_param)
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
        elif path == "/api/update/self_update_status":
            self._send_json(global_self_updater.get_status())
        elif path == "/api/update/pip_status":
            self._send_json(global_pip_upgrader.get_status())
        elif path == "/api/pr/status":
            query = parse_qs(parsed_url.query)
            pr_id = query.get("pr", [None])[0]
            res = get_pr_checks(pr_identifier=pr_id, workspace_dir=self.workspace_path)
            self._send_json(res)
        elif path == "/api/pr/details":
            query = parse_qs(parsed_url.query)
            pr_id = query.get("pr", [None])[0]
            res = get_pr_details(pr_identifier=pr_id, workspace_dir=self.workspace_path)
            self._send_json(res)
        elif path == "/api/pr/failures":
            query = parse_qs(parsed_url.query)
            pr_id = query.get("pr", [None])[0]
            res = get_pr_failure_logs(pr_identifier=pr_id, workspace_dir=self.workspace_path)
            self._send_json(res)
        elif path == "/api/pr/list":
            query = parse_qs(parsed_url.query)
            limit = int(query.get("limit", ["15"])[0])
            res = list_pull_requests(workspace_dir=self.workspace_path, limit=limit)
            self._send_json(res)
        elif path == "/api/pr/active_monitors":
            self._send_json({"ok": True, "monitors": global_pr_manager.list_monitors()})
        elif path == "/api/pr/gh_status":
            self._send_json(check_gh_cli_status())
        elif path == "/api/remotes":
            query = parse_qs(parsed_url.query)
            force_probe = query.get("probe", ["false"])[0].lower() in ("true", "1", "yes") or query.get("refresh", ["false"])[0].lower() in ("true", "1", "yes")
            self._handle_api_remotes_list(force_probe=force_probe)
        elif path == "/api/remotes/status":
            query = parse_qs(parsed_url.query)
            name = query.get("name", [None])[0]
            refresh = query.get("refresh", ["false"])[0].lower() in ("true", "1", "yes")
            self._handle_api_remotes_status(name, refresh)
        elif path == "/api/remotes/doctor":
            query = parse_qs(parsed_url.query)
            name = query.get("name", [None])[0]
            self._handle_api_remotes_doctor(name)
        elif path == "/api/conversations":
            query = parse_qs(parsed_url.query)
            self._handle_api_conversations_list(query)
        elif path == "/api/conversations/detail":
            query = parse_qs(parsed_url.query)
            self._handle_api_conversations_detail(query)
        elif path == "/api/conversations/context":
            query = parse_qs(parsed_url.query)
            self._handle_api_conversations_context(query)
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
        elif path == "/api/open-url":
            self._handle_api_open_url(body)
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
        elif path == "/api/update/self_update":
            force = body.get("force", False)
            version = body.get("version")
            started = global_self_updater.start_update(force=force, version=version)
            self._send_json({"ok": started, "status": global_self_updater.get_status()})
        elif path == "/api/update/self_update_cancel":
            cancelled = global_self_updater.cancel()
            self._send_json({"ok": cancelled, "status": global_self_updater.get_status()})
        elif path == "/api/update/self_update_reset":
            global_self_updater.reset()
            self._send_json({"ok": True, "status": global_self_updater.get_status()})
        elif path in ("/api/update/restart", "/api/update/relaunch_app"):
            self._handle_api_restart(force_desktop=(path == "/api/update/relaunch_app"))
        elif path == "/api/pr/monitor":
            pr_id = body.get("pr")
            cfg = load_config(self.workspace_path)
            interval = body.get("interval") or cfg.pr_monitor_interval
            auto_merge = body.get("auto_merge") if "auto_merge" in body else cfg.pr_monitor_auto_merge
            monitor = global_pr_manager.start_monitor(
                pr_identifier=pr_id,
                workspace_dir=self.workspace_path,
                interval=interval,
                auto_merge=auto_merge,
            )
            self._send_json({
                "ok": True,
                "is_running": monitor.is_running,
                "pr_identifier": monitor.pr_identifier,
                "interval": monitor.interval,
                "auto_merge": monitor.auto_merge,
            })
        elif path == "/api/pr/stop_monitor":
            pr_id = body.get("pr")
            stopped = global_pr_manager.stop_monitor(pr_identifier=pr_id, workspace_dir=self.workspace_path)
            self._send_json({"ok": stopped})
        elif path == "/api/pr/merge":
            pr_id = body.get("pr")
            auto = body.get("auto", False)
            method = body.get("method", "squash")
            res = merge_pr(pr_identifier=pr_id, workspace_dir=self.workspace_path, auto=auto, method=method)
            self._send_json(res)
        elif path == "/api/pr/config":
            cfg = load_config(self.workspace_path)
            if "auto_pr_monitor" in body:
                cfg.auto_pr_monitor = bool(body["auto_pr_monitor"])
            if "pr_monitor_auto_merge" in body:
                cfg.pr_monitor_auto_merge = bool(body["pr_monitor_auto_merge"])
            if "pr_monitor_interval" in body:
                try:
                    cfg.pr_monitor_interval = max(3, int(body["pr_monitor_interval"]))
                except (ValueError, TypeError):
                    pass
            scope = body.get("scope", "global")
            if scope == "global":
                save_global_config(cfg)
            else:
                save_workspace_config(cfg, self.workspace_path)
            self._send_json({
                "ok": True,
                "auto_pr_monitor": cfg.auto_pr_monitor,
                "pr_monitor_auto_merge": cfg.pr_monitor_auto_merge,
                "pr_monitor_interval": cfg.pr_monitor_interval,
            })
        elif path == "/api/remotes/add":
            self._handle_api_remotes_add(body)
        elif path == "/api/remotes/remove":
            self._handle_api_remotes_remove(body)
        elif path == "/api/remotes/test":
            self._handle_api_remotes_test(body)
        elif path == "/api/remotes/start":
            self._handle_api_remotes_start(body)
        elif path == "/api/remotes/stop":
            self._handle_api_remotes_stop(body)
        elif path == "/api/remotes/protect":
            self._handle_api_remotes_protect(body)
        elif path == "/api/conversations/resume":
            self._handle_api_conversations_resume(body)
        elif path == "/api/conversations/open":
            self._handle_api_conversations_open(body)
        elif path == "/api/conversations/export":
            self._handle_api_conversations_export(body)
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_static_html(self) -> None:
        html_file = Path(__file__).parent / "assets" / "index.html"
        if not html_file.is_file():
            body = (
                "<!doctype html><meta charset='utf-8'><title>AntiAgent</title>"
                "<body style='font-family:sans-serif;max-width:40em;margin:4em auto'>"
                "<h1>Dashboard assets are missing</h1>"
                f"<p>AntiAgent {__version__} is running, but <code>{html_file}</code> "
                "was not found. Reinstall with <code>pip install --upgrade --force-reinstall "
                "https://github.com/aiden-guan/AntiAgent/archive/refs/heads/main.zip</code> "
                "and restart any running AntiAgent process.</p></body>"
            ).encode("utf-8")
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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

        runtime_info = get_runtime_install_info()
        resp = {
            "version": __version__,
            "server_instance_id": SERVER_INSTANCE_ID,
            "install_mode": runtime_info.install_mode.value,
            "current_executable": runtime_info.current_executable,
            "package_path": runtime_info.package_path,
            "macos_bundle_path": runtime_info.macos_bundle_path,
            "is_site_packages": runtime_info.is_site_packages,
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
            "auto_pr_monitor": cfg.auto_pr_monitor,
            "pr_monitor_auto_merge": cfg.pr_monitor_auto_merge,
            "pr_monitor_interval": cfg.pr_monitor_interval,
            "pr_monitor_auto_fix": cfg.pr_monitor_auto_fix,
            "custom_allow_patterns": cfg.custom_allow_patterns,
            "custom_deny_patterns": cfg.custom_deny_patterns,
            "audit_log_path": cfg.audit_log_path,
            "audit_include_tool_calls": cfg.audit_include_tool_calls,
            "onboarding_completed": cfg.onboarding_completed,
        }
        self._send_json(resp)

    def _handle_api_audit(self, limit: int, include_tool_calls_param: Optional[str] = None) -> None:
        cfg = load_config(self.workspace_path)
        if include_tool_calls_param is not None:
            include_tool_calls = include_tool_calls_param.lower() in ("true", "1", "yes")
        else:
            include_tool_calls = cfg.audit_include_tool_calls
        logger = AuditLogger(cfg.audit_log_path)
        entries = logger.read_recent(limit=limit, include_tool_calls=include_tool_calls)
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
        if "audit_include_tool_calls" in body:
            cfg.audit_include_tool_calls = bool(body["audit_include_tool_calls"])
        if "auto_pr_monitor" in body:
            cfg.auto_pr_monitor = bool(body["auto_pr_monitor"])
        if "pr_monitor_auto_merge" in body:
            cfg.pr_monitor_auto_merge = bool(body["pr_monitor_auto_merge"])
        if "pr_monitor_interval" in body:
            try:
                cfg.pr_monitor_interval = max(3, int(body["pr_monitor_interval"]))
            except (ValueError, TypeError):
                pass
        if "pr_monitor_auto_fix" in body:
            cfg.pr_monitor_auto_fix = bool(body["pr_monitor_auto_fix"])
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
            elif sys.platform.startswith("linux"):
                try:
                    res = subprocess.run(
                        ["zenity", "--file-selection", "--directory", "--title=Select Project Folder for AntiAgent:"],
                        capture_output=True,
                        text=True,
                    )
                    if res.returncode == 0:
                        chosen_path = res.stdout.strip()
                except Exception:
                    pass

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

    def _handle_api_open_url(self, body: Dict[str, Any]) -> None:
        raw_url = (body.get("url") or "").strip()
        if not raw_url:
            self._send_json({"ok": False, "error": "Missing URL"}, status=400)
            return

        parsed = urlparse(raw_url)
        if parsed.scheme.lower() not in ("http", "https", "mailto"):
            self._send_json({"ok": False, "error": "Invalid URL scheme"}, status=400)
            return

        try:
            webbrowser.open(raw_url)
            self._send_json({"ok": True, "url": raw_url})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def _handle_api_restart(self, force_desktop: bool = False) -> None:
        runtime_info = get_runtime_install_info()
        pending = load_pending_update()
        staged_path = getattr(global_self_updater, "staged_bundle_path", None)
        if not staged_path and pending:
            staged_path = pending.get("staged_path")
        staged_path_obj = Path(staged_path) if staged_path else None

        target_ver = (
            getattr(global_self_updater, "target_version", None)
            or (pending and pending.get("target_version"))
            or __version__
        )

        is_macos_desktop = (runtime_info.install_mode == InstallMode.MACOS_BUNDLE) or force_desktop or is_desktop_app_running()
        active_bundle = find_active_macos_bundle(inspect_processes=True)
        if not active_bundle and pending and pending.get("app_path"):
            cand = Path(pending["app_path"])
            if cand.is_dir():
                active_bundle = cand
        if not active_bundle and is_macos_desktop:
            installed = find_installed_macos_bundles()
            if installed:
                active_bundle = installed[0]

        is_bundle_mode = bool(is_macos_desktop and active_bundle)
        mode_str = "macos_bundle" if is_bundle_mode else runtime_info.install_mode.value

        resp_data = {
            "ok": True,
            "mode": mode_str,
            "target_version": target_ver,
            "expected_app_path": str(active_bundle) if is_bundle_mode else None,
            "app_path": str(active_bundle) if is_bundle_mode else None,
            "desktop_relaunch": is_bundle_mode,
            "server_instance_id": SERVER_INSTANCE_ID,
            "message": (
                "Relaunching AntiAgent desktop app..."
                if is_bundle_mode
                else "Dashboard server restarting..."
            ),
        }
        self._send_json(resp_data)

        if os.environ.get("ANTIAGENT_TESTING") == "1":
            return

        if is_bundle_mode and active_bundle:
            threading.Thread(
                target=_relaunch_desktop_worker,
                args=(active_bundle, staged_path_obj, target_ver),
                daemon=True,
            ).start()
        else:
            threading.Thread(
                target=_restart_cli_worker,
                args=(self.launch_context or get_current_launch_context(),),
                daemon=True,
            ).start()

    def _handle_api_remotes_list(self, force_probe: bool = False) -> None:
        hosts = global_remote_manager.registry.list_hosts()
        probes = global_remote_manager.probe_all(bypass_cache=force_probe)
        probe_map = {p.name: p.to_dict() for p in probes}
        items = []
        for h in hosts:
            h_dict = h.to_dict()
            h_dict["probe"] = probe_map.get(h.name)
            h_dict["default_workspace"] = h.workspace
            items.append(h_dict)
        self._send_json({"ok": True, "hosts": items, "machines": items})

    def _handle_api_remotes_status(self, name: Optional[str], refresh: bool) -> None:
        if not name:
            self._send_json({"ok": False, "error": "Remote machine name is required."}, status=400)
            return
        host = global_remote_manager.registry.get_host(name)
        if not host:
            self._send_json({"ok": False, "error": f"Machine '{name}' not found."}, status=404)
            return
        probe = global_remote_manager.probe(host, bypass_cache=refresh)
        self._send_json({"ok": probe.ok, "probe": probe.to_dict()})

    def _handle_api_remotes_doctor(self, name: Optional[str]) -> None:
        if not name:
            self._send_json({"ok": False, "error": "Remote machine name is required."}, status=400)
            return
        res = global_remote_manager.doctor(name)
        status = 200 if res.get("ok") else 400
        self._send_json(res, status=status)

    def _handle_api_remotes_add(self, body: Dict[str, Any]) -> None:
        name = (body.get("name") or "").strip()
        ssh_host = (body.get("ssh_host") or "").strip()
        if not name or not ssh_host:
            self._send_json({"ok": False, "error": "Display name and SSH host are required."}, status=400)
            return
        try:
            port = int(body["port"]) if body.get("port") else None
            ws = (body.get("workspace") or body.get("default_workspace") or "").strip() or None
            host = RemoteHost(
                name=name,
                ssh_host=ssh_host,
                hostname=(body.get("hostname") or "").strip() or None,
                user=(body.get("user") or "").strip() or None,
                port=port,
                identity_file=(body.get("identity_file") or "").strip() or None,
                remote_os=(body.get("remote_os") or "auto").strip(),
                workspace=ws,
                antigravity_name=(body.get("antigravity_name") or "").strip() or None,
                agy_path=(body.get("agy_path") or "").strip() or None,
            )
            existing = global_remote_manager.registry.get_host(name)
            if existing:
                saved = global_remote_manager.registry.update_host(host)
            else:
                saved = global_remote_manager.registry.add_host(host)
            global_remote_manager.clear_cache(name)
            global_remote_manager._log_audit(
                "remote_machine_add" if not existing else "remote_machine_update",
                name,
                True,
                f"Remote host '{name}' ({ssh_host}) configured.",
            )
            self._send_json({"ok": True, "host": saved.to_dict()})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=400)

    def _handle_api_remotes_remove(self, body: Dict[str, Any]) -> None:
        name = (body.get("name") or "").strip()
        if not name:
            self._send_json({"ok": False, "error": "Machine name required."}, status=400)
            return
        removed = global_remote_manager.registry.remove_host(name)
        global_remote_manager.clear_cache(name)
        if removed:
            global_remote_manager._log_audit(
                "remote_machine_remove",
                name,
                True,
                f"Remote host '{name}' removed from registry.",
            )
            self._send_json({"ok": True})
        else:
            self._send_json({"ok": False, "error": f"Machine '{name}' not found."}, status=404)

    def _handle_api_remotes_test(self, body: Dict[str, Any]) -> None:
        name = (body.get("name") or "").strip()
        if name:
            res = global_remote_manager.test_connection(name)
        else:
            ssh_host = (body.get("ssh_host") or "").strip()
            if not ssh_host:
                self._send_json({"ok": False, "error": "Machine name or SSH host required to test."}, status=400)
                return
            try:
                port = int(body["port"]) if body.get("port") else None
                temp_host = RemoteHost(
                    name="test-target",
                    ssh_host=ssh_host,
                    hostname=(body.get("hostname") or "").strip() or None,
                    user=(body.get("user") or "").strip() or None,
                    port=port,
                    identity_file=(body.get("identity_file") or "").strip() or None,
                    remote_os=(body.get("remote_os") or "auto").strip(),
                )
                res = global_remote_manager.test_connection(temp_host)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
                return
        status = 200 if res.get("ok") else 400
        payload = dict(res)
        payload["probe"] = dict(res)
        self._send_json(payload, status=status)

    def _handle_api_remotes_start(self, body: Dict[str, Any]) -> None:
        name = (body.get("name") or "").strip()
        if not name:
            self._send_json({"ok": False, "error": "Machine name required."}, status=400)
            return
        instance_name = (body.get("instance_name") or "").strip() or None
        workspace = (body.get("workspace") or "").strip() or None
        res = global_remote_manager.start_remote_control(name, instance_name=instance_name, workspace=workspace)
        status = 200 if res.get("ok") else 400
        self._send_json(res, status=status)

    def _handle_api_remotes_stop(self, body: Dict[str, Any]) -> None:
        name = (body.get("name") or "").strip()
        if not name:
            self._send_json({"ok": False, "error": "Machine name required."}, status=400)
            return
        res = global_remote_manager.stop_remote_control(name)
        status = 200 if res.get("ok") else 400
        self._send_json(res, status=status)

    def _handle_api_remotes_protect(self, body: Dict[str, Any]) -> None:
        name = (body.get("name") or "").strip()
        if not name:
            self._send_json({"ok": False, "error": "Machine name required."}, status=400)
            return
        res = global_remote_manager.protect_remote(name)
        status = 200 if res.get("ok") else 400
        self._send_json(res, status=status)

    def _handle_api_conversations_list(self, query: Dict[str, List[str]]) -> None:
        source = query.get("source", ["all"])[0]
        if source not in ("all", "desktop", "ide", "cli"):
            self._send_json({"ok": False, "error": f"Invalid source '{source}'. Must be all, desktop, ide, or cli."}, status=400)
            return

        search = query.get("q", query.get("search", [None]))[0]
        workspace = query.get("workspace", [None])[0]
        try:
            limit = max(1, min(100, int(query.get("limit", ["25"])[0])))
            offset = max(0, int(query.get("offset", ["0"])[0]))
        except (ValueError, TypeError):
            self._send_json({"ok": False, "error": "Invalid limit or offset parameter."}, status=400)
            return

        refresh = query.get("refresh", ["false"])[0].lower() in ("true", "1", "yes")

        store = get_conversation_store()
        try:
            convs, total = store.list_conversations(
                source=source,
                workspace=workspace,
                search=search,
                limit=limit,
                offset=offset,
                bypass_cache=refresh,
            )
            sources_summary = store.get_sources_summary()
            caps = detect_agy_capabilities()

            self._send_json({
                "ok": True,
                "conversations": [c.to_dict() for c in convs],
                "total": total,
                "limit": limit,
                "offset": offset,
                "sources": sources_summary,
                "agy_capabilities": caps.to_dict(),
            })
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def _handle_api_conversations_detail(self, query: Dict[str, List[str]]) -> None:
        cid = query.get("id", [None])[0]
        if not cid:
            self._send_json({"ok": False, "error": "Conversation ID is required ('id')."}, status=400)
            return

        try:
            validate_conversation_id(cid)
        except ValueError as e:
            self._send_json({"ok": False, "error": str(e)}, status=400)
            return

        source = query.get("source", [None])[0]
        if source and source not in VALID_SOURCES:
            self._send_json({"ok": False, "error": f"Invalid source '{source}'."}, status=400)
            return

        try:
            limit = max(1, min(1000, int(query.get("limit", ["100"])[0])))
            offset_val = query.get("offset", query.get("before", ["0"]))[0]
            offset = max(0, int(offset_val))
        except (ValueError, TypeError):
            self._send_json({"ok": False, "error": "Invalid limit or offset parameter."}, status=400)
            return

        store = get_conversation_store()
        try:
            lookup = store.get_conversation(cid, source=source)
        except AmbiguousConversationError as e:
            self._send_json({"ok": False, "error": str(e), "sources": e.sources, "ambiguous": True}, status=409)
            return

        if not lookup:
            self._send_json({"ok": False, "error": f"Conversation '{cid}' not found."}, status=404)
            return

        matched_source, summary = lookup
        try:
            messages, total, ctx_state = store.get_conversation_messages(
                cid, source=matched_source, limit=limit, offset=offset
            )
            self._send_json({
                "ok": True,
                "conversation": summary.to_dict(),
                "messages": [m.to_dict() for m in messages],
                "total_messages": total,
                "context_state": ctx_state.to_dict(),
            })
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def _handle_api_conversations_context(self, query: Dict[str, List[str]]) -> None:
        cid = query.get("id", [None])[0]
        if not cid:
            self._send_json({"ok": False, "error": "Conversation ID is required ('id')."}, status=400)
            return

        try:
            validate_conversation_id(cid)
        except ValueError as e:
            self._send_json({"ok": False, "error": str(e)}, status=400)
            return

        source = query.get("source", [None])[0]
        if source and source not in VALID_SOURCES:
            self._send_json({"ok": False, "error": f"Invalid source '{source}'."}, status=400)
            return

        store = get_conversation_store()
        try:
            lookup = store.get_conversation(cid, source=source)
        except AmbiguousConversationError as e:
            self._send_json({"ok": False, "error": str(e), "sources": e.sources, "ambiguous": True}, status=409)
            return

        if not lookup:
            self._send_json({"ok": False, "error": f"Conversation '{cid}' not found."}, status=404)
            return

        matched_source, _ = lookup
        try:
            ctx_state = store.get_context_state(cid, source=matched_source)
            self._send_json({"ok": True, "context_state": ctx_state.to_dict()})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)

    def _handle_api_conversations_resume(self, body: Dict[str, Any]) -> None:
        cid = (body.get("conversation_id") or "").strip()
        source = (body.get("source") or "").strip() or None
        launch = bool(body.get("launch", True))

        if not cid:
            self._send_json({"ok": False, "error": "'conversation_id' is required."}, status=400)
            return

        try:
            validate_conversation_id(cid)
        except ValueError as e:
            self._send_json({"ok": False, "error": str(e)}, status=400)
            return

        if source and source not in VALID_SOURCES:
            self._send_json({"ok": False, "error": f"Invalid source '{source}'. Must be desktop, ide, or cli."}, status=400)
            return

        store = get_conversation_store()
        try:
            lookup = store.get_conversation(cid, source=source)
        except AmbiguousConversationError as e:
            self._send_json({"ok": False, "error": str(e), "sources": e.sources, "ambiguous": True}, status=409)
            return

        if not lookup:
            msg = f"Conversation '{cid}' not found in source '{source}'." if source else f"Conversation '{cid}' not found."
            self._send_json({"ok": False, "error": msg}, status=404)
            return

        matched_source, _ = lookup
        res = store.resume_conversation(cid, source=matched_source, launch=launch)
        if not res.get("ok"):
            status = 409 if not res.get("supported") else 400
            self._send_json(res, status=status)
            return

        self._send_json(res, status=200)

    def _handle_api_conversations_open(self, body: Dict[str, Any]) -> None:
        cid = (body.get("conversation_id") or "").strip()
        source = (body.get("source") or "desktop").strip()

        if not cid:
            self._send_json({"ok": False, "error": "Conversation ID is required."}, status=400)
            return

        try:
            validate_conversation_id(cid)
        except ValueError as e:
            self._send_json({"ok": False, "error": str(e)}, status=400)
            return

        if source not in VALID_SOURCES:
            self._send_json({"ok": False, "error": f"Invalid source '{source}'."}, status=400)
            return

        store = get_conversation_store()
        try:
            lookup = store.get_conversation(cid, source=source)
        except AmbiguousConversationError as e:
            self._send_json({"ok": False, "error": str(e), "sources": e.sources, "ambiguous": True}, status=409)
            return

        if not lookup:
            self._send_json({"ok": False, "error": f"Conversation '{cid}' not found in source '{source}'."}, status=404)
            return

        if source == SOURCE_DESKTOP and sys.platform == "darwin":
            app_p = Path("/Applications/Antigravity.app")
            if app_p.exists():
                try:
                    subprocess.run(["open", "-a", "Antigravity"], check=False)
                    self._send_json({"ok": True, "message": "Opened Google Antigravity Desktop app."})
                    return
                except Exception as e:
                    self._send_json({"ok": False, "error": str(e)}, status=500)
                    return

        instructions = (
            f"To open this {source.capitalize()} session, switch to Antigravity {source.upper()} "
            "and locate the chat in your conversation history."
        )
        self._send_json({
            "ok": False,
            "supported": False,
            "source": source,
            "conversation_id": cid,
            "instructions": instructions,
        }, status=409)

    def _handle_api_conversations_export(self, body: Dict[str, Any]) -> None:
        cid = (body.get("conversation_id") or "").strip()
        source = (body.get("source") or "desktop").strip()
        fmt = (body.get("format") or "markdown").strip()

        if not cid:
            self._send_json({"ok": False, "error": "Conversation ID is required."}, status=400)
            return

        try:
            validate_conversation_id(cid)
        except ValueError as e:
            self._send_json({"ok": False, "error": str(e)}, status=400)
            return

        store = get_conversation_store()
        try:
            lookup = store.get_conversation(cid, source=source)
        except AmbiguousConversationError as e:
            self._send_json({"ok": False, "error": str(e), "sources": e.sources, "ambiguous": True}, status=409)
            return

        if not lookup:
            self._send_json({"ok": False, "error": f"Conversation '{cid}' not found in source '{source}'."}, status=404)
            return

        matched_source, _ = lookup
        try:
            content = store.export_conversation(cid, source=matched_source, format=fmt)
            self._send_json({"ok": True, "format": fmt, "content": content})
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
    runtime_info = get_runtime_install_info()
    abs_ws = str(Path(workspace_path).resolve())
    launch_ctx = DashboardLaunchContext(
        host=host,
        port=port,
        workspace_path=abs_ws,
        open_browser=open_browser,
        install_mode=runtime_info.install_mode.value,
        python_executable=runtime_info.current_executable,
        macos_bundle_path=runtime_info.macos_bundle_path,
    )
    set_current_launch_context(launch_ctx)
    DashboardRequestHandler.workspace_path = abs_ws
    DashboardRequestHandler.launch_context = launch_ctx

    url = f"http://{host}:{port}"
    try:
        server = DashboardHTTPServer((host, port), DashboardRequestHandler)
        DashboardRequestHandler.server_instance = server
    except OSError as e:
        if (
            "Address already in use" in str(e)
            or getattr(e, "errno", None) in (48, 98, 10048, 10013)
            or getattr(e, "winerror", None) in (10048, 10013)
        ):
            print(f"🛡️  AntiAgent Dashboard is already running at: {url}")
            if open_browser:
                try:
                    if sys.platform == "darwin":
                        subprocess.run(["open", url], check=False)
                    else:
                        webbrowser.open(url)
                except Exception:
                    pass
            return
        raise

    print(f"🛡️  AntiAgent Dashboard running at: {url}")
    print("   Press Ctrl+C to stop the dashboard.")

    if open_browser:
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", url], check=False)
            else:
                webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Stopping AntiAgent Dashboard.")
    finally:
        try:
            server.server_close()
        except Exception:
            pass
