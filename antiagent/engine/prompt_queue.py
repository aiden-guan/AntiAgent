"""Codex-style FIFO Prompt Queue and Safe Dispatch Contract.

Ensures user turns are queued safely during active runs or interactive prompts,
held atomically per conversation, and released strictly one-at-a-time when safe.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from antiagent.engine.interaction_state import (
    ActiveSurface,
    ConversationState,
    InteractionState,
    _sanitize_filename,
    get_runtime_dir,
)

PromptSource = Literal["tab", "enter", "steer"]
PromptStatus = Literal["queued", "dispatching", "sent", "cancelled", "failed", "held"]

DEFAULT_MAX_QUEUE_DEPTH = 50
DEFAULT_MAX_PROMPT_BYTES = 100_000


class PromptQueueError(Exception):
    """Base exception for prompt queue operations."""


class QueueFullError(PromptQueueError):
    """Raised when the prompt queue reaches maximum capacity."""


class PromptTooLargeError(PromptQueueError):
    """Raised when a prompt exceeds the maximum byte limit."""


class EmptyPromptError(PromptQueueError):
    """Raised when attempting to queue an empty prompt."""


@dataclass
class QueuedPrompt:
    """A prompt submitted by the user and held in the queue."""

    id: str
    conversation_id: str
    text: str
    created_at: float
    source: PromptSource
    status: PromptStatus = "queued"
    retry_count: int = 0
    dispatched_at: Optional[float] = None
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> QueuedPrompt:
        return cls(
            id=data["id"],
            conversation_id=data.get("conversation_id", ""),
            text=data["text"],
            created_at=float(data.get("created_at", 0.0)),
            source=data.get("source", "enter"),
            status=data.get("status", "queued"),
            retry_count=int(data.get("retry_count", 0)),
            dispatched_at=float(data["dispatched_at"]) if data.get("dispatched_at") is not None else None,
            note=data.get("note"),
        )


def can_dispatch_user_turn(
    state: ConversationState,
    queue: PromptQueue,
    current_conversation_id: str,
    active_surface: Optional[ActiveSurface] = None,
    lease_owner: Optional[str] = None,
) -> bool:
    """Predicate governing whether a queued user turn can be safely dispatched.

    Returns True ONLY when affirmative evidence of safety is satisfied:
    1. Agent execution is fully idle (state == IDLE, fully_idle == True).
    2. No unresolved approval exists (not AWAITING_APPROVAL, not pending_approval).
    3. No preview exists (not AWAITING_PREVIEW, not pending_preview).
    4. No question/input request exists (not AWAITING_QUESTION, not pending_question).
    5. No modal is consuming Enter (surface is PROMPT or default).
    6. No interruption transition is occurring (not INTERRUPTING, not STEERING).
    7. No previous queued item is currently dispatching.
    8. Conversation identity is still the expected conversation.
    9. Queue has at least one 'queued' item.
    10. State is NOT UNKNOWN (affirmative evidence of safety required).
    """
    if not current_conversation_id:
        return False

    if queue.conversation_id != current_conversation_id:
        return False

    # 1. State must be affirmatively IDLE
    if state.state != InteractionState.IDLE:
        return False

    if not state.fully_idle:
        return False

    # 2. No unresolved approvals
    if state.state == InteractionState.AWAITING_APPROVAL or state.pending_approval:
        return False

    # 3. No unresolved preview
    if state.state == InteractionState.AWAITING_PREVIEW or state.pending_preview:
        return False

    # 4. No unresolved question modal
    if state.state == InteractionState.AWAITING_QUESTION or state.pending_question:
        return False

    # 5. Active surface must be PROMPT
    surface = active_surface or state.active_surface
    if surface not in (ActiveSurface.PROMPT, None):
        return False

    # 6. No interruption or steering in-flight
    if state.state in (InteractionState.INTERRUPTING, InteractionState.STEERING):
        return False

    # 7. No dispatch lease currently held by another worker
    now = time.time()
    if state.dispatch_lease_owner and state.dispatch_lease_expires > now:
        if lease_owner is None or state.dispatch_lease_owner != lease_owner:
            return False

    # 8. Queue must have ready queued items and no currently dispatching items
    if queue.has_dispatching_item():
        return False

    if not queue.has_queued_items():
        return False

    return True


def recover_stale_sessions(runtime_dir: Optional[Path] = None) -> int:
    """Scans runtime directory and marks any leftover queued/dispatching items from
    prior crashed sessions as 'held' so they never auto-replay on restart."""
    r_dir = runtime_dir or get_runtime_dir()
    if not r_dir.is_dir():
        return 0
    total_held = 0
    for qfile in r_dir.glob("queue_*.json"):
        try:
            data = json.loads(qfile.read_text(encoding="utf-8"))
            if isinstance(data, list):
                changed = False
                for item in data:
                    if item.get("status") in ("queued", "dispatching"):
                        item["status"] = "held"
                        item["note"] = "Held on session recovery"
                        changed = True
                        total_held += 1
                if changed:
                    qfile.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass
    return total_held


class PromptQueue:
    """Thread-safe, conversation-scoped, FIFO prompt queue with atomic persistence."""

    def __init__(
        self,
        conversation_id: str,
        runtime_dir: Optional[Path] = None,
        max_depth: int = DEFAULT_MAX_QUEUE_DEPTH,
        max_bytes: int = DEFAULT_MAX_PROMPT_BYTES,
        recover_as_held: bool = False,
    ):
        self.conversation_id = conversation_id or "default"
        self.runtime_dir = runtime_dir or get_runtime_dir()
        self.max_depth = max_depth
        self.max_bytes = max_bytes
        self._lock = threading.RLock()
        self._items: List[QueuedPrompt] = []
        self._file_mtime_ns: Optional[int] = None
        self._load_and_initialize(recover_as_held=recover_as_held)

    def _queue_file(self) -> Path:
        safe_id = _sanitize_filename(self.conversation_id)
        return self.runtime_dir / f"queue_{safe_id}.json"

    def _load_and_initialize(self, recover_as_held: bool = False) -> None:
        """Loads persisted items from disk.

        If recover_as_held is True (on session launch), items that were previously
        'queued' or 'dispatching' are held so a restart never blindly auto-replays.
        """
        path = self._queue_file()
        if not path.is_file():
            return

        try:
            stat = path.stat()
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                loaded = [QueuedPrompt.from_dict(d) for d in data]
                if recover_as_held:
                    for item in loaded:
                        if item.status in ("queued", "dispatching"):
                            item.status = "held"
                            item.note = "Held on session recovery"
                self._items = loaded
                self._file_mtime_ns = stat.st_mtime_ns
                if recover_as_held:
                    self._persist()
        except Exception:
            self._items = []

    def _sync_from_disk(self) -> None:
        """Reloads items from disk if the queue file has been modified by another process."""
        path = self._queue_file()
        if not path.is_file():
            return
        try:
            stat = path.stat()
            if self._file_mtime_ns is not None and stat.st_mtime_ns == self._file_mtime_ns:
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._items = [QueuedPrompt.from_dict(d) for d in data]
                self._file_mtime_ns = stat.st_mtime_ns
        except Exception:
            pass

    def _persist(self) -> None:
        """Atomically persists items to disk with restricted permissions."""
        path = self._queue_file()
        content = json.dumps([item.to_dict() for item in self._items], indent=2)
        target_dir = path.parent
        target_dir.mkdir(parents=True, exist_ok=True)

        prefix = f".tmp_queue_{path.stem}_"
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

            os.replace(temp_file, path)
            try:
                self._file_mtime_ns = path.stat().st_mtime_ns
            except OSError:
                pass
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass

    def enqueue(
        self,
        text: str,
        source: PromptSource = "enter",
        at_front: bool = False,
    ) -> QueuedPrompt:
        """Enqueues a prompt atomically.

        Args:
            text: Prompt text. Must not be empty or whitespace-only.
            source: Source of enqueue ('tab', 'enter', 'steer').
            at_front: If True (e.g. high-priority steer), inserts at the front.

        Returns:
            The created QueuedPrompt.

        Raises:
            EmptyPromptError: if text is empty/whitespace.
            PromptTooLargeError: if prompt exceeds max_bytes.
            QueueFullError: if active queue exceeds max_depth.
        """
        cleaned = text.strip()
        if not cleaned:
            raise EmptyPromptError("Cannot queue an empty prompt.")

        text_bytes = len(cleaned.encode("utf-8"))
        if text_bytes > self.max_bytes:
            raise PromptTooLargeError(
                f"Prompt size ({text_bytes} bytes) exceeds limit of {self.max_bytes} bytes."
            )

        with self._lock:
            self._sync_from_disk()
            active_count = sum(1 for item in self._items if item.status in ("queued", "dispatching"))
            if active_count >= self.max_depth:
                raise QueueFullError(
                    f"Queue reached maximum depth ({self.max_depth} items)."
                )

            # Prevent unbounded memory/file growth by pruning old sent/terminal items beyond 2x depth
            if len(self._items) > self.max_depth * 2:
                terminal_indices = [
                    idx for idx, it in enumerate(self._items)
                    if it.status in ("sent", "cancelled", "failed")
                ]
                if terminal_indices:
                    to_remove = len(self._items) - (self.max_depth * 2)
                    remove_set = set(terminal_indices[:to_remove])
                    self._items = [it for idx, it in enumerate(self._items) if idx not in remove_set]

            item = QueuedPrompt(
                id=str(uuid.uuid4()),
                conversation_id=self.conversation_id,
                text=cleaned,
                created_at=time.time(),
                source=source,
                status="queued",
            )
            if at_front:
                # Find insertion index after any currently dispatching item
                insert_idx = 0
                while insert_idx < len(self._items) and self._items[insert_idx].status == "dispatching":
                    insert_idx += 1
                self._items.insert(insert_idx, item)
            else:
                self._items.append(item)

            self._persist()
            return item

    def peek(self) -> Optional[QueuedPrompt]:
        """Returns the next 'queued' item without removing it."""
        with self._lock:
            self._sync_from_disk()
            for item in self._items:
                if item.status == "queued":
                    return item
            return None

    def pop_next_for_dispatch(self) -> Optional[QueuedPrompt]:
        """Atomically marks the next 'queued' item as 'dispatching' and returns it."""
        with self._lock:
            self._sync_from_disk()
            for item in self._items:
                if item.status == "queued":
                    item.status = "dispatching"
                    item.dispatched_at = time.time()
                    self._persist()
                    return item
            return None

    def mark_sent(self, prompt_id: str) -> None:
        """Marks an item as successfully sent."""
        with self._lock:
            self._sync_from_disk()
            for item in self._items:
                if item.id == prompt_id:
                    item.status = "sent"
                    self._persist()
                    return

    def mark_failed(self, prompt_id: str, reason: str = "") -> None:
        """Marks an item as failed."""
        with self._lock:
            self._sync_from_disk()
            for item in self._items:
                if item.id == prompt_id:
                    item.status = "failed"
                    item.note = reason
                    item.retry_count += 1
                    self._persist()
                    return

    def mark_cancelled(self, prompt_id: str) -> None:
        """Marks an item as cancelled."""
        with self._lock:
            self._sync_from_disk()
            for item in self._items:
                if item.id == prompt_id:
                    item.status = "cancelled"
                    self._persist()
                    return

    def resume_held_items(self) -> int:
        """Transitions held items back to queued status."""
        count = 0
        with self._lock:
            self._sync_from_disk()
            for item in self._items:
                if item.status == "held":
                    item.status = "queued"
                    item.note = None
                    count += 1
            if count > 0:
                self._persist()
        return count

    def clear(self, status_filter: Optional[PromptStatus] = None) -> int:
        """Clears items from the queue. If status_filter is None, clears all non-dispatching."""
        removed = 0
        with self._lock:
            self._sync_from_disk()
            remaining: List[QueuedPrompt] = []
            for item in self._items:
                if item.status == "dispatching":
                    remaining.append(item)
                elif status_filter is None or item.status == status_filter:
                    removed += 1
                else:
                    remaining.append(item)
            self._items = remaining
            self._persist()
        return removed

    def has_queued_items(self) -> bool:
        """Returns True if at least one item has status 'queued'."""
        with self._lock:
            self._sync_from_disk()
            return any(item.status == "queued" for item in self._items)

    def has_dispatching_item(self, timeout_seconds: float = 60.0) -> bool:
        """Returns True if any item is currently in 'dispatching' status within timeout."""
        with self._lock:
            self._sync_from_disk()
            now = time.time()
            has_active = False
            mutated = False
            for item in self._items:
                if item.status == "dispatching":
                    if item.dispatched_at and (now - item.dispatched_at) > timeout_seconds:
                        item.status = "failed"
                        item.note = "Dispatch timed out"
                        mutated = True
                    else:
                        has_active = True
            if mutated:
                self._persist()
            return has_active

    def queued_count(self) -> int:
        """Returns number of items currently in 'queued' status."""
        with self._lock:
            self._sync_from_disk()
            return sum(1 for item in self._items if item.status == "queued")

    def total_count(self) -> int:
        """Returns total number of items in memory."""
        with self._lock:
            self._sync_from_disk()
            return len(self._items)

    def list_items(self) -> List[QueuedPrompt]:
        """Returns a snapshot of all queued items."""
        with self._lock:
            self._sync_from_disk()
            return list(self._items)
