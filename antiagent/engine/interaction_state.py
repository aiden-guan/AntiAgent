"""Interaction State Machine and State Store for Antigravity & AntiAgent.

Maintains fine-grained interaction states and active UI surface tracking,
bridging Antigravity lifecycle hooks, terminal observations, and keyboard controls.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from antiagent.config import get_global_config_dir


class InteractionState(str, Enum):
    """Execution and lifecycle states of the Antigravity agent."""

    IDLE = "IDLE"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    AWAITING_QUESTION = "AWAITING_QUESTION"
    AWAITING_PREVIEW = "AWAITING_PREVIEW"
    INTERRUPTING = "INTERRUPTING"
    STEERING = "STEERING"
    BACKGROUND_BUSY = "BACKGROUND_BUSY"
    UNKNOWN = "UNKNOWN"


class ActiveSurface(str, Enum):
    """Focused UI surface within the Antigravity TUI."""

    PROMPT = "PROMPT"
    APPROVAL = "APPROVAL"
    QUESTION = "QUESTION"
    TEAMWORK_PREVIEW = "TEAMWORK_PREVIEW"
    ARTIFACT_REVIEW = "ARTIFACT_REVIEW"
    SETTINGS = "SETTINGS"
    DIFF_REVIEW = "DIFF_REVIEW"
    UNKNOWN_MODAL = "UNKNOWN_MODAL"


@dataclass
class ConversationState:
    """Interaction and lifecycle state for a specific conversation."""

    conversation_id: str
    state: InteractionState = InteractionState.UNKNOWN
    active_surface: ActiveSurface = ActiveSurface.PROMPT
    last_event: str = ""
    last_event_time: float = 0.0
    step_idx: int = -1
    invocation_num: int = -1
    execution_num: int = -1
    fully_idle: bool = False
    pending_approval: bool = False
    pending_preview: bool = False
    pending_question: bool = False
    dispatch_lease_owner: Optional[str] = None
    dispatch_lease_expires: float = 0.0
    active_tool: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "state": self.state.value if isinstance(self.state, InteractionState) else str(self.state),
            "active_surface": self.active_surface.value if isinstance(self.active_surface, ActiveSurface) else str(self.active_surface),
            "last_event": self.last_event,
            "last_event_time": self.last_event_time,
            "step_idx": self.step_idx,
            "invocation_num": self.invocation_num,
            "execution_num": self.execution_num,
            "fully_idle": self.fully_idle,
            "pending_approval": self.pending_approval,
            "pending_preview": self.pending_preview,
            "pending_question": self.pending_question,
            "dispatch_lease_owner": self.dispatch_lease_owner,
            "dispatch_lease_expires": self.dispatch_lease_expires,
            "active_tool": self.active_tool,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ConversationState:
        raw_state = data.get("state", InteractionState.UNKNOWN.value)
        try:
            state = InteractionState(raw_state)
        except ValueError:
            state = InteractionState.UNKNOWN

        raw_surface = data.get("active_surface", ActiveSurface.PROMPT.value)
        try:
            surface = ActiveSurface(raw_surface)
        except ValueError:
            surface = ActiveSurface.PROMPT

        return cls(
            conversation_id=data.get("conversation_id", ""),
            state=state,
            active_surface=surface,
            last_event=data.get("last_event", ""),
            last_event_time=float(data.get("last_event_time", 0.0)),
            step_idx=int(data.get("step_idx", -1)),
            invocation_num=int(data.get("invocation_num", -1)),
            execution_num=int(data.get("execution_num", -1)),
            fully_idle=bool(data.get("fully_idle", False)),
            pending_approval=bool(data.get("pending_approval", False)),
            pending_preview=bool(data.get("pending_preview", False)),
            pending_question=bool(data.get("pending_question", False)),
            dispatch_lease_owner=data.get("dispatch_lease_owner"),
            dispatch_lease_expires=float(data.get("dispatch_lease_expires", 0.0)),
            active_tool=data.get("active_tool"),
            metadata=dict(data.get("metadata", {})),
        )


# Strips ANSI escape sequences for text observation
_ANSI_STRIP_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Centralized fallback patterns for interactive TUI surfaces
_TEAMWORK_PREVIEW_PATTERNS = [
    re.compile(r"(?i)(?:/teamwork-preview|teamwork preview|proposed team:|accept team|approve teamwork)"),
]

_QUESTION_PATTERNS = [
    re.compile(r"(?i)(?:ask_question|\(submit/skip\)|select all options that apply|select an option:)"),
]

_APPROVAL_PATTERNS = [
    re.compile(r"(?i)(?:permission required|requires confirmation|allow once|always allow|approve\s*\[y/n\])"),
]

_SETTINGS_PATTERNS = [
    re.compile(r"(?i)(?:antigravity settings|agy configuration|settings\s*>\s*)"),
]


def strip_ansi(text: str) -> str:
    """Removes ANSI escape codes from terminal output."""
    return _ANSI_STRIP_RE.sub("", text)


def detect_surface_from_terminal_output(raw_output: str) -> Optional[Tuple[InteractionState, ActiveSurface]]:
    """Centralized detector for interactive UI surfaces observed in terminal output.

    Used ONLY as a fallback or supplemental observer for surfaces that the lifecycle
    hook cannot detect directly.
    """
    clean = strip_ansi(raw_output)
    if not clean:
        return None

    # Check teamwork preview first
    for pat in _TEAMWORK_PREVIEW_PATTERNS:
        if pat.search(clean):
            return InteractionState.AWAITING_PREVIEW, ActiveSurface.TEAMWORK_PREVIEW

    # Check question modals
    for pat in _QUESTION_PATTERNS:
        if pat.search(clean):
            return InteractionState.AWAITING_QUESTION, ActiveSurface.QUESTION

    # Check permission approvals
    for pat in _APPROVAL_PATTERNS:
        if pat.search(clean):
            return InteractionState.AWAITING_APPROVAL, ActiveSurface.APPROVAL

    # Check settings screen
    for pat in _SETTINGS_PATTERNS:
        if pat.search(clean):
            return InteractionState.IDLE, ActiveSurface.SETTINGS

    return None


def get_runtime_dir() -> Path:
    """Returns the user-only runtime directory ~/.antiagent/runtime."""
    runtime_dir = get_global_config_dir() / "runtime"
    if not runtime_dir.exists():
        runtime_dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            try:
                os.chmod(runtime_dir, 0o700)
            except OSError:
                pass
    return runtime_dir


def _sanitize_filename(name: str) -> str:
    """Sanitize string for safe filenames."""
    sanitized = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", name)
    return sanitized or "default"


class InteractionStateStore:
    """Thread-safe and process-safe persistent store for conversation interaction states."""

    _instance: Optional[InteractionStateStore] = None
    _lock = threading.RLock()

    def __init__(self, runtime_dir: Optional[Path] = None):
        self.runtime_dir = runtime_dir or get_runtime_dir()
        self._memory_cache: Dict[str, ConversationState] = {}
        self._file_mtime_ns: Dict[str, int] = {}
        self._cache_lock = threading.RLock()

    @classmethod
    def default(cls) -> InteractionStateStore:
        """Returns the singleton store instance."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _state_file(self, conversation_id: str) -> Path:
        safe_id = _sanitize_filename(conversation_id)
        return self.runtime_dir / f"state_{safe_id}.json"

    def _active_cid_file(self) -> Path:
        return self.runtime_dir / "active_conversation.txt"

    def get_active_conversation_id(self) -> Optional[str]:
        """Returns the ID of the currently active/focused conversation, if recorded."""
        path = self._active_cid_file()
        if path.is_file():
            try:
                cid = path.read_text(encoding="utf-8").strip()
                return cid if cid else None
            except OSError:
                pass
        return None

    def set_active_conversation_id(self, conversation_id: str) -> None:
        """Records the ID of the currently active conversation."""
        if not conversation_id:
            return
        path = self._active_cid_file()
        try:
            self._write_atomic(path, conversation_id)
        except Exception:
            pass

    def get_state(self, conversation_id: str) -> ConversationState:
        """Retrieves state for a conversation, consulting memory cache and disk."""
        cid = conversation_id or "default"
        path = self._state_file(cid)

        with self._cache_lock:
            if path.is_file():
                try:
                    stat = path.stat()
                    last_mtime = self._file_mtime_ns.get(cid)
                    # Return memory cache if file has not changed on disk
                    if cid in self._memory_cache and last_mtime == stat.st_mtime_ns:
                        return self._memory_cache[cid]

                    data = json.loads(path.read_text(encoding="utf-8"))
                    state = ConversationState.from_dict(data)
                    self._memory_cache[cid] = state
                    self._file_mtime_ns[cid] = stat.st_mtime_ns
                    return state
                except Exception:
                    if cid in self._memory_cache:
                        return self._memory_cache[cid]
            elif cid in self._memory_cache:
                return self._memory_cache[cid]

            # Default initial state
            state = ConversationState(conversation_id=cid)
            self._memory_cache[cid] = state
            return state

    def save_state(self, state: ConversationState) -> None:
        """Persists conversation state atomically to disk and memory."""
        cid = state.conversation_id or "default"
        path = self._state_file(cid)
        content = json.dumps(state.to_dict(), indent=2)
        self._write_atomic(path, content)

        with self._cache_lock:
            self._memory_cache[cid] = state
            try:
                self._file_mtime_ns[cid] = path.stat().st_mtime_ns
            except OSError:
                pass

    def update_state(self, conversation_id: str, **kwargs: Any) -> ConversationState:
        """Atomically updates specific fields of the conversation state."""
        with self._cache_lock:
            current = self.get_state(conversation_id)
            data = current.to_dict()
            for k, v in kwargs.items():
                if k in data:
                    data[k] = v
            new_state = ConversationState.from_dict(data)
            self.save_state(new_state)
            return new_state

    def acquire_dispatch_lease(
        self,
        conversation_id: str,
        lease_holder: str,
        ttl_seconds: float = 5.0,
    ) -> bool:
        """Atomically acquires a temporary lease to dispatch a queued prompt.

        Prevents duplicate drains from multiple rapid lifecycle events.
        """
        with self._cache_lock:
            state = self.get_state(conversation_id)
            now = time.time()
            if (
                state.dispatch_lease_owner
                and state.dispatch_lease_owner != lease_holder
                and state.dispatch_lease_expires > now
            ):
                return False

            state.dispatch_lease_owner = lease_holder
            state.dispatch_lease_expires = now + ttl_seconds
            self.save_state(state)
            return True

    def release_dispatch_lease(self, conversation_id: str, lease_holder: str) -> None:
        """Releases a dispatch lease if held by lease_holder."""
        with self._cache_lock:
            state = self.get_state(conversation_id)
            if state.dispatch_lease_owner == lease_holder:
                state.dispatch_lease_owner = None
                state.dispatch_lease_expires = 0.0
                self.save_state(state)

    def _write_atomic(self, target_path: Path, content: str) -> None:
        """Writes content to target_path using an atomic replace with user-only permissions."""
        target_dir = target_path.parent
        target_dir.mkdir(parents=True, exist_ok=True)

        prefix = f".tmp_{target_path.stem}_"
        fd, temp_file = tempfile.mkstemp(dir=target_dir, prefix=prefix, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                if hasattr(os, "fsync"):
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass

            if os.name != "nt":
                try:
                    os.chmod(temp_file, 0o600)
                except OSError:
                    pass

            os.replace(temp_file, target_path)
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
