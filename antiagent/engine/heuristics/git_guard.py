"""Git command safety evaluator."""

import re
from typing import Optional, Tuple

from antiagent.constants import (
    DECISION_ALLOW,
    DECISION_ASK,
    RISKY_GIT_OPERATIONS,
    SAFE_GIT_SUBCOMMANDS,
)


class GitGuard:
    """Evaluates git operations to prevent history rewriting, forced pushes, or lost work."""

    def evaluate(self, cmd_line: str) -> Optional[Tuple[str, str]]:
        """Evaluate a git command string.
        Returns (decision, reason) if a git rule applies, or None.
        """
        cleaned = cmd_line.strip()
        if not re.match(r"^git(\s+.*)?$", cleaned):
            return None

        # 1. Check risky git operations
        for pattern in RISKY_GIT_OPERATIONS:
            if re.search(pattern, cleaned):
                return (
                    DECISION_ASK,
                    f"⚠️ Irreversible git operation detected ({pattern}). Manual confirmation requested.",
                )

        # 2. Check safe read-only git operations
        # Extract subcommands
        tokens = cleaned.split()
        if len(tokens) >= 2:
            sub = tokens[1].lower()
            if sub in SAFE_GIT_SUBCOMMANDS:
                return DECISION_ALLOW, f"Safe git read operation: 'git {sub}'"

            # Check two-token subcommands like "stash list" or "config --get"
            if len(tokens) >= 3:
                two_token = f"{sub} {tokens[2].lower()}"
                if two_token in SAFE_GIT_SUBCOMMANDS:
                    return DECISION_ALLOW, f"Safe git read operation: 'git {two_token}'"

        return None
