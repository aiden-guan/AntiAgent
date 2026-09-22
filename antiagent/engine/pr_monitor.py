"""Pull Request and CI/CD monitoring engine for AntiAgent (Claude Code style).

Provides inspection, live tracking, failure log extraction, and automated merging
for pull requests using the GitHub CLI (`gh`).
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional


def check_gh_cli_status() -> Dict[str, Any]:
    """Check whether the GitHub CLI (`gh`) is installed and authenticated."""
    gh_bin = shutil.which("gh")
    if not gh_bin:
        return {
            "installed": False,
            "version": "",
            "authenticated": False,
            "user": "",
            "error": "GitHub CLI ('gh') is not installed or not in PATH.",
        }

    # Get version
    version = ""
    try:
        ver_proc = subprocess.run(
            [gh_bin, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if ver_proc.returncode == 0:
            first_line = ver_proc.stdout.strip().split("\n")[0]
            m = re.search(r"version\s+([0-9.]+)", first_line, re.IGNORECASE)
            version = m.group(1) if m else first_line
    except Exception as e:
        version = f"Error: {e}"

    # Check authentication
    authenticated = False
    username = ""
    auth_err = ""
    try:
        auth_proc = subprocess.run(
            [gh_bin, "auth", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=6,
        )
        combined = (auth_proc.stdout or "") + "\n" + (auth_proc.stderr or "")
        if auth_proc.returncode == 0 or "Logged in to" in combined:
            authenticated = True
            m_user = re.search(r"Logged in to [^\s]+ account ([a-zA-Z0-9_-]+)", combined)
            if m_user:
                username = m_user.group(1)
        else:
            auth_err = combined.strip() or "Not logged in to GitHub."
    except Exception as e:
        auth_err = str(e)

    return {
        "installed": True,
        "path": gh_bin,
        "version": version,
        "authenticated": authenticated,
        "user": username,
        "error": None if authenticated else auth_err,
    }


class TupleResult:
    """Lightweight command result."""

    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout or ""
        self.stderr = stderr or ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _run_gh_command(
    args: List[str],
    workspace_dir: Optional[str] = None,
    timeout: int = 20,
) -> TupleResult:
    """Execute a gh command safely and return (returncode, stdout, stderr)."""
    gh_bin = shutil.which("gh") or "gh"
    cwd = workspace_dir or os.getcwd()

    env = os.environ.copy()
    env["GH_NO_UPDATE_NOTIFIER"] = "1"
    env["NO_COLOR"] = "1"

    try:
        proc = subprocess.run(
            [gh_bin] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        return TupleResult(proc.returncode, proc.stdout, proc.stderr)
    except FileNotFoundError:
        return TupleResult(127, "", "GitHub CLI ('gh') not found in PATH.")
    except subprocess.TimeoutExpired:
        return TupleResult(124, "", f"Command timed out after {timeout}s: gh {' '.join(args)}")
    except Exception as e:
        return TupleResult(1, "", f"Execution error: {e}")


def get_pr_details(
    pr_identifier: Optional[str] = None,
    workspace_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieve metadata for a pull request (by branch, number, url, or current branch)."""
    cmd = [
        "pr",
        "view",
        "--json",
        "number,title,url,state,author,headRefName,baseRefName,isDraft,mergeable,mergeStateStatus,reviewDecision,updatedAt",
    ]
    if pr_identifier:
        cmd.insert(2, str(pr_identifier))

    res = _run_gh_command(cmd, workspace_dir=workspace_dir, timeout=15)
    if not res.ok:
        combined = (res.stderr + " " + res.stdout).lower()
        if "no pull requests found" in combined or "no open pull requests" in combined or "could not resolve to a pull request" in combined:
            return {"ok": False, "error": "No pull request associated with current branch.", "no_pr": True}
        return {"ok": False, "error": res.stderr.strip() or res.stdout.strip() or "Failed to query pull request."}

    try:
        data = json.loads(res.stdout)
        data["ok"] = True
        if isinstance(data.get("author"), dict):
            data["author"] = data["author"].get("login", "")
        return data
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"Failed to parse gh output: {e}"}


def get_pr_checks(
    pr_identifier: Optional[str] = None,
    workspace_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Inspect CI/CD status and check runs for a pull request."""
    # First get general PR view including statusCheckRollup
    cmd = [
        "pr",
        "view",
        "--json",
        "number,title,url,state,headRefName,baseRefName,statusCheckRollup,mergeable,mergeStateStatus,reviewDecision",
    ]
    if pr_identifier:
        cmd.insert(2, str(pr_identifier))

    res = _run_gh_command(cmd, workspace_dir=workspace_dir, timeout=20)
    if not res.ok:
        combined = (res.stderr + " " + res.stdout).lower()
        if "no pull requests found" in combined or "no open pull requests" in combined:
            return {"ok": False, "error": "No pull request found for current branch.", "no_pr": True}
        return {"ok": False, "error": res.stderr.strip() or "Failed to inspect PR checks."}

    try:
        raw = json.loads(res.stdout)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"Failed to parse gh output: {e}"}

    raw_rollup = raw.get("statusCheckRollup") or []
    checks: List[Dict[str, Any]] = []

    passed_count = 0
    failed_count = 0
    pending_count = 0
    skipped_count = 0

    for item in raw_rollup:
        typename = item.get("__typename", "")
        if typename == "CheckRun":
            name = item.get("name", "")
            workflow = item.get("workflowName", "") or ""
            status = (item.get("status") or "").upper()
            conclusion = (item.get("conclusion") or "").upper()
            url = item.get("detailsUrl") or ""

            # Classify status
            is_done = status == "COMPLETED"
            if is_done:
                if conclusion in ("SUCCESS", "NEUTRAL"):
                    norm_conclusion = "SUCCESS"
                    passed_count += 1
                elif conclusion in ("SKIPPED",):
                    norm_conclusion = "SKIPPED"
                    skipped_count += 1
                elif conclusion in ("FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "CANCELLED"):
                    norm_conclusion = conclusion or "FAILURE"
                    failed_count += 1
                else:
                    norm_conclusion = conclusion or "UNKNOWN"
                    passed_count += 1
            else:
                norm_conclusion = "PENDING"
                pending_count += 1

            checks.append({
                "type": "CheckRun",
                "name": name,
                "workflow": workflow,
                "status": status or "QUEUED",
                "conclusion": norm_conclusion,
                "url": url,
            })

        elif typename == "StatusContext":
            context = item.get("context", "")
            state = (item.get("state") or "").upper()
            target_url = item.get("targetUrl") or ""
            desc = item.get("description") or ""

            if state == "SUCCESS":
                norm_conclusion = "SUCCESS"
                passed_count += 1
                norm_status = "COMPLETED"
            elif state in ("FAILURE", "ERROR"):
                norm_conclusion = "FAILURE"
                failed_count += 1
                norm_status = "COMPLETED"
            elif state in ("PENDING", "EXPECTED"):
                norm_conclusion = "PENDING"
                norm_status = "IN_PROGRESS"
                pending_count += 1
            else:
                norm_conclusion = state
                norm_status = "COMPLETED"

            checks.append({
                "type": "StatusContext",
                "name": context,
                "workflow": "",
                "status": norm_status,
                "conclusion": norm_conclusion,
                "url": target_url,
                "description": desc,
            })

    total_checks = len(checks)

    # Determine overall status rollup
    if total_checks == 0:
        overall_status = "NO_CHECKS"
    elif failed_count > 0:
        overall_status = "FAILURE"
    elif pending_count > 0:
        overall_status = "PENDING"
    else:
        overall_status = "SUCCESS"

    return {
        "ok": True,
        "number": raw.get("number"),
        "title": raw.get("title", ""),
        "url": raw.get("url", ""),
        "state": raw.get("state", "OPEN"),
        "branch": raw.get("headRefName", ""),
        "base_branch": raw.get("baseRefName", ""),
        "mergeable": raw.get("mergeable", "UNKNOWN"),
        "merge_state_status": raw.get("mergeStateStatus", "UNKNOWN"),
        "review_decision": raw.get("reviewDecision", "NONE"),
        "overall_status": overall_status,
        "is_all_passed": overall_status == "SUCCESS",
        "has_failures": failed_count > 0,
        "is_pending": pending_count > 0,
        "summary": {
            "total": total_checks,
            "passed": passed_count,
            "failed": failed_count,
            "pending": pending_count,
            "skipped": skipped_count,
        },
        "checks": checks,
    }


def get_pr_failure_logs(
    pr_identifier: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    max_lines: int = 200,
) -> Dict[str, Any]:
    """Extract failure logs for failing checks on the PR using gh run view --log-failed."""
    checks_data = get_pr_checks(pr_identifier=pr_identifier, workspace_dir=workspace_dir)
    if not checks_data.get("ok"):
        return checks_data

    failures: List[Dict[str, Any]] = []

    # Identify failed checks
    failed_checks = [c for c in checks_data.get("checks", []) if c.get("conclusion") in ("FAILURE", "TIMED_OUT", "CANCELLED")]
    if not failed_checks:
        return {
            "ok": True,
            "pr_number": checks_data.get("number"),
            "has_failures": False,
            "message": "No failed checks found on this pull request.",
            "failures": [],
        }

    # Query recent failed runs for this branch
    branch = checks_data.get("branch")
    run_args = ["run", "list", "--json", "databaseId,name,status,conclusion,headBranch", "--limit", "10"]
    if branch:
        run_args.extend(["--branch", branch])

    run_list_res = _run_gh_command(run_args, workspace_dir=workspace_dir)
    run_ids = []
    if run_list_res.ok:
        try:
            runs = json.loads(run_list_res.stdout)
            for r in runs:
                if (r.get("conclusion") or "").upper() in ("FAILURE", "TIMED_OUT", "CANCELLED"):
                    run_ids.append(r.get("databaseId"))
        except Exception:
            pass

    # Extract log-failed for available run IDs
    collected_logs: List[Dict[str, Any]] = []
    for rid in run_ids[:3]:
        log_res = _run_gh_command(["run", "view", str(rid), "--log-failed"], workspace_dir=workspace_dir, timeout=25)
        if log_res.ok and log_res.stdout.strip():
            lines = log_res.stdout.strip().split("\n")
            if len(lines) > max_lines:
                # keep the most informative tail of the error log
                truncated = "\n".join(lines[-max_lines:])
            else:
                truncated = "\n".join(lines)

            collected_logs.append({
                "run_id": rid,
                "log": truncated,
            })

    return {
        "ok": True,
        "pr_number": checks_data.get("number"),
        "pr_title": checks_data.get("title"),
        "has_failures": True,
        "failed_checks": failed_checks,
        "failure_logs": collected_logs,
    }


def merge_pr(
    pr_identifier: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    auto: bool = True,
    method: str = "squash",
    delete_branch: bool = False,
) -> Dict[str, Any]:
    """Merge or enable auto-merge on a pull request once CI checks pass."""
    args = ["pr", "merge"]
    if pr_identifier:
        args.append(str(pr_identifier))

    if auto:
        args.append("--auto")

    if method == "squash":
        args.append("--squash")
    elif method == "rebase":
        args.append("--rebase")
    elif method == "merge":
        args.append("--merge")

    if delete_branch:
        args.append("--delete-branch")

    res = _run_gh_command(args, workspace_dir=workspace_dir, timeout=25)
    if res.ok:
        return {
            "ok": True,
            "message": res.stdout.strip() or ("Auto-merge successfully configured." if auto else "Pull request merged."),
        }
    else:
        return {
            "ok": False,
            "error": res.stderr.strip() or res.stdout.strip() or "Failed to merge pull request.",
        }


def list_pull_requests(
    workspace_dir: Optional[str] = None,
    limit: int = 15,
) -> Dict[str, Any]:
    """List open pull requests in the repository."""
    cmd = ["pr", "list", "--json", "number,title,state,headRefName,author,updatedAt,url", "--limit", str(limit)]
    res = _run_gh_command(cmd, workspace_dir=workspace_dir, timeout=15)
    if not res.ok:
        return {"ok": False, "error": res.stderr.strip() or "Failed to list PRs."}

    try:
        data = json.loads(res.stdout)
        return {"ok": True, "pull_requests": data}
    except Exception as e:
        return {"ok": False, "error": f"Failed to parse PR list: {e}"}


class PRMonitor:
    """Monitors a PR in a background thread or foreground loop."""

    def __init__(
        self,
        pr_identifier: Optional[str] = None,
        workspace_dir: Optional[str] = None,
        interval: int = 15,
        auto_merge: bool = False,
        timeout_seconds: int = 1800,
    ):
        self.pr_identifier = pr_identifier
        self.workspace_dir = workspace_dir
        self.interval = max(3, interval)
        self.auto_merge = auto_merge
        self.timeout_seconds = timeout_seconds

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.last_status: Optional[Dict[str, Any]] = None
        self.status_history: List[Dict[str, Any]] = []
        self.is_finished = False
        self.error: Optional[str] = None
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None

        # Callbacks
        self.on_update: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_complete: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_failure: Optional[Callable[[Dict[str, Any]], None]] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def poll_once(self) -> Dict[str, Any]:
        """Perform a single check update."""
        data = get_pr_checks(pr_identifier=self.pr_identifier, workspace_dir=self.workspace_dir)
        self.last_status = data
        if data.get("ok"):
            self.status_history.append({
                "timestamp": time.time(),
                "overall_status": data.get("overall_status"),
                "summary": data.get("summary"),
            })
            # Bound history size
            if len(self.status_history) > 50:
                self.status_history = self.status_history[-50:]
        return data

    def start_background(self) -> bool:
        """Start monitoring in a background daemon thread."""
        if self.is_running:
            return False

        self._stop_event.clear()
        self.is_finished = False
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Signal the monitor to stop."""
        self._stop_event.set()
        self.is_finished = True
        self.finished_at = time.time()

    def _run_loop(self) -> None:
        start_time = time.time()
        while not self._stop_event.is_set():
            if time.time() - start_time > self.timeout_seconds:
                self.error = f"Monitoring timed out after {self.timeout_seconds}s"
                self.is_finished = True
                break

            status = self.poll_once()

            if self.on_update and status.get("ok"):
                try:
                    self.on_update(status)
                except Exception:
                    pass

            if not status.get("ok"):
                self.error = status.get("error", "Failed checking PR status")
                # Wait before retry
                if self._stop_event.wait(self.interval):
                    break
                continue

            overall = status.get("overall_status")
            if overall == "SUCCESS":
                # All passed!
                if self.auto_merge:
                    merge_pr(
                        pr_identifier=self.pr_identifier,
                        workspace_dir=self.workspace_dir,
                        auto=False,
                        method="squash",
                    )
                self.is_finished = True
                self.finished_at = time.time()
                if self.on_complete:
                    try:
                        self.on_complete(status)
                    except Exception:
                        pass
                break

            elif overall == "FAILURE":
                # Check failure detected
                self.is_finished = True
                self.finished_at = time.time()
                if self.on_failure:
                    try:
                        self.on_failure(status)
                    except Exception:
                        pass
                break

            # Wait for next interval
            if self._stop_event.wait(self.interval):
                break

        self.finished_at = time.time()
        self.is_finished = True


class PRMonitorManager:
    """Thread-safe manager for all active PR monitors."""

    def __init__(self):
        self._monitors: Dict[str, PRMonitor] = {}
        self._lock = threading.Lock()

    def _make_key(self, pr_id: Optional[str], workspace_dir: Optional[str]) -> str:
        ws = os.path.abspath(workspace_dir or os.getcwd())
        return f"{ws}::{pr_id or 'current'}"

    def start_monitor(
        self,
        pr_identifier: Optional[str] = None,
        workspace_dir: Optional[str] = None,
        interval: int = 15,
        auto_merge: bool = False,
    ) -> PRMonitor:
        """Start a new background monitor or return existing active one."""
        key = self._make_key(pr_identifier, workspace_dir)
        with self._lock:
            existing = self._monitors.get(key)
            if existing and existing.is_running:
                return existing

            monitor = PRMonitor(
                pr_identifier=pr_identifier,
                workspace_dir=workspace_dir,
                interval=interval,
                auto_merge=auto_merge,
            )
            monitor.start_background()
            self._monitors[key] = monitor
            return monitor

    def stop_monitor(
        self,
        pr_identifier: Optional[str] = None,
        workspace_dir: Optional[str] = None,
    ) -> bool:
        """Stop an active monitor."""
        key = self._make_key(pr_identifier, workspace_dir)
        with self._lock:
            m = self._monitors.get(key)
            if m:
                m.stop()
                return True
            return False

    def get_monitor(
        self,
        pr_identifier: Optional[str] = None,
        workspace_dir: Optional[str] = None,
    ) -> Optional[PRMonitor]:
        """Get monitor instance if exists."""
        key = self._make_key(pr_identifier, workspace_dir)
        with self._lock:
            return self._monitors.get(key)

    def list_monitors(self) -> List[Dict[str, Any]]:
        """List summary of all monitors."""
        res = []
        with self._lock:
            for k, m in self._monitors.items():
                last = m.last_status or {}
                res.append({
                    "key": k,
                    "pr_identifier": m.pr_identifier,
                    "workspace_dir": m.workspace_dir,
                    "is_running": m.is_running,
                    "is_finished": m.is_finished,
                    "overall_status": last.get("overall_status", "UNKNOWN"),
                    "summary": last.get("summary", {}),
                    "started_at": m.started_at,
                    "finished_at": m.finished_at,
                    "error": m.error,
                })
        return res


# Global singleton manager instance
global_pr_manager = PRMonitorManager()
