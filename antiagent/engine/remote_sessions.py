"""Remote sessions management over OpenSSH for AntiAgent.

Allows AntiAgent to configure remote machines, probe their Antigravity/AntiAgent status,
and remotely start/control Google Antigravity sessions via OpenSSH.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import select
import shlex
import signal
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from antiagent.audit.logger import AuditLogger
from antiagent.config import get_global_config_dir

# Operational defaults
DEFAULT_SSH_CONNECT_TIMEOUT = 8
DEFAULT_SSH_COMMAND_TIMEOUT = 15
DEFAULT_SSH_DAEMON_TIMEOUT = 30
DEFAULT_CACHE_TTL = 10
REMOTES_SCHEMA_VERSION = 1
OFFICIAL_ANTIGRAVITY_URL = "https://antigravity.google.com/"
ALLOWED_ANTIGRAVITY_HOSTS = ("antigravity.google.com", "google.com")

_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]{1,64}\Z")
_INSTANCE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\. ]{1,64}\Z")
_URL_PATTERN = re.compile(r"https://antigravity\.google\.com/r/[a-zA-Z0-9_\-]+")


def get_connect_timeout() -> int:
    """Return SSH connection timeout from env or default."""
    val = os.getenv("ANTIAGENT_SSH_CONNECT_TIMEOUT")
    if val:
        try:
            return max(2, int(val))
        except ValueError:
            pass
    return DEFAULT_SSH_CONNECT_TIMEOUT


def get_command_timeout() -> int:
    """Return SSH command timeout from env or default."""
    val = os.getenv("ANTIAGENT_SSH_COMMAND_TIMEOUT")
    if val:
        try:
            return max(5, int(val))
        except ValueError:
            pass
    return DEFAULT_SSH_COMMAND_TIMEOUT


def validate_remote_name(name: str) -> None:
    """Ensure host alias name is safe and contains no control or shell characters."""
    if not name or not isinstance(name, str):
        raise ValueError("Remote name cannot be empty.")
    if name.startswith("-"):
        raise ValueError(f"Invalid remote machine name '{name}': cannot start with a hyphen.")
    if not _NAME_PATTERN.match(name):
        raise ValueError(
            f"Invalid remote machine name '{name}'. Must be 1-64 characters "
            "containing only letters, numbers, hyphens, underscores, or dots."
        )


def validate_ssh_host(ssh_host: str) -> None:
    """Ensure SSH host or ~/.ssh/config alias contains no shell injection metacharacters."""
    if not ssh_host or not isinstance(ssh_host, str):
        raise ValueError("SSH host cannot be empty.")
    if ssh_host.startswith("-"):
        raise ValueError(f"Invalid SSH host '{ssh_host}': cannot start with a hyphen.")
    forbidden = set(" \t\r\n;&|`$<>'\"\\")
    if any(c in forbidden for c in ssh_host):
        raise ValueError(f"Invalid SSH host '{ssh_host}': contains whitespace or shell metacharacters.")
    if len(ssh_host) > 255:
        raise ValueError("SSH host is too long (maximum 255 characters).")


def validate_instance_name(name: Optional[str]) -> None:
    """Ensure Antigravity instance nickname is conservative and safe."""
    if not name:
        return
    if any(c in name for c in ("\n", "\r", "\0", ";", "&", "|", "`", "$", "<", ">", '"', "'", "\\")):
        raise ValueError(f"Invalid Antigravity instance name '{name}': contains forbidden characters.")
    if not _INSTANCE_NAME_PATTERN.match(name):
        raise ValueError(
            f"Invalid Antigravity instance name '{name}'. Must be 1-64 characters "
            "containing only alphanumeric characters, spaces, hyphens, underscores, or dots."
        )


def validate_workspace_path(path: Optional[str]) -> None:
    """Ensure workspace path contains no control characters or newlines."""
    if not path:
        return
    if any(c in path for c in ("\n", "\r", "\0")):
        raise ValueError("Workspace path contains control characters or newlines.")


def validate_identity_file_path(path: Optional[str]) -> None:
    """Ensure identity file path is a path, NOT actual private key contents."""
    if not path:
        return
    if any(c in path for c in ("\n", "\r", "\0")):
        raise ValueError("Identity file path contains control characters or newlines.")
    if "PRIVATE KEY" in path or "BEGIN OPENSSH" in path:
        raise ValueError("Never supply private key contents. Supply only a local file path.")


def is_safe_antigravity_url(url: str) -> bool:
    """Validate that a URL is a legitimate HTTPS Antigravity endpoint."""
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() != "https":
            return False
        hostname = (parsed.hostname or "").lower()
        if hostname == "antigravity.google.com" or hostname.endswith(".antigravity.google.com"):
            return True
        return False
    except Exception:
        return False


def quote_posix_arg(arg: str) -> str:
    """Safely quote an argument for POSIX shells."""
    return shlex.quote(arg)


def quote_windows_arg(arg: str) -> str:
    """Safely quote an argument for Windows shells."""
    escaped = arg.replace('"', '""')
    return f'"{escaped}"'


@dataclass
class RemoteHost:
    """Configuration for a saved remote machine."""

    name: str
    ssh_host: str
    hostname: Optional[str] = None
    user: Optional[str] = None
    port: Optional[int] = None
    identity_file: Optional[str] = None
    remote_os: str = "auto"
    workspace: Optional[str] = None
    antigravity_name: Optional[str] = None
    agy_path: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        validate_remote_name(self.name)
        validate_ssh_host(self.ssh_host)
        if self.hostname:
            validate_ssh_host(self.hostname)
        if self.user:
            validate_ssh_host(self.user)
        if self.port is not None:
            if not isinstance(self.port, int) or not (1 <= self.port <= 65535):
                raise ValueError(f"Invalid port '{self.port}'. Must be between 1 and 65535.")
        if self.identity_file:
            validate_identity_file_path(self.identity_file)
        if self.remote_os not in ("auto", "posix", "windows"):
            raise ValueError(f"Invalid remote_os '{self.remote_os}'. Must be 'auto', 'posix', or 'windows'.")
        if self.workspace:
            validate_workspace_path(self.workspace)
        if self.antigravity_name:
            validate_instance_name(self.antigravity_name)
        if self.agy_path:
            validate_workspace_path(self.agy_path)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dictionary without exposing secrets."""
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RemoteHost:
        """Instantiate RemoteHost ignoring unknown or dangerous keys."""
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        # Explicitly ban secrets if someone attempts to insert them
        banned = {"password", "private_key", "secret", "token", "key_content"}
        filtered = {k: v for k, v in data.items() if k in valid_keys and k not in banned}
        return cls(**filtered)


@dataclass
class CommandResult:
    """Result of a command invocation."""

    argv: List[str]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False


@dataclass
class AntigravityStatus:
    installed: bool = False
    path: Optional[str] = None
    version: Optional[str] = None
    authenticated: bool = True
    raw_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AntiAgentRemoteStatus:
    installed: bool = False
    path: Optional[str] = None
    version: Optional[str] = None
    global_hook_active: bool = False
    protection_status: str = "Unknown"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RemoteControlStatus:
    supported: bool = False
    running: Optional[bool] = False
    instance_name: Optional[str] = None
    authenticated: Optional[bool] = True
    raw_status: Optional[str] = None
    url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RemoteProbeResult:
    """Structured result returned by probing a remote host."""

    ok: bool
    name: str
    ssh_connected: bool
    latency_ms: Optional[int] = None
    remote_os: str = "unknown"
    hostname: Optional[str] = None
    workspace_exists: Optional[bool] = None
    workspace_path: Optional[str] = None
    antigravity: AntigravityStatus = field(default_factory=AntigravityStatus)
    antiagent: AntiAgentRemoteStatus = field(default_factory=AntiAgentRemoteStatus)
    remote_control: RemoteControlStatus = field(default_factory=RemoteControlStatus)
    error: Optional[str] = None
    error_type: Optional[str] = None
    cached: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "name": self.name,
            "ssh_connected": self.ssh_connected,
            "latency_ms": self.latency_ms,
            "remote_os": self.remote_os,
            "hostname": self.hostname,
            "workspace_exists": self.workspace_exists,
            "workspace_path": self.workspace_path,
            "antigravity": self.antigravity.to_dict(),
            "antiagent": self.antiagent.to_dict(),
            "remote_control": self.remote_control.to_dict(),
            "error": self.error,
            "error_type": self.error_type,
            "cached": self.cached,
            "timestamp": self.timestamp,
        }


def build_ssh_argv(
    host: RemoteHost,
    remote_command: Optional[List[str]] = None,
    raw_command: Optional[str] = None,
    interactive: bool = False,
    connect_timeout: Optional[int] = None,
) -> List[str]:
    """Construct safe argv list for invoking the system OpenSSH client.

    Never uses StrictHostKeyChecking=no.
    Never uses shell=True.
    """
    argv = ["ssh"]

    if interactive:
        argv.append("-t")
    else:
        argv.extend(["-o", "BatchMode=yes"])
        timeout = connect_timeout if connect_timeout is not None else get_connect_timeout()
        argv.extend(["-o", f"ConnectTimeout={timeout}"])

    if host.port:
        argv.extend(["-p", str(host.port)])

    if host.identity_file:
        argv.extend(["-i", os.path.expanduser(host.identity_file)])

    # Target
    target_host = host.hostname if host.hostname else host.ssh_host
    if host.user and "@" not in target_host:
        target = f"{host.user}@{target_host}"
    else:
        target = target_host
    argv.append(target)

    # Remote command arguments
    if raw_command is not None:
        argv.append(raw_command)
    elif remote_command:
        if host.remote_os == "windows":
            command_str = " ".join(quote_windows_arg(a) for a in remote_command)
        else:
            command_str = " ".join(quote_posix_arg(a) for a in remote_command)
        argv.append(command_str)

    return argv


def classify_ssh_error(returncode: int, stdout: str, stderr: str, timed_out: bool) -> Tuple[str, str]:
    """Classify SSH error output into structured categories."""
    if timed_out:
        return "timeout", "Connection timed out."

    combined = f"{stderr}\n{stdout}".lower()

    if "permission denied" in combined or "authentication failed" in combined:
        return (
            "auth_failed",
            "SSH authentication failed. AntiAgent does not store SSH passwords. "
            "Verify this host using: ssh <host>",
        )

    if (
        "host key verification failed" in combined
        or "remote host identification has changed" in combined
        or "host key has changed" in combined
    ):
        return (
            "host_key_failed",
            "Host key verification failed. Verify the machine's fingerprint and connect once "
            "using your normal SSH client.",
        )

    if "could not resolve hostname" in combined or "name or service not known" in combined:
        return "unreachable", "Could not reach host: hostname resolution failed."

    if (
        "no route to host" in combined
        or "connection refused" in combined
        or "network is unreachable" in combined
        or "operation timed out" in combined
    ):
        return "unreachable", "Could not reach host: network connection refused or unreachable."

    if "command not found" in combined:
        return "command_not_found", "Requested command not found on remote machine."

    err_text = stderr.strip() or stdout.strip() or f"Process exited with code {returncode}."
    return "command_failed", err_text


class SSHClient:
    """Manages execution of OpenSSH commands."""

    def __init__(self, ssh_executable: str = "ssh"):
        self.ssh_executable = ssh_executable

    def run_command(
        self,
        host: RemoteHost,
        remote_command: Optional[List[str]] = None,
        timeout: Optional[int] = None,
        connect_timeout: Optional[int] = None,
    ) -> CommandResult:
        """Run a non-interactive command over SSH."""
        argv = build_ssh_argv(
            host=host,
            remote_command=remote_command,
            interactive=False,
            connect_timeout=connect_timeout,
        )
        cmd_timeout = timeout if timeout is not None else get_command_timeout()

        start_time = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=cmd_timeout,
                shell=False,
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return CommandResult(
                argv=argv,
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=duration_ms,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            stdout = e.stdout if isinstance(e.stdout, str) else (e.stdout.decode() if e.stdout else "")
            stderr = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else "")
            return CommandResult(
                argv=argv,
                returncode=124,
                stdout=stdout,
                stderr=stderr or "Command timed out.",
                duration_ms=duration_ms,
                timed_out=True,
            )
        except FileNotFoundError:
            return CommandResult(
                argv=argv,
                returncode=127,
                stdout="",
                stderr="OpenSSH client was not found in PATH.",
                duration_ms=0,
                timed_out=False,
            )


def parse_remote_control_status(stdout: str, stderr: str, exit_code: int) -> RemoteControlStatus:
    """Resiliently parse `agy remote-control status` output."""
    raw = f"{stdout}\n{stderr}".strip()
    lowered = raw.lower()

    # Supported check
    supported = True
    if "unknown command 'remote-control'" in lowered or "unrecognized arguments: remote-control" in lowered:
        supported = False

    # Authenticated check
    authenticated: Optional[bool] = True
    auth_negatives = [
        "not logged in",
        "authentication required",
        "please authenticate",
        "please log in",
        "not authenticated",
        "unauthenticated",
        "auth login",
        "agy auth",
        "agy login",
        "no credentials found",
        "credentials not found",
        "token expired",
        "sign in with google",
    ]
    if any(ind in lowered for ind in auth_negatives):
        authenticated = False

    # Running check
    running: Optional[bool] = False
    positive_indicators = [
        "remote control is running",
        "remote control: running",
        "status: running",
        "state: running",
        "active (running)",
        "running (pid",
        "remote control session active",
    ]
    negative_indicators = [
        "not running",
        "is stopped",
        "status: stopped",
        "state: stopped",
        "inactive",
        "no active remote control",
        "no running instance",
    ]

    has_positive = any(ind in lowered for ind in positive_indicators)
    has_negative = any(ind in lowered for ind in negative_indicators)

    if has_positive and not has_negative:
        running = True
    elif has_negative:
        running = False
    elif exit_code == 0 and ("running" in lowered or "active" in lowered):
        running = True
    elif exit_code != 0:
        if authenticated is False:
            running = False
        elif not raw:
            running = None
        else:
            running = False
    elif exit_code == 0 and not raw:
        running = None
    elif not has_positive and not has_negative:
        running = None

    # Instance name check
    instance_name = None
    name_match = re.search(r"(?i)(?:instance|name|session)(?:\s+name)?\s*[:=]\s*[\"']?([a-zA-Z0-9_\-\. ]+)[\"']?", raw)
    if name_match:
        cand = name_match.group(1).strip()
        if cand and cand.lower() not in ("running", "stopped", "active", "true", "false", "none", "null", "unknown"):
            instance_name = cand

    # URL check
    url = None
    url_match = _URL_PATTERN.search(raw)
    if url_match:
        cand_url = url_match.group(0)
        if is_safe_antigravity_url(cand_url):
            url = cand_url
    if not url and running is True:
        url = OFFICIAL_ANTIGRAVITY_URL

    return RemoteControlStatus(
        supported=supported,
        running=running,
        instance_name=instance_name,
        authenticated=authenticated,
        raw_status=raw,
        url=url,
    )


def parse_antiagent_status(stdout: str, stderr: str, exit_code: int) -> AntiAgentRemoteStatus:
    """Parse output from remote `antiagent status`."""
    raw = f"{stdout}\n{stderr}".strip()
    if not raw or (exit_code != 0 and "command not found" in raw.lower()):
        return AntiAgentRemoteStatus(
            installed=False,
            version=None,
            global_hook_active=False,
            protection_status="AntiAgent not installed",
        )

    installed = True
    ver_match = re.search(r"v(\d+\.\d+\.\d+)", raw)
    version = ver_match.group(1) if ver_match else None

    global_hook_active = False
    if re.search(r"Global Hook:\s*(?:🟢\s*Active|ACTIVE|true)", raw, re.IGNORECASE):
        global_hook_active = True

    if global_hook_active:
        protection_status = "Protected"
    else:
        protection_status = "AntiAgent installed but hook disabled"

    return AntiAgentRemoteStatus(
        installed=installed,
        version=version,
        global_hook_active=global_hook_active,
        protection_status=protection_status,
    )


# POSIX remote probe shell script template
_POSIX_PROBE_SCRIPT = r"""
os=$(uname -s 2>/dev/null || echo "unknown")
host=$(uname -n 2>/dev/null || hostname 2>/dev/null || echo "")

echo "---AA_PROBE---"
echo "OS:$os"
echo "HOST:$host"

# Check workspace
ws_arg="$1"
if [ -n "$ws_arg" ]; then
  case "$ws_arg" in
    "~/"*) ws_eval="$HOME/${ws_arg#\~/}" ;;
    "~") ws_eval="$HOME" ;;
    *) ws_eval="$ws_arg" ;;
  esac
  if [ -d "$ws_eval" ]; then
    echo "WS:EXISTS:$ws_eval"
  else
    echo "WS:MISSING:$ws_eval"
  fi
else
  echo "WS:NONE:"
fi

# Locate agy
agy_override="$2"
agy_bin=""
if [ -n "$agy_override" ] && [ -x "$agy_override" ]; then
  agy_bin="$agy_override"
fi
if [ -z "$agy_bin" ] && command -v agy >/dev/null 2>&1; then
  agy_bin=$(command -v agy 2>/dev/null)
fi
if [ -z "$agy_bin" ]; then
  for p in "$HOME/.local/bin/agy" "$HOME/.antigravity/bin/agy" "$HOME/bin/agy" "/usr/local/bin/agy" "/opt/homebrew/bin/agy" "/usr/bin/agy" "$HOME/.cargo/bin/agy"; do
    if [ -x "$p" ]; then
      agy_bin="$p"
      break
    fi
  done
fi
if [ -z "$agy_bin" ] && [ -n "$SHELL" ]; then
  login_agy=$("$SHELL" -l -c "command -v agy" 2>/dev/null)
  if [ -n "$login_agy" ] && [ -x "$login_agy" ]; then
    agy_bin="$login_agy"
  fi
fi

if [ -n "$agy_bin" ]; then
  echo "AGY_PATH:$agy_bin"
  ver=$("$agy_bin" --version 2>&1 | head -n 1)
  echo "AGY_VER:$ver"
  echo "---AGY_STATUS_START---"
  "$agy_bin" remote-control status 2>&1
  echo "---AGY_STATUS_END---"
else
  echo "AGY_PATH:"
  echo "AGY_VER:"
fi

# Locate antiagent
aa_bin=""
if command -v antiagent >/dev/null 2>&1; then
  aa_bin=$(command -v antiagent 2>/dev/null)
fi
if [ -z "$aa_bin" ]; then
  for p in "$HOME/.local/bin/antiagent" "$HOME/bin/antiagent" "/usr/local/bin/antiagent" "/opt/homebrew/bin/antiagent" "$HOME/.cargo/bin/antiagent"; do
    if [ -x "$p" ]; then
      aa_bin="$p"
      break
    fi
  done
fi
if [ -z "$aa_bin" ] && [ -n "$SHELL" ]; then
  login_aa=$("$SHELL" -l -c "command -v antiagent" 2>/dev/null)
  if [ -n "$login_aa" ] && [ -x "$login_aa" ]; then
    aa_bin="$login_aa"
  fi
fi

if [ -n "$aa_bin" ]; then
  echo "AA_PATH:$aa_bin"
  ver=$("$aa_bin" --version 2>&1 | head -n 1)
  echo "AA_VER:$ver"
  echo "---AA_STATUS_START---"
  "$aa_bin" status 2>&1
  echo "---AA_STATUS_END---"
else
  echo "AA_PATH:"
  echo "AA_VER:"
fi
echo "---AA_PROBE_END---"
"""


def parse_probe_output(stdout: str, stderr: str, exit_code: int, host: RemoteHost, latency_ms: int) -> RemoteProbeResult:
    """Parse the consolidated probe command output into RemoteProbeResult."""
    if exit_code != 0 and "---AA_PROBE---" not in stdout:
        err_type, err_msg = classify_ssh_error(exit_code, stdout, stderr, False)
        return RemoteProbeResult(
            ok=False,
            name=host.name,
            ssh_connected=False,
            latency_ms=latency_ms,
            workspace_path=host.workspace,
            error=err_msg,
            error_type=err_type,
        )

    remote_os = "unknown"
    hostname = None
    ws_exists = None
    ws_path = None
    agy_path = None
    agy_ver = None
    agy_status_raw = ""
    aa_path = None
    aa_ver = None
    aa_status_raw = ""

    lines = stdout.splitlines()
    in_agy_status = False
    in_aa_status = False

    for line in lines:
        if line == "---AGY_STATUS_START---":
            in_agy_status = True
            continue
        elif line == "---AGY_STATUS_END---":
            in_agy_status = False
            continue
        elif line == "---AA_STATUS_START---":
            in_aa_status = True
            continue
        elif line == "---AA_STATUS_END---":
            in_aa_status = False
            continue

        if in_agy_status:
            agy_status_raw += line + "\n"
            continue
        if in_aa_status:
            aa_status_raw += line + "\n"
            continue

        if line.startswith("OS:"):
            raw_os = line[3:].strip()
            if raw_os.lower() == "darwin":
                remote_os = "macOS"
            elif raw_os.lower() == "linux":
                remote_os = "Linux"
            elif "win" in raw_os.lower():
                remote_os = "Windows"
            else:
                remote_os = raw_os
        elif line.startswith("HOST:"):
            hostname = line[5:].strip() or None
        elif line.startswith("WS:"):
            parts = line.split(":", 2)
            if len(parts) >= 2:
                status_code = parts[1]
                if status_code == "EXISTS":
                    ws_exists = True
                    ws_path = parts[2] if len(parts) > 2 else host.workspace
                elif status_code == "MISSING":
                    ws_exists = False
                    ws_path = parts[2] if len(parts) > 2 else host.workspace
        elif line.startswith("AGY_PATH:"):
            agy_path = line[9:].strip() or None
        elif line.startswith("AGY_VER:"):
            agy_ver = line[8:].strip() or None
        elif line.startswith("AA_PATH:"):
            aa_path = line[8:].strip() or None
        elif line.startswith("AA_VER:"):
            aa_ver = line[7:].strip() or None

    # Antigravity status
    agy_installed = bool(agy_path)
    agy_authenticated = True
    agy_raw_error = None

    if agy_installed:
        rc_status = parse_remote_control_status(agy_status_raw, "", 0)
        if rc_status.authenticated is False:
            agy_authenticated = False
            agy_raw_error = "Antigravity authentication is required on this machine."
    else:
        rc_status = RemoteControlStatus(
            supported=False,
            running=False,
            authenticated=None,
            raw_status="Antigravity CLI is not installed.",
        )

    antigravity_info = AntigravityStatus(
        installed=agy_installed,
        path=agy_path,
        version=agy_ver,
        authenticated=agy_authenticated,
        raw_error=agy_raw_error,
    )

    # AntiAgent status
    if aa_path:
        aa_status = parse_antiagent_status(aa_status_raw, "", 0)
        aa_status.path = aa_path
        if aa_ver and not aa_status.version:
            aa_status.version = aa_ver
    else:
        aa_status = AntiAgentRemoteStatus(
            installed=False,
            path=None,
            version=None,
            global_hook_active=False,
            protection_status="AntiAgent not installed",
        )

    return RemoteProbeResult(
        ok=True,
        name=host.name,
        ssh_connected=True,
        latency_ms=latency_ms,
        remote_os=remote_os,
        hostname=hostname or host.hostname or host.ssh_host,
        workspace_exists=ws_exists,
        workspace_path=ws_path or host.workspace,
        antigravity=antigravity_info,
        antiagent=aa_status,
        remote_control=rc_status,
    )


class RemoteHostRegistry:
    """Persistent storage for configured remote hosts in ~/.antiagent/remotes.json."""

    def __init__(self, registry_path: Optional[Path] = None):
        if registry_path:
            self.registry_path = Path(registry_path)
        else:
            self.registry_path = get_global_config_dir() / "remotes.json"
        self._lock = threading.Lock()

    def _ensure_dir_permissions(self) -> None:
        parent = self.registry_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "chmod") and sys.platform != "win32":
            try:
                os.chmod(parent, 0o700)
            except Exception:
                pass

    def load(self) -> Dict[str, RemoteHost]:
        """Load saved remote hosts. Malformed data will never crash AntiAgent."""
        with self._lock:
            if not self.registry_path.is_file():
                return {}
            try:
                content = self.registry_path.read_text(encoding="utf-8")
                if not content.strip():
                    return {}
                data = json.loads(content)
                if not isinstance(data, dict):
                    return {}
                hosts_data = data.get("hosts", {})
                if not isinstance(hosts_data, dict):
                    return {}

                hosts: Dict[str, RemoteHost] = {}
                for name, item in hosts_data.items():
                    if not isinstance(item, dict):
                        continue
                    try:
                        item["name"] = name
                        host = RemoteHost.from_dict(item)
                        hosts[name] = host
                    except Exception:
                        continue
                return hosts
            except Exception as e:
                sys.stderr.write(f"[antiagent] Warning: Failed to load {self.registry_path}: {e}\n")
                return {}

    def save(self, hosts: Dict[str, RemoteHost]) -> Path:
        """Atomically write hosts with restrictive POSIX permissions (0600)."""
        with self._lock:
            self._ensure_dir_permissions()
            payload = {
                "version": REMOTES_SCHEMA_VERSION,
                "hosts": {name: h.to_dict() for name, h in hosts.items()},
            }
            tmp_path = self.registry_path.with_name(f"{self.registry_path.name}.tmp.{os.getpid()}")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
                    f.flush()
                    if hasattr(os, "fsync"):
                        os.fsync(f.fileno())

                if hasattr(os, "chmod") and sys.platform != "win32":
                    try:
                        os.chmod(tmp_path, 0o600)
                    except Exception:
                        pass

                os.replace(tmp_path, self.registry_path)
                return self.registry_path
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass

    def list_hosts(self) -> List[RemoteHost]:
        hosts = self.load()
        return list(hosts.values())

    def get_host(self, name: str) -> Optional[RemoteHost]:
        hosts = self.load()
        return hosts.get(name)

    def add_host(self, host: RemoteHost) -> RemoteHost:
        hosts = self.load()
        if host.name in hosts:
            raise ValueError(f"Remote machine '{host.name}' already exists. Use update or remove first.")
        hosts[host.name] = host
        self.save(hosts)
        return host

    def update_host(self, host: RemoteHost) -> RemoteHost:
        hosts = self.load()
        host.updated_at = datetime.now(timezone.utc).isoformat()
        hosts[host.name] = host
        self.save(hosts)
        return host

    def remove_host(self, name: str) -> bool:
        hosts = self.load()
        if name in hosts:
            del hosts[name]
            self.save(hosts)
            return True
        return False


def launch_interactive_ssh(argv: List[str], open_browser: bool = True) -> int:
    """Launch an interactive SSH terminal session with PTY handling and URL detection."""
    from antiagent.engine.pty import (
        is_pty_supported,
        open_pty,
        raw_terminal_context,
        sync_window_size as pty_sync_window_size,
    )

    # On Windows or non-terminal environments, pass directly to subprocess.call
    if not is_pty_supported() or not hasattr(sys.stdin, "isatty") or not sys.stdin.isatty():
        return subprocess.call(argv)

    try:
        master_fd, slave_fd = open_pty()
    except Exception:
        return subprocess.call(argv)

    def _sync_win() -> None:
        pty_sync_window_size(sys.stdin.fileno(), [slave_fd, master_fd])

    _sync_win()
    old_winch = signal.getsignal(signal.SIGWINCH)
    signal.signal(signal.SIGWINCH, lambda sig, frame: _sync_win())

    url_opened = False
    collected_buf = ""

    try:
        proc = subprocess.Popen(
            argv,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)

        with raw_terminal_context():
            while proc.poll() is None:
                r, _, _ = select.select([sys.stdin.fileno(), master_fd], [], [], 0.05)
                if sys.stdin.fileno() in r:
                    try:
                        data = os.read(sys.stdin.fileno(), 1024)
                        if not data:
                            break
                        os.write(master_fd, data)
                    except OSError:
                        break
                if master_fd in r:
                    try:
                        data = os.read(master_fd, 1024)
                        if not data:
                            break
                        os.write(sys.stdout.fileno(), data)
                        if not url_opened:
                            collected_buf += data.decode("utf-8", errors="ignore")
                            if len(collected_buf) > 4096:
                                collected_buf = collected_buf[-4096:]
                            match = _URL_PATTERN.search(collected_buf)
                            if match:
                                found_url = match.group(0)
                                if is_safe_antigravity_url(found_url):
                                    url_opened = True
                                    msg = f"\r\n\033[1;32mRemote Control ready:\033[0m {found_url}\r\n"
                                    os.write(sys.stdout.fileno(), msg.encode("utf-8"))
                                    if open_browser:
                                        try:
                                            webbrowser.open(found_url)
                                        except Exception:
                                            pass
                    except OSError:
                        break
        proc.wait()
        return proc.returncode
    finally:
        signal.signal(signal.SIGWINCH, old_winch)
        try:
            os.close(master_fd)
        except Exception:
            pass


class RemoteSessionManager:
    """High-level coordinator for remote SSH machines and Antigravity sessions."""

    def __init__(
        self,
        registry: Optional[RemoteHostRegistry] = None,
        ssh_client: Optional[SSHClient] = None,
        cache_ttl: int = DEFAULT_CACHE_TTL,
        audit_log_path: Optional[str] = None,
    ):
        self.registry = registry or RemoteHostRegistry()
        self.ssh_client = ssh_client or SSHClient()
        self.client = self.ssh_client
        self.cache_ttl = cache_ttl
        self.audit_log_path = audit_log_path
        self._cache: Dict[str, Tuple[float, RemoteProbeResult]] = {}
        self._cache_lock = threading.Lock()

    def _log_audit(self, action: str, host_name: str, success: bool, reason: str, extra: Optional[Dict[str, Any]] = None) -> None:
        try:
            logger = AuditLogger(self.audit_log_path)
            args = {"host": host_name}
            if extra:
                # Ensure no secrets leak
                safe_extra = {
                    k: v for k, v in extra.items()
                    if "key" not in k.lower()
                    and "pass" not in k.lower()
                    and "token" not in k.lower()
                    and "secret" not in k.lower()
                    and "auth" not in k.lower()
                }
                args.update(safe_extra)
            logger.log_event(
                tool_name=action,
                tool_args=args,
                decision="allow" if success else "deny",
                reason=reason,
                category="remote_management",
            )
        except Exception:
            pass

    def test_connection(self, host_or_name: Union[str, RemoteHost]) -> Dict[str, Any]:
        """Verify basic SSH reachability and report latency."""
        host = self.registry.get_host(host_or_name) if isinstance(host_or_name, str) else host_or_name
        if not host:
            return {"ok": False, "error": f"Remote machine '{host_or_name}' not found."}

        res = self.ssh_client.run_command(host, ["uname", "-s"], connect_timeout=get_connect_timeout())
        success = res.returncode == 0
        if success:
            remote_os = res.stdout.strip()
            self._log_audit("remote_connection_test", host.name, True, f"Connection verified in {res.duration_ms}ms")
            return {
                "ok": True,
                "host": host.name,
                "latency_ms": res.duration_ms,
                "remote_os": "macOS" if remote_os == "Darwin" else ("Linux" if remote_os == "Linux" else remote_os),
            }
        else:
            err_type, err_msg = classify_ssh_error(res.returncode, res.stdout, res.stderr, res.timed_out)
            self._log_audit("remote_connection_test", host.name, False, f"Failed: {err_msg}")
            return {
                "ok": False,
                "host": host.name,
                "error": err_msg,
                "error_type": err_type,
                "latency_ms": res.duration_ms,
            }

    def probe(self, host_or_name: Union[str, RemoteHost], bypass_cache: bool = False) -> RemoteProbeResult:
        """Probe the remote machine for OS, workspace, Antigravity, and AntiAgent status."""
        host = self.registry.get_host(host_or_name) if isinstance(host_or_name, str) else host_or_name
        if not host:
            return RemoteProbeResult(
                ok=False,
                name=str(host_or_name),
                ssh_connected=False,
                error=f"Remote machine '{host_or_name}' not found in registry.",
                error_type="not_found",
            )

        now = time.time()
        if not bypass_cache:
            with self._cache_lock:
                if host.name in self._cache:
                    cached_time, cached_res = self._cache[host.name]
                    if now - cached_time < self.cache_ttl:
                        # Return cached copy with cached=True
                        cached_copy = RemoteProbeResult(
                            ok=cached_res.ok,
                            name=cached_res.name,
                            ssh_connected=cached_res.ssh_connected,
                            latency_ms=cached_res.latency_ms,
                            remote_os=cached_res.remote_os,
                            hostname=cached_res.hostname,
                            workspace_exists=cached_res.workspace_exists,
                            workspace_path=cached_res.workspace_path,
                            antigravity=cached_res.antigravity,
                            antiagent=cached_res.antiagent,
                            remote_control=cached_res.remote_control,
                            error=cached_res.error,
                            error_type=cached_res.error_type,
                            cached=True,
                            timestamp=cached_res.timestamp,
                        )
                        return cached_copy

        # Build probe command
        workspace_arg = host.workspace or ""
        agy_path_arg = host.agy_path or ""
        cmd = ["sh", "-c", _POSIX_PROBE_SCRIPT, "sh", workspace_arg, agy_path_arg]

        res = self.ssh_client.run_command(host, cmd, timeout=get_command_timeout())
        probe_res = parse_probe_output(
            stdout=res.stdout,
            stderr=res.stderr,
            exit_code=res.returncode,
            host=host,
            latency_ms=res.duration_ms,
        )

        with self._cache_lock:
            self._cache[host.name] = (now, probe_res)

        return probe_res

    def probe_all(self, bypass_cache: bool = False, max_workers: int = 4) -> List[RemoteProbeResult]:
        """Probe all saved remote hosts concurrently."""
        hosts = self.registry.list_hosts()
        if not hosts:
            return []

        results: List[RemoteProbeResult] = []
        workers = min(max_workers, len(hosts))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_host = {executor.submit(self.probe, h, bypass_cache): h for h in hosts}
            for future in concurrent.futures.as_completed(future_to_host):
                try:
                    res = future.result()
                    results.append(res)
                except Exception as e:
                    h = future_to_host[future]
                    results.append(
                        RemoteProbeResult(
                            ok=False,
                            name=h.name,
                            ssh_connected=False,
                            error=str(e),
                            error_type="probe_failed",
                        )
                    )

        # Sort to match original host order
        host_order = {h.name: i for i, h in enumerate(hosts)}
        results.sort(key=lambda r: host_order.get(r.name, 0))
        return results

    def clear_cache(self, name: Optional[str] = None) -> None:
        """Invalidate cached probe results."""
        with self._cache_lock:
            if name:
                self._cache.pop(name, None)
            else:
                self._cache.clear()

    def _resolve_agy_bin(self, host: RemoteHost) -> str:
        """Resolve path to agy binary, using host override, cached probe, or default."""
        if host.agy_path:
            return host.agy_path
        with self._cache_lock:
            cached = self._cache.get(host.name)
            if cached and cached[1].antigravity and cached[1].antigravity.path:
                return cached[1].antigravity.path
        return "agy"

    def start_remote_control(
        self,
        name: str,
        instance_name: Optional[str] = None,
        workspace: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Start the official Antigravity Remote Control daemon on the remote host."""
        host = self.registry.get_host(name)
        if not host:
            return {"ok": False, "error": f"Remote machine '{name}' not found."}

        target_name = instance_name or host.antigravity_name or host.name
        validate_instance_name(target_name)

        agy_bin = self._resolve_agy_bin(host)
        target_ws = workspace or host.workspace
        if target_ws:
            validate_workspace_path(target_ws)

        if target_ws and host.remote_os != "windows":
            start_cmd = ["sh", "-c", f"cd {quote_posix_arg(target_ws)} && {quote_posix_arg(agy_bin)} remote-control start --name {quote_posix_arg(target_name)}"]
        else:
            start_cmd = [agy_bin, "remote-control", "start", "--name", target_name]

        # Execute start command
        res = self.ssh_client.run_command(host, start_cmd, timeout=DEFAULT_SSH_DAEMON_TIMEOUT)
        self.clear_cache(name)

        # Verify whether daemon is actually running
        status_res = self.ssh_client.run_command(host, [agy_bin, "remote-control", "status"], timeout=DEFAULT_SSH_COMMAND_TIMEOUT)
        parsed_status = parse_remote_control_status(status_res.stdout, status_res.stderr, status_res.returncode)

        if parsed_status.running:
            self._log_audit(
                "remote_control_start",
                host.name,
                True,
                f"Antigravity Remote Control started for instance '{target_name}'.",
                {"instance_name": target_name},
            )
            return {
                "ok": True,
                "running": True,
                "instance_name": target_name,
                "url": parsed_status.url or OFFICIAL_ANTIGRAVITY_URL,
                "raw_status": parsed_status.raw_status,
            }
        else:
            err_msg = res.stderr.strip() or parsed_status.raw_status or "Daemon failed to verify as running."
            self._log_audit(
                "remote_control_start",
                host.name,
                False,
                f"Failed to start Antigravity Remote Control: {err_msg}",
            )
            return {
                "ok": False,
                "running": False,
                "error": f"Failed to start Antigravity Remote Control: {err_msg}",
                "raw_status": parsed_status.raw_status,
            }

    def stop_remote_control(self, name: str) -> Dict[str, Any]:
        """Stop the Antigravity Remote Control daemon on the remote host."""
        host = self.registry.get_host(name)
        if not host:
            return {"ok": False, "error": f"Remote machine '{name}' not found."}

        agy_bin = self._resolve_agy_bin(host)
        stop_cmd = [agy_bin, "remote-control", "stop"]

        res = self.ssh_client.run_command(host, stop_cmd, timeout=DEFAULT_SSH_DAEMON_TIMEOUT)
        self.clear_cache(name)

        # Verify daemon is stopped
        status_res = self.ssh_client.run_command(host, [agy_bin, "remote-control", "status"], timeout=DEFAULT_SSH_COMMAND_TIMEOUT)
        parsed_status = parse_remote_control_status(status_res.stdout, status_res.stderr, status_res.returncode)

        if not parsed_status.running:
            self._log_audit("remote_control_stop", host.name, True, "Antigravity Remote Control daemon stopped.")
            return {"ok": True, "running": False, "raw_status": parsed_status.raw_status}
        else:
            self._log_audit("remote_control_stop", host.name, False, "Daemon still reported as running.")
            return {
                "ok": False,
                "running": True,
                "error": "Failed to stop daemon; Antigravity still reports active session.",
                "raw_status": parsed_status.raw_status,
            }

    def protect_remote(self, name: str) -> Dict[str, Any]:
        """Enable AntiAgent global safety hook on the remote machine."""
        host = self.registry.get_host(name)
        if not host:
            return {"ok": False, "error": f"Remote machine '{name}' not found."}

        # 1. Probe to verify antiagent is installed
        probe_res = self.probe(host, bypass_cache=True)
        if not probe_res.antiagent.installed:
            return {
                "ok": False,
                "error": (
                    f"AntiAgent is not installed on remote host '{name}'. "
                    "AntiAgent does not automatically curl scripts from the internet. "
                    "Please install AntiAgent on the remote host first (e.g. 'pip install antiagent')."
                ),
            }

        # 2. Run antiagent install --global using discovered binary if known
        aa_bin = probe_res.antiagent.path or "antiagent"
        res = self.ssh_client.run_command(host, [aa_bin, "install", "--global"], timeout=DEFAULT_SSH_COMMAND_TIMEOUT)
        self.clear_cache(name)

        # 3. Verify status
        status_probe = self.probe(host, bypass_cache=True)
        success = status_probe.antiagent.global_hook_active

        if success:
            self._log_audit("remote_protection_enable", host.name, True, "Global hook enabled on remote machine.")
            return {
                "ok": True,
                "protection_status": "Protected",
                "message": f"AntiAgent global safety hook successfully enabled on '{name}'.",
            }
        else:
            err_msg = res.stderr.strip() or res.stdout.strip() or "Failed to activate global hook."
            self._log_audit("remote_protection_enable", host.name, False, err_msg)
            return {"ok": False, "error": err_msg}

    def doctor(self, name: str) -> Dict[str, Any]:
        """Generate comprehensive doctor diagnostics for a remote host."""
        host = self.registry.get_host(name)
        if not host:
            return {"ok": False, "error": f"Remote machine '{name}' not found."}

        probe = self.probe(host, bypass_cache=True)
        checks: List[Dict[str, Any]] = []
        has_errors = False

        # 1. SSH reachability
        if probe.ssh_connected:
            lat = f"{probe.latency_ms}ms" if probe.latency_ms is not None else "connected"
            checks.append({
                "name": "SSH Reachability",
                "status": "ok",
                "detail": f"Connected to {probe.hostname or host.ssh_host} ({lat})",
            })
        else:
            has_errors = True
            checks.append({
                "name": "SSH Reachability",
                "status": "fail",
                "detail": probe.error or "Failed to connect via OpenSSH.",
                "remedy": f"Verify connection with: ssh {host.ssh_host}",
            })

        # 2. Remote OS
        if probe.ssh_connected:
            checks.append({
                "name": "Remote Operating System",
                "status": "ok",
                "detail": f"{probe.remote_os} on host '{probe.hostname or host.ssh_host}'",
            })

        # 3. Workspace
        if host.workspace:
            if probe.workspace_exists:
                checks.append({
                    "name": "Configured Workspace",
                    "status": "ok",
                    "detail": f"Directory exists: {probe.workspace_path or host.workspace}",
                })
            else:
                checks.append({
                    "name": "Configured Workspace",
                    "status": "warn",
                    "detail": f"Directory not found: {probe.workspace_path or host.workspace}",
                    "remedy": f"Verify the path exists on {host.name}.",
                })
        else:
            checks.append({
                "name": "Configured Workspace",
                "status": "ok",
                "detail": "No default workspace specified (will use remote home directory).",
            })

        # 4. Antigravity CLI
        if probe.antigravity.installed:
            ver = f" (v{probe.antigravity.version})" if probe.antigravity.version else ""
            p = f" at {probe.antigravity.path}" if probe.antigravity.path else ""
            checks.append({
                "name": "Google Antigravity CLI",
                "status": "ok",
                "detail": f"Installed{ver}{p}",
            })
        elif probe.ssh_connected:
            has_errors = True
            checks.append({
                "name": "Google Antigravity CLI",
                "status": "fail",
                "detail": "Antigravity CLI ('agy') not found in PATH or standard install locations.",
                "remedy": "Install Google Antigravity CLI on the remote host, or specify custom --agy-path.",
            })

        # 5. Antigravity Authentication
        if probe.antigravity.installed:
            if probe.antigravity.authenticated:
                checks.append({
                    "name": "Antigravity Authentication",
                    "status": "ok",
                    "detail": "Google Antigravity is authenticated and ready.",
                })
            else:
                has_errors = True
                checks.append({
                    "name": "Antigravity Authentication",
                    "status": "warn",
                    "detail": "Authentication required to use Antigravity on this host.",
                    "remedy": f"antiagent remote login {host.name}",
                })

        # 6. Remote Control daemon
        if probe.antigravity.installed:
            if probe.remote_control.running:
                name_info = f" (Instance: '{probe.remote_control.instance_name}')" if probe.remote_control.instance_name else ""
                checks.append({
                    "name": "Remote Control Daemon",
                    "status": "ok",
                    "detail": f"Running{name_info}",
                })
            else:
                checks.append({
                    "name": "Remote Control Daemon",
                    "status": "ok" if probe.remote_control.supported else "warn",
                    "detail": "Stopped (ready to start)" if probe.remote_control.supported else "Remote control feature not supported by this agy version.",
                    "remedy": f"antiagent remote start {host.name}" if probe.remote_control.supported else None,
                })

        # 7. AntiAgent Installation
        if probe.antiagent.installed:
            ver = f" (v{probe.antiagent.version})" if probe.antiagent.version else ""
            checks.append({
                "name": "AntiAgent Remote CLI",
                "status": "ok",
                "detail": f"Installed{ver}",
            })
        elif probe.ssh_connected:
            checks.append({
                "name": "AntiAgent Remote CLI",
                "status": "warn",
                "detail": "AntiAgent is not installed on remote host.",
                "remedy": "Install with: pip install antiagent (or pipx install antiagent) on the remote machine.",
            })

        # 8. AntiAgent Protection Hook
        if probe.antiagent.installed:
            if probe.antiagent.global_hook_active:
                checks.append({
                    "name": "AntiAgent Protection Hook",
                    "status": "ok",
                    "detail": "Global security hook active. Remote Antigravity sessions are protected.",
                })
            else:
                checks.append({
                    "name": "AntiAgent Protection Hook",
                    "status": "warn",
                    "detail": "Global hook inactive. Remote tool calls will not be evaluated.",
                    "remedy": f"antiagent remote protect {host.name}",
                })

        return {
            "ok": probe.ok and not (has_errors and not probe.ssh_connected),
            "has_errors": has_errors,
            "host": host.to_dict(),
            "probe": probe.to_dict(),
            "checks": checks,
        }

    def connect_interactive(
        self,
        name: str,
        workspace_override: Optional[str] = None,
        no_open: bool = False,
    ) -> int:
        """Launch interactive PTY SSH session running `agy --remote-control`."""
        host = self.registry.get_host(name)
        if not host:
            sys.stderr.write(f"❌ Remote machine '{name}' not found in registry.\n")
            return 1

        workspace = workspace_override or host.workspace
        agy_bin = host.agy_path or "agy"

        if workspace:
            remote_cmd = f"cd {quote_posix_arg(workspace)} && {quote_posix_arg(agy_bin)} --remote-control"
        else:
            remote_cmd = f"{quote_posix_arg(agy_bin)} --remote-control"

        argv = build_ssh_argv(host, raw_command=remote_cmd, interactive=True)
        print(f"🚀 Connecting to {host.name} ({host.ssh_host})...")
        if workspace:
            print(f"📁 Remote Workspace: {workspace}")
        print("⚡ Launching Google Antigravity in Remote Control mode...\n")

        return launch_interactive_ssh(argv, open_browser=not no_open)

    def login_interactive(self, name: str) -> int:
        """Launch interactive PTY SSH session running `agy` to complete OAuth login."""
        host = self.registry.get_host(name)
        if not host:
            sys.stderr.write(f"❌ Remote machine '{name}' not found in registry.\n")
            return 1

        agy_bin = host.agy_path or "agy"
        argv = build_ssh_argv(host, raw_command=quote_posix_arg(agy_bin), interactive=True)

        print(f"🔐 Starting Antigravity login on {host.name} ({host.ssh_host})...")
        print("💡 Follow the terminal prompts to complete Google Antigravity OAuth.\n")

        ret = launch_interactive_ssh(argv, open_browser=False)
        self.clear_cache(name)

        print("\n🔍 Verifying authentication status...")
        probe = self.probe(host, bypass_cache=True)
        if probe.antigravity.authenticated:
            print(f"✅ Google Antigravity is now authenticated on {host.name}!")
        else:
            print(f"⚠️ Antigravity is still reporting authentication required on {host.name}.")

        return ret
