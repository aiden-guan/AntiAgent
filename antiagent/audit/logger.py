"""Audit logging for AntiAgent tool call decisions."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class AuditLogger:
    """Logs tool evaluations to a persistent JSONL audit trail."""

    def __init__(self, log_path: Optional[str] = None):
        if not log_path:
            log_dir = Path(os.path.expanduser("~/.antiagent"))
            log_dir.mkdir(parents=True, exist_ok=True)
            self.log_path = log_dir / "audit.log"
        else:
            self.log_path = Path(log_path)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log_event(
        self,
        tool_name: str,
        tool_args: dict,
        decision: str,
        reason: str,
        conversation_id: Optional[str] = None,
        step_idx: Optional[int] = None,
    ) -> None:
        """Record an audit event."""
        entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "conversationId": conversation_id or "",
            "stepIdx": step_idx if step_idx is not None else -1,
            "tool": tool_name,
            "args": tool_args,
            "decision": decision,
            "reason": reason,
        }

        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            sys.stderr.write(f"[antiagent] Failed to write audit log: {e}\n")

    def read_recent(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Read the most recent audit entries."""
        if not self.log_path.is_file():
            return []

        entries = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in lines[-limit:]:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except Exception as e:
            sys.stderr.write(f"[antiagent] Failed to read audit log: {e}\n")

        return entries
