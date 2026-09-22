"""Shell command parser and deterministic safety guard."""

import re
import shlex
from typing import List, Optional, Tuple

from antiagent.constants import (
    DECISION_ALLOW,
    DECISION_ASK,
    DECISION_DENY,
    HARD_DENY_PATTERNS,
    SAFE_COMMAND_PREFIXES,
    SAFE_GH_SUBCOMMANDS,
)


class CommandGuard:
    """Deterministic parser and security evaluator for shell commands."""

    def __init__(self, custom_allow_patterns: Optional[List[str]] = None, custom_deny_patterns: Optional[List[str]] = None):
        self.custom_allow_patterns = custom_allow_patterns or []
        self.custom_deny_patterns = custom_deny_patterns or []

    def evaluate(self, cmd_line: str) -> Optional[Tuple[str, str]]:
        """Evaluate a shell command line.
        Returns:
            (decision, reason) if a deterministic rule hits.
            None if command needs higher-level / LLM inspection.
        """
        if not cmd_line or not cmd_line.strip():
            return DECISION_ALLOW, "Empty command is harmless."

        cleaned = cmd_line.strip()

        # Split pipeline to inspect individual sub-commands as well as the full line
        sub_cmds = [s.strip() for s in re.split(r"[\r\n;&]+", cleaned) if s.strip()]

        # 1. Check custom deny patterns first (full line and individual sub-commands)
        for pat in self.custom_deny_patterns:
            if re.search(pat, cleaned, re.IGNORECASE | re.MULTILINE) or any(re.search(pat, s, re.IGNORECASE | re.MULTILINE) for s in sub_cmds):
                return DECISION_DENY, f"Blocked by custom deny pattern: '{pat}'"

        # 2. Check Hard-Deny list (catastrophic / malicious) on full line and each sub-command
        for pat in HARD_DENY_PATTERNS:
            if re.search(pat, cleaned, re.IGNORECASE | re.MULTILINE) or any(re.search(pat, s, re.IGNORECASE | re.MULTILINE) for s in sub_cmds):
                return DECISION_DENY, f"🚨 Hard-blocked dangerous command matching pattern: '{pat}'"

        # 3. Check custom allow patterns
        for pat in self.custom_allow_patterns:
            if re.search(pat, cleaned):
                return DECISION_ALLOW, f"Permitted by custom allow pattern: '{pat}'"

        # 4. Check for destructive file removals (rm -rf, rm -r, Remove-Item, del /s)
        # Even if not targeting root, general recursive deletion in workspace should trigger user confirmation in balanced mode
        rm_match = re.search(
            r"\brm\s+(?:-[a-zA-Z0-9_-]+\s+)*(?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)\s+(.*)",
            cleaned,
        )
        if rm_match:
            target = rm_match.group(1).strip()
            return DECISION_ASK, f"⚠️ Recursive deletion detected: 'rm' targeting '{target}'. Confirmation required."

        win_rm_match = re.search(
            r"\b(?:cmd(?:\.exe)?\s+/c\s+)?\b(?:Remove-Item|del|rmdir|rd)\s+.*(?:-Recurse|-Force|/s|/q)",
            cleaned,
            re.IGNORECASE,
        )
        if win_rm_match:
            return DECISION_ASK, f"⚠️ Recursive/forced deletion detected: '{cleaned[:45]}'. Confirmation required."

        # 5. Check for privilege escalation
        if re.search(r"\b(sudo|doas)\b", cleaned):
            return DECISION_ASK, f"⚠️ Root privilege command requires review or confirmation: '{cleaned[:40]}'."

        # 6. Check for background killer / process nukes
        if re.search(r"\b(killall|pkill)\s+(-9\s+)?(node|python|zsh|bash)", cleaned):
            return DECISION_ASK, "Process termination of runtime engines requires confirmation."

        # 7. Check if command is strictly read-only
        if self._is_strictly_read_only(cleaned):
            return DECISION_ALLOW, "Safe read-only command verified."

        # 8. Check if command is a safe test/build command
        if self._is_safe_test_command(cleaned):
            return DECISION_ALLOW, "Auto-approved: routine dev test suite run verified."

        # 9. Check if command is creating a pull request
        if re.search(r"^\s*gh\s+pr\s+create\b", cleaned):
            return DECISION_ALLOW, "Auto-approved: pull request creation (gh pr create)."

        # Otherwise, requires LLM review or profile-based fallback
        return None

    def _is_strictly_read_only(self, cmd: str) -> bool:
        """Determines if a command pipeline only performs read-only operations."""
        # If output is redirected to write files (> or >>), it is not read-only
        if ">" in cmd or ">>" in cmd:
            return False

        # If command contains command substitutions or process substitutions, it is not strictly read-only
        if re.search(r"(\$\(|`|<\(|>\()", cmd):
            return False

        # Split commands separated by newlines, ;, &&, ||, |
        sub_cmds = re.split(r"[\r\n;&|]+", cmd)
        if not sub_cmds:
            return False

        for sub in sub_cmds:
            sub = sub.strip()
            if not sub:
                continue

            try:
                tokens = shlex.split(sub)
            except ValueError:
                # If syntax error in quotes, be safe and say not read-only
                return False

            if not tokens:
                continue

            first = tokens[0].lower()
            # Handle environment prefixes like FOO=bar cmd
            while "=" in first and len(tokens) > 1:
                tokens = tokens[1:]
                first = tokens[0].lower()

            # Check basename of executable (e.g. /bin/ls -> ls, C:\bin\dir.exe -> dir)
            first_base = first.replace("\\", "/").split("/")[-1]
            if first_base.endswith(".exe"):
                first_base = first_base[:-4]

            # Handle shell wrapper invocations (cmd /c dir, powershell -Command Get-ChildItem)
            if first_base in ("cmd", "powershell", "pwsh") and len(tokens) > 1:
                # Strip wrapper flags like /c, /k, -command, -c
                sub_tokens = tokens[1:]
                while sub_tokens and sub_tokens[0].lower() in ("/c", "/k", "-c", "-command"):
                    sub_tokens = sub_tokens[1:]
                if sub_tokens:
                    inner = sub_tokens[0].lower().replace("\\", "/").split("/")[-1]
                    if inner.endswith(".exe"):
                        inner = inner[:-4]
                    if inner in SAFE_COMMAND_PREFIXES:
                        continue
                    else:
                        return False

            # Check gh (GitHub CLI) commands
            if first_base == "gh":
                gh_args = [t.lower() for t in tokens[1:] if not t.startswith("-")]
                if gh_args:
                    two_tok = f"{gh_args[0]} {gh_args[1]}" if len(gh_args) >= 2 else ""
                    one_tok = gh_args[0]
                    if two_tok in SAFE_GH_SUBCOMMANDS or one_tok in SAFE_GH_SUBCOMMANDS:
                        continue
                return False

            # Check antiagent status/doctor/test/audit/pr inspection commands
            if first_base == "antiagent":
                aa_args = [t.lower() for t in tokens[1:] if not t.startswith("-")]
                if aa_args:
                    if aa_args[0] in ("status", "doctor", "test", "audit", "status"):
                        continue
                    if aa_args[0] == "pr" and len(aa_args) >= 2 and aa_args[1] in ("status", "list", "failures", "autofix"):
                        continue
                return False

            if first_base not in SAFE_COMMAND_PREFIXES:
                return False

        return True

    def _is_safe_test_command(self, cmd: str) -> bool:
        """Determines if a command is a standard test/typecheck run without mutations."""
        safe_test_runners = [
            r"^npm\s+test(\s+.*)?$",
            r"^npm\s+run\s+(test|lint|typecheck|check)(\s+.*)?$",
            r"^npx\s+(tsc\s+--noEmit|eslint\s+.*|jest\s+.*|vitest\s+run.*)$",
            r"^pytest(\s+.*)?$",
            r"^(python[0-9]?|py)\s+-m\s+(unittest|pytest)(\s+.*)?$",
            r"^cargo\s+test(\s+.*)?$",
            r"^cargo\s+check(\s+.*)?$",
            r"^go\s+test(\s+.*)?$",
            r"^dotnet\s+test(\s+.*)?$",
        ]
        cleaned = cmd.strip()
        # No file redirection
        if ">" in cleaned:
            return False

        for pattern in safe_test_runners:
            if re.match(pattern, cleaned):
                return True
        return False
