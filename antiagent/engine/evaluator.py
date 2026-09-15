"""Core evaluation coordinator for AntiAgent."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from antiagent.config import AntiAgentConfig
from antiagent.constants import (
    DECISION_ALLOW,
    DECISION_ASK,
    DECISION_DENY,
    PROFILE_PARANOID,
    SAFE_READ_TOOLS,
)
from antiagent.engine.heuristics.command_guard import CommandGuard
from antiagent.engine.heuristics.fs_guard import FSGuard
from antiagent.engine.heuristics.git_guard import GitGuard
from antiagent.engine.supervisor.cache import DecisionCache
from antiagent.engine.supervisor.reviewer import LLMSupervisor


@dataclass
class EvaluationResult:
    """The result of an AntiAgent safety evaluation."""

    decision: str
    reason: str
    overwrite: Optional[Dict[str, Any]] = None
    permission_overrides: List[str] = field(default_factory=list)

    def to_antigravity_dict(self) -> Dict[str, Any]:
        """Format as expected by Antigravity PreToolUse hook."""
        payload: Dict[str, Any] = {
            "decision": self.decision,
            "reason": self.reason,
        }
        if self.overwrite:
            payload["overwrite"] = self.overwrite
        if self.permission_overrides:
            payload["permissionOverrides"] = self.permission_overrides
        return payload


class AntiAgentEvaluator:
    """Multi-tier security evaluator coordinating heuristics and LLM subagent review."""

    def __init__(self, config: AntiAgentConfig, workspace_paths: Optional[List[str]] = None):
        self.config = config
        self.workspace_paths = workspace_paths or []

        self.fs_guard = FSGuard(self.workspace_paths)
        self.cmd_guard = CommandGuard(
            custom_allow_patterns=config.custom_allow_patterns,
            custom_deny_patterns=config.custom_deny_patterns,
        )
        self.git_guard = GitGuard()
        self.cache = DecisionCache()
        self.supervisor = LLMSupervisor(config)

    def evaluate(self, tool_name: str, tool_args: dict) -> EvaluationResult:
        """Evaluate a tool call against security policies."""
        # 1. Quick cache check
        cached = self.cache.get(tool_name, tool_args)
        if cached:
            dec, rsn = cached
            return EvaluationResult(decision=dec, reason=rsn)

        # 2. Check inherently safe read-only tools
        if self.config.auto_approve_reads and tool_name in SAFE_READ_TOOLS:
            # Check FS boundary for sensitive reads
            target = (
                tool_args.get("AbsolutePath")
                or tool_args.get("TargetFile")
                or tool_args.get("DirectoryPath")
                or tool_args.get("SearchPath")
            )
            if target:
                is_sens, sens_reason = self.fs_guard.is_sensitive_target(target)
                if is_sens:
                    return EvaluationResult(
                        decision=DECISION_ASK,
                        reason=f"Reading sensitive credential target: {sens_reason}",
                    )

            return EvaluationResult(
                decision=DECISION_ALLOW,
                reason=f"Auto-approved safe read-only tool: '{tool_name}'",
            )

        # 3. Check filesystem mutation tools (write_to_file, replace_file_content, delete_file)
        if tool_name in ("write_to_file", "replace_file_content", "delete_file"):
            fs_verdict = self.fs_guard.evaluate_file_tool(tool_name, tool_args)
            if fs_verdict:
                decision, reason = fs_verdict
                return EvaluationResult(decision=decision, reason=reason)

            if self.config.profile == PROFILE_PARANOID:
                return EvaluationResult(
                    decision=DECISION_ASK,
                    reason="Paranoid mode: manual approval required for file modification.",
                )

        # 4. Check shell command executions (run_command)
        if tool_name == "run_command":
            cmd_line = tool_args.get("CommandLine", "")

            # A. Check Git operations first if it's a git command
            git_verdict = self.git_guard.evaluate(cmd_line)
            if git_verdict:
                decision, reason = git_verdict
                self.cache.put(tool_name, tool_args, decision, reason)
                return EvaluationResult(decision=decision, reason=reason)

            # B. Check general command guard heuristics
            cmd_verdict = self.cmd_guard.evaluate(cmd_line)
            if cmd_verdict:
                decision, reason = cmd_verdict
                # If command is hard-denied, don't allow caching bypass
                if decision != DECISION_DENY:
                    self.cache.put(tool_name, tool_args, decision, reason)
                return EvaluationResult(decision=decision, reason=reason)

        # 5. If we reach here, the action is mutating or non-trivial.
        # Tier 2: AI Supervisor Review
        ai_decision, ai_reason = self.supervisor.review(
            tool_name, tool_args, self.workspace_paths
        )
        self.cache.put(tool_name, tool_args, ai_decision, ai_reason)

        return EvaluationResult(decision=ai_decision, reason=ai_reason)
