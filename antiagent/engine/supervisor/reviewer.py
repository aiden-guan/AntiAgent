"""LLM-based supervisor client supporting Gemini, OpenAI, Ollama, and offline fallback."""

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

from antiagent.config import AntiAgentConfig
from antiagent.constants import (
    DECISION_ALLOW,
    DECISION_ASK,
    DECISION_DENY,
    PROFILE_AUTONOMOUS,
    PROFILE_PARANOID,
)
from antiagent.engine.supervisor.prompt import (
    SUPERVISOR_SYSTEM_PROMPT,
    format_review_prompt,
)

logger = logging.getLogger("antiagent.supervisor")


class LLMSupervisor:
    """Invokes an LLM reviewer to evaluate ambiguous or mutating actions."""

    def __init__(self, config: AntiAgentConfig):
        self.config = config
        self.timeout = 5.0  # Hook response timeout in seconds

    def review(
        self,
        tool_name: str,
        tool_args: dict,
        workspace_paths: Optional[List[str]] = None,
    ) -> Tuple[str, str]:
        """Perform an AI supervisor review.
        Returns:
            (decision, reason)
        """
        paths = workspace_paths or [os.getcwd()]
        provider = (self.config.provider or "offline").lower()

        try:
            if provider == "native":
                return self._review_native(tool_name, tool_args, paths)
            elif provider == "gemini":
                return self._review_gemini(tool_name, tool_args, paths)
            elif provider == "openai":
                return self._review_openai(tool_name, tool_args, paths)
            elif provider == "ollama":
                return self._review_ollama(tool_name, tool_args, paths)
            else:
                return self._fallback_review(tool_name, tool_args, "offline")
        except Exception as e:
            logger.warning(f"Supervisor review error ({provider}): {e}")
            return self._fallback_review(tool_name, tool_args, f"error: {str(e)[:40]}")

    def _review_native(
        self, tool_name: str, tool_args: dict, paths: List[str]
    ) -> Tuple[str, str]:
        """Antigravity Native Mode (Zero API Key required).
        Performs contextual inspection without requiring an external Gemini or OpenAI API key.
        """
        profile = self.config.profile

        # 1. Paranoid mode always flags mutating actions
        if profile == PROFILE_PARANOID:
            return (
                DECISION_ASK,
                f"[Antigravity Native] {tool_name} requires confirmation in paranoid mode.",
            )

        # 2. Workspace file creation and updates
        if tool_name in ("write_to_file", "replace_file_content"):
            target = tool_args.get("TargetFile") or ""
            base_name = target.split("/")[-1] if target else "file"
            return (
                DECISION_ALLOW,
                f"[Antigravity Native] Auto-approved safe workspace edit to '{base_name}'.",
            )

        # 3. Shell commands
        if tool_name == "run_command":
            cmd = tool_args.get("CommandLine", "").strip()

            # Safe dev workflows like mkdir, git add, git commit
            safe_patterns = [
                r"^mkdir\s+(-p\s+)?",
                r"^git\s+(add|commit|checkout\s+-b|switch\s+-c)\b",
                r"^touch\s+",
                r"^cp\s+.*",
                r"^mv\s+.*",
            ]
            for pat in safe_patterns:
                if re.match(pat, cmd):
                    return (
                        DECISION_ALLOW,
                        f"[Antigravity Native] Auto-approved routine dev workflow: '{cmd[:35]}...'",
                    )

            if profile == PROFILE_AUTONOMOUS:
                return (
                    DECISION_ALLOW,
                    f"[Antigravity Native] Auto-approved in autonomous mode.",
                )

            # In balanced mode, ask before executing unclassified shell commands
            return (
                DECISION_ASK,
                f"[Antigravity Native] Proposed command '{cmd[:40]}' modifies system/workspace state. Confirmation requested.",
            )

        return (
            DECISION_ASK,
            f"[Antigravity Native] {tool_name} requires user confirmation.",
        )

    def _review_gemini(
        self, tool_name: str, tool_args: dict, paths: List[str]
    ) -> Tuple[str, str]:
        api_key = self.config.api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            return self._fallback_review(tool_name, tool_args, "no GEMINI_API_KEY")

        model = self.config.model or "gemini-2.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

        user_content = format_review_prompt(
            tool_name, tool_args, paths, self.config.profile
        )

        payload = {
            "system_instruction": {"parts": [{"text": SUPERVISOR_SYSTEM_PROMPT}]},
            "contents": [{"parts": [{"text": user_content}]}],
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json",
            },
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            candidate = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            return self._parse_llm_json(candidate)

    def _review_openai(
        self, tool_name: str, tool_args: dict, paths: List[str]
    ) -> Tuple[str, str]:
        api_key = self.config.api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return self._fallback_review(tool_name, tool_args, "no OPENAI_API_KEY")

        model = self.config.model or "gpt-4o-mini"
        endpoint = self.config.endpoint_url or "https://api.openai.com/v1/chat/completions"

        user_content = format_review_prompt(
            tool_name, tool_args, paths, self.config.profile
        )

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SUPERVISOR_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            return self._parse_llm_json(content)

    def _review_ollama(
        self, tool_name: str, tool_args: dict, paths: List[str]
    ) -> Tuple[str, str]:
        endpoint = self.config.endpoint_url or "http://localhost:11434/api/chat"
        model = self.config.model or "llama3.2"

        user_content = format_review_prompt(
            tool_name, tool_args, paths, self.config.profile
        )

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SUPERVISOR_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        }

        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data.get("message", {}).get("content", "")
            return self._parse_llm_json(content)

    def _parse_llm_json(self, text: str) -> Tuple[str, str]:
        """Extract and validate decision and reason from LLM output."""
        cleaned = text.strip()
        # Strip markdown ```json ... ``` if present
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned).strip()

        parsed = json.loads(cleaned)
        decision = parsed.get("decision", "").lower().strip()
        reason = parsed.get("reason", "Reviewed by AI supervisor.")
        risk = parsed.get("risk_score", 5)

        if decision not in (DECISION_ALLOW, DECISION_ASK, DECISION_DENY):
            decision = DECISION_ASK

        return decision, f"[AI Supervisor, Risk {risk}/10] {reason}"

    def _fallback_review(
        self, tool_name: str, tool_args: dict, fallback_reason: str
    ) -> Tuple[str, str]:
        """Fallback behavior when no LLM is reachable."""
        profile = self.config.profile

        if profile == PROFILE_AUTONOMOUS:
            return (
                DECISION_ALLOW,
                f"Auto-approved in autonomous mode ({fallback_reason}).",
            )
        elif profile == PROFILE_PARANOID:
            return (
                DECISION_ASK,
                f"Confirmation required in paranoid mode ({fallback_reason}).",
            )
        else:
            # Balanced mode: file edits inside workspace can proceed, shell execution needs confirmation
            if tool_name in ("write_to_file", "replace_file_content"):
                return (
                    DECISION_ALLOW,
                    f"Auto-approved workspace file change ({fallback_reason}).",
                )
            return (
                DECISION_ASK,
                f"Action requires confirmation ({fallback_reason}).",
            )
