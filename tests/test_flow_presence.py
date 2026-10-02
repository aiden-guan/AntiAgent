"""Tests for pay-only-when-used flow tracking (bridge presence gating)."""

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
from antiagent.engine import flow_presence
from antiagent.engine.interaction_state import InteractionState, InteractionStateStore
from antiagent.engine.prompt_queue import PromptQueue

REPO_ROOT = Path(__file__).resolve().parent.parent


class _HomeCase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self._env = patch.dict(os.environ, {"HOME": self.home})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.home, ignore_errors=True)


class TestPresence(_HomeCase):
    def test_no_markers_means_inactive(self):
        self.assertFalse(flow_presence.any_bridge_active())

    def test_register_and_unregister(self):
        flow_presence.register_bridge()
        self.assertTrue(flow_presence.any_bridge_active())
        flow_presence.unregister_bridge()
        self.assertFalse(flow_presence.any_bridge_active())

    def test_dead_pid_marker_is_pruned(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        d = flow_presence.bridges_dir()
        d.mkdir(parents=True)
        (d / str(proc.pid)).write_text("")
        self.assertFalse(flow_presence.any_bridge_active())
        self.assertFalse((d / str(proc.pid)).exists())


@unittest.skipIf(sys.platform == "win32", "flow hooks are not installed on Windows")
class TestShellGate(_HomeCase):
    def _gated(self, inner: str) -> str:
        from antiagent.cli import gate_flow_command

        return gate_flow_command(inner)

    def _run(self, cmd: str) -> subprocess.CompletedProcess:
        return subprocess.run(["sh", "-c", cmd], input=b'{"conversationId":"c"}', capture_output=True,
                              env={**os.environ, "HOME": self.home}, timeout=30)

    def test_no_bridge_answers_empty_json_without_running_command(self):
        sentinel = Path(self.home) / "ran"
        p = self._run(self._gated(f"touch {sentinel}"))
        self.assertEqual(p.stdout, b"{}")
        self.assertEqual(p.returncode, 0)
        self.assertFalse(sentinel.exists())

    def test_active_bridge_runs_real_hook(self):
        flow_presence.register_bridge()  # this test process is alive
        inner = f"{sys.executable} -m antiagent.flow_hook pre-invocation"
        env = {**os.environ, "HOME": self.home, "PYTHONPATH": str(REPO_ROOT)}
        p = subprocess.run(["sh", "-c", self._gated(inner)], input=b'{"conversationId":"gate-c"}',
                           capture_output=True, env=env, timeout=60)
        self.assertEqual(p.stdout, b"{}")
        state_file = Path(self.home) / ".antiagent" / "runtime" / "state_gate-c.json"
        self.assertEqual(json.loads(state_file.read_text())["state"], "RUNNING")

    def test_gate_is_much_cheaper_than_python(self):
        cmd = self._gated(f"{sys.executable} -m antiagent.flow_hook stop")
        t0 = time.perf_counter()
        for _ in range(10):
            self._run(cmd)
        gated = (time.perf_counter() - t0) / 10
        t0 = time.perf_counter()
        for _ in range(10):
            self._run(f"{sys.executable} -c pass")
        py = (time.perf_counter() - t0) / 10
        self.assertLess(gated, py)


class TestFlowHookWithoutBridge(_HomeCase):
    def test_legacy_ungated_command_does_no_state_io(self):
        env = {**os.environ, "HOME": self.home, "PYTHONPATH": str(REPO_ROOT)}
        p = subprocess.run([sys.executable, "-m", "antiagent.flow_hook", "post-tool-use"],
                           input=b'{"conversationId":"c"}', capture_output=True, env=env, timeout=60)
        self.assertEqual(p.stdout, b"{}")
        self.assertFalse((Path(self.home) / ".antiagent" / "runtime" / "state_c.json").exists())

    def test_guard_still_evaluates_but_skips_state_without_bridge(self):
        from antiagent import hook

        with patch("antiagent.engine.interaction_state.InteractionStateStore.update_state") as upd:
            res = hook.handle_pre_tool_use({
                "conversationId": "g", "workspacePaths": [self.home],
                "toolCall": {"name": "run_command", "args": {"CommandLine": "git push --force"}},
            })
            upd.assert_not_called()
        self.assertEqual(res["decision"], "force_ask")


class TestInstallPlatform(unittest.TestCase):
    def test_windows_never_installs_flow_hooks(self):
        from antiagent import cli

        ws = tempfile.mkdtemp()
        try:
            with patch.object(cli.sys, "platform", "win32"), \
                    patch("antiagent.cli.load_config", return_value=AntiAgentConfig()), \
                    patch("antiagent.cli.get_hook_command", return_value="python -m antiagent.hook"):
                cli.install_hook(is_global=False, workspace_path=ws, quiet=True)
            data = json.loads((Path(ws) / ".agents" / "hooks.json").read_text())
            self.assertIn("antiagent-guard", data)
            self.assertNotIn("antiagent-flow-state", data)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    @unittest.skipIf(sys.platform == "win32", "posix only")
    def test_installed_commands_are_gated_and_ungated_specs_are_migrated(self):
        from antiagent import cli

        self.assertFalse(cli._flow_spec_is_current({"enabled": True, "PreInvocation": [{"command": "python -m antiagent.flow_hook pre-invocation"}], "Stop": [{"command": "x"}]}))
        with patch("antiagent.cli._is_importable_without_pythonpath", return_value=True):
            spec = cli.build_flow_hook_spec()
        self.assertTrue(cli._flow_spec_is_current(spec))


class TestBridgeSessionState(_HomeCase):
    def test_state_from_before_session_is_ignored(self):
        from antiagent.engine.pty_bridge import PTYBridge

        rt = Path(self.home) / "rt"
        store = InteractionStateStore(runtime_dir=rt)
        store.update_state("c", state=InteractionState.RUNNING, last_event_time=time.time() - 3600)
        bridge = PTYBridge(conversation_id="c", config=AntiAgentConfig(), store=store,
                           queue=PromptQueue("c", runtime_dir=rt, recover_as_held=False))
        bridge._started_at = time.time()
        bridge.master_fd = 999
        bridge.prompt_buffer.insert("hello")
        with patch("os.write", return_value=1):
            consumed = bridge.handle_keyboard_event("enter", b"\r")
        self.assertFalse(consumed)  # stale RUNNING from an untracked run must not queue Enter

    def test_bridge_run_registers_and_unregisters(self):
        from antiagent.engine.pty_bridge import PTYBridge

        seen = {}

        class Boom(Exception):
            pass

        def fake_popen(*a, **k):
            seen["active"] = flow_presence.any_bridge_active()
            raise Boom()

        bridge = PTYBridge(conversation_id="c", config=AntiAgentConfig(),
                           store=InteractionStateStore(runtime_dir=Path(self.home) / "rt"),
                           queue=PromptQueue("c", runtime_dir=Path(self.home) / "rt", recover_as_held=False))
        r, w = os.pipe()
        with patch("antiagent.engine.pty_bridge.pty_engine.is_pty_supported", return_value=True), \
                patch("antiagent.engine.pty_bridge.pty_engine.open_pty", return_value=(r, w)), \
                patch("subprocess.Popen", side_effect=fake_popen):
            with self.assertRaises(Boom):
                bridge.run(["true"])
        self.assertTrue(seen["active"])
        self.assertFalse(flow_presence.any_bridge_active())
