"""Opt-in lifecycle hook profiler (ANTIAGENT_PROFILE_HOOKS=1).

When disabled (the default) every entry point is a cheap no-op. When enabled,
hook entrypoints record per-invocation timings as JSON lines in
~/.antiagent/runtime/hook_profile.jsonl. The profiler never writes to stdout
(stdout is the Antigravity hook protocol) and swallows all of its own errors.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

ENV_VAR = "ANTIAGENT_PROFILE_HOOKS"

# Captured as early as possible so in-process time includes antiagent imports
# that happen after this module is first loaded.
_MODULE_LOADED_NS = time.perf_counter_ns()

_spans: Dict[str, int] = {}


def enabled() -> bool:
    return os.environ.get(ENV_VAR, "").strip().lower() in ("1", "true", "yes")


@contextmanager
def span(name: str) -> Iterator[None]:
    """Accumulates elapsed nanoseconds under `name` while profiling is enabled."""
    if not enabled():
        yield
        return
    start = time.perf_counter_ns()
    try:
        yield
    finally:
        _spans[name] = _spans.get(name, 0) + (time.perf_counter_ns() - start)


def reset() -> None:
    _spans.clear()


def snapshot_ms() -> Dict[str, float]:
    return {f"{k}_ms": round(v / 1e6, 3) for k, v in _spans.items()}


def profile_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".antiagent", "runtime", "hook_profile.jsonl")


def emit(
    event: str,
    started_ns: int,
    conversation_id: Optional[str] = None,
    step_idx: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Appends one profile record. Never raises."""
    if not enabled():
        return
    try:
        finished_ns = time.perf_counter_ns()
        record: Dict[str, Any] = {
            "event": event,
            "pid": os.getpid(),
            "conversation_id": conversation_id,
            "step_idx": step_idx,
            "wall_time": time.time(),
            "started_ns": started_ns,
            "finished_ns": finished_ns,
            "duration_ms": round((finished_ns - started_ns) / 1e6, 3),
            "since_profiler_import_ms": round((finished_ns - _MODULE_LOADED_NS) / 1e6, 3),
        }
        record.update(snapshot_ms())
        if extra:
            record.update(extra)
        path = profile_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass
    finally:
        reset()
