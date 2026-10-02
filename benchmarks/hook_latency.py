"""Hook latency benchmark harness for AntiAgent lifecycle hooks.

Reproduces Antigravity's documented hook execution model: every handler runs
synchronously via `sh -c "<command>"` with the JSON payload on stdin, cwd set
to the directory containing hooks.json, and the agent loop blocked until the
command exits. Hook commands are taken from the hooks.json that
`antiagent install` actually generates, so the benchmark tracks whatever the
current code installs.

Sections:
  A  raw process startup (python -c pass vs. flow hook vs. guard hook)
  B  in-process state persistence (get/update/save and write components)
  C  simulated agent runs, A/B/C/D configurations
  E  in-process breakdown from ANTIAGENT_PROFILE_HOOKS=1 records

Usage:
  python benchmarks/hook_latency.py --label before [--reps 3] [--startup-n 60]
  python benchmarks/hook_latency.py --compare results/before.json results/after.json
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = Path(__file__).resolve().parent / "results"

SCENARIOS: List[Tuple[int, int]] = [(1, 5), (5, 20), (10, 50), (20, 100)]

TOOLS = [
    ("view_file", lambda ws: {"AbsolutePath": f"{ws}/src/app.py"}),
    ("grep_search", lambda ws: {"Query": "def main", "SearchPath": ws}),
    ("run_command", lambda ws: {"CommandLine": "ls -la", "Cwd": ws}),
    ("list_dir", lambda ws: {"DirectoryPath": ws}),
    ("write_to_file", lambda ws: {"TargetFile": f"{ws}/src/new.py", "CodeContent": "x = 1\n"}),
]


# --------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------


class BenchEnv:
    """Isolated HOME + workspace so the benchmark never touches real user state."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="antiagent_bench_"))
        self.home = self.root / "home"
        self.ws = self.root / "ws"
        (self.ws / "src").mkdir(parents=True)
        (self.ws / "src" / "app.py").write_text("def main():\n    pass\n")
        self.home.mkdir()
        self.cid = str(uuid.uuid4())
        self.transcript = self.root / "transcript.jsonl"
        lines = []
        for i in range(60):
            lines.append(json.dumps({"stepIdx": i, "type": "USER_INPUT" if i == 0 else "TOOL", "content": "refactor main module " * 4}))
        self.transcript.write_text("\n".join(lines) + "\n")
        self.env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and not k.startswith("ANTIAGENT_")}
        self.env["HOME"] = str(self.home)
        self.env["USERPROFILE"] = str(self.home)

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def common(self) -> Dict[str, Any]:
        return {
            "conversationId": self.cid,
            "workspacePaths": [str(self.ws)],
            "transcriptPath": str(self.transcript),
            "artifactDirectoryPath": str(self.home / ".gemini" / "antigravity" / "brain" / self.cid),
            "modelName": "auto",
        }


def generate_hooks(env: BenchEnv) -> Dict[str, Any]:
    """Runs the real installer (in a child with the isolated HOME) and returns hooks.json."""
    code = (
        "import sys; from antiagent.cli import install_hook; "
        f"install_hook(is_global=False, workspace_path={str(env.ws)!r}, quiet=True)"
    )
    subprocess.run([sys.executable, "-c", code], env=env.env, check=True, cwd=str(REPO_ROOT))
    return json.loads((env.ws / ".agents" / "hooks.json").read_text())


def commands_for(hooks: Dict[str, Any], event: str, tool: str = "") -> List[str]:
    """Flattens hooks.json into the ordered list of commands Antigravity would run."""
    import re

    out: List[str] = []
    for spec in hooks.values():
        if not isinstance(spec, dict) or not spec.get("enabled", True):
            continue
        for entry in spec.get(event, []) or []:
            if "hooks" in entry:
                matcher = entry.get("matcher", "")
                if matcher not in ("", "*") and not re.fullmatch(matcher, tool):
                    continue
                out.extend(h["command"] for h in entry["hooks"])
            else:
                out.append(entry["command"])
    return out


def run_hook(env: BenchEnv, command: str, payload: Dict[str, Any], cwd: Path, extra_env: Dict[str, str] | None = None) -> float:
    e = env.env if not extra_env else {**env.env, **extra_env}
    data = json.dumps(payload).encode()
    t0 = time.perf_counter()
    proc = subprocess.run(["sh", "-c", command], input=data, capture_output=True, cwd=str(cwd), env=e)
    dt = (time.perf_counter() - t0) * 1000.0
    try:
        json.loads(proc.stdout.decode() or "null")
    except ValueError:
        raise RuntimeError(f"hook emitted non-JSON stdout: {proc.stdout!r} stderr={proc.stderr!r}")
    if proc.returncode != 0:
        raise RuntimeError(f"hook failed rc={proc.returncode}: {proc.stderr.decode()}")
    return dt


def stats(samples: List[float]) -> Dict[str, float]:
    s = sorted(samples)
    if not s:
        return {}
    p95 = s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]
    return {
        "n": len(s),
        "median": round(statistics.median(s), 3),
        "mean": round(statistics.fmean(s), 3),
        "p95": round(p95, 3),
        "min": round(s[0], 3),
        "max": round(s[-1], 3),
        "total": round(sum(s), 3),
    }


# --------------------------------------------------------------------------
# Section A: raw startup
# --------------------------------------------------------------------------


def section_a(env: BenchEnv, hooks: Dict[str, Any], n: int) -> Dict[str, Any]:
    py = shlex.quote(sys.executable)
    cwd = env.ws / ".agents"
    post = commands_for(hooks, "PostToolUse", "view_file")
    flow_post = [c for c in post if "flow_hook" in c]
    guard = [c for c in commands_for(hooks, "PreToolUse", "view_file") if "antiagent.hook" in c]
    pre_tool = {**env.common(), "toolCall": {"name": "view_file", "args": TOOLS[0][1](str(env.ws))}, "stepIdx": 3}
    post_tool = {**env.common(), "stepIdx": 3}

    variants: Dict[str, Tuple[str, Dict[str, Any]]] = {
        "python_pass": (f"{py} -c pass", {}),
        "python_import_json": (f"{py} -c 'import json,sys; sys.stdout.write(json.dumps({{}}))'", {}),
        "guard_pre_tool_use": (guard[0], pre_tool),
    }
    if flow_post:
        variants["flow_post_tool_use"] = (flow_post[0], post_tool)
    flow_stop = [c for c in commands_for(hooks, "Stop") if "flow_hook" in c]
    if flow_stop:
        variants["flow_stop_installed"] = (flow_stop[0], {**env.common(), "fullyIdle": True})
    samples: Dict[str, List[float]] = {k: [] for k in variants}
    for name, (cmd, payload) in variants.items():  # warm-up
        run_hook(env, cmd, payload, cwd)
    for _ in range(n):  # interleave to spread drift evenly
        for name, (cmd, payload) in variants.items():
            samples[name].append(run_hook(env, cmd, payload, cwd))
    return {k: stats(v) for k, v in samples.items()}


# --------------------------------------------------------------------------
# Section B: in-process persistence
# --------------------------------------------------------------------------


def section_b(env: BenchEnv, n: int) -> Dict[str, Any]:
    os.environ["HOME"] = str(env.home)
    sys.path.insert(0, str(REPO_ROOT))
    from antiagent.engine.interaction_state import ConversationState, InteractionState, InteractionStateStore

    rdir = env.home / ".antiagent" / "runtime_b"
    store = InteractionStateStore(runtime_dir=rdir)
    cid = env.cid
    base = ConversationState(conversation_id=cid, state=InteractionState.RUNNING, step_idx=12, invocation_num=4,
                             active_tool="run_command", metadata={"note": "x" * 200})
    store.save_state(base)

    def timeit(fn: Callable[[int], Any]) -> Dict[str, float]:
        out = []
        for i in range(n):
            t0 = time.perf_counter_ns()
            fn(i)
            out.append((time.perf_counter_ns() - t0) / 1e6)
        return stats(out)

    res: Dict[str, Any] = {}
    res["get_state_cold"] = timeit(lambda i: InteractionStateStore(runtime_dir=rdir).get_state(cid))
    res["get_state_warm"] = timeit(lambda i: store.get_state(cid))
    res["update_state_changed"] = timeit(lambda i: store.update_state(cid, step_idx=i, last_event_time=time.time()))
    res["update_state_identical"] = timeit(lambda i: store.update_state(cid, step_idx=7, state=InteractionState.RUNNING))
    res["save_state"] = timeit(lambda i: store.save_state(store.get_state(cid)))

    # Component breakdown of the atomic write path, replicated step by step.
    path = rdir / f"state_{cid}.json"
    content = json.dumps(base.to_dict(), indent=2)
    comps: Dict[str, List[float]] = {k: [] for k in ("serialize", "mkstemp", "write_flush", "fsync", "chmod", "replace")}
    for _ in range(n):
        t = time.perf_counter_ns
        a = t(); c = json.dumps(base.to_dict(), indent=2); b = t(); comps["serialize"].append((b - a) / 1e6)
        a = t(); fd, tmp = tempfile.mkstemp(dir=rdir, prefix=".tmp_bench_", text=True); b = t(); comps["mkstemp"].append((b - a) / 1e6)
        f = os.fdopen(fd, "w", encoding="utf-8")
        a = t(); f.write(c); f.flush(); b = t(); comps["write_flush"].append((b - a) / 1e6)
        a = t(); os.fsync(f.fileno()); b = t(); comps["fsync"].append((b - a) / 1e6)
        f.close()
        a = t(); os.chmod(tmp, 0o600); b = t(); comps["chmod"].append((b - a) / 1e6)
        a = t(); os.replace(tmp, path); b = t(); comps["replace"].append((b - a) / 1e6)
    res["write_components"] = {k: stats(v) for k, v in comps.items()}
    res["content_bytes"] = len(content)
    return res


# --------------------------------------------------------------------------
# Section C: simulated agent runs
# --------------------------------------------------------------------------


def lifecycle(invocations: int, tools: int) -> List[Tuple[str, str]]:
    """Ordered (event, tool) sequence for one agent execution."""
    seq: List[Tuple[str, str]] = []
    per = [tools // invocations + (1 if i < tools % invocations else 0) for i in range(invocations)]
    k = 0
    for i in range(invocations):
        seq.append(("PreInvocation", ""))
        for _ in range(per[i]):
            name = TOOLS[k % len(TOOLS)][0]
            seq.append(("PreToolUse", name))
            seq.append(("PostToolUse", name))
            k += 1
        seq.append(("PostInvocation", ""))
    seq.append(("Stop", ""))
    return seq


def payload_for(env: BenchEnv, event: str, tool: str, step: int, inv: int) -> Dict[str, Any]:
    p = env.common()
    if event == "PreToolUse":
        builder = dict(TOOLS)[tool]
        p.update({"toolCall": {"name": tool, "args": builder(str(env.ws))}, "stepIdx": step})
    elif event == "PostToolUse":
        p.update({"stepIdx": step})
    elif event in ("PreInvocation", "PostInvocation"):
        p.update({"invocationNum": inv, "initialNumSteps": step})
    elif event == "Stop":
        p.update({"executionNum": 1, "terminationReason": "model_stop", "fullyIdle": True})
    return p


NOFSYNC_PRELUDE = "import os; os.fsync = lambda *a: None; import runpy, sys; "


def without_fsync(cmd: str) -> str:
    """Rewrites `<py> -m <module> [arg]` to run the same module with os.fsync disabled."""
    import re

    gated = re.search(r"exec (.*?); fi;", cmd)
    if gated:
        return cmd.replace(gated.group(1), without_fsync(gated.group(1)))
    parts = shlex.split(cmd)
    if "-m" not in parts:
        return cmd
    i = parts.index("-m")
    py, module, args = parts[:i], parts[i + 1], parts[i + 2:]
    code = NOFSYNC_PRELUDE + f"sys.argv = ['{module}'] + {args!r}; runpy.run_module('{module}', run_name='__main__')"
    return " ".join(shlex.quote(x) for x in py) + " -c " + shlex.quote(code)


def section_c(env: BenchEnv, hooks: Dict[str, Any], reps: int) -> Dict[str, Any]:
    cwd = env.ws / ".agents"
    guard_only = {k: v for k, v in hooks.items() if k != "antiagent-flow-state"}
    sys.path.insert(0, str(REPO_ROOT))
    os.environ["HOME"] = str(env.home)

    def make_inproc():
        from antiagent import flow_hook
        from antiagent.engine.interaction_state import InteractionStateStore

        store = InteractionStateStore()
        handlers = {
            "PreInvocation": flow_hook.handle_pre_invocation,
            "PostInvocation": getattr(flow_hook, "handle_post_invocation", None),
            "PostToolUse": flow_hook.handle_post_tool_use,
            "Stop": flow_hook.handle_stop,
        }
        return store, handlers

    configs = ["A_guard_on_flow_on", "B_guard_on_flow_off", "C_flow_on_no_fsync", "D_flow_inprocess"]
    results: Dict[str, Any] = {}
    for inv, tools in SCENARIOS:
        key = f"{inv}inv_{tools}tools"
        results[key] = {}
        seq = lifecycle(inv, tools)
        for cfg in configs:
            totals: List[float] = []
            per_event: Dict[str, List[float]] = {}
            flow_samples: List[float] = []
            for _ in range(reps):
                total = 0.0
                step = 0
                inv_n = 0
                store = handlers = None
                if cfg == "D_flow_inprocess":
                    store, handlers = make_inproc()
                for event, tool in seq:
                    if event == "PreInvocation":
                        inv_n += 1
                    if event == "PreToolUse":
                        step += 1
                    payload = payload_for(env, event, tool, step, inv_n)
                    src = guard_only if cfg in ("B_guard_on_flow_off", "D_flow_inprocess") else hooks
                    cmds = commands_for(src, event, tool)
                    if cfg == "C_flow_on_no_fsync":
                        cmds = [without_fsync(c) for c in cmds]
                    for c in cmds:
                        dt = run_hook(env, c, payload, cwd)
                        total += dt
                        per_event.setdefault(event, []).append(dt)
                        if "flow_hook" in c:
                            flow_samples.append(dt)
                    if cfg == "D_flow_inprocess" and handlers.get(event) and commands_for(hooks, event, tool):
                        flow_cmds = [c for c in commands_for(hooks, event, tool) if "flow_hook" in c]
                        if flow_cmds:
                            t0 = time.perf_counter()
                            handlers[event](payload, store)
                            dt = (time.perf_counter() - t0) * 1000.0
                            total += dt
                            per_event.setdefault(event + "(inproc)", []).append(dt)
                            flow_samples.append(dt)
                totals.append(total)
            results[key][cfg] = {
                "total_ms_median": round(statistics.median(totals), 3),
                "total_ms_all": [round(t, 3) for t in totals],
                "per_event": {k: stats(v) for k, v in per_event.items()},
                "flow_hook": stats(flow_samples),
                "invocations": inv,
                "tool_calls": tools,
            }
        a = results[key]["A_guard_on_flow_on"]["total_ms_median"]
        b = results[key]["B_guard_on_flow_off"]["total_ms_median"]
        results[key]["flow_overhead_ms"] = round(a - b, 3)
        results[key]["flow_overhead_pct_of_hook_time"] = round(100.0 * (a - b) / a, 1) if a else 0.0
        results[key]["flow_overhead_per_tool_call_ms"] = round((a - b) / tools, 3)
        results[key]["flow_overhead_per_invocation_ms"] = round((a - b) / inv, 3)
        print(f"  {key}: A={a:.0f}ms B={b:.0f}ms flow overhead={a - b:.0f}ms", file=sys.stderr)
    return results


# --------------------------------------------------------------------------
# Section E: in-process profile breakdown
# --------------------------------------------------------------------------


def section_e(env: BenchEnv, hooks: Dict[str, Any], n: int) -> Dict[str, Any]:
    cwd = env.ws / ".agents"
    prof = env.home / ".antiagent" / "runtime" / "hook_profile.jsonl"
    if prof.exists():
        prof.unlink()
    extra = {"ANTIAGENT_PROFILE_HOOKS": "1"}
    seq = lifecycle(5, 20)
    step = 0
    for _ in range(max(1, n // len(seq))):
        for event, tool in seq:
            if event == "PreToolUse":
                step += 1
            for c in commands_for(hooks, event, tool):
                run_hook(env, c, payload_for(env, event, tool, step, 1), cwd, extra_env=extra)
    if not prof.exists():
        return {"records": 0}
    recs = [json.loads(line) for line in prof.read_text().splitlines() if line.strip()]
    by_event: Dict[str, Dict[str, List[float]]] = {}
    for r in recs:
        d = by_event.setdefault(r["event"], {})
        for k, v in r.items():
            if k.endswith("_ms"):
                d.setdefault(k, []).append(float(v))
    out = {ev: {k: round(statistics.median(v), 3) for k, v in d.items()} | {"n": len(next(iter(d.values()), []))}
           for ev, d in by_event.items()}
    return {"records": len(recs), "by_event_median": out}


# --------------------------------------------------------------------------


def run(label: str, reps: int, startup_n: int, persist_n: int, bridge_active: bool = False) -> Path:
    env = BenchEnv()
    if bridge_active:
        # Simulate a running `antiagent agy` session: the harness PID is a live bridge.
        bridges = env.home / ".antiagent" / "runtime" / "bridges"
        bridges.mkdir(parents=True, exist_ok=True)
        (bridges / str(os.getpid())).write_text("")
    try:
        hooks = generate_hooks(env)
        events = {ev: len(commands_for(hooks, ev, "view_file")) for ev in ("PreInvocation", "PostInvocation", "PreToolUse", "PostToolUse", "Stop")}
        print(f"[{label}] installed handlers per event: {events}", file=sys.stderr)
        result: Dict[str, Any] = {
            "label": label,
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "installed_handlers": events,
            "bridge_active": bridge_active,
        }
        print("[A] startup", file=sys.stderr)
        result["A_startup"] = section_a(env, hooks, startup_n)
        print("[B] persistence", file=sys.stderr)
        result["B_persistence"] = section_b(env, persist_n)
        print("[C] simulated runs", file=sys.stderr)
        result["C_runs"] = section_c(env, hooks, reps)
        print("[E] profile breakdown", file=sys.stderr)
        result["E_profile"] = section_e(env, hooks, 100)
    finally:
        env.cleanup()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{label}.json"
    out.write_text(json.dumps(result, indent=2))
    print(f"wrote {out}", file=sys.stderr)
    return out


def compare(before_path: str, after_path: str) -> None:
    b = json.loads(Path(before_path).read_text())
    a = json.loads(Path(after_path).read_text())

    def row(name: str, x: float, y: float) -> None:
        imp = (100.0 * (x - y) / x) if x else 0.0
        print(f"{name:<44} {x:>10.1f} ms {y:>10.1f} ms {imp:>8.1f}%")

    print(f"{'Scenario':<44} {'Before':>13} {'After':>13} {'Improvement':>9}")
    print("-" * 84)
    fb, fa = b["A_startup"].get("flow_post_tool_use", {}), a["A_startup"].get("flow_post_tool_use", {})
    if fb and fa:
        row("flow PostToolUse hook median (if installed)", fb["median"], fa["median"])
        row("flow PostToolUse hook p95", fb["p95"], fa["p95"])
    row("guard PreToolUse hook median", b["A_startup"]["guard_pre_tool_use"]["median"], a["A_startup"]["guard_pre_tool_use"]["median"])
    row("update_state (changed) median", b["B_persistence"]["update_state_changed"]["median"], a["B_persistence"]["update_state_changed"]["median"])
    row("update_state (identical) median", b["B_persistence"]["update_state_identical"]["median"], a["B_persistence"]["update_state_identical"]["median"])
    for key in b["C_runs"]:
        x = b["C_runs"][key]["A_guard_on_flow_on"]["total_ms_median"]
        y = a["C_runs"][key]["A_guard_on_flow_on"]["total_ms_median"]
        row(f"{key} total hook time (config A)", x, y)
    for key in b["C_runs"]:
        row(f"{key} flow overhead (A-B)", b["C_runs"][key]["flow_overhead_ms"], a["C_runs"][key]["flow_overhead_ms"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--startup-n", type=int, default=60)
    ap.add_argument("--persist-n", type=int, default=300)
    ap.add_argument("--bridge-active", action="store_true", help="simulate a running antiagent agy session")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    args = ap.parse_args()
    if args.compare:
        compare(*args.compare)
        return
    run(args.label, args.reps, args.startup_n, args.persist_n, args.bridge_active)


if __name__ == "__main__":
    main()
