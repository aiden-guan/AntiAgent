"""Unit tests for Codex-style PromptQueue and Safe Dispatch Contract."""

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
)
from antiagent.engine.prompt_queue import (
    EmptyPromptError,
    PromptQueue,
    PromptTooLargeError,
    QueueFullError,
    can_dispatch_user_turn,
    recover_stale_sessions,
)


class TestPromptQueue(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.runtime_dir = Path(self.temp_dir) / "runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.cid = "test-conversation-1"
        self.queue = PromptQueue(self.cid, runtime_dir=self.runtime_dir, recover_as_held=False)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_fifo_ordering(self):
        p1 = self.queue.enqueue("1. first prompt", source="tab")
        p2 = self.queue.enqueue("2. second prompt", source="enter")
        p3 = self.queue.enqueue("3. third prompt", source="tab")

        self.assertEqual(self.queue.queued_count(), 3)

        # Pop 1
        d1 = self.queue.pop_next_for_dispatch()
        self.assertIsNotNone(d1)
        self.assertEqual(d1.id, p1.id)
        self.assertEqual(d1.text, "1. first prompt")
        self.assertEqual(d1.status, "dispatching")
        self.queue.mark_sent(d1.id)

        # Pop 2
        d2 = self.queue.pop_next_for_dispatch()
        self.assertIsNotNone(d2)
        self.assertEqual(d2.id, p2.id)
        self.assertEqual(d2.text, "2. second prompt")
        self.queue.mark_sent(d2.id)

        # Pop 3
        d3 = self.queue.pop_next_for_dispatch()
        self.assertIsNotNone(d3)
        self.assertEqual(d3.id, p3.id)
        self.assertEqual(d3.text, "3. third prompt")
        self.queue.mark_sent(d3.id)

        self.assertIsNone(self.queue.pop_next_for_dispatch())
        self.assertEqual(self.queue.queued_count(), 0)

    def test_enqueue_at_front_for_steering(self):
        p1 = self.queue.enqueue("normal prompt 1", source="enter")
        p2 = self.queue.enqueue("normal prompt 2", source="enter")

        # Steer inserts at front
        steer = self.queue.enqueue("steer immediately!", source="steer", at_front=True)

        d1 = self.queue.pop_next_for_dispatch()
        self.assertEqual(d1.id, steer.id)
        self.assertEqual(d1.source, "steer")

    def test_reject_empty_or_whitespace_prompts(self):
        with self.assertRaises(EmptyPromptError):
            self.queue.enqueue("")

        with self.assertRaises(EmptyPromptError):
            self.queue.enqueue("    \n\t   ")

    def test_max_prompt_bytes(self):
        small_queue = PromptQueue(self.cid, runtime_dir=self.runtime_dir, max_bytes=20, recover_as_held=False)
        small_queue.enqueue("short prompt")  # 12 bytes: OK

        with self.assertRaises(PromptTooLargeError):
            small_queue.enqueue("this prompt is way too long for the 20 byte limit")

    def test_max_queue_depth(self):
        shallow_queue = PromptQueue(self.cid, runtime_dir=self.runtime_dir, max_depth=3, recover_as_held=False)
        shallow_queue.enqueue("prompt 1")
        shallow_queue.enqueue("prompt 2")
        shallow_queue.enqueue("prompt 3")

        with self.assertRaises(QueueFullError):
            shallow_queue.enqueue("prompt 4 should be rejected")

    def test_stale_persisted_queue_does_not_auto_replay(self):
        # 1. First session queues prompts
        q1 = PromptQueue(self.cid, runtime_dir=self.runtime_dir, recover_as_held=False)
        q1.enqueue("unexecuted prompt from earlier session")
        self.assertEqual(q1.queued_count(), 1)

        # 2. Simulated app restart / new session with recover_as_held=True
        q2 = PromptQueue(self.cid, runtime_dir=self.runtime_dir, recover_as_held=True)
        # Should NOT be in 'queued' status
        self.assertEqual(q2.queued_count(), 0)
        items = q2.list_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].status, "held")

        # Calling pop_next_for_dispatch returns None (safe against blind auto-replay!)
        self.assertIsNone(q2.pop_next_for_dispatch())

        # Can be resumed manually if requested
        resumed = q2.resume_held_items()
        self.assertEqual(resumed, 1)
        self.assertEqual(q2.queued_count(), 1)
        self.assertIsNotNone(q2.pop_next_for_dispatch())

    def test_safe_dispatch_contract_idle(self):
        self.queue.enqueue("run tests")
        state = ConversationState(
            conversation_id=self.cid,
            state=InteractionState.IDLE,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=True,
        )
        self.assertTrue(can_dispatch_user_turn(state, self.queue, self.cid))

    def test_safe_dispatch_contract_running_blocks(self):
        self.queue.enqueue("run tests")
        state = ConversationState(
            conversation_id=self.cid,
            state=InteractionState.RUNNING,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=False,
        )
        self.assertFalse(can_dispatch_user_turn(state, self.queue, self.cid))

    def test_safe_dispatch_contract_post_invocation_alone_does_not_drain(self):
        self.queue.enqueue("run tests")
        # PostInvocation happened, but agent is still RUNNING and not fully_idle
        state = ConversationState(
            conversation_id=self.cid,
            state=InteractionState.RUNNING,
            active_surface=ActiveSurface.PROMPT,
            last_event="PostInvocation",
            fully_idle=False,
        )
        self.assertFalse(can_dispatch_user_turn(state, self.queue, self.cid))

    def test_safe_dispatch_contract_stop_fully_idle_true_drains(self):
        self.queue.enqueue("run tests")
        state = ConversationState(
            conversation_id=self.cid,
            state=InteractionState.IDLE,
            active_surface=ActiveSurface.PROMPT,
            last_event="Stop",
            fully_idle=True,
        )
        self.assertTrue(can_dispatch_user_turn(state, self.queue, self.cid))

    def test_safe_dispatch_contract_stop_fully_idle_false_blocks(self):
        self.queue.enqueue("run tests")
        # Background task is still running
        state = ConversationState(
            conversation_id=self.cid,
            state=InteractionState.BACKGROUND_BUSY,
            active_surface=ActiveSurface.PROMPT,
            last_event="Stop",
            fully_idle=False,
        )
        self.assertFalse(can_dispatch_user_turn(state, self.queue, self.cid))

    def test_safe_dispatch_contract_approval_blocks(self):
        self.queue.enqueue("run tests")
        state = ConversationState(
            conversation_id=self.cid,
            state=InteractionState.AWAITING_APPROVAL,
            active_surface=ActiveSurface.APPROVAL,
            pending_approval=True,
            fully_idle=True,
        )
        self.assertFalse(can_dispatch_user_turn(state, self.queue, self.cid))

    def test_safe_dispatch_contract_preview_blocks(self):
        self.queue.enqueue("run tests")
        state = ConversationState(
            conversation_id=self.cid,
            state=InteractionState.AWAITING_PREVIEW,
            active_surface=ActiveSurface.TEAMWORK_PREVIEW,
            pending_preview=True,
            fully_idle=True,
        )
        self.assertFalse(can_dispatch_user_turn(state, self.queue, self.cid))

    def test_safe_dispatch_contract_question_blocks(self):
        self.queue.enqueue("run tests")
        state = ConversationState(
            conversation_id=self.cid,
            state=InteractionState.AWAITING_QUESTION,
            active_surface=ActiveSurface.QUESTION,
            pending_question=True,
            fully_idle=True,
        )
        self.assertFalse(can_dispatch_user_turn(state, self.queue, self.cid))

    def test_safe_dispatch_contract_modal_consuming_enter_blocks(self):
        self.queue.enqueue("run tests")
        state = ConversationState(
            conversation_id=self.cid,
            state=InteractionState.IDLE,
            active_surface=ActiveSurface.SETTINGS,
            fully_idle=True,
        )
        self.assertFalse(can_dispatch_user_turn(state, self.queue, self.cid, active_surface=ActiveSurface.SETTINGS))

    def test_safe_dispatch_contract_conversation_change_blocks(self):
        self.queue.enqueue("run tests for conversation 1")
        state = ConversationState(
            conversation_id="other-conversation-2",
            state=InteractionState.IDLE,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=True,
        )
        # Conversation ID does not match queue's conversation ID
        self.assertFalse(can_dispatch_user_turn(state, self.queue, "other-conversation-2"))

    def test_safe_dispatch_contract_unknown_state_blocks(self):
        self.queue.enqueue("run tests")
        state = ConversationState(
            conversation_id=self.cid,
            state=InteractionState.UNKNOWN,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=True,
        )
        self.assertFalse(can_dispatch_user_turn(state, self.queue, self.cid))

    def test_draining_strictly_one_at_a_time(self):
        self.queue.enqueue("item 1")
        self.queue.enqueue("item 2")

        state = ConversationState(
            conversation_id=self.cid,
            state=InteractionState.IDLE,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=True,
        )
        self.assertTrue(can_dispatch_user_turn(state, self.queue, self.cid))

        # Pop item 1
        item1 = self.queue.pop_next_for_dispatch()
        self.assertEqual(item1.text, "item 1")

        # Now an item is dispatching -> can_dispatch becomes FALSE until item finishes!
        self.assertFalse(can_dispatch_user_turn(state, self.queue, self.cid))

        # Turn starts and completes
        state.state = InteractionState.RUNNING
        state.fully_idle = False
        self.assertFalse(can_dispatch_user_turn(state, self.queue, self.cid))

        self.queue.mark_sent(item1.id)
        # Agent becomes safely idle again
        state.state = InteractionState.IDLE
        state.fully_idle = True
        self.assertTrue(can_dispatch_user_turn(state, self.queue, self.cid))

        # Pop item 2
        item2 = self.queue.pop_next_for_dispatch()
        self.assertEqual(item2.text, "item 2")
        self.queue.mark_sent(item2.id)

        # Queue empty -> cannot dispatch
        self.assertFalse(can_dispatch_user_turn(state, self.queue, self.cid))

    def test_default_init_does_not_mutate_queued_to_held(self):
        """Regression test: Creating a PromptQueue instance must NOT mark active items as held."""
        self.queue.enqueue("active prompt")
        self.assertEqual(self.queue.queued_count(), 1)

        # Create another instance with default arguments (e.g. status command)
        q2 = PromptQueue(self.cid, runtime_dir=self.runtime_dir)
        self.assertEqual(q2.queued_count(), 1)
        item = q2.peek()
        self.assertIsNotNone(item)
        self.assertEqual(item.status, "queued")

    def test_recover_stale_sessions(self):
        """Verify recover_stale_sessions scans runtime dir and marks orphaned queued items as held."""
        # Create an uncompleted queue on disk
        q_file = self.runtime_dir / "queue_stale_test.json"
        data = [
            {"id": "1", "conversation_id": "stale_test", "text": "orphaned prompt", "created_at": 100.0, "source": "enter", "status": "queued"}
        ]
        q_file.write_text(json.dumps(data), encoding="utf-8")

        held_count = recover_stale_sessions(self.runtime_dir)
        self.assertEqual(held_count, 1)

        reloaded = json.loads(q_file.read_text(encoding="utf-8"))
        self.assertEqual(reloaded[0]["status"], "held")
        self.assertEqual(reloaded[0]["note"], "Held on session recovery")

    def test_dispatch_lease_owner_check(self):
        """Verify can_dispatch_user_turn permits the active lease owner but blocks others."""
        self.queue.enqueue("prompt for turn")
        state = ConversationState(
            conversation_id=self.cid,
            state=InteractionState.IDLE,
            active_surface=ActiveSurface.PROMPT,
            fully_idle=True,
            dispatch_lease_owner="worker_1",
            dispatch_lease_expires=time.time() + 10.0,
        )

        # Other worker blocked
        self.assertFalse(can_dispatch_user_turn(state, self.queue, self.cid, lease_owner="worker_2"))
        self.assertFalse(can_dispatch_user_turn(state, self.queue, self.cid, lease_owner=None))

        # Lease holder allowed
        self.assertTrue(can_dispatch_user_turn(state, self.queue, self.cid, lease_owner="worker_1"))

    def test_dispatching_timeout_cleanup(self):
        """Verify has_dispatching_item cleans up abandoned dispatching items past timeout."""
        p = self.queue.enqueue("prompt that crashed during dispatch")
        dispatched = self.queue.pop_next_for_dispatch()
        self.assertIsNotNone(dispatched)
        self.assertEqual(dispatched.status, "dispatching")

        # Set dispatched_at to 100s ago
        dispatched.dispatched_at = time.time() - 100.0
        self.queue._persist()

        # Should detect timeout, mark as failed, and return False (so queue is not permanently wedged!)
        has_active = self.queue.has_dispatching_item(timeout_seconds=60.0)
        self.assertFalse(has_active)

        items = self.queue.list_items()
        self.assertEqual(items[0].status, "failed")
        self.assertEqual(items[0].note, "Dispatch timed out")

    def test_cross_process_queue_clear_synchronization(self):
        """Verify another process calling queue.clear() is reflected immediately in active queue instance."""
        q2 = PromptQueue(self.cid, runtime_dir=self.runtime_dir)
        self.queue.enqueue("item 1")
        self.queue.enqueue("item 2")
        self.assertEqual(self.queue.queued_count(), 2)

        # q2 clears the queue on disk
        q2.clear()

        # self.queue must immediately see 0 queued items on disk
        self.assertEqual(self.queue.queued_count(), 0)
        self.assertFalse(self.queue.has_queued_items())
        self.assertIsNone(self.queue.peek())

    def test_cross_process_queue_resume_synchronization(self):
        """Verify another process resuming held items is reflected immediately."""
        self.queue.enqueue("held item")
        item = self.queue.peek()
        self.assertIsNotNone(item)
        item.status = "held"
        self.queue._persist()
        self.assertEqual(self.queue.queued_count(), 0)

        # Process 2 resumes held items
        q2 = PromptQueue(self.cid, runtime_dir=self.runtime_dir)
        resumed = q2.resume_held_items()
        self.assertEqual(resumed, 1)

        # Process 1 immediately sees item back in queued state
        self.assertEqual(self.queue.queued_count(), 1)
        self.assertTrue(self.queue.has_queued_items())

    def test_prune_old_terminal_items(self):
        """Verify that completed items are pruned to prevent unbounded memory/file growth."""
        shallow_queue = PromptQueue(self.cid, runtime_dir=self.runtime_dir, max_depth=5, recover_as_held=False)
        # Enqueue and complete 10 items
        for i in range(10):
            it = shallow_queue.enqueue(f"cmd {i}")
            dispatched = shallow_queue.pop_next_for_dispatch()
            self.assertIsNotNone(dispatched)
            shallow_queue.mark_sent(dispatched.id)

        # Now enqueue 2 new items
        shallow_queue.enqueue("active 1")
        shallow_queue.enqueue("active 2")

        # Total items should be bounded around 2x max_depth (<= 12 items)
        self.assertLessEqual(shallow_queue.total_count(), shallow_queue.max_depth * 2 + 2)
        self.assertEqual(shallow_queue.queued_count(), 2)


if __name__ == "__main__":
    unittest.main()
