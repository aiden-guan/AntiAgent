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
                if os.name == "nt":
                    norm_resolved = Path(os.path.normcase(str(resolved)))
                    norm_ws = Path(os.path.normcase(str(ws)))
                    norm_resolved.relative_to(norm_ws)
                else:
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
        resolved_path = Path(os.path.abspath(os.path.expanduser(target_path))).resolve()
        resolved = str(resolved_path).replace("\\", "/")

        for pattern in SENSITIVE_TARGETS:
            if re.search(pattern, norm_path, re.IGNORECASE) or re.search(pattern, resolved, re.IGNORECASE):
                return True, f"Target matches sensitive credential/secret pattern: {pattern}"

        # Windows system roots
        win_sys_roots = [
            "C:/Windows",
            "C:/Program Files",
            "C:/Program Files (x86)",
            "C:/ProgramData",
        ]
        for env_var in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            val = os.environ.get(env_var)
            if val:
                norm_val = val.replace("\\", "/")
                if norm_val not in win_sys_roots:
                    win_sys_roots.append(norm_val)

        for wroot in win_sys_roots:
            norm_wroot = wroot.lower()
            if (
                resolved.lower() == norm_wroot
                or resolved.lower().startswith(norm_wroot + "/")
                or norm_path.lower() == norm_wroot
                or norm_path.lower().startswith(norm_wroot + "/")
            ):
                return True, f"Target is a protected Windows system path: {wroot}"

        # Unix / macOS protected system roots (including macOS /private canonical aliases)
        system_roots = [
            "/etc",
            "/private/etc",
            "/usr",
            "/var",
            "/private/var",
            "/bin",
            "/sbin",
            "/System",
            "/Library",
            "/root",
            "/proc",
            "/sys",
            "/dev",
            "/boot",
        ]
        for sroot in system_roots:
            if (
                resolved == sroot
                or resolved.startswith(sroot + "/")
                or norm_path == sroot
                or norm_path.startswith(sroot + "/")
            ):
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
