"""Antigravity Lifecycle Hook entrypoint for PreToolUse.

Receives Antigravity's PreToolUse JSON payload on stdin and emits the
evaluation verdict JSON on stdout.
"""

import json
import sys
import time
from typing import Any, Dict

from antiagent.engine import hook_profiler as _prof
from antiagent.audit.logger import AuditLogger
from antiagent.config import load_config
from antiagent.constants import DECISION_ASK
from antiagent.engine.context_extractor import TranscriptContextExtractor
from antiagent.engine.evaluator import AntiAgentEvaluator


def handle_pre_tool_use(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Processes an Antigravity PreToolUse event payload."""
    tool_call = payload.get("toolCall", {})
    tool_name = tool_call.get("name", "")
    tool_args = tool_call.get("args", {})
    workspace_paths = payload.get("workspacePaths", [])
    conversation_id = payload.get("conversationId") or payload.get("conversation_id") or ""
    if not conversation_id and isinstance(payload.get("session"), dict):
        conversation_id = payload["session"].get("id", "")
    if not conversation_id:
        try:
            from antiagent.engine.interaction_state import InteractionStateStore

            conversation_id = InteractionStateStore.default().get_active_conversation_id() or ""
        except Exception:
            pass
    if not conversation_id:
        conversation_id = "default"
    step_idx = payload.get("stepIdx", -1)
    transcript_path = payload.get("transcriptPath")
    artifact_directory = payload.get("artifactDirectoryPath")
    model_name = payload.get("modelName")

    # Determine primary workspace
    primary_workspace = workspace_paths[0] if workspace_paths else None
    config = load_config(primary_workspace)

    # Extract bounded task context if available (OpenAI "Approve for Me" model)
    context = None
    if conversation_id or transcript_path:
        try:
            extractor = TranscriptContextExtractor()
            context = extractor.extract_context(
                conversation_id=conversation_id,
                step_idx=step_idx,
                transcript_path=transcript_path,
            )
        except Exception:
            context = None

    trusted_artifact_paths = (
        [artifact_directory]
        if isinstance(artifact_directory, str) and artifact_directory.strip()
        else []
    )

    evaluator = AntiAgentEvaluator(
        config,
        workspace_paths=workspace_paths,
        trusted_artifact_paths=trusted_artifact_paths,
    )
    with _prof.span("evaluate"):
        result = evaluator.evaluate(tool_name, tool_args, context=context)

    # Update interaction state for prompt queue safety (only consumed by flow features)
    flow_enabled = config.prompt_queue_enabled or config.smart_enter_enabled or config.steer_enabled
    if flow_enabled:
        from antiagent.engine.flow_presence import any_bridge_active

        flow_enabled = any_bridge_active()
    if conversation_id and flow_enabled:
        try:
            import time
            from antiagent.constants import DECISION_FORCE_ASK
            from antiagent.engine.interaction_state import (
                ActiveSurface,
                InteractionState,
                InteractionStateStore,
            )

            store = InteractionStateStore.default()
            store.set_active_conversation_id(conversation_id)
            if result.decision in (DECISION_ASK, DECISION_FORCE_ASK):
                store.update_state(
                    conversation_id,
                    state=InteractionState.AWAITING_APPROVAL,
                    active_surface=ActiveSurface.APPROVAL,
                    pending_approval=True,
                    active_tool=tool_name,
                    last_event=f"PreToolUse:{result.decision}",
                    last_event_time=time.time(),
                    step_idx=step_idx,
                    fully_idle=False,
                )
            elif tool_name == "ask_question":
                store.update_state(
                    conversation_id,
                    state=InteractionState.AWAITING_QUESTION,
                    active_surface=ActiveSurface.QUESTION,
                    pending_question=True,
                    active_tool=tool_name,
                    last_event="PreToolUse:ask_question",
                    last_event_time=time.time(),
                    step_idx=step_idx,
                    fully_idle=False,
                )
            else:
                store.update_state(
                    conversation_id,
                    state=InteractionState.RUNNING,
                    active_surface=ActiveSurface.PROMPT,
                    pending_approval=False,
                    active_tool=tool_name,
                    last_event=f"PreToolUse:{result.decision}",
                    last_event_time=time.time(),
                    step_idx=step_idx,
                    fully_idle=False,
                )
        except Exception:
            pass

    # Log to audit trail if enabled
    if config.audit_enabled:
        is_command = (tool_name == "run_command")
        is_trusted_match = "trusted tool" in (result.reason or "").lower()
        if is_command or config.audit_include_tool_calls or is_trusted_match:
            audit_logger = AuditLogger(config.audit_log_path)
            audit_logger.log_event(
                tool_name=tool_name,
                tool_args=tool_args,
                decision=result.decision,
                reason=result.reason,
                conversation_id=conversation_id,
                step_idx=step_idx,
            )

    return result.to_antigravity_dict()


def main() -> None:
    """CLI / hook entrypoint reading from stdin and writing to stdout."""
    # Ensure UTF-8 on Windows standard streams to prevent CP1252 encoding crashes
    if sys.platform == "win32":
        if hasattr(sys.stdin, "reconfigure"):
            try:
                sys.stdin.reconfigure(encoding="utf-8")
            except Exception:
                pass
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass
        if hasattr(sys.stderr, "reconfigure"):
            try:
                sys.stderr.reconfigure(encoding="utf-8")
            except Exception:
                pass

    started_ns = time.perf_counter_ns()
    payload: Dict[str, Any] = {}
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            fallback = {
                "decision": DECISION_ASK,
                "reason": "AntiAgent received empty input. Confirmation required for safety.",
            }
            sys.stdout.write(json.dumps(fallback, ensure_ascii=True))
            return

        payload = json.loads(raw_input)
        response = handle_pre_tool_use(payload)
        sys.stdout.write(json.dumps(response, ensure_ascii=True))
    except Exception as e:
        # Failsafe: on unexpected crash, require confirmation and write to stderr
        sys.stderr.write(f"[antiagent-hook error] {e}\n")
        fallback = {
            "decision": DECISION_ASK,
            "reason": f"AntiAgent internal error: {e}. Confirmation required for safety.",
        }
        sys.stdout.write(json.dumps(fallback, ensure_ascii=True))

    if _prof.enabled():
        sys.stdout.flush()
        _prof.emit(
            "guard:pre-tool-use",
            started_ns,
            conversation_id=payload.get("conversationId") if isinstance(payload, dict) else None,
            step_idx=payload.get("stepIdx") if isinstance(payload, dict) else None,
        )


if __name__ == "__main__":
    main()
