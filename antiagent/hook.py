"""Antigravity Lifecycle Hook entrypoint for PreToolUse.

Receives Antigravity's PreToolUse JSON payload on stdin and emits the
evaluation verdict JSON on stdout.
"""

import json
import sys
from typing import Any, Dict

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
    conversation_id = payload.get("conversationId", "")
    step_idx = payload.get("stepIdx", -1)

    # Determine primary workspace
    primary_workspace = workspace_paths[0] if workspace_paths else None
    config = load_config(primary_workspace)

    # Extract bounded task context if available (OpenAI "Approve for Me" model)
    context = None
    if conversation_id:
        try:
            extractor = TranscriptContextExtractor()
            context = extractor.extract_context(conversation_id, step_idx=step_idx)
        except Exception:
            context = None

    evaluator = AntiAgentEvaluator(config, workspace_paths=workspace_paths)
    result = evaluator.evaluate(tool_name, tool_args, context=context)

    # Log to audit trail if enabled
    if config.audit_enabled:
        is_command = (tool_name == "run_command")
        if is_command or config.audit_include_tool_calls:
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


if __name__ == "__main__":
    main()
