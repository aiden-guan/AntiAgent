"""Deterministic policy guard for trusted tool calls and MCP integration."""

import re
from typing import List, Optional


class ToolGuard:
    """Evaluates tool identities against user-configured exact names and regex patterns."""

    def __init__(
        self,
        trusted_tools: Optional[List[str]] = None,
        trusted_tool_patterns: Optional[List[str]] = None,
    ):
        self.trusted_tools: List[str] = [
            t.strip()
            for t in (trusted_tools or [])
            if isinstance(t, str) and t.strip()
        ]
        self.trusted_tool_patterns: List[str] = [
            p.strip()
            for p in (trusted_tool_patterns or [])
            if isinstance(p, str) and p.strip()
        ]

    def is_trusted(self, tool_name: str) -> Optional[str]:
        """Check if tool_name is trusted by exact match or regex pattern.

        Returns:
            A descriptive reason identifying the matching rule if trusted,
            or None if the tool is not trusted.
        """
        if not tool_name or not isinstance(tool_name, str):
            return None

        cleaned_name = tool_name.strip()

        # 1. Exact match against trusted_tools
        for trusted in self.trusted_tools:
            if cleaned_name == trusted:
                return f"Trusted tool exact match: '{cleaned_name}'"

        # 2. Regex match against trusted_tool_patterns
        for pattern in self.trusted_tool_patterns:
            try:
                if re.search(pattern, cleaned_name):
                    return f"Trusted tool matched pattern: '{pattern}'"
            except (re.error, Exception):
                # Invalid regex patterns must fail safely and must never crash AntiAgent
                continue

        return None
