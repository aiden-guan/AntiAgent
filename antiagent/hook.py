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

    evaluator = AntiAgentEvaluator(config, workspace_paths=workspace_paths)
    result = evaluator.evaluate(tool_name, tool_args)

    # Log to audit trail if enabled
    if config.audit_enabled:
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
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            # If empty input, allow or do nothing
            sys.stdout.write(
                json.dumps({"decision": "allow", "reason": "No input received."})
            )
            return

        payload = json.loads(raw_input)
        response = handle_pre_tool_use(payload)
        sys.stdout.write(json.dumps(response))
    except Exception as e:
        # Failsafe: on unexpected crash, require confirmation and write to stderr
        sys.stderr.write(f"[antiagent-hook error] {e}\n")
        fallback = {
            "decision": DECISION_ASK,
            "reason": f"AntiAgent internal error: {e}. Confirmation required for safety.",
        }
        sys.stdout.write(json.dumps(fallback))


if __name__ == "__main__":
    main()
