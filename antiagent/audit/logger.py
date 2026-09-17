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
            if hasattr(os, "chmod") and sys.platform != "win32":
                try:
                    os.chmod(log_dir, 0o700)
                except Exception:
                    pass
            self.log_path = log_dir / "audit.log"
        else:
            self.log_path = Path(log_path)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if hasattr(os, "chmod") and sys.platform != "win32":
                try:
                    os.chmod(self.log_path.parent, 0o700)
                except Exception:
                    pass

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
            is_new = not self.log_path.exists()
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            if is_new and hasattr(os, "chmod") and sys.platform != "win32":
                try:
                    os.chmod(self.log_path, 0o600)
                except Exception:
                    pass
        except Exception as e:
            sys.stderr.write(f"[antiagent] Failed to write audit log: {e}\n")

    def read_recent(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Read the most recent audit entries without unbounded memory consumption."""
        if not self.log_path.is_file():
            return []

        entries = []
        try:
            file_size = self.log_path.stat().st_size
            if file_size == 0:
                return []

            # Tail up to 256KB or full file to avoid loading gigabytes into memory
            max_read = min(file_size, 262144)
            with open(self.log_path, "rb") as f:
                f.seek(file_size - max_read)
                chunk = f.read(max_read).decode("utf-8", errors="replace")
                lines = chunk.splitlines()
                # If we didn't read from start of file, the first line might be partial
                if file_size > max_read and len(lines) > 1:
                    lines = lines[1:]

                for line in lines[-limit:]:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except Exception:
                            continue
        except Exception as e:
            sys.stderr.write(f"[antiagent] Failed to read audit log: {e}\n")

        return entries
