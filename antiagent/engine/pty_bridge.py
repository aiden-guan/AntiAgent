"""Interactive AGY PTY Bridge & Keyboard Interceptor.

Provides state-aware keyboard interception for Google Antigravity (AGY) CLI:
- Tab: Queues prompt while agent is running/busy.
- Enter: Submits immediately if idle; queues without submitting if agent is running
         or waiting at an approval/preview/question modal.
- Ctrl+S: Steers the active agent with a controlled single interruption.
"""

from __future__ import annotations

import os
import select
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from antiagent.config import AntiAgentConfig, load_config
from antiagent.engine import pty as pty_engine
from antiagent.engine.flow_presence import register_bridge, unregister_bridge
from antiagent.engine.interaction_state import (
    ActiveSurface,
    ConversationState,
    InteractionState,
    InteractionStateStore,
    detect_surface_from_terminal_output,
    strip_ansi,
)
from antiagent.engine.prompt_queue import (
    PromptQueue,
    QueuedPrompt,
    can_dispatch_user_turn,
)
from antiagent.engine.pty import (
    raw_terminal_context,
    sync_window_size,
)


class PromptBuffer:
    """Maintains an in-memory mirror of the text typed into the AGY prompt editor."""

    def __init__(self) -> None:
        self._chars: List[str] = []
        self._cursor: int = 0
        self._lock = threading.RLock()

    @property
    def text(self) -> str:
        """Returns the current buffered text."""
        with self._lock:
            return "".join(self._chars)

    @property
    def is_empty(self) -> bool:
        """Returns True if the buffer contains only whitespace or nothing."""
        return not self.text.strip()

    @property
    def line_count(self) -> int:
        """Returns the number of lines currently in the buffer."""
        with self._lock:
            return "".join(self._chars).count("\n") + 1

    def insert(self, s: str) -> None:
        """Inserts text at current cursor position."""
        with self._lock:
            for ch in s:
                self._chars.insert(self._cursor, ch)
                self._cursor += 1

    def insert_newline(self) -> None:
        """Inserts a newline character at current cursor position."""
        with self._lock:
            self._chars.insert(self._cursor, "\n")
            self._cursor += 1

    def backspace(self) -> None:
        """Deletes character before cursor."""
        with self._lock:
            if self._cursor > 0:
                self._cursor -= 1
                self._chars.pop(self._cursor)

    def delete(self) -> None:
        """Deletes character at cursor."""
        with self._lock:
            if self._cursor < len(self._chars):
                self._chars.pop(self._cursor)

    def delete_word_left(self) -> None:
        """Deletes previous word before cursor (Ctrl+W or Alt+Backspace)."""
        with self._lock:
            if self._cursor == 0:
                return
            idx = self._cursor
            # Skip trailing spaces before cursor
            while idx > 0 and self._chars[idx - 1].isspace() and self._chars[idx - 1] != "\n":
                idx -= 1
            # Skip word characters
            while idx > 0 and not self._chars[idx - 1].isspace():
                idx -= 1
            count = self._cursor - idx
            for _ in range(count):
                self._cursor -= 1
                self._chars.pop(self._cursor)

    def kill_to_start(self) -> None:
        """Deletes characters from cursor to start of current line (Ctrl+U)."""
        with self._lock:
            if self._cursor == 0:
                return
            idx = self._cursor
            while idx > 0 and self._chars[idx - 1] != "\n":
                idx -= 1
            count = self._cursor - idx
            if count == 0 and idx > 0:
                count = 1
                idx -= 1
            for _ in range(count):
                self._cursor -= 1
                self._chars.pop(self._cursor)

    def kill_to_end(self) -> None:
        """Deletes characters from cursor to end of current line (Ctrl+K)."""
        with self._lock:
            if self._cursor >= len(self._chars):
                return
            idx = self._cursor
            while idx < len(self._chars) and self._chars[idx] != "\n":
                idx += 1
            count = idx - self._cursor
            if count == 0 and idx < len(self._chars):
                count = 1
            for _ in range(count):
                self._chars.pop(self._cursor)

    def cursor_left(self) -> None:
        with self._lock:
            self._cursor = max(0, self._cursor - 1)

    def cursor_right(self) -> None:
        with self._lock:
            self._cursor = min(len(self._chars), self._cursor + 1)

    def cursor_home(self) -> None:
        with self._lock:
            self._cursor = 0

    def cursor_end(self) -> None:
        with self._lock:
            self._cursor = len(self._chars)

    def clear(self) -> None:
        with self._lock:
            self._chars.clear()
            self._cursor = 0


def parse_input_events(raw_bytes: bytes) -> Iterator[Tuple[str, bytes]]:
    """Splits raw terminal input bytes into recognized key events and sequences.

    Yields (event_name, event_bytes).
    """
    i = 0
    n = len(raw_bytes)

    while i < n:
        b = raw_bytes[i : i + 1]

        # Enter (Carriage return \r)
        if b == b"\r":
            yield ("enter", b)
            i += 1
            continue

        # Linefeed / Ctrl+J (Newline in multiline prompts)
        if b == b"\n":
            yield ("ctrl+j", b)
            i += 1
            continue

        # Tab
        if b == b"\t":
            yield ("tab", b)
            i += 1
            continue

        # Ctrl+S (\x13)
        if b == b"\x13":
            yield ("ctrl+s", b)
            i += 1
            continue

        # Ctrl+W (\x17 - word backspace)
        if b == b"\x17":
            yield ("word_backspace", b)
            i += 1
            continue

        # Ctrl+U (\x15 - kill line to start)
        if b == b"\x15":
            yield ("kill_to_start", b)
            i += 1
            continue

        # Ctrl+K (\x0b - kill line to end)
        if b == b"\x0b":
            yield ("kill_to_end", b)
            i += 1
            continue

        # Backspace (\x7f or \x08)
        if b in (b"\x7f", b"\x08"):
            yield ("backspace", b)
            i += 1
            continue

        # ANSI Escape Sequences
        if b == b"\x1b":
            # Bracketed paste: \x1b[200~ ... \x1b[201~
            if raw_bytes[i:].startswith(b"\x1b[200~"):
                end_paste = raw_bytes.find(b"\x1b[201~", i + 6)
                if end_paste != -1:
                    paste_bytes = raw_bytes[i : end_paste + 6]
                    yield ("bracketed_paste", paste_bytes)
                    i = end_paste + 6
                    continue

            # Alt+Backspace / Option+Backspace (\x1b\x7f or \x1b\x08)
            # Must NOT be treated as bare escape (which would cancel AGY run!)
            if raw_bytes[i:].startswith((b"\x1b\x7f", b"\x1b\x08")):
                yield ("word_backspace", raw_bytes[i : i + 2])
                i += 2
                continue

            # Shift+Enter sequences (CSI u, xterm modified key, etc.)
            shift_enter_seqs = (
                b"\x1b[13;2u",
                b"\x1b[27;2;13~",
                b"\x1bOM",
                b"\x1b[13;5u",
                b"\x1b[27;5;13~",
            )
            matched_se = False
            for se in shift_enter_seqs:
                if raw_bytes[i:].startswith(se):
                    yield ("shift_enter", se)
                    i += len(se)
                    matched_se = True
                    break
            if matched_se:
                continue

            # Check common multi-byte sequences
            seq_candidates = [
                (b"\x1b[3~", "delete"),
                (b"\x1b[3;5~", "delete"),
                (b"\x1b[D", "left"),
                (b"\x1b[C", "right"),
                (b"\x1b[A", "up"),
                (b"\x1b[B", "down"),
                (b"\x1b[H", "home"),
                (b"\x1b[F", "end"),
                (b"\x1b[1~", "home"),
                (b"\x1b[4~", "end"),
                (b"\x1b[1;5D", "word_left"),
                (b"\x1b[1;5C", "word_right"),
                (b"\x1b[1;3D", "word_left"),
                (b"\x1b[1;3C", "word_right"),
            ]
            matched = False
            for seq, name in seq_candidates:
                if raw_bytes[i:].startswith(seq):
                    yield (name, seq)
                    i += len(seq)
                    matched = True
                    break
            if matched:
                continue

            # Check generic CSI sequence \x1b[ ... [@-~] or SS3 \x1bO ...
            if i + 1 < n and raw_bytes[i + 1 : i + 2] in (b"[", b"O"):
                j = i + 2
                while j < n and not (0x40 <= raw_bytes[j] <= 0x7E):
                    j += 1
                if j < n:
                    j += 1
                    yield ("escape_seq", raw_bytes[i:j])
                    i = j
                    continue

            # Standalone Escape
            yield ("escape", b"\x1b")
            i += 1
            continue

        # Ctrl+A (Home)
        if b == b"\x01":
            yield ("home", b)
            i += 1
            continue

        # Ctrl+E (End)
        if b == b"\x05":
            yield ("end", b)
            i += 1
            continue

        # Regular UTF-8 characters
        lead = b[0]
        char_len = 1
        if (lead & 0xE0) == 0xC0:
            char_len = 2
        elif (lead & 0xF0) == 0xE0:
            char_len = 3
        elif (lead & 0xF8) == 0xF0:
            char_len = 4

        char_bytes = raw_bytes[i : i + char_len]
        yield ("char", char_bytes)
        i += len(char_bytes)


def show_transient_notice(msg: str, out_fd: Optional[int] = None) -> None:
    """Displays a transient notification in the terminal, preserving cursor position."""
    if out_fd is None:
        try:
            out_fd = sys.stdout.fileno()
        except Exception:
            return

    # Use ANSI save/restore cursor without issuing extra newlines that scroll the screen
    formatted = f"\x1b7\r\x1b[2K\x1b[1;36m{msg}\x1b[0m\x1b8"
    try:
        os.write(out_fd, formatted.encode("utf-8"))
    except OSError:
        pass


def get_clear_editor_bytes(prompt_len: int = 0, line_count: int = 1) -> bytes:
    """Produces keystrokes to clear the current line in AGY's prompt editor without Escape.

    Uses Ctrl+U (\x15), Ctrl+A Ctrl+K (\x01\x0b), and backspaces (\x7f) without sending spaces.
    """
    clear_line = b"\x01\x0b\x15" + (b"\x7f" * min(max(prompt_len, 0), 300))
    if line_count <= 1:
        return clear_line
    seq = bytearray(clear_line)
    for _ in range(line_count - 1):
        seq.extend(b"\x1b[A\x01\x0b\x15" + (b"\x7f" * 100))
    return bytes(seq)


# After a Stop with fullyIdle=false, Smart Enter queues typed prompts while background
# tasks finish. If no lifecycle event has arrived for this long, an explicit Enter on a
# clean prompt surface is forwarded instead of being queued behind a state that may
# never be cleared. Queued items are never dispatched on this timer.
BACKGROUND_BUSY_STALE_SECONDS = 30.0


class SteeringCoordinator:
    """Coordinates single-shot agent steering via Ctrl+S."""

    def __init__(
        self,
        conversation_id: str,
        store: InteractionStateStore,
        queue: PromptQueue,
        master_fd: int,
    ):
        self.conversation_id = conversation_id
        self.store = store
        self.queue = queue
        self.master_fd = master_fd
        self._steering_in_progress = False

    def steer(self, steering_text: str) -> bool:
        """Executes a controlled single interruption and queues the steering prompt.

        Returns True if steering was successfully initiated.
        """
        cleaned = steering_text.strip()
        if not cleaned:
            return False

        if self._steering_in_progress:
            return False

        self._steering_in_progress = True
        try:
            # 1. Update state to INTERRUPTING
            self.store.update_state(
                self.conversation_id,
                state=InteractionState.INTERRUPTING,
                last_event="SteerInterrupt",
                last_event_time=time.time(),
            )

            # 2. Enqueue steering prompt at front of queue
            self.queue.enqueue(cleaned, source="steer", at_front=True)

            # 3. Show notice
            show_transient_notice("AntiAgent · steering…")

            # 4. Trigger exactly ONE native interrupt keystroke (Escape)
            if self.master_fd >= 0:
                try:
                    os.write(self.master_fd, b"\x1b")
                except OSError:
                    pass

            return True
        finally:
            self._steering_in_progress = False


class PTYBridge:
    """Interactive PTY bridge connecting the user's terminal to the AGY process."""

    def __init__(
        self,
        conversation_id: Optional[str] = None,
        config: Optional[AntiAgentConfig] = None,
        store: Optional[InteractionStateStore] = None,
        queue: Optional[PromptQueue] = None,
    ):
        self.store = store or InteractionStateStore.default()
        self.conversation_id = conversation_id or self.store.get_active_conversation_id() or "default"
        self.config = config or load_config()
        self.queue = queue or PromptQueue(self.conversation_id)
        self.prompt_buffer = PromptBuffer()
        self.master_fd: Optional[int] = None
        self._running = False
        self._steering_coord = SteeringCoordinator(
            self.conversation_id, self.store, self.queue, -1
        )
        self._terminal_buffer = ""
        self._started_at = 0.0

    def _current_state(self) -> ConversationState:
        """State for the active conversation, ignoring records from before this session.

        Lifecycle hooks only write state while a bridge is running, so a record older
        than this session may be left over from an untracked run and must not drive
        Enter/queue decisions; it is treated as UNKNOWN until a fresh event arrives.
        """
        state = self.store.get_state(self.conversation_id)
        if self._started_at and state.last_event_time < self._started_at:
            return ConversationState(conversation_id=self.conversation_id)
        return state

    def set_conversation_id(self, new_cid: str) -> None:
        """Updates active conversation identity if it changes."""
        if new_cid and new_cid != self.conversation_id:
            self.conversation_id = new_cid
            self.store.set_active_conversation_id(new_cid)
            self.queue = PromptQueue(new_cid)
            self._steering_coord = SteeringCoordinator(
                new_cid, self.store, self.queue, self.master_fd if self.master_fd is not None else -1
            )

    def can_steer(self, state: ConversationState) -> bool:
        """Determines whether Ctrl+S should trigger steering."""
        if not self.config.steer_enabled:
            return False

        # Do not steal Ctrl+S from settings or review surfaces
        if state.active_surface in (ActiveSurface.SETTINGS, ActiveSurface.DIFF_REVIEW, ActiveSurface.ARTIFACT_REVIEW):
            return False

        # Agent must be active/running or waiting
        active_states = (
            InteractionState.RUNNING,
            InteractionState.BACKGROUND_BUSY,
            InteractionState.AWAITING_APPROVAL,
            InteractionState.AWAITING_PREVIEW,
            InteractionState.AWAITING_QUESTION,
            InteractionState.INTERRUPTING,
        )
        return state.state in active_states

    def handle_keyboard_event(self, event_name: str, event_bytes: bytes) -> bool:
        """Processes a single keyboard event according to interaction state and active surface.

        Returns True if the event was consumed by AntiAgent (do NOT forward to AGY),
        or False if the event should be forwarded to AGY natively.
        """
        # Read current conversation and interaction state
        active_cid = self.store.get_active_conversation_id()
        if active_cid and active_cid != self.conversation_id:
            self.set_conversation_id(active_cid)

        state = self._current_state()
        surface = state.active_surface
        has_prompt_text = not self.prompt_buffer.is_empty

        # Check configurable hotkeys
        cfg_steer = (self.config.steer_key or "ctrl+s").strip().lower()
        cfg_queue = (self.config.queue_key or "tab").strip().lower()

        is_steer_key = (
            False
            if cfg_steer in ("none", "disabled", "off")
            else (event_name == "ctrl+s" if cfg_steer in ("ctrl+s", "ctrl-s", "") else event_name == cfg_steer)
        )
        is_queue_key = (
            False
            if cfg_queue in ("none", "disabled", "off")
            else (event_name == "tab" if cfg_queue in ("tab", "") else event_name == cfg_queue)
        )

        agent_is_busy = (
            state.state in (
                InteractionState.RUNNING,
                InteractionState.BACKGROUND_BUSY,
                InteractionState.AWAITING_APPROVAL,
                InteractionState.AWAITING_PREVIEW,
                InteractionState.AWAITING_QUESTION,
                InteractionState.INTERRUPTING,
                InteractionState.STEERING,
            )
            or surface in (
                ActiveSurface.APPROVAL,
                ActiveSurface.TEAMWORK_PREVIEW,
                ActiveSurface.QUESTION,
                ActiveSurface.UNKNOWN_MODAL,
            )
            or state.pending_approval
            or state.pending_preview
            or state.pending_question
        )

        # A BACKGROUND_BUSY state with no lifecycle event for a long time may never be
        # cleared (no further Stop arrives once background tasks finish). On a clean
        # prompt surface, let an explicit Enter through rather than queueing forever.
        background_stale = (
            state.state == InteractionState.BACKGROUND_BUSY
            and surface == ActiveSurface.PROMPT
            and not (state.pending_approval or state.pending_preview or state.pending_question)
            and state.last_event_time > 0
            and (time.time() - state.last_event_time) >= BACKGROUND_BUSY_STALE_SECONDS
        )

        # ---------------------------------------------------------
        # 1. ENTER KEY
        # ---------------------------------------------------------
        if event_name == "enter":
            if has_prompt_text and self.config.smart_enter_enabled:
                # If agent is running or in approval/preview/question modal:
                if agent_is_busy and not background_stale:
                    # QUEUE the prompt
                    text_to_queue = self.prompt_buffer.text
                    lines = self.prompt_buffer.line_count
                    self.prompt_buffer.clear()

                    # Clear AGY prompt editor without sending Esc
                    if self.master_fd is not None and self.master_fd >= 0:
                        try:
                            os.write(self.master_fd, get_clear_editor_bytes(len(text_to_queue), lines))
                        except OSError:
                            pass

                    self.queue.enqueue(text_to_queue, source="enter")
                    show_transient_notice(f"AntiAgent · queued #{self.queue.queued_count()}")
                    return True  # CONSUMED: Do NOT forward Enter to AGY!

                # Agent is IDLE or initial UNKNOWN on PROMPT surface: normal submit
                if state.state in (InteractionState.IDLE, InteractionState.UNKNOWN) and surface == ActiveSurface.PROMPT:
                    self.prompt_buffer.clear()
                    return False  # Forward Enter immediately to AGY

            if not self.config.smart_enter_enabled:
                self.prompt_buffer.clear()
                return False

            # Empty prompt: preserve native AGY Enter behavior (e.g. modal navigation/approval)
            return False

        # ---------------------------------------------------------
        # 2. TAB KEY (QUEUE)
        # ---------------------------------------------------------
        if is_queue_key:
            if (
                self.config.prompt_queue_enabled
                and has_prompt_text
                and agent_is_busy
                and surface in (
                    ActiveSurface.PROMPT,
                    ActiveSurface.APPROVAL,
                    ActiveSurface.TEAMWORK_PREVIEW,
                    ActiveSurface.QUESTION,
                )
            ):
                text_to_queue = self.prompt_buffer.text
                lines = self.prompt_buffer.line_count
                self.prompt_buffer.clear()

                if self.master_fd is not None and self.master_fd >= 0:
                    try:
                        os.write(self.master_fd, get_clear_editor_bytes(len(text_to_queue), lines))
                    except OSError:
                        pass

                self.queue.enqueue(text_to_queue, source="tab")
                show_transient_notice(f"AntiAgent · queued #{self.queue.queued_count()}")
                return True  # CONSUMED: Do NOT forward Tab to AGY!

            # Otherwise preserve native Tab (autocomplete, focus)
            return False

        # ---------------------------------------------------------
        # 3. CTRL+S (STEER)
        # ---------------------------------------------------------
        if is_steer_key:
            if self.can_steer(state):
                if has_prompt_text:
                    steering_text = self.prompt_buffer.text
                    lines = self.prompt_buffer.line_count
                    self.prompt_buffer.clear()

                    if self.master_fd is not None and self.master_fd >= 0:
                        try:
                            os.write(self.master_fd, get_clear_editor_bytes(len(steering_text), lines))
                        except OSError:
                            pass

                    coord = self._steering_coord or SteeringCoordinator(
                        self.conversation_id, self.store, self.queue, self.master_fd if self.master_fd is not None else -1
                    )
                    coord.master_fd = self.master_fd if self.master_fd is not None else -1
                    coord.steer(steering_text)
                    return True  # CONSUMED
                else:
                    show_transient_notice("AntiAgent · Type steering instruction first")
                    return True  # CONSUMED

            # In settings, idle, or when steer disabled: preserve native Ctrl+S
            return False

        # ---------------------------------------------------------
        # 4. BUFFER EDITING KEYS
        # ---------------------------------------------------------
        # Track prompt buffer for normal prompt editing, including when in modals/previews
        # where the user might type a follow-up instruction
        if surface not in (ActiveSurface.SETTINGS, ActiveSurface.DIFF_REVIEW, ActiveSurface.ARTIFACT_REVIEW):
            if event_name == "char":
                try:
                    s = event_bytes.decode("utf-8", errors="ignore")
                    self.prompt_buffer.insert(s)
                except Exception:
                    pass
            elif event_name in ("ctrl+j", "shift_enter", "newline"):
                self.prompt_buffer.insert_newline()
            elif event_name == "bracketed_paste":
                raw_paste = event_bytes[6:-6] if len(event_bytes) >= 12 else event_bytes
                try:
                    s = raw_paste.decode("utf-8", errors="ignore")
                    s = strip_ansi(s).replace("\r\n", "\n").replace("\r", "\n")
                    self.prompt_buffer.insert(s)
                except Exception:
                    pass
            elif event_name == "backspace":
                self.prompt_buffer.backspace()
            elif event_name == "delete":
                self.prompt_buffer.delete()
            elif event_name == "word_backspace":
                self.prompt_buffer.delete_word_left()
            elif event_name == "kill_to_start":
                self.prompt_buffer.kill_to_start()
            elif event_name == "kill_to_end":
                self.prompt_buffer.kill_to_end()
            elif event_name == "left":
                self.prompt_buffer.cursor_left()
            elif event_name == "right":
                self.prompt_buffer.cursor_right()
            elif event_name == "home":
                self.prompt_buffer.cursor_home()
            elif event_name == "end":
                self.prompt_buffer.cursor_end()

        # Forward editing keys to AGY natively
        return False

    def observe_terminal_output(self, data: bytes) -> None:
        """Fallback interactive-surface detection over a rolling window of AGY output."""
        text_chunk = data.decode("utf-8", errors="ignore")
        self._terminal_buffer += text_chunk
        if len(self._terminal_buffer) > 4096:
            self._terminal_buffer = self._terminal_buffer[-4096:]

        obs = detect_surface_from_terminal_output(self._terminal_buffer)
        if obs:
            # Consume the matched window: the observation is recorded in the store, and
            # rescanning the same stale text on every later chunk would keep re-asserting
            # a dialog that has already been resolved (and rewrite state per chunk). A
            # TUI redraw of a still-open dialog re-emits the text and is detected again.
            self._terminal_buffer = ""
            obs_state, obs_surface = obs
            self.store.update_state(
                self.conversation_id,
                state=obs_state,
                active_surface=obs_surface,
                pending_preview=(obs_surface == ActiveSurface.TEAMWORK_PREVIEW),
                pending_approval=(obs_surface == ActiveSurface.APPROVAL),
                pending_question=(obs_surface == ActiveSurface.QUESTION),
                last_event="TerminalObservation",
                last_event_time=time.time(),
            )

    def try_drain_queue(self) -> bool:
        """Attempts to dispatch one queued prompt if safe dispatch conditions are met."""
        if self.master_fd is None or self.master_fd < 0:
            return False

        active_cid = self.store.get_active_conversation_id() or self.conversation_id
        if active_cid != self.conversation_id:
            self.set_conversation_id(active_cid)

        state = self._current_state()
        lease_owner = f"pty_bridge_{os.getpid()}"

        if not can_dispatch_user_turn(state, self.queue, self.conversation_id, lease_owner=lease_owner):
            return False

        # Acquire dispatch lease to prevent race conditions
        if not self.store.acquire_dispatch_lease(self.conversation_id, lease_owner, ttl_seconds=6.0):
            return False

        prompt = self.queue.pop_next_for_dispatch()
        if prompt is None:
            self.store.release_dispatch_lease(self.conversation_id, lease_owner)
            return False

        try:
            # Clear editor line before writing prompt
            try:
                buf_len = len(self.prompt_buffer.text)
                buf_lines = self.prompt_buffer.line_count
                clear_len = max(buf_len, len(prompt.text))
                clear_lines = max(buf_lines, prompt.text.count("\n") + 1)
                os.write(self.master_fd, get_clear_editor_bytes(clear_len, clear_lines))
            except OSError:
                pass
            self.prompt_buffer.clear()

            remaining = self.queue.queued_count()
            if prompt.source == "steer":
                show_transient_notice("AntiAgent · applying steer…")
                self.store.update_state(
                    self.conversation_id,
                    state=InteractionState.STEERING,
                    last_event="SteerDispatch",
                    last_event_time=time.time(),
                )
            else:
                if remaining > 0:
                    show_transient_notice(f"AntiAgent · sending queued prompt ({remaining} remaining)…")
                else:
                    show_transient_notice("AntiAgent · sending queued prompt…")

            # Submit prompt text with normalized newlines followed by carriage return into child PTY
            clean_text = prompt.text.replace("\r\n", "\n").replace("\r", "\n")
            submission = f"{clean_text}\r".encode("utf-8")
            os.write(self.master_fd, submission)
            self.queue.mark_sent(prompt.id)

            self.store.update_state(
                self.conversation_id,
                state=InteractionState.RUNNING,
                fully_idle=False,
                last_event="DispatchQueuedTurn",
                last_event_time=time.time(),
            )
            return True
        except OSError:
            self.queue.mark_failed(prompt.id, "PTY write error")
            return False
        finally:
            self.store.release_dispatch_lease(self.conversation_id, lease_owner)

    def run(self, argv: List[str], env: Optional[Dict[str, str]] = None) -> int:
        """Launches AGY with the PTY bridge and runs the event loop until completion."""
        import subprocess

        if not pty_engine.is_pty_supported():
            sys.stderr.write(
                "Notice: Full keyboard prompt queue requires POSIX PTY support.\n"
                "Launching AGY in standard passthrough mode...\n"
            )
            return subprocess.call(argv, env=env)

        master_fd, slave_fd = pty_engine.open_pty()
        self.master_fd = master_fd
        self._steering_coord = SteeringCoordinator(
            self.conversation_id, self.store, self.queue, master_fd
        )
        self._terminal_buffer = ""

        def _sync_win() -> None:
            if hasattr(sys.stdin, "fileno"):
                try:
                    sync_window_size(sys.stdin.fileno(), [master_fd])
                except Exception:
                    pass

        _sync_win()
        old_winch = None
        if hasattr(signal, "SIGWINCH"):
            try:
                old_winch = signal.getsignal(signal.SIGWINCH)
                signal.signal(signal.SIGWINCH, lambda sig, frame: _sync_win())
            except Exception:
                old_winch = None

        self._running = True
        self._started_at = time.time()
        register_bridge()

        try:
            proc = subprocess.Popen(
                argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                env=env,
            )
            os.close(slave_fd)

            with raw_terminal_context():
                while proc.poll() is None and self._running:
                    # Check if safe to drain next queued prompt
                    self.try_drain_queue()

                    r, _, _ = select.select([sys.stdin.fileno(), master_fd], [], [], 0.05)

                    # 1. Output from AGY process
                    if master_fd in r:
                        try:
                            data = os.read(master_fd, 2048)
                            if not data:
                                break
                            os.write(sys.stdout.fileno(), data)
                            self.observe_terminal_output(data)
                        except OSError:
                            break

                    # 2. Input from User keyboard
                    if sys.stdin.fileno() in r:
                        try:
                            in_bytes = os.read(sys.stdin.fileno(), 1024)
                            if not in_bytes:
                                break

                            for ev_name, ev_bytes in parse_input_events(in_bytes):
                                consumed = self.handle_keyboard_event(ev_name, ev_bytes)
                                if not consumed:
                                    os.write(master_fd, ev_bytes)
                        except OSError:
                            break

            # Drain any trailing output buffered before child exit
            try:
                while True:
                    r, _, _ = select.select([master_fd], [], [], 0.05)
                    if not r:
                        break
                    data = os.read(master_fd, 2048)
                    if not data:
                        break
                    os.write(sys.stdout.fileno(), data)
            except OSError:
                pass

            proc.wait()
            return proc.returncode

        finally:
            self._running = False
            unregister_bridge()
            if old_winch is not None and hasattr(signal, "SIGWINCH"):
                try:
                    signal.signal(signal.SIGWINCH, old_winch)
                except Exception:
                    pass
            if self.master_fd is not None:
                try:
                    os.close(self.master_fd)
                except OSError:
                    pass
                self.master_fd = None
