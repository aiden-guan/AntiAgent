"""Antigravity Lifecycle State Flow Hook entrypoint.

Handles PreInvocation, PostInvocation, PostToolUse, and Stop lifecycle events
emitted by Antigravity CLI / runtime to maintain the canonical InteractionState.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict

from antiagent.engine.interaction_state import (
    ActiveSurface,
    InteractionState,
    InteractionStateStore,
)


def _extract_cid(payload: Dict[str, Any], store: InteractionStateStore) -> str:
    cid = payload.get("conversationId") or payload.get("conversation_id") or ""
    if not cid and isinstance(payload.get("session"), dict):
        cid = payload["session"].get("id", "")
    if not cid:
        cid = store.get_active_conversation_id() or ""
    return str(cid).strip() or "default"


def _extract_bool(payload: Dict[str, Any], camel_key: str, snake_key: str, default: bool = False) -> bool:
    if camel_key in payload:
        return bool(payload[camel_key])
    if snake_key in payload:
        return bool(payload[snake_key])
    return default


def _extract_int(payload: Dict[str, Any], camel_key: str, snake_key: str, default: int = -1) -> int:
    val = payload.get(camel_key, payload.get(snake_key))
    if val is not None:
        try:
            return int(val)
        except (ValueError, TypeError):
            pass
    return default


def handle_pre_invocation(payload: Dict[str, Any], store: InteractionStateStore) -> Dict[str, Any]:
    """Handles PreInvocation lifecycle event."""
    cid = _extract_cid(payload, store)
    if cid:
        store.set_active_conversation_id(cid)
        store.update_state(
            cid,
            state=InteractionState.RUNNING,
            active_surface=ActiveSurface.PROMPT,
            last_event="PreInvocation",
            last_event_time=time.time(),
            invocation_num=_extract_int(payload, "invocationNum", "invocation_num", -1),
            fully_idle=False,
            pending_approval=False,
            pending_preview=False,
            pending_question=False,
            active_tool=None,
        )
    return {}


def handle_post_invocation(payload: Dict[str, Any], store: InteractionStateStore) -> Dict[str, Any]:
    """Handles PostInvocation lifecycle event."""
    cid = _extract_cid(payload, store)
    if cid:
        store.update_state(
            cid,
            last_event="PostInvocation",
            last_event_time=time.time(),
        )
    return {}


def handle_post_tool_use(payload: Dict[str, Any], store: InteractionStateStore) -> Dict[str, Any]:
    """Handles PostToolUse lifecycle event."""
    cid = _extract_cid(payload, store)
    if cid:
        current = store.get_state(cid)
        new_state = current.state
        new_surface = current.active_surface
        if current.state in (
            InteractionState.AWAITING_APPROVAL,
            InteractionState.AWAITING_QUESTION,
            InteractionState.INTERRUPTING,
        ):
            new_state = InteractionState.RUNNING
            new_surface = ActiveSurface.PROMPT

        store.update_state(
            cid,
            state=new_state,
            active_surface=new_surface,
            last_event="PostToolUse",
            last_event_time=time.time(),
            step_idx=_extract_int(payload, "stepIdx", "step_idx", -1),
            pending_approval=False,
            pending_question=False,
            active_tool=None,
        )
    return {}


def handle_stop(payload: Dict[str, Any], store: InteractionStateStore) -> Dict[str, Any]:
    """Handles Stop lifecycle event."""
    cid = _extract_cid(payload, store)
    if cid:
        fully_idle = _extract_bool(payload, "fullyIdle", "fully_idle", False)
        if fully_idle:
            new_state = InteractionState.IDLE
            new_surface = ActiveSurface.PROMPT
            pending_approval = False
            pending_preview = False
            pending_question = False
        else:
            new_state = InteractionState.BACKGROUND_BUSY
            new_surface = ActiveSurface.PROMPT
            pending_approval = False
            pending_preview = False
            pending_question = False

        store.update_state(
            cid,
            state=new_state,
            active_surface=new_surface,
            last_event="Stop",
            last_event_time=time.time(),
            execution_num=_extract_int(payload, "executionNum", "execution_num", -1),
            fully_idle=fully_idle,
            pending_approval=pending_approval,
            pending_preview=pending_preview,
            pending_question=pending_question,
            active_tool=None,
        )
    return {}


def main() -> None:
    """CLI hook entrypoint reading from stdin and writing valid response to stdout."""
    # Ensure UTF-8 on Windows standard streams
    if sys.platform == "win32":
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                try:
                    stream.reconfigure(encoding="utf-8")
                except Exception:
                    pass

    event_type = sys.argv[1] if len(sys.argv) > 1 else ""
    event_type = event_type.lower().strip().replace("_", "-")

    try:
        raw_input = sys.stdin.read()
        payload = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    store = InteractionStateStore.default()
    response: Dict[str, Any] = {}

    try:
        if event_type in ("pre-invocation", "preinvocation"):
            response = handle_pre_invocation(payload, store)
        elif event_type in ("post-invocation", "postinvocation"):
            response = handle_post_invocation(payload, store)
        elif event_type in ("post-tool-use", "posttooluse"):
            response = handle_post_tool_use(payload, store)
        elif event_type == "stop":
            response = handle_stop(payload, store)
        else:
            response = {}
    except Exception as e:
        # Failsafe: never crash or emit malformed stdout to AGY
        sys.stderr.write(f"[antiagent-flow-hook error] {e}\n")
        response = {}

    sys.stdout.write(json.dumps(response, ensure_ascii=True))


if __name__ == "__main__":
    main()
