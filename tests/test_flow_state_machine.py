"""State-machine latency/regression tests for Stop handling, BACKGROUND_BUSY and
terminal surface observation in the PTY bridge."""

import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from antiagent.config import AntiAgentConfig
from antiagent.engine.interaction_state import (
    ActiveSurface,
    InteractionState,
    InteractionStateStore,
)
from antiagent.engine.prompt_queue import PromptQueue
from antiagent.engine.pty_bridge import BACKGROUND_BUSY_STALE_SECONDS, PTYBridge
from antiagent.flow_hook import handle_pre_invocation, handle_stop


class _BridgeCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.runtime_dir = Path(self.temp_dir) / "runtime"
        self.cid = "sm-test-cid"
        self.store = InteractionStateStore(runtime_dir=self.runtime_dir)
        self.store.set_active_conversation_id(self.cid)
        self.queue = PromptQueue(self.cid, runtime_dir=self.runtime_dir, recover_as_held=False)
        cfg = AntiAgentConfig(prompt_queue_enabled=True, smart_enter_enabled=True, steer_enabled=True)
        self.bridge = PTYBridge(conversation_id=self.cid, config=cfg, store=self.store, queue=self.queue)
        self.bridge.master_fd = 999
        self.written = b""

        def fake_write(fd, data):
            if fd == 999:
                self.written += data
            return len(data)

        self._p = patch("os.write", side_effect=fake_write)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def type_and_enter(self, text: str) -> bool:
        self.bridge.prompt_buffer.clear()
        self.bridge.prompt_buffer.insert(text)
        return self.bridge.handle_keyboard_event("enter", b"\r")


class TestStopHandling(_BridgeCase):
    def test_stop_fully_idle_true_releases_queue(self):
        handle_pre_invocation({"conversationId": self.cid}, self.store)
        self.queue.enqueue("next task")
        self.assertFalse(self.bridge.try_drain_queue())
        handle_stop({"conversationId": self.cid, "fullyIdle": True}, self.store)
        self.assertTrue(self.bridge.try_drain_queue())
        self.assertIn(b"next task\r", self.written)

    def test_stop_fully_idle_false_holds_queue(self):
        handle_pre_invocation({"conversationId": self.cid}, self.store)
        self.queue.enqueue("next task")
        handle_stop({"conversationId": self.cid, "fullyIdle": False}, self.store)
        st = self.store.get_state(self.cid)
        self.assertEqual(st.state, InteractionState.BACKGROUND_BUSY)
        self.assertFalse(self.bridge.try_drain_queue())

    def test_stop_missing_fully_idle_is_not_treated_as_idle(self):
        # protojson omits false booleans, so a missing fullyIdle must not be read as idle.
        handle_pre_invocation({"conversationId": self.cid}, self.store)
        self.queue.enqueue("next task")
        handle_stop({"conversationId": self.cid}, self.store)
        st = self.store.get_state(self.cid)
        self.assertEqual(st.state, InteractionState.BACKGROUND_BUSY)
        self.assertFalse(st.fully_idle)
        self.assertFalse(self.bridge.try_drain_queue())

    def test_background_busy_then_fully_idle_stop_drains(self):
        handle_pre_invocation({"conversationId": self.cid}, self.store)
        self.queue.enqueue("next task")
        handle_stop({"conversationId": self.cid, "fullyIdle": False}, self.store)
        self.assertFalse(self.bridge.try_drain_queue())
        handle_stop({"conversationId": self.cid, "fullyIdle": True}, self.store)
        self.assertTrue(self.bridge.try_drain_queue())

    def test_stop_never_arrives_keeps_queue_held(self):
        handle_pre_invocation({"conversationId": self.cid}, self.store)
        self.queue.enqueue("next task")
        for _ in range(5):
            self.assertFalse(self.bridge.try_drain_queue())
        self.assertEqual(self.queue.queued_count(), 1)

    def test_delayed_stop_dispatches_exactly_once(self):
        handle_pre_invocation({"conversationId": self.cid}, self.store)
        self.queue.enqueue("next task")
        handle_stop({"conversationId": self.cid, "fullyIdle": True}, self.store)
        handle_stop({"conversationId": self.cid, "fullyIdle": True}, self.store)
        self.assertTrue(self.bridge.try_drain_queue())
        self.assertFalse(self.bridge.try_drain_queue())
        self.assertEqual(self.written.count(b"next task\r"), 1)


class TestStaleBackgroundBusy(_BridgeCase):
    def _enter_background_busy(self, age_seconds: float) -> None:
        handle_pre_invocation({"conversationId": self.cid}, self.store)
        handle_stop({"conversationId": self.cid, "fullyIdle": False}, self.store)
        self.store.update_state(self.cid, last_event_time=time.time() - age_seconds)

    def test_fresh_background_busy_smart_enter_queues(self):
        self._enter_background_busy(0)
        self.assertTrue(self.type_and_enter("hello"))
        self.assertEqual(self.queue.queued_count(), 1)

    def test_stale_background_busy_smart_enter_submits(self):
        self._enter_background_busy(BACKGROUND_BUSY_STALE_SECONDS + 5)
        consumed = self.type_and_enter("hello")
        self.assertFalse(consumed)  # forwarded to AGY immediately
        self.assertEqual(self.queue.queued_count(), 0)

    def test_stale_background_busy_never_dispatches_queue_on_timer(self):
        self._enter_background_busy(BACKGROUND_BUSY_STALE_SECONDS + 5)
        self.queue.enqueue("queued earlier")
        self.assertFalse(self.bridge.try_drain_queue())
        self.assertEqual(self.queue.queued_count(), 1)

    def test_stale_background_busy_does_not_bypass_approval(self):
        self._enter_background_busy(BACKGROUND_BUSY_STALE_SECONDS + 5)
        self.store.update_state(
            self.cid, active_surface=ActiveSurface.APPROVAL, pending_approval=True,
            last_event_time=time.time() - BACKGROUND_BUSY_STALE_SECONDS - 5,
        )
        self.assertTrue(self.type_and_enter("y"))
        self.assertNotIn(b"\r", self.written)

    def test_stale_background_busy_does_not_bypass_preview_or_question(self):
        for surface, flag in (
            (ActiveSurface.TEAMWORK_PREVIEW, "pending_preview"),
            (ActiveSurface.QUESTION, "pending_question"),
        ):
            self._enter_background_busy(BACKGROUND_BUSY_STALE_SECONDS + 5)
            self.store.update_state(
                self.cid, active_surface=surface, **{flag: True},
                last_event_time=time.time() - BACKGROUND_BUSY_STALE_SECONDS - 5,
            )
            self.written = b""
            self.assertTrue(self.type_and_enter("ok"))
            self.assertNotIn(b"\r", self.written)


class TestTerminalObservation(_BridgeCase):
    def test_stale_approval_text_does_not_reassert_after_stop(self):
        handle_pre_invocation({"conversationId": self.cid}, self.store)
        self.bridge.observe_terminal_output(b"Permission required: run rm? Allow once / Always allow\r\n")
        self.assertEqual(self.store.get_state(self.cid).state, InteractionState.AWAITING_APPROVAL)

        # Approval resolved, agent finishes.
        handle_stop({"conversationId": self.cid, "fullyIdle": True}, self.store)
        self.queue.enqueue("follow-up")

        # Subsequent benign output must not resurrect the old approval surface.
        self.bridge.observe_terminal_output(b"Done. All tests pass.\r\n")
        st = self.store.get_state(self.cid)
        self.assertEqual(st.state, InteractionState.IDLE)
        self.assertTrue(self.bridge.try_drain_queue())

    def test_matched_surface_is_written_once_not_per_chunk(self):
        handle_pre_invocation({"conversationId": self.cid}, self.store)
        with patch.object(self.store, "_write_atomic", wraps=self.store._write_atomic) as w:
            self.bridge.observe_terminal_output(b"Allow once / Always allow\r\n")
            for i in range(50):
                self.bridge.observe_terminal_output(f"streaming line {i}\r\n".encode())
            self.assertLessEqual(w.call_count, 1)

    def test_split_pattern_still_detected(self):
        self.bridge.observe_terminal_output(b"... /teamwork-")
        self.bridge.observe_terminal_output(b"preview proposed\r\n")
        st = self.store.get_state(self.cid)
        self.assertEqual(st.state, InteractionState.AWAITING_PREVIEW)
        self.assertTrue(st.pending_preview)

    def test_redrawn_modal_is_redetected(self):
        handle_pre_invocation({"conversationId": self.cid}, self.store)
        self.bridge.observe_terminal_output(b"Allow once\r\n")
        # A lifecycle event clears it, but the TUI redraws the dialog: must re-detect.
        self.store.update_state(self.cid, state=InteractionState.RUNNING,
                                active_surface=ActiveSurface.PROMPT, pending_approval=False)
        self.bridge.observe_terminal_output(b"\x1b[2J Allow once \r\n")
        self.assertEqual(self.store.get_state(self.cid).state, InteractionState.AWAITING_APPROVAL)


if __name__ == "__main__":
    unittest.main()
