"""Tracks whether any `antiagent agy` PTY bridge session is running.

Lifecycle flow state is only consumed by the PTY bridge (prompt queue, Smart Enter,
Ctrl+S steering). Each running bridge registers a marker file named after its PID in
~/.antiagent/runtime/bridges/. The installed flow hook command checks for a marker with
shell builtins before starting Python, so sessions without the wrapper (plain agy,
IDE, Antigravity app) pay no interpreter startup for flow tracking.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def bridges_dir() -> Path:
    return Path(os.path.expanduser("~")) / ".antiagent" / "runtime" / "bridges"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def register_bridge(pid: Optional[int] = None) -> Path:
    """Marks a bridge session active and prunes markers left by crashed sessions."""
    d = bridges_dir()
    d.mkdir(parents=True, exist_ok=True)
    any_bridge_active()  # prune stale markers
    marker = d / str(pid or os.getpid())
    marker.write_text("", encoding="utf-8")
    return marker


def unregister_bridge(pid: Optional[int] = None) -> None:
    try:
        (bridges_dir() / str(pid or os.getpid())).unlink()
    except OSError:
        pass


def any_bridge_active() -> bool:
    """True if at least one live bridge marker exists. Removes markers of dead PIDs."""
    try:
        entries = list(os.scandir(bridges_dir()))
    except OSError:
        return False
    active = False
    for entry in entries:
        try:
            pid = int(entry.name)
        except ValueError:
            continue
        if _pid_alive(pid):
            active = True
        else:
            try:
                os.unlink(entry.path)
            except OSError:
                pass
    return active
