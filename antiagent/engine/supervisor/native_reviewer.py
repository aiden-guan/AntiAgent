"""Context-aware native semantic reviewer for AntiAgent (Zero-API Mode).

Implements intuitive "Approve for Me" decision-making:
Evaluates whether proposed actions are coherent with the user's stated intent,
detects contextual anomalies, and ensures safety without requiring external API keys.
"""

import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from antiagent.constants import (
    DECISION_ALLOW,
    DECISION_ASK,
    DECISION_DENY,
    PROFILE_AUTONOMOUS,
    PROFILE_PARANOID,
)
from antiagent.engine.context_extractor import TaskContext


DISPOSABLE_BUILD_TARGETS = {
    "dist",
    "build",
    "out",
    "target",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".next",
    ".nuxt",
    ".turbo",
    ".cache",
    ".parcel-cache",
    ".gradle",
    "coverage",
    ".nyc_output",
    "venv",
    ".venv",
    ".tox",
    "vendor",
}


class ContextAwareNativeReviewer:
    """Evaluates proposed agent actions against Bounded Task Context."""

    def __init__(self):
        # Domain intent keyword dictionaries
        self.domain_keywords = {
            "test": ["test", "spec", "eval", "verify", "check", "pytest", "jest", "unit", "assert"],
            "install": ["install", "add", "setup", "dependency", "package", "reqs", "pip", "npm", "yarn", "pnpm", "cargo", "apt", "brew"],
            "build": ["build", "compile", "bundle", "make", "transpile", "dist", "webpack", "vite", "tsc", "rebuild"],
            "lint": ["lint", "format", "style", "clean", "flake8", "eslint", "prettier", "black", "ruff"],
            "git": ["git", "commit", "save", "branch", "repo", "version", "stash", "checkout"],
            "server": ["serve", "run", "start", "dev", "launch", "listen", "host", "endpoint", "restart"],
            "clean": ["clean", "remove", "delete", "clear", "purge", "prune", "reset", "rebuild", "reinstall"],
        }

    def review(
        self,
        tool_name: str,
        tool_args: dict,
        workspace_paths: List[str],
        context: Optional[TaskContext] = None,
        profile: str = "balanced",
    ) -> Tuple[str, str]:
        """Evaluate proposed action against intent and context.
        Returns:
            (decision, reason)
        """
        # 1. Paranoid mode always flags mutating actions
        if profile == PROFILE_PARANOID:
            return (
                DECISION_ASK,
                f"[Auto-Review] {tool_name} requires explicit confirmation in paranoid mode.",
            )

        # 2. File modification tools (write_to_file, replace_file_content)
        if tool_name in ("write_to_file", "replace_file_content"):
            target = tool_args.get("TargetFile") or tool_args.get("path") or ""
            norm_target = target.replace("\\", "/")
            base_name = norm_target.split("/")[-1] if norm_target else "file"

            # Check if editing unprompted sensitive locations
            if (
                any(p in norm_target for p in (".bashrc", ".zshrc", ".bash_profile", ".profile", "/etc/", ".git/hooks", ".git/config", ".ssh/", ".aws/"))
                or norm_target.endswith((".npmrc", ".pypirc", ".netrc", ".git-credentials", ".env"))
            ):
                return (
                    DECISION_DENY,
                    f"🚨 [Auto-Review] Target '{target}' alters shell, git, credential, or system configuration.",
                )

            return (
                DECISION_ALLOW,
                f"[Auto-Review] Coherent file operation: modifying project file '{base_name}'.",
            )

        # 3. Shell commands
        if tool_name == "run_command":
            cmd = tool_args.get("CommandLine", "").strip()
            return self._evaluate_command(cmd, workspace_paths, context, profile)

        return (
            DECISION_ASK,
            f"[Auto-Review] {tool_name} requires user confirmation.",
        )

    def _evaluate_command(
        self,
        cmd: str,
        workspace_paths: List[str],
        context: Optional[TaskContext],
        profile: str,
    ) -> Tuple[str, str]:
        """Contextual evaluation of terminal command execution."""
        goal_text = ""
        if context:
            goal_text = f"{context.primary_goal} {context.user_prompt}".lower()

        # Unwrap privilege prefix if present
        is_sudo = bool(re.search(r"^(?:sudo|doas)\b", cmd))
        inner_cmd = re.sub(r"^(?:sudo|doas)\s+(-[a-zA-Z0-9_-]+\s+)*", "", cmd).strip() if is_sudo else cmd

        # Deep Analysis 1: Check recursive directory deletion (rm -rf / rm -r)
        rm_match = re.search(r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+(.*)", inner_cmd)
        if rm_match:
            targets_raw = rm_match.group(1).strip()
            raw_tokens = [t for t in targets_raw.split() if not t.startswith("-")]
            if raw_tokens:
                all_disposable = True
                for t in raw_tokens:
                    base = t.rstrip("/").split("/")[-1]
                    is_disp = base in DISPOSABLE_BUILD_TARGETS or t.startswith("/tmp/") or t.startswith("./tmp/")
                    if not is_disp:
                        all_disposable = False
                        break

                if all_disposable:
                    disp_names = ", ".join(t.rstrip("/").split("/")[-1] for t in raw_tokens[:3])
                    if profile == PROFILE_AUTONOMOUS:
                        return (
                            DECISION_ALLOW,
                            f"[Auto-Review] Auto-approved deletion of disposable build artifact(s): '{disp_names}'.",
                        )
                    # In balanced mode: auto-approve if dev intent mentions clean/build/rebuild or standard caches
                    if any(k in goal_text for k in ["clean", "build", "rebuild", "reset", "reinstall", "install", "setup", "test", "fix", "delete", "remove"]):
                        return (
                            DECISION_ALLOW,
                            f"[Auto-Review] Auto-approved cleanup of build cache '{disp_names}' matching dev intent.",
                        )
                    if any(t.rstrip("/").split("/")[-1] in ("__pycache__", ".pytest_cache", ".cache") for t in raw_tokens):
                        return (
                            DECISION_ALLOW,
                            f"[Auto-Review] Auto-approved routine cache cleanup: '{disp_names}'.",
                        )
                    return (
                        DECISION_ASK,
                        f"⚠️ [Auto-Review] Recursive deletion of build artifact '{disp_names}' requires confirmation.",
                    )
                else:
                    # Non-disposable project directory (e.g. src/, lib/, components/)
                    target_name = raw_tokens[0].rstrip("/").split("/")[-1]
                    if context and any(k in goal_text for k in ["delete", "remove", "clean", "purge"]) and target_name.lower() in goal_text:
                        return (
                            DECISION_ALLOW,
                            f"[Auto-Review] Auto-approved deletion of '{target_name}' matching explicit user request.",
                        )
                    if profile == PROFILE_AUTONOMOUS:
                        return (
                            DECISION_DENY,
                            f"🚨 [Autonomous Block] Contextual Anomaly: unprompted deletion of project directory '{target_name}'.",
                        )
                    return (
                        DECISION_ASK,
                        f"⚠️ [Auto-Review] Recursive deletion of project directory '{target_name}' requires confirmation.",
                    )

        # Deep Analysis 2: Check privilege escalation (sudo / doas)
        if is_sudo:
            is_pkg_admin = bool(re.search(r"^(?:apt|apt-get|brew|dnf|yum|pacman|zypper|apk)\s+(install|update|upgrade|add)\b", inner_cmd))
            is_service_admin = bool(re.search(r"^(?:systemctl|service|brew\s+services)\s+(start|stop|restart|status|reload)\b", inner_cmd))
            is_perm_admin = bool(re.search(r"^(?:chown|chmod)\s+", inner_cmd))
            is_container_admin = bool(re.search(r"^(?:docker|podman)\s+", inner_cmd))

            if is_pkg_admin or is_service_admin or is_perm_admin or is_container_admin:
                # If unprompted in an unrelated task, flag as anomaly
                if context and any(k in goal_text for k in ["css", "style", "typo", "readme", "doc"]):
                    if profile == PROFILE_AUTONOMOUS:
                        return (
                            DECISION_DENY,
                            f"🚨 [Autonomous Block] Privileged operation '{inner_cmd[:40]}' is out-of-scope for the active task '{context.primary_goal[:45]}...'.",
                        )
                    return (
                        DECISION_ASK,
                        f"⚠️ [Auto-Review] Privileged operation '{inner_cmd[:40]}' appears out-of-scope for the active task '{context.primary_goal[:45]}...'.",
                    )
                if profile == PROFILE_AUTONOMOUS:
                    return (
                        DECISION_ALLOW,
                        f"[Auto-Review] Auto-approved safe privileged operation '{inner_cmd[:40]}' in autonomous mode.",
                    )
                # In balanced mode: auto-approve if user context specifically asked for setup/install/sudo
                if any(k in goal_text for k in ["setup", "install", "system", "apt", "brew", "service", "start", "sudo"]):
                    return (
                        DECISION_ALLOW,
                        f"[Auto-Review] Auto-approved privileged operation '{inner_cmd[:40]}' matching user setup intent.",
                    )
                return (
                    DECISION_ASK,
                    f"⚠️ [Auto-Review] Root privilege execution: '{inner_cmd[:45]}' requires confirmation.",
                )
            else:
                if profile == PROFILE_AUTONOMOUS:
                    return (
                        DECISION_DENY,
                        f"🚨 [Autonomous Block] Contextual Anomaly: Unrecognized root privilege command '{inner_cmd[:45]}'.",
                    )
                return (
                    DECISION_ASK,
                    f"⚠️ [Auto-Review] Root privilege execution: '{cmd[:45]}' requires confirmation.",
                )

        # A. Detect command domain
        is_test_cmd = bool(re.search(r"(?:^|\s)(pytest|npm\s+test|npx\s+jest|cargo\s+test|go\s+test|python[0-9]?\s+-m\s+(?:unittest|pytest))(?:\s|$)", inner_cmd))
        is_install_cmd = bool(re.search(r"(?:^|\s)(pip[0-9]?\s+install|npm\s+(?:i|install|add)|yarn\s+add|pnpm\s+add|cargo\s+add)(?:\s|$)", inner_cmd))
        is_build_cmd = bool(re.search(r"(?:^|\s)(npm\s+run\s+build|cargo\s+build|go\s+build|make|tsc|vite\s+build)(?:\s|$)", inner_cmd))
        is_lint_cmd = bool(re.search(r"(?:^|\s)(ruff|black|flake8|eslint|prettier|cargo\s+clippy)(?:\s|$)", inner_cmd))
        is_git_staging = bool(re.search(r"^git\s+(?:add|commit|checkout\s+-b|switch\s+-c|status|diff|branch)(?:\s|$)", inner_cmd))
        is_routine_fs = bool(re.search(r"^(?:mkdir|touch|cp|mv)\s+", inner_cmd))
        is_anomaly_risky = bool(re.search(r"(?:^|\s)(pkill|killall|kill\s+-9|crontab|reboot|shutdown)(?:\s|$)", inner_cmd))
        is_script_eval = bool(re.search(r"^(?:python[0-9]?\s+-c|node\s+-e|osascript\s+-e)\s+", inner_cmd))

        # B. Check for contextual anomalies
        if is_anomaly_risky:
            # Only allow process killing if user explicitly instructed it
            if not any(k in goal_text for k in ["kill", "stop", "terminate", "restart"]):
                if profile == PROFILE_AUTONOMOUS:
                    return (
                        DECISION_DENY,
                        f"🚨 [Autonomous Block] Contextual Anomaly: Command '{cmd[:40]}' terminates processes without user instruction.",
                    )
                return (
                    DECISION_ASK,
                    f"⚠️ [Auto-Review] Contextual Anomaly: Command '{cmd[:40]}' terminates processes but user did not request process termination.",
                )
            return (
                DECISION_ALLOW,
                f"[Auto-Review] Auto-approved process control '{cmd[:40]}' matching explicit user instruction.",
            )

        # C. Intent Coherence Matching
        if is_test_cmd:
            if not goal_text or any(k in goal_text for k in self.domain_keywords["test"]):
                return (
                    DECISION_ALLOW,
                    f"[Auto-Review] Auto-approved: test runner '{cmd[:35]}' directly matches verification goal.",
                )
            return (
                DECISION_ALLOW,
                f"[Auto-Review] Auto-approved routine test execution.",
            )

        if is_install_cmd:
            if context and any(k in goal_text for k in self.domain_keywords["install"]):
                return (
                    DECISION_ALLOW,
                    f"[Auto-Review] Auto-approved: package install '{cmd[:35]}' aligns with setup intent.",
                )
            # If installing unprompted in an unrelated task
            if context and any(k in goal_text for k in ["css", "style", "typo", "readme", "doc"]):
                if profile == PROFILE_AUTONOMOUS:
                    return (
                        DECISION_DENY,
                        f"🚨 [Autonomous Block] Installing packages is out-of-scope for the active task '{context.primary_goal[:45]}...'.",
                    )
                return (
                    DECISION_ASK,
                    f"⚠️ [Auto-Review] Installing packages appears out-of-scope for the active task '{context.primary_goal[:45]}...'.",
                )
            if profile == PROFILE_AUTONOMOUS:
                return (
                    DECISION_ALLOW,
                    f"[Auto-Review] Package installation confined to workspace environment.",
                )
            return (
                DECISION_ASK,
                f"[Auto-Review] Package installation '{cmd[:35]}' requires confirmation.",
            )

        if is_build_cmd:
            return (
                DECISION_ALLOW,
                f"[Auto-Review] Auto-approved: build and compilation command '{cmd[:35]}'.",
            )

        if is_lint_cmd:
            return (
                DECISION_ALLOW,
                f"[Auto-Review] Auto-approved: code formatting / linting operation.",
            )

        if is_git_staging:
            return (
                DECISION_ALLOW,
                f"[Auto-Review] Auto-approved safe git development workflow: '{cmd[:35]}'.",
            )

        if is_routine_fs:
            return (
                DECISION_ALLOW,
                f"[Auto-Review] Auto-approved routine directory/file setup: '{cmd[:35]}'.",
            )

        if is_script_eval:
            has_destructive = bool(re.search(r"\b(rm|rmdir|shutil\.rmtree|os\.remove|os\.unlink|system\([\"']rm)\b", inner_cmd))
            if not has_destructive:
                if profile == PROFILE_AUTONOMOUS:
                    return (
                        DECISION_ALLOW,
                        f"[Auto-Review] Auto-approved inline script evaluation in autonomous mode: '{cmd[:35]}'.",
                    )
                if not goal_text or any(k in goal_text for k in ["test", "verify", "check", "script", "folder", "path", "python", "node", "osascript", "dialog", "choose"]):
                    return (
                        DECISION_ALLOW,
                        f"[Auto-Review] Auto-approved safe inline script test: '{cmd[:35]}'.",
                    )

        # Autonomous mode allows benign commands confined to workspace
        if profile == PROFILE_AUTONOMOUS:
            return (
                DECISION_ALLOW,
                f"[Auto-Review] Auto-approved in autonomous mode.",
            )

        # In balanced mode, ask for unclassified or ambiguous operations
        return (
            DECISION_ASK,
            f"[Auto-Review] Command '{cmd[:45]}' is unclassified for current task. Confirmation requested.",
        )
