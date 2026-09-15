"""Filesystem boundary and sensitive file inspection guard."""

import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from antiagent.constants import (
    DECISION_ALLOW,
    DECISION_ASK,
    DECISION_DENY,
    SENSITIVE_TARGETS,
)


class FSGuard:
    """Evaluates filesystem access against workspace boundaries and sensitive file rules."""

    def __init__(self, workspace_paths: Optional[List[str]] = None):
        self.workspace_paths = [
            Path(os.path.abspath(p)).resolve() for p in (workspace_paths or [os.getcwd()])
        ]

    def is_inside_workspace(self, target_path: str) -> bool:
        """Check whether target_path resolves to a location inside one of the workspace directories."""
        if not target_path:
            return True

        resolved = Path(os.path.abspath(os.path.expanduser(target_path))).resolve()
        for ws in self.workspace_paths:
            try:
                # Check if target_path is relative to or identical to ws
                resolved.relative_to(ws)
                return True
            except ValueError:
                continue
        return False

    def is_sensitive_target(self, target_path: str) -> Tuple[bool, str]:
        """Check if target_path matches known sensitive files or credentials."""
        if not target_path:
            return False, ""

        norm_path = target_path.replace("\\", "/")
        for pattern in SENSITIVE_TARGETS:
            if re.search(pattern, norm_path):
                return True, f"Target matches sensitive credential/secret pattern: {pattern}"

        # Check for system directories
        resolved = str(Path(os.path.abspath(os.path.expanduser(target_path))).resolve())
        system_roots = ["/etc", "/usr", "/var", "/bin", "/sbin", "/System", "/Library"]
        for sroot in system_roots:
            if resolved == sroot or resolved.startswith(sroot + "/"):
                return True, f"Target is a protected system path: {sroot}"

        return False, ""

    def evaluate_file_tool(
        self, tool_name: str, tool_args: dict
    ) -> Optional[Tuple[str, str]]:
        """Evaluate a file tool call (write_to_file, replace_file_content, delete_file, etc.).
        Returns (decision, reason) if a deterministic rule triggers, or None.
        """
        # Extract target path from various tool schemas
        target_path = (
            tool_args.get("TargetFile")
            or tool_args.get("AbsolutePath")
            or tool_args.get("path")
            or tool_args.get("DirectoryPath")
        )

        if not target_path:
            return None

        # 1. Check for sensitive target
        is_sens, sens_reason = self.is_sensitive_target(target_path)
        if is_sens:
            # Writing or replacing sensitive files is flagged or denied
            if tool_name in ("write_to_file", "replace_file_content", "delete_file"):
                return DECISION_ASK, f"⚠️ Mutating sensitive target: {sens_reason}"
            return DECISION_ASK, f"Accessing sensitive target: {sens_reason}"

        # 2. Check workspace boundary
        if not self.is_inside_workspace(target_path):
            if tool_name in ("write_to_file", "replace_file_content", "delete_file"):
                return (
                    DECISION_ASK,
                    f"⚠️ Operation targets file outside active workspace: '{target_path}'. Explicit confirmation required.",
                )

        return None
