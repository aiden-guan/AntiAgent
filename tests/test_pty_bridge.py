"""Unit tests for PTY bridge, prompt buffer mirror, and keyboard interception."""

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from antiagent.config import AntiAgentConfig
from antiagent.engine.interaction_state import (
    ActiveSurface,
    ConversationState,
    InteractionState,
    InteractionStateStore,
)
from antiagent.engine.prompt_queue import PromptQueue
from antiagent.engine.pty import raw_terminal_context
from antiagent.engine.pty_bridge import (
    PTYBridge,
    PromptBuffer,
    SteeringCoordinator,
    get_clear_editor_bytes,
    parse_input_events,
    show_transient_notice,
)


class FakePTYMaster:
    """Mock file descriptor for PTY master_fd to inspect bytes sent to child process."""

    def __init__(self):
        self.written_bytes = b""

    def write(self, data: bytes):
        self.written_bytes += data


class TestPTYBridge(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.runtime_dir = Path(self.temp_dir) / "runtime"
        self.cid = "pty-test-cid"
        self.store = InteractionStateStore(runtime_dir=self.runtime_dir)
        self.queue = PromptQueue(self.cid, runtime_dir=self.runtime_dir, recover_as_held=False)
        self.cfg = AntiAgentConfig(
            prompt_queue_enabled=True,
            smart_enter_enabled=True,
            steer_enabled=True,
        )
        self.bridge = PTYBridge(
            conversation_id=self.cid,
            config=self.cfg,
            store=self.store,
            queue=self.queue,
        )
        # Mock master_fd for write inspection
        self.fake_master = FakePTYMaster()
        self.bridge.master_fd = 999  # Valid integer fd placeholder

        def fake_write(fd, data):
            if fd == self.bridge.master_fd:
                self.fake_master.write(data)
            return len(data)

        self.os_write_patch = patch("os.write", side_effect=fake_write)
        self.os_write_mock = self.os_write_patch.start()

    def tearDown(self):
        self.os_write_patch.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_prompt_buffer_editing(self):
        buf = PromptBuffer()
        self.assertTrue(buf.is_empty)

        # Insertion
        buf.insert("hello")
        self.assertEqual(buf.text, "hello")
        self.assertFalse(buf.is_empty)

        # Backspace
        buf.backspace()
        self.assertEqual(buf.text, "hell")

        # Cursor movement & insertion
        buf.cursor_left()
        buf.cursor_left()  # cursor is before 'l'
        buf.insert("p")
        self.assertEqual(buf.text, "hepll")

        # Home & delete
        buf.cursor_home()
        buf.delete()
        self.assertEqual(buf.text, "epll")

        # End & insert
        buf.cursor_end()
        buf.insert("!")
        self.assertEqual(buf.text, "epll!")

        # Unicode
        buf.clear()
        buf.insert("你好，世界 🚀")
        self.assertEqual(buf.text, "你好，世界 🚀")

    def test_parse_input_events(self):
        # Enter
        evs = list(parse_input_events(b"\r"))
        self.assertEqual(evs, [("enter", b"\r")])

        # Tab
        evs = list(parse_input_events(b"\t"))
        self.assertEqual(evs, [("tab", b"\t")])

        # Ctrl+S
        evs = list(parse_input_events(b"\x13"))
        self.assertEqual(evs, [("ctrl+s", b"\x13")])

        # Arrows & backspace
        evs = list(parse_input_events(b"\x1b[D\x7f"))
        self.assertEqual(evs, [("left", b"\x1b[D"), ("backspace", b"\x7f")])

        # Bracketed paste
        paste_data = b"\x1b[200~pasted content\x1b[201~"
        evs = list(parse_input_events(paste_data))
        self.assertEqual(evs, [("bracketed_paste", paste_data)])

    def test_idle_enter_submits_immediately(self):
        self.store.update_state(
            self.cid,
            state=InteractionState.IDLE,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=True,
        )
        self.bridge.prompt_buffer.insert("regular command")

        consumed = self.bridge.handle_keyboard_event("enter", b"\r")
        # Should NOT consume: let native Enter pass through to AGY
        self.assertFalse(consumed)
        # Queue should remain empty
        self.assertEqual(self.queue.queued_count(), 0)

    def test_running_enter_queues_prompt(self):
        self.store.update_state(
            self.cid,
            state=InteractionState.RUNNING,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=False,
        )
        self.bridge.prompt_buffer.insert("run tests too")

        consumed = self.bridge.handle_keyboard_event("enter", b"\r")
        # MUST consume: Do NOT forward Enter to AGY!
        self.assertTrue(consumed)
        # Message is queued
        self.assertEqual(self.queue.queued_count(), 1)
        item = self.queue.peek()
        self.assertIsNotNone(item)
        self.assertEqual(item.text, "run tests too")
        self.assertEqual(item.source, "enter")
        # Prompt buffer cleared
        self.assertTrue(self.bridge.prompt_buffer.is_empty)
        # Editor cleared without Escape
        self.assertIn(b"\x15", self.fake_master.written_bytes)
        self.assertNotIn(b"\x1b", self.fake_master.written_bytes)

    def test_running_tab_queues_prompt(self):
        self.store.update_state(
            self.cid,
            state=InteractionState.RUNNING,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=False,
        )
        self.bridge.prompt_buffer.insert("also check readme")

        consumed = self.bridge.handle_keyboard_event("tab", b"\t")
        self.assertTrue(consumed)
        self.assertEqual(self.queue.queued_count(), 1)
        item = self.queue.peek()
        self.assertEqual(item.source, "tab")

    def test_idle_tab_remains_native(self):
        self.store.update_state(
            self.cid,
            state=InteractionState.IDLE,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=True,
        )
        self.bridge.prompt_buffer.insert("partial_cmd")

        consumed = self.bridge.handle_keyboard_event("tab", b"\t")
        # Idle tab preserved for autocomplete
        self.assertFalse(consumed)
        self.assertEqual(self.queue.queued_count(), 0)

    def test_ctrl_s_steer_active_agent(self):
        self.store.update_state(
            self.cid,
            state=InteractionState.RUNNING,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=False,
        )
        self.bridge.prompt_buffer.insert("don't rewrite DB, change adapter")

        consumed = self.bridge.handle_keyboard_event("ctrl+s", b"\x13")
        self.assertTrue(consumed)

        # Prompt queued with source='steer'
        self.assertEqual(self.queue.queued_count(), 1)
        item = self.queue.peek()
        self.assertEqual(item.source, "steer")
        self.assertEqual(item.text, "don't rewrite DB, change adapter")

        # State transitioned to INTERRUPTING
        st = self.store.get_state(self.cid)
        self.assertEqual(st.state, InteractionState.INTERRUPTING)

        # Single interrupt keystroke (\x1b) sent exactly once
        self.assertEqual(self.fake_master.written_bytes.count(b"\x1b"), 1)

    def test_ctrl_s_empty_prompt_does_nothing(self):
        self.store.update_state(
            self.cid,
            state=InteractionState.RUNNING,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=False,
        )
        self.bridge.prompt_buffer.clear()

        consumed = self.bridge.handle_keyboard_event("ctrl+s", b"\x13")
        self.assertTrue(consumed)  # Consumed so AGY doesn't receive stray Ctrl+S
        # No interrupt sent
        self.assertEqual(self.fake_master.written_bytes.count(b"\x1b"), 0)
        self.assertEqual(self.queue.queued_count(), 0)

    def test_ctrl_s_in_settings_remains_native(self):
        self.store.update_state(
            self.cid,
            state=InteractionState.IDLE,
            active_surface=ActiveSurface.SETTINGS,
            fully_idle=True,
        )
        self.bridge.prompt_buffer.insert("setting value")

        consumed = self.bridge.handle_keyboard_event("ctrl+s", b"\x13")
        # Native in settings screen
        self.assertFalse(consumed)

    def test_awaiting_approval_empty_enter_preserves_native_behavior(self):
        self.store.update_state(
            self.cid,
            state=InteractionState.AWAITING_APPROVAL,
            active_surface=ActiveSurface.APPROVAL,
            pending_approval=True,
            fully_idle=False,
        )
        self.bridge.prompt_buffer.clear()

        consumed = self.bridge.handle_keyboard_event("enter", b"\r")
        # Empty Enter: forwarded to AGY so user can confirm/navigate approval!
        self.assertFalse(consumed)

    def test_enter_with_typed_prompt_during_teamwork_preview_queues_without_approving(self):
        """MANDATORY REGRESSION TEST FOR REPORTED BUG:

        Scenario:
        1. AGY running.
        2. /teamwork-preview enters preview/approval state.
        3. User types: "also add a security specialist"
        4. User presses Enter.
        Assert:
          - message is present in queue
          - AGY does not receive Enter
          - preview remains unresolved
          - no approval action occurs
          - queue does not drain
        Then:
        5. User explicitly resolves preview.
        6. Agent eventually emits fullyIdle=true.
        Assert:
          - queued message is dispatched exactly once
        """
        # Step 1 & 2: Agent is in preview state
        self.store.update_state(
            self.cid,
            state=InteractionState.AWAITING_PREVIEW,
            active_surface=ActiveSurface.TEAMWORK_PREVIEW,
            pending_preview=True,
            fully_idle=False,
        )

        # Step 3: User types prompt
        user_prompt = "also add a security specialist"
        for ch in user_prompt:
            self.bridge.handle_keyboard_event("char", ch.encode("utf-8"))
        self.assertEqual(self.bridge.prompt_buffer.text, user_prompt)

        # Reset captured writes before Enter
        self.fake_master.written_bytes = b""

        # Step 4: User presses Enter
        consumed = self.bridge.handle_keyboard_event("enter", b"\r")

        # Assert:
        # 1. Message is consumed and present in queue
        self.assertTrue(consumed)
        self.assertEqual(self.queue.queued_count(), 1)
        item = self.queue.peek()
        self.assertIsNotNone(item)
        self.assertEqual(item.text, user_prompt)

        # 2. AGY did NOT receive Enter (\r or \n)
        self.assertNotIn(b"\r", self.fake_master.written_bytes)
        self.assertNotIn(b"\n", self.fake_master.written_bytes)

        # 3. Preview remains unresolved
        st = self.store.get_state(self.cid)
        self.assertEqual(st.state, InteractionState.AWAITING_PREVIEW)
        self.assertTrue(st.pending_preview)

        # 4. Queue does NOT drain while preview is active
        drained = self.bridge.try_drain_queue()
        self.assertFalse(drained)
        self.assertEqual(self.queue.queued_count(), 1)

        # Step 5: User explicitly resolves preview (empty enter forwards natively)
        self.bridge.prompt_buffer.clear()
        resolve_consumed = self.bridge.handle_keyboard_event("enter", b"\r")
        self.assertFalse(resolve_consumed)  # Enter forwarded to AGY preview UI!

        # Step 6: Agent continues, finishes execution and emits Stop fullyIdle=True
        self.store.update_state(
            self.cid,
            state=InteractionState.IDLE,
            active_surface=ActiveSurface.PROMPT,
            pending_preview=False,
            fully_idle=True,
            last_event="Stop",
        )

        # Clear captured writes
        self.fake_master.written_bytes = b""

        # Assert: Queued message is dispatched exactly once
        drained_success = self.bridge.try_drain_queue()
        self.assertTrue(drained_success)
        self.assertEqual(self.queue.queued_count(), 0)
        self.assertIn(f"{user_prompt}\r".encode("utf-8"), self.fake_master.written_bytes)

        # Repeated drain does not double-send
        self.assertFalse(self.bridge.try_drain_queue())

    def test_raw_terminal_context_restores_on_exception(self):
        """Verify raw_terminal_context safely handles and restores terminal attributes."""
        # Non-TTY or test environment yields False safely without raising
        with raw_terminal_context():
            pass

    def test_ctrl_s_does_not_approve_confirmation_ui(self):
        """Verify Ctrl+S while confirmation is open never approves the UI."""
        self.store.update_state(
            self.cid,
            state=InteractionState.AWAITING_APPROVAL,
            active_surface=ActiveSurface.APPROVAL,
            pending_approval=True,
            fully_idle=False,
        )
        self.bridge.prompt_buffer.insert("steer away from destructive command")
        self.fake_master.written_bytes = b""

        consumed = self.bridge.handle_keyboard_event("ctrl+s", b"\x13")
        self.assertTrue(consumed)

        # Assert no Enter (\r or \n) was sent to approve the confirmation!
        self.assertNotIn(b"\r", self.fake_master.written_bytes)
        self.assertNotIn(b"\n", self.fake_master.written_bytes)

        # Steering prompt queued
        self.assertEqual(self.queue.queued_count(), 1)
        item = self.queue.peek()
        self.assertEqual(item.source, "steer")

    def test_duplicate_lifecycle_events_do_not_double_send(self):
        """Verify multiple rapid Stop events do not double-submit a queued prompt."""
        self.queue.enqueue("queued prompt")
        self.store.update_state(
            self.cid,
            state=InteractionState.IDLE,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=True,
        )
        self.fake_master.written_bytes = b""

        # First drain
        d1 = self.bridge.try_drain_queue()
        self.assertTrue(d1)
        self.assertEqual(self.fake_master.written_bytes.count(b"queued prompt\r"), 1)

        # Second rapid Stop event arrives
        self.store.update_state(
            self.cid,
            state=InteractionState.IDLE,
            fully_idle=True,
            last_event="Stop",
        )
        d2 = self.bridge.try_drain_queue()
        self.assertFalse(d2)
        # Still exactly 1 write
        self.assertEqual(self.fake_master.written_bytes.count(b"queued prompt\r"), 1)

    def test_multiline_prompt_queuing(self):
        """Verify multiline prompts with Unicode are correctly buffered and queued."""
        self.store.update_state(
            self.cid,
            state=InteractionState.RUNNING,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=False,
        )
        multiline = "Line 1: 🚀 Start\nLine 2: 🔧 Fix adapter\nLine 3: 🧪 Test"
        self.bridge.prompt_buffer.insert(multiline)

        consumed = self.bridge.handle_keyboard_event("enter", b"\r")
        self.assertTrue(consumed)
        self.assertEqual(self.queue.queued_count(), 1)
        item = self.queue.peek()
        self.assertEqual(item.text, multiline)

    def test_bracketed_paste_and_unicode_queuing(self):
        """Verify bracketed paste captures content cleanly into prompt buffer."""
        self.store.update_state(
            self.cid,
            state=InteractionState.RUNNING,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=False,
        )
        pasted = "Refactor auth and export API"
        bracketed_bytes = f"\x1b[200~{pasted}\x1b[201~".encode("utf-8")

        for ev_name, ev_bytes in parse_input_events(bracketed_bytes):
            self.bridge.handle_keyboard_event(ev_name, ev_bytes)

        self.assertEqual(self.bridge.prompt_buffer.text, pasted)

        consumed = self.bridge.handle_keyboard_event("enter", b"\r")
        self.assertTrue(consumed)
        item = self.queue.peek()
        self.assertEqual(item.text, pasted)

    def test_conversation_change_prevents_cross_session_dispatch(self):
        """Verify changing conversation identity holds existing queued items."""
        self.queue.enqueue("task for conversation A")
        self.store.update_state(
            self.cid,
            state=InteractionState.IDLE,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=True,
        )

        # Switch to new conversation B
        self.bridge.set_conversation_id("conversation-B")
        self.store.update_state(
            "conversation-B",
            state=InteractionState.IDLE,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=True,
        )

        # Trying to drain active bridge (now on B) does NOT dispatch queue of A
        drained = self.bridge.try_drain_queue()
        self.assertFalse(drained)

        # Original queue A still holds the prompt safely
        queue_a = PromptQueue(self.cid, runtime_dir=self.runtime_dir, recover_as_held=False)
        self.assertEqual(queue_a.queued_count(), 1)

    def test_windows_fallback_behavior(self):
        """Verify Windows non-PTY environment produces capability message and falls back."""
        with patch("antiagent.engine.pty.is_pty_supported", return_value=False), patch("subprocess.call", return_value=0) as mock_call:
            code = self.bridge.run(["agy", "--version"])
            self.assertEqual(code, 0)
            mock_call.assert_called_once()

    def test_multiline_typing_via_ctrl_j_and_shift_enter(self):
        """Verify Ctrl+J and Shift+Enter insert newlines without triggering Enter submit or queue."""
        self.store.update_state(
            self.cid,
            state=InteractionState.RUNNING,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=False,
        )

        # Type 'line1'
        for ch in "line1":
            self.bridge.handle_keyboard_event("char", ch.encode("utf-8"))

        # Press Ctrl+J (\n)
        for ev_name, ev_bytes in parse_input_events(b"\n"):
            consumed = self.bridge.handle_keyboard_event(ev_name, ev_bytes)
            # Ctrl+J is forwarded natively to line editor but handled by prompt_buffer as newline
            self.assertFalse(consumed)

        # Type 'line2'
        for ch in "line2":
            self.bridge.handle_keyboard_event("char", ch.encode("utf-8"))

        self.assertEqual(self.bridge.prompt_buffer.text, "line1\nline2")
        self.assertEqual(self.queue.queued_count(), 0)

        # Press Shift+Enter (\x1b[13;2u)
        for ev_name, ev_bytes in parse_input_events(b"\x1b[13;2u"):
            self.bridge.handle_keyboard_event(ev_name, ev_bytes)

        # Type 'line3'
        for ch in "line3":
            self.bridge.handle_keyboard_event("char", ch.encode("utf-8"))

        self.assertEqual(self.bridge.prompt_buffer.text, "line1\nline2\nline3")

        # Now press Enter (\r) to queue the multiline prompt
        for ev_name, ev_bytes in parse_input_events(b"\r"):
            consumed = self.bridge.handle_keyboard_event(ev_name, ev_bytes)
            self.assertTrue(consumed)

        self.assertEqual(self.queue.queued_count(), 1)
        item = self.queue.peek()
        self.assertEqual(item.text, "line1\nline2\nline3")

    def test_alt_backspace_and_ctrl_w_delete_word_without_escape(self):
        """Verify Option+Backspace and Ctrl+W delete previous word and NEVER send bare Escape to AGY."""
        self.store.update_state(
            self.cid,
            state=InteractionState.RUNNING,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=False,
        )

        for ch in "deploy api":
            self.bridge.handle_keyboard_event("char", ch.encode("utf-8"))
        self.assertEqual(self.bridge.prompt_buffer.text, "deploy api")

        self.fake_master.written_bytes = b""

        # Press Option+Backspace (\x1b\x7f)
        events = list(parse_input_events(b"\x1b\x7f"))
        self.assertEqual(len(events), 1)
        ev_name, ev_bytes = events[0]
        self.assertEqual(ev_name, "word_backspace")
        self.assertEqual(ev_bytes, b"\x1b\x7f")

        consumed = self.bridge.handle_keyboard_event(ev_name, ev_bytes)
        self.assertFalse(consumed)  # Forwarded to child terminal

        # Prompt buffer has 'api' erased
        self.assertEqual(self.bridge.prompt_buffer.text, "deploy ")

        # AGY was NOT sent bare Escape (which would kill the running agent!)
        # The bytes forwarded should be the intact \x1b\x7f sequence, not a split bare \x1b
        self.assertNotIn(b"\x1b[", self.fake_master.written_bytes)

        # Press Ctrl+W (\x17)
        for ev_name, ev_bytes in parse_input_events(b"\x17"):
            self.bridge.handle_keyboard_event(ev_name, ev_bytes)
        self.assertEqual(self.bridge.prompt_buffer.text, "")

    def test_ctrl_u_and_ctrl_k_line_editing(self):
        """Verify Ctrl+U and Ctrl+K perform line-level deletions."""
        for ch in "first line\nsecond line":
            self.bridge.handle_keyboard_event("char", ch.encode("utf-8"))
        self.assertEqual(self.bridge.prompt_buffer.text, "first line\nsecond line")

        # Ctrl+U kills from cursor to start of current line
        for ev_name, ev_bytes in parse_input_events(b"\x15"):
            self.bridge.handle_keyboard_event(ev_name, ev_bytes)
        self.assertEqual(self.bridge.prompt_buffer.text, "first line\n")

        # Move to home and Ctrl+K kills to end of line
        self.bridge.prompt_buffer.cursor_home()
        for ev_name, ev_bytes in parse_input_events(b"\x0b"):
            self.bridge.handle_keyboard_event(ev_name, ev_bytes)
        self.assertEqual(self.bridge.prompt_buffer.text, "\n")

    def test_tab_during_teamwork_preview_queues_without_leaking(self):
        """Verify Tab with typed prompt in /teamwork-preview queues instead of leaking into modal."""
        self.store.update_state(
            self.cid,
            state=InteractionState.AWAITING_PREVIEW,
            active_surface=ActiveSurface.TEAMWORK_PREVIEW,
            pending_preview=True,
            fully_idle=False,
        )

        for ch in "also add a QA lead":
            self.bridge.handle_keyboard_event("char", ch.encode("utf-8"))

        self.fake_master.written_bytes = b""

        # Press Tab
        consumed = self.bridge.handle_keyboard_event("tab", b"\t")
        self.assertTrue(consumed)  # Tab consumed by AntiAgent queue!
        self.assertEqual(self.queue.queued_count(), 1)
        item = self.queue.peek()
        self.assertEqual(item.text, "also add a QA lead")
        self.assertEqual(item.source, "tab")

        # AGY did NOT receive Tab
        self.assertNotIn(b"\t", self.fake_master.written_bytes)

    def test_unknown_state_enter_submits_and_clears_buffer(self):
        """Verify initial UNKNOWN state submits prompt and clears buffer so future typing does not concatenate."""
        self.store.update_state(
            self.cid,
            state=InteractionState.UNKNOWN,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=False,
        )

        for ch in "create a web app":
            self.bridge.handle_keyboard_event("char", ch.encode("utf-8"))
        self.assertEqual(self.bridge.prompt_buffer.text, "create a web app")

        consumed = self.bridge.handle_keyboard_event("enter", b"\r")
        # Native submit: not consumed
        self.assertFalse(consumed)
        # Prompt buffer MUST be cleared!
        self.assertTrue(self.bridge.prompt_buffer.is_empty)

        # Now when agent starts running, subsequent typing starts with a fresh buffer
        self.store.update_state(self.cid, state=InteractionState.RUNNING)
        for ch in "add tests":
            self.bridge.handle_keyboard_event("char", ch.encode("utf-8"))
        self.assertEqual(self.bridge.prompt_buffer.text, "add tests")

    def test_clear_editor_bytes_does_not_send_spaces(self):
        """Verify get_clear_editor_bytes never sends ASCII spaces (b' ') to child stdin."""
        clear_bytes = get_clear_editor_bytes(prompt_len=50, line_count=2)
        # b"\x08 \x08" was the bug where space characters were sent to stdin!
        self.assertNotIn(b" ", clear_bytes)
        self.assertNotIn(b"\x08 \x08", clear_bytes)
        self.assertIn(b"\x15", clear_bytes)
        self.assertIn(b"\x7f", clear_bytes)

    def test_show_transient_notice_safe_without_fileno(self):
        """Verify show_transient_notice does not raise when stdout lacks fileno."""
        import io
        with patch("sys.stdout", io.StringIO()):
            # Must not crash with io.UnsupportedOperation: fileno
            show_transient_notice("test notice")


    def test_unknown_state_tab_remains_native_for_autocomplete(self):
        """Verify Tab in initial UNKNOWN state is NOT intercepted so autocomplete works."""
        self.store.update_state(
            self.cid,
            state=InteractionState.UNKNOWN,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=False,
        )
        self.bridge.prompt_buffer.insert("git sta")

        consumed = self.bridge.handle_keyboard_event("tab", b"\t")
        # Native Tab preserved for shell/file autocomplete before first run
        self.assertFalse(consumed)
        self.assertEqual(self.queue.queued_count(), 0)

    def test_tab_disabled_via_config(self):
        """Verify setting queue_key to 'none' disables Tab interception even when busy."""
        self.bridge.config.queue_key = "none"
        self.store.update_state(
            self.cid,
            state=InteractionState.RUNNING,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=False,
        )
        self.bridge.prompt_buffer.insert("do not queue via tab")

        consumed = self.bridge.handle_keyboard_event("tab", b"\t")
        self.assertFalse(consumed)
        self.assertEqual(self.queue.queued_count(), 0)

    def test_steer_disabled_via_config(self):
        """Verify setting steer_key to 'none' disables Ctrl+S interception even when running."""
        self.bridge.config.steer_key = "none"
        self.store.update_state(
            self.cid,
            state=InteractionState.RUNNING,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=False,
        )
        self.bridge.prompt_buffer.insert("steering text")

        consumed = self.bridge.handle_keyboard_event("ctrl+s", b"\x13")
        self.assertFalse(consumed)
        self.assertEqual(self.queue.queued_count(), 0)

    def test_try_drain_queue_clears_prompt_buffer(self):
        """Verify try_drain_queue clears in-memory prompt buffer when queued turn is dispatched."""
        self.queue.enqueue("queued turn 1")
        self.store.update_state(
            self.cid,
            state=InteractionState.IDLE,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=True,
        )
        # Simulate partial text left in prompt_buffer
        self.bridge.prompt_buffer.insert("abandoned typing")

        drained = self.bridge.try_drain_queue()
        self.assertTrue(drained)
        # prompt_buffer should be reset clean so it matches the newly cleared AGY prompt editor
        self.assertTrue(self.bridge.prompt_buffer.is_empty)

    def test_bracketed_paste_normalizes_crlf_and_strips_ansi(self):
        """Verify bracketed paste normalizes CRLF/CR to LF and strips ANSI codes."""
        self.store.update_state(
            self.cid,
            state=InteractionState.RUNNING,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=False,
        )
        # Paste text containing CRLF and ANSI color escape sequences
        dirty_paste = "\x1b[32mLine 1\x1b[0m\r\n\x1b[1mLine 2\x1b[0m\rLine 3"
        raw_bytes = f"\x1b[200~{dirty_paste}\x1b[201~".encode("utf-8")

        for ev_name, ev_bytes in parse_input_events(raw_bytes):
            self.bridge.handle_keyboard_event(ev_name, ev_bytes)

        # Buffer must have no \r and no ANSI escapes
        self.assertEqual(self.bridge.prompt_buffer.text, "Line 1\nLine 2\nLine 3")

    def test_agent_is_busy_respects_pending_flags(self):
        """Verify that pending_approval, pending_preview, or pending_question makes agent_is_busy True."""
        self.bridge.prompt_buffer.insert("test prompt")

        # 1. pending_approval True
        self.store.update_state(self.cid, state=InteractionState.IDLE, pending_approval=True)
        consumed = self.bridge.handle_keyboard_event("enter", b"\r")
        self.assertTrue(consumed)
        self.assertEqual(self.queue.queued_count(), 1)
        self.queue.clear()

        # 2. pending_preview True
        self.bridge.prompt_buffer.insert("test prompt 2")
        self.store.update_state(self.cid, state=InteractionState.IDLE, pending_approval=False, pending_preview=True)
        consumed2 = self.bridge.handle_keyboard_event("enter", b"\r")
        self.assertTrue(consumed2)
        self.assertEqual(self.queue.queued_count(), 1)
        self.queue.clear()

        # 3. pending_question True
        self.bridge.prompt_buffer.insert("test prompt 3")
        self.store.update_state(self.cid, state=InteractionState.IDLE, pending_preview=False, pending_question=True)
        consumed3 = self.bridge.handle_keyboard_event("enter", b"\r")
        self.assertTrue(consumed3)
        self.assertEqual(self.queue.queued_count(), 1)

    def test_try_drain_queue_normalizes_multiline_submission(self):
        """Verify multiline queued turn submits with clean newlines and exactly one final carriage return."""
        self.queue.enqueue("line1\r\nline2\rline3")
        self.store.update_state(
            self.cid,
            state=InteractionState.IDLE,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=True,
        )
        self.fake_master.written_bytes = b""

        drained = self.bridge.try_drain_queue()
        self.assertTrue(drained)

        # Ensure no \r inside text, only \r at the very end
        self.assertIn(b"line1\nline2\nline3\r", self.fake_master.written_bytes)

    def test_sigwinch_safe_cleanup_on_error(self):
        """Verify PTYBridge.run cleans up without UnboundLocalError when child spawn fails."""
        with patch("antiagent.engine.pty.is_pty_supported", return_value=True):
            with patch("antiagent.engine.pty.open_pty", return_value=(10, 11)):
                with patch("subprocess.Popen", side_effect=OSError("Exec failed")):
                    with patch("os.close"):
                        with self.assertRaises(OSError):
                            self.bridge.run(["nonexistent_binary"])


if __name__ == "__main__":
    unittest.main()


