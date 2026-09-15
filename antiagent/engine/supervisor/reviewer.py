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
from antiagent.engine.context_extractor import TaskContext
from antiagent.engine.supervisor.native_reviewer import ContextAwareNativeReviewer
from antiagent.engine.supervisor.prompt import (
    SUPERVISOR_SYSTEM_PROMPT,
    format_review_prompt,
)

logger = logging.getLogger("antiagent.supervisor")


class LLMSupervisor:
    """Invokes an LLM reviewer or native semantic reviewer to evaluate actions."""

    def __init__(self, config: AntiAgentConfig):
        self.config = config
        self.timeout = 5.0  # Hook response timeout in seconds
        self.native_reviewer = ContextAwareNativeReviewer()

    def review(
        self,
        tool_name: str,
        tool_args: dict,
        workspace_paths: Optional[List[str]] = None,
        context: Optional[TaskContext] = None,
    ) -> Tuple[str, str]:
        """Perform an AI supervisor review with bounded task context.
        Returns:
            (decision, reason)
        """
        paths = workspace_paths or [os.getcwd()]
        provider = (self.config.provider or "offline").lower()

        try:
            if provider == "native":
                return self._review_native(tool_name, tool_args, paths, context=context)
            elif provider == "gemini":
                return self._review_gemini(tool_name, tool_args, paths, context=context)
            elif provider == "openai":
                return self._review_openai(tool_name, tool_args, paths, context=context)
            elif provider == "ollama":
                return self._review_ollama(tool_name, tool_args, paths, context=context)
            else:
                return self._fallback_review(tool_name, tool_args, "offline")
        except Exception as e:
            logger.warning(f"Supervisor review error ({provider}): {e}")
            return self._fallback_review(tool_name, tool_args, f"error: {str(e)[:40]}")

    def _review_native(
        self,
        tool_name: str,
        tool_args: dict,
        paths: List[str],
        context: Optional[TaskContext] = None,
    ) -> Tuple[str, str]:
        """Antigravity Native Mode (Zero API Key required).
        Performs intuitive contextual semantic review using Bounded Task Context.
        """
        return self.native_reviewer.review(
            tool_name=tool_name,
            tool_args=tool_args,
            workspace_paths=paths,
            context=context,
            profile=self.config.profile,
        )
    def _review_gemini(
        self,
        tool_name: str,
        tool_args: dict,
        paths: List[str],
        context: Optional[TaskContext] = None,
    ) -> Tuple[str, str]:
        api_key = self.config.api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            return self._fallback_review(tool_name, tool_args, "no GEMINI_API_KEY")

        model = self.config.model or "gemini-2.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

        ctx_summary = context.to_summary() if context else None
        user_content = format_review_prompt(
            tool_name, tool_args, paths, self.config.profile, context_summary=ctx_summary
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
        self,
        tool_name: str,
        tool_args: dict,
        paths: List[str],
        context: Optional[TaskContext] = None,
    ) -> Tuple[str, str]:
        api_key = self.config.api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return self._fallback_review(tool_name, tool_args, "no OPENAI_API_KEY")

        model = self.config.model or "gpt-4o-mini"
        endpoint = self.config.endpoint_url or "https://api.openai.com/v1/chat/completions"

        ctx_summary = context.to_summary() if context else None
        user_content = format_review_prompt(
            tool_name, tool_args, paths, self.config.profile, context_summary=ctx_summary
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
        self,
        tool_name: str,
        tool_args: dict,
        paths: List[str],
        context: Optional[TaskContext] = None,
    ) -> Tuple[str, str]:
        endpoint = self.config.endpoint_url or "http://localhost:11434/api/chat"
        model = self.config.model or "llama3.2"

        ctx_summary = context.to_summary() if context else None
        user_content = format_review_prompt(
            tool_name, tool_args, paths, self.config.profile, context_summary=ctx_summary
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
