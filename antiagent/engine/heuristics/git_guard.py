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

        # 1. Check risky git operations immediately
        for pattern in RISKY_GIT_OPERATIONS:
            if re.search(pattern, cleaned):
                return (
                    DECISION_ASK,
                    f"⚠️ Irreversible git operation detected ({pattern}). Manual confirmation requested.",
                )

        # If the command contains command chaining, piping, or redirection, do NOT auto-approve via GitGuard;
        # let CommandGuard and HARD_DENY_PATTERNS inspect the full pipeline.
        if re.search(r"[\r\n;&|`>]|\$\(", cleaned):
            return None

        # If writing output to file via --output, do not treat as pure read
        if "--output" in cleaned:
            return None

        # 2. Check safe read-only git operations
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

            # Strict inspect for query-only branch, tag, and remote invocations
            if sub == "branch":
                rest = [t.lower() for t in tokens[2:]]
                read_only_flags = {"-a", "-r", "-v", "-vv", "--list", "--show-current", "--sort"}
                if not rest or all(t in read_only_flags or t.startswith("--sort=") for t in rest):
                    return DECISION_ALLOW, "Safe git read operation: 'git branch'"
                return DECISION_ASK, f"Git branch mutation detected: '{cleaned[:40]}'. Confirmation required."

            if sub == "tag":
                rest = [t.lower() for t in tokens[2:]]
                if not rest or "-l" in rest or "--list" in rest:
                    return DECISION_ALLOW, "Safe git read operation: 'git tag'"
                return DECISION_ASK, f"Git tag mutation detected: '{cleaned[:40]}'. Confirmation required."

            if sub == "remote":
                rest = [t.lower() for t in tokens[2:]]
                if not rest or rest == ["-v"] or (rest and rest[0] == "show"):
                    return DECISION_ALLOW, "Safe git read operation: 'git remote'"
                return DECISION_ASK, f"Git remote configuration change detected: '{cleaned[:40]}'. Confirmation required."

        return None
