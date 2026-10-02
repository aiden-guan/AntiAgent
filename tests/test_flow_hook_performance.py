"""Regression tests for flow-hook overhead reductions, profiling, and conditional install."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from antiagent.config import AntiAgentConfig
from antiagent.engine import hook_profiler
from antiagent.engine.interaction_state import InteractionState, InteractionStateStore
from antiagent.flow_hook import handle_post_invocation, handle_post_tool_use, handle_pre_invocation

REPO_ROOT = Path(__file__).resolve().parent.parent


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.store = InteractionStateStore(runtime_dir=Path(self.temp_dir) / "runtime")
        self.cid = "perf-cid"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)


class TestStatePersistence(_StoreCase):
    def test_redundant_update_does_not_write(self):
        self.store.update_state(self.cid, state=InteractionState.RUNNING, step_idx=3)
        with patch.object(self.store, "_write_atomic") as w:
            self.store.update_state(self.cid, state=InteractionState.RUNNING, step_idx=3)
            self.store.update_state(self.cid, last_event="X", last_event_time=time.time())
            w.assert_not_called()
        # Informational fields are still visible in-process.
        self.assertEqual(self.store.get_state(self.cid).last_event, "X")

    def test_meaningful_change_writes_once(self):
        self.store.update_state(self.cid, state=InteractionState.RUNNING)
        with patch.object(self.store, "_write_atomic", wraps=self.store._write_atomic) as w:
            self.store.update_state(self.cid, state=InteractionState.IDLE, fully_idle=True)
            self.assertEqual(w.call_count, 1)
        fresh = InteractionStateStore(runtime_dir=self.store.runtime_dir)
        self.assertEqual(fresh.get_state(self.cid).state, InteractionState.IDLE)

    def test_state_write_does_not_fsync(self):
        with patch("os.fsync") as fs:
            self.store.update_state(self.cid, state=InteractionState.RUNNING)
            self.store.set_active_conversation_id(self.cid)
            fs.assert_not_called()
        self.assertTrue((self.store.runtime_dir / f"state_{self.cid}.json").is_file())

    def test_state_write_is_atomic_and_user_only(self):
        self.store.update_state(self.cid, state=InteractionState.RUNNING)
        path = self.store.runtime_dir / f"state_{self.cid}.json"
        json.loads(path.read_text())
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p for p in self.store.runtime_dir.iterdir() if p.name.startswith(".tmp_")], [])

    def test_unchanged_active_conversation_id_does_not_write(self):
        self.store.set_active_conversation_id(self.cid)
        with patch.object(self.store, "_write_atomic") as w:
            self.store.set_active_conversation_id(self.cid)
            w.assert_not_called()

    def test_prompt_queue_still_fsyncs(self):
        from antiagent.engine.prompt_queue import PromptQueue

        q = PromptQueue(self.cid, runtime_dir=self.store.runtime_dir, recover_as_held=False)
        with patch("os.fsync") as fs:
            q.enqueue("keep me durable")
            self.assertTrue(fs.called)


class TestLegacyHandlers(_StoreCase):
    def test_post_invocation_performs_no_io(self):
        handle_pre_invocation({"conversationId": self.cid}, self.store)
        with patch.object(self.store, "_write_atomic") as w:
            self.assertEqual(handle_post_invocation({"conversationId": self.cid}, self.store), {})
            w.assert_not_called()

    def test_post_tool_use_noop_when_running(self):
        handle_pre_invocation({"conversationId": self.cid}, self.store)
        with patch.object(self.store, "_write_atomic") as w:
            handle_post_tool_use({"conversationId": self.cid, "stepIdx": 2}, self.store)
            w.assert_not_called()
        self.assertEqual(self.store.get_state(self.cid).state, InteractionState.RUNNING)

    def test_post_tool_use_still_clears_approval(self):
        self.store.update_state(self.cid, state=InteractionState.AWAITING_APPROVAL, pending_approval=True)
        handle_post_tool_use({"conversationId": self.cid}, self.store)
        st = self.store.get_state(self.cid)
        self.assertEqual(st.state, InteractionState.RUNNING)
        self.assertFalse(st.pending_approval)


class TestProfiler(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("ANTIAGENT_")}
        self.env["HOME"] = self.home
        self.env["PYTHONPATH"] = str(REPO_ROOT)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _run(self, module, arg, payload, profile):
        env = dict(self.env)
        if profile:
            env["ANTIAGENT_PROFILE_HOOKS"] = "1"
        cmd = [sys.executable, "-m", module] + ([arg] if arg else [])
        return subprocess.run(cmd, input=json.dumps(payload).encode(), capture_output=True, env=env, timeout=60)

    def test_profiling_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(hook_profiler.ENV_VAR, None)
            self.assertFalse(hook_profiler.enabled())
            with hook_profiler.span("x"):
                pass
            self.assertEqual(hook_profiler.snapshot_ms(), {})
        p = self._run("antiagent.flow_hook", "stop", {"conversationId": "c1", "fullyIdle": True}, profile=False)
        self.assertEqual(p.stdout, b"{}")
        self.assertFalse((Path(self.home) / ".antiagent" / "runtime" / "hook_profile.jsonl").exists())

    def test_profiling_records_timings_without_touching_stdout(self):
        bridges = Path(self.home) / ".antiagent" / "runtime" / "bridges"
        bridges.mkdir(parents=True)
        (bridges / str(os.getpid())).write_text("")  # this test process stands in for a live bridge
        p = self._run("antiagent.flow_hook", "pre-invocation", {"conversationId": "c1", "stepIdx": 4}, profile=True)
        self.assertEqual(p.stdout, b"{}")
        g = self._run(
            "antiagent.hook", None,
            {"conversationId": "c1", "toolCall": {"name": "view_file", "args": {"AbsolutePath": f"{self.home}/a.txt"}},
             "workspacePaths": [self.home], "stepIdx": 5},
            profile=True,
        )
        self.assertIn("decision", json.loads(g.stdout))
        lines = (Path(self.home) / ".antiagent" / "runtime" / "hook_profile.jsonl").read_text().splitlines()
        recs = [json.loads(x) for x in lines]
        self.assertEqual([r["event"] for r in recs], ["flow:pre-invocation", "guard:pre-tool-use"])
        for r in recs:
            self.assertGreater(r["duration_ms"], 0)
            self.assertEqual(r["conversation_id"], "c1")
            self.assertIn("state_write_ms", r)
        self.assertIn("evaluate_ms", recs[1])

    def test_profiling_errors_never_break_hook(self):
        with patch.dict(os.environ, {hook_profiler.ENV_VAR: "1"}), \
                patch("antiagent.engine.hook_profiler.open", side_effect=OSError("disk full"), create=True):
            hook_profiler.emit("flow:stop", time.perf_counter_ns())  # must not raise


class TestConditionalInstall(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp()
        self.hooks_file = Path(self.ws) / ".agents" / "hooks.json"

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def _install(self, cfg):
        from antiagent import cli

        with patch("antiagent.cli.load_config", return_value=cfg), \
                patch("antiagent.cli._is_importable_without_pythonpath", return_value=True):
            cli.install_hook(is_global=False, workspace_path=self.ws, quiet=True)
        return json.loads(self.hooks_file.read_text())

    def test_flow_hooks_installed_when_any_flow_feature_enabled(self):
        for flags in ((True, False, False), (False, True, False), (False, False, True)):
            cfg = AntiAgentConfig(prompt_queue_enabled=flags[0], smart_enter_enabled=flags[1], steer_enabled=flags[2])
            data = self._install(cfg)
            self.assertEqual(sorted(k for k in data["antiagent-flow-state"] if k != "enabled"), ["PreInvocation", "Stop"])

    def test_flow_hooks_not_installed_when_all_disabled(self):
        self._install(AntiAgentConfig())
        data = self._install(AntiAgentConfig(prompt_queue_enabled=False, smart_enter_enabled=False, steer_enabled=False))
        self.assertNotIn("antiagent-flow-state", data)
        self.assertIn("antiagent-guard", data)  # security hook unaffected

    def test_refresh_migrates_legacy_flow_spec(self):
        from antiagent import cli

        self.hooks_file.parent.mkdir(parents=True)
        legacy = {
            "antiagent-guard": {"enabled": True, "PreToolUse": []},
            "antiagent-flow-state": {"enabled": True, "PreInvocation": [], "PostInvocation": [],
                                     "PostToolUse": [], "Stop": []},
            "user-hook": {"enabled": True, "Stop": [{"command": "echo hi"}]},
        }
        self.hooks_file.write_text(json.dumps(legacy))
        fake_home = tempfile.mkdtemp()
        try:
            with patch.dict(os.environ, {"HOME": fake_home}), \
                    patch("antiagent.cli.load_config", return_value=AntiAgentConfig()), \
                    patch("antiagent.cli._is_importable_without_pythonpath", return_value=True):
                cli.refresh_installed_hooks(self.ws)
        finally:
            shutil.rmtree(fake_home, ignore_errors=True)
        data = json.loads(self.hooks_file.read_text())
        self.assertEqual(sorted(k for k in data["antiagent-flow-state"] if k != "enabled"), ["PreInvocation", "Stop"])
        self.assertIn("user-hook", data)

    def test_refresh_removes_flow_hook_when_features_disabled(self):
        from antiagent import cli

        self._install(AntiAgentConfig())
        fake_home = tempfile.mkdtemp()
        off = AntiAgentConfig(prompt_queue_enabled=False, smart_enter_enabled=False, steer_enabled=False)
        try:
            with patch.dict(os.environ, {"HOME": fake_home}), \
                    patch("antiagent.cli.load_config", return_value=off), \
                    patch("antiagent.cli._is_importable_without_pythonpath", return_value=True):
                cli.refresh_installed_hooks(self.ws)
        finally:
            shutil.rmtree(fake_home, ignore_errors=True)
        self.assertNotIn("antiagent-flow-state", json.loads(self.hooks_file.read_text()))


class TestGuardHookFlowGating(unittest.TestCase):
    def test_guard_skips_state_writes_when_flow_disabled_but_still_evaluates(self):
        from antiagent import hook

        off = AntiAgentConfig(prompt_queue_enabled=False, smart_enter_enabled=False, steer_enabled=False)
        with patch("antiagent.hook.load_config", return_value=off), \
                patch("antiagent.engine.interaction_state.InteractionStateStore.update_state") as upd:
            res = hook.handle_pre_tool_use({
                "conversationId": "g1",
                "toolCall": {"name": "run_command", "args": {"CommandLine": "rm -rf /"}},
                "workspacePaths": ["/tmp/ws"],
            })
            upd.assert_not_called()
        self.assertEqual(res["decision"], "deny")


if __name__ == "__main__":
    unittest.main()
