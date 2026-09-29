"""Unit tests for InteractionState, ActiveSurface, and InteractionStateStore."""

import concurrent.futures
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from antiagent.engine.interaction_state import (
    ActiveSurface,
    ConversationState,
    InteractionState,
    InteractionStateStore,
    detect_surface_from_terminal_output,
    strip_ansi,
)
from antiagent.flow_hook import (
    handle_post_invocation,
    handle_post_tool_use,
    handle_pre_invocation,
    handle_stop,
)


class TestInteractionState(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.runtime_dir = Path(self.temp_dir) / "runtime"
        self.store = InteractionStateStore(runtime_dir=self.runtime_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_strip_ansi(self):
        colored = "\x1b[31mRed text\x1b[0m and \x1b[1;32mBold Green\x1b[0m"
        self.assertEqual(strip_ansi(colored), "Red text and Bold Green")

    def test_detect_surface_from_terminal_output(self):
        # 1. Teamwork preview
        preview_text = "Review Proposed Team:\n1. Specialist A\n2. Specialist B\nAccept team [y/n]?"
        res = detect_surface_from_terminal_output(preview_text)
        self.assertIsNotNone(res)
        state, surface = res
        self.assertEqual(state, InteractionState.AWAITING_PREVIEW)
        self.assertEqual(surface, ActiveSurface.TEAMWORK_PREVIEW)

        # 2. Teamwork preview slash command
        slash_cmd_text = "Running /teamwork-preview for research architecture..."
        res_slash = detect_surface_from_terminal_output(slash_cmd_text)
        self.assertEqual(res_slash, (InteractionState.AWAITING_PREVIEW, ActiveSurface.TEAMWORK_PREVIEW))

        # 3. Question modal
        question_text = "Tool ask_question: Which framework would you prefer? (Submit/Skip)"
        res_q = detect_surface_from_terminal_output(question_text)
        self.assertEqual(res_q, (InteractionState.AWAITING_QUESTION, ActiveSurface.QUESTION))

        # 4. Approval dialog
        approval_text = "Permission required: Do you want to run: rm -rf ./cache? [Allow once / Always allow]"
        res_app = detect_surface_from_terminal_output(approval_text)
        self.assertEqual(res_app, (InteractionState.AWAITING_APPROVAL, ActiveSurface.APPROVAL))

        # 5. Settings screen
        settings_text = "AGY Configuration & Antigravity Settings Panel"
        res_set = detect_surface_from_terminal_output(settings_text)
        self.assertEqual(res_set, (InteractionState.IDLE, ActiveSurface.SETTINGS))

        # 6. Unrelated regular text
        regular_text = "Writing unit test file tests/test_app.py ... done."
        self.assertIsNone(detect_surface_from_terminal_output(regular_text))

    def test_state_store_save_and_retrieve(self):
        cid = "conv-123"
        state = ConversationState(
            conversation_id=cid,
            state=InteractionState.RUNNING,
            active_surface=ActiveSurface.PROMPT,
            step_idx=3,
            fully_idle=False,
        )
        self.store.save_state(state)

        # Retrieve and verify
        retrieved = self.store.get_state(cid)
        self.assertEqual(retrieved.conversation_id, cid)
        self.assertEqual(retrieved.state, InteractionState.RUNNING)
        self.assertEqual(retrieved.active_surface, ActiveSurface.PROMPT)
        self.assertEqual(retrieved.step_idx, 3)
        self.assertFalse(retrieved.fully_idle)

    def test_state_store_atomic_update(self):
        cid = "conv-update"
        self.store.update_state(
            cid,
            state=InteractionState.AWAITING_APPROVAL,
            active_surface=ActiveSurface.APPROVAL,
            pending_approval=True,
        )
        s1 = self.store.get_state(cid)
        self.assertEqual(s1.state, InteractionState.AWAITING_APPROVAL)
        self.assertEqual(s1.active_surface, ActiveSurface.APPROVAL)
        self.assertTrue(s1.pending_approval)

        self.store.update_state(
            cid,
            state=InteractionState.IDLE,
            active_surface=ActiveSurface.PROMPT,
            pending_approval=False,
            fully_idle=True,
        )
        s2 = self.store.get_state(cid)
        self.assertEqual(s2.state, InteractionState.IDLE)
        self.assertFalse(s2.pending_approval)
        self.assertTrue(s2.fully_idle)

    def test_state_store_active_conversation_id(self):
        self.assertIsNone(self.store.get_active_conversation_id())
        self.store.set_active_conversation_id("session-abc")
        self.assertEqual(self.store.get_active_conversation_id(), "session-abc")

    def test_dispatch_lease_management(self):
        cid = "conv-lease"
        # First worker acquires lease
        self.assertTrue(self.store.acquire_dispatch_lease(cid, "worker_1", ttl_seconds=1.0))

        # Second worker cannot acquire active lease
        self.assertFalse(self.store.acquire_dispatch_lease(cid, "worker_2", ttl_seconds=1.0))

        # First worker re-acquiring succeeds
        self.assertTrue(self.store.acquire_dispatch_lease(cid, "worker_1", ttl_seconds=1.0))

        # Release lease
        self.store.release_dispatch_lease(cid, "worker_1")

        # Now worker 2 can acquire
        self.assertTrue(self.store.acquire_dispatch_lease(cid, "worker_2", ttl_seconds=1.0))

    def test_concurrent_hook_writes(self):
        cid = "conv-concurrent"
        workers = 10
        updates_per_worker = 15

        def worker_task(worker_id: int):
            for i in range(updates_per_worker):
                self.store.update_state(
                    cid,
                    step_idx=worker_id * 100 + i,
                    last_event=f"worker_{worker_id}_{i}",
                    last_event_time=time.time(),
                )

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(worker_task, i) for i in range(workers)]
            for f in concurrent.futures.as_completed(futures):
                f.result()

        final = self.store.get_state(cid)
        self.assertEqual(final.conversation_id, cid)
        self.assertTrue(final.step_idx >= 0)
        self.assertTrue(final.last_event != "")

    def test_flow_hook_handlers(self):
        cid = "conv-flow-hooks"

        # 1. PreInvocation -> RUNNING
        res_pre = handle_pre_invocation({"conversationId": cid, "invocationNum": 1}, self.store)
        self.assertEqual(res_pre, {})
        s1 = self.store.get_state(cid)
        self.assertEqual(s1.state, InteractionState.RUNNING)
        self.assertEqual(s1.invocation_num, 1)
        self.assertFalse(s1.fully_idle)

        # 2. PostInvocation -> preserves state, records event
        res_post = handle_post_invocation({"conversationId": cid, "invocationNum": 1}, self.store)
        self.assertEqual(res_post, {})
        s2 = self.store.get_state(cid)
        self.assertEqual(s2.last_event, "PostInvocation")

        # 3. PostToolUse -> clears pending approval
        self.store.update_state(cid, state=InteractionState.AWAITING_APPROVAL, pending_approval=True)
        res_tool = handle_post_tool_use({"conversationId": cid, "stepIdx": 4}, self.store)
        self.assertEqual(res_tool, {})
        s3 = self.store.get_state(cid)
        self.assertEqual(s3.state, InteractionState.RUNNING)
        self.assertFalse(s3.pending_approval)

        # 4. Stop with fullyIdle=False -> BACKGROUND_BUSY
        res_stop_busy = handle_stop({"conversationId": cid, "fullyIdle": False}, self.store)
        self.assertEqual(res_stop_busy, {})
        s4 = self.store.get_state(cid)
        self.assertEqual(s4.state, InteractionState.BACKGROUND_BUSY)
        self.assertFalse(s4.fully_idle)

        # 5. Stop with fullyIdle=True -> IDLE
        res_stop_idle = handle_stop({"conversationId": cid, "fullyIdle": True}, self.store)
        self.assertEqual(res_stop_idle, {})
        s5 = self.store.get_state(cid)
        self.assertEqual(s5.state, InteractionState.IDLE)
        self.assertTrue(s5.fully_idle)

    def test_flow_hook_snake_case_payloads(self):
        """Verify flow hooks correctly parse snake_case payload keys."""
        cid = "conv-snake-case"

        # 1. pre_invocation with snake_case
        handle_pre_invocation({"conversation_id": cid, "invocation_num": 2}, self.store)
        s1 = self.store.get_state(cid)
        self.assertEqual(s1.state, InteractionState.RUNNING)
        self.assertEqual(s1.invocation_num, 2)

        # 2. post_tool_use with snake_case
        handle_post_tool_use({"conversation_id": cid, "step_idx": 7}, self.store)
        s2 = self.store.get_state(cid)
        self.assertEqual(s2.step_idx, 7)

        # 3. stop with snake_case fully_idle=true
        handle_stop({"conversation_id": cid, "fully_idle": True, "execution_num": 3}, self.store)
        s3 = self.store.get_state(cid)
        self.assertEqual(s3.state, InteractionState.IDLE)
        self.assertTrue(s3.fully_idle)
        self.assertEqual(s3.execution_num, 3)

    def test_detect_surface_split_across_chunks(self):
        """Verify rolling buffer accumulates terminal output and detects keywords split across chunks."""
        rolling_buffer = ""

        # Chunk 1 ends in partial keyword
        chunk1 = "Agent preparing team structure... running /teamwork-"
        rolling_buffer += chunk1
        # Incomplete chunk does not match
        self.assertIsNone(detect_surface_from_terminal_output(chunk1))

        # Chunk 2 provides the rest of the keyword
        chunk2 = "preview for approval [y/n]"
        rolling_buffer += chunk2
        # Second chunk alone does not match
        self.assertIsNone(detect_surface_from_terminal_output(chunk2))

        # But rolling buffer matches cleanly!
        res = detect_surface_from_terminal_output(rolling_buffer)
        self.assertIsNotNone(res)
        state, surface = res
        self.assertEqual(state, InteractionState.AWAITING_PREVIEW)
        self.assertEqual(surface, ActiveSurface.TEAMWORK_PREVIEW)

    def test_cross_process_state_synchronization(self):
        """Verify two independent InteractionStateStore instances sync via disk mtime."""
        cid = "conv-cross-process"
        store2 = InteractionStateStore(runtime_dir=self.runtime_dir)

        # Instance 1 reads initial default state
        s1 = self.store.get_state(cid)
        self.assertEqual(s1.state, InteractionState.UNKNOWN)

        # Instance 2 (representing a hook in another process) updates state to IDLE
        store2.update_state(cid, state=InteractionState.IDLE, fully_idle=True, last_event="Stop")

        # Instance 1 must immediately see the updated state from disk!
        s1_updated = self.store.get_state(cid)
        self.assertEqual(s1_updated.state, InteractionState.IDLE)
        self.assertTrue(s1_updated.fully_idle)
        self.assertEqual(s1_updated.last_event, "Stop")

    def test_flow_hook_default_conversation_id_fallback(self):
        """Verify lifecycle hooks fall back to 'default' when conversationId is missing."""
        # Empty payload without conversationId
        handle_pre_invocation({}, self.store)
        st_default = self.store.get_state("default")
        self.assertEqual(st_default.state, InteractionState.RUNNING)

        handle_stop({"fully_idle": True}, self.store)
        st_default_stop = self.store.get_state("default")
        self.assertEqual(st_default_stop.state, InteractionState.IDLE)
        self.assertTrue(st_default_stop.fully_idle)

    def test_flow_hook_post_tool_use_resets_question_and_active_tool(self):
        """Verify PostToolUse resets question modal flags and clears active_tool."""
        cid = "conv-tool-reset"
        self.store.update_state(
            cid,
            state=InteractionState.AWAITING_QUESTION,
            active_surface=ActiveSurface.QUESTION,
            pending_question=True,
            active_tool="ask_question",
        )

        handle_post_tool_use({"conversationId": cid, "stepIdx": 5}, self.store)
        st = self.store.get_state(cid)
        self.assertEqual(st.state, InteractionState.RUNNING)
        self.assertEqual(st.active_surface, ActiveSurface.PROMPT)
        self.assertFalse(st.pending_question)
        self.assertIsNone(st.active_tool)


if __name__ == "__main__":
    unittest.main()
