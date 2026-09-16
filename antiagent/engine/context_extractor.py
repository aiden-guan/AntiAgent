"""Bounded Task Context extractor for Antigravity conversations.

Reads Antigravity's live transcript log (transcript.jsonl) to extract:
1. The user's latest stated prompt and primary task goal.
2. Recent turn trajectory (tools run, files created/modified).
This provides the semantic ground truth for the Auto-Review engine.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class TaskContext:
    """Structured context surrounding a proposed agent action."""

    user_prompt: str = ""
    primary_goal: str = ""
    recent_tools: List[str] = field(default_factory=list)
    recent_files: List[str] = field(default_factory=list)
    conversation_id: str = ""
    step_idx: int = -1

    def to_summary(self) -> str:
        """Returns a concise text summary suitable for LLM review prompts."""
        parts = []
        if self.primary_goal:
            parts.append(f"User Goal: {self.primary_goal}")
        elif self.user_prompt:
            parts.append(f"User Request: {self.user_prompt}")
        if self.recent_tools:
            parts.append(f"Recent Actions: {', '.join(self.recent_tools[-4:])}")
        if self.recent_files:
            parts.append(f"Active Files: {', '.join(self.recent_files[-3:])}")
        return " | ".join(parts) if parts else "No active task context available."


def clean_user_prompt(raw: str) -> str:
    """Strips XML tags and metadata from raw Antigravity user input."""
    # Remove <ADDITIONAL_METADATA>...</ADDITIONAL_METADATA>
    cleaned = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", raw, flags=re.DOTALL)
    # Remove <USER_SETTINGS_CHANGE>...</USER_SETTINGS_CHANGE>
    cleaned = re.sub(r"<USER_SETTINGS_CHANGE>.*?</USER_SETTINGS_CHANGE>", "", cleaned, flags=re.DOTALL)
    # Extract <USER_REQUEST>...</USER_REQUEST> if present
    match = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", cleaned, flags=re.DOTALL)
    if match:
        cleaned = match.group(1)
    # Strip leading/trailing whitespace
    return cleaned.strip()


def is_affirmative_or_brief(prompt: str) -> bool:
    """Detects whether a prompt is a brief follow-up or confirmation."""
    text = prompt.lower().strip().rstrip(".!?,")
    brief_tokens = {
        "yes", "yeah", "yep", "y", "ok", "okay", "sure", "proceed",
        "go ahead", "do it", "do it for me", "continue", "approve",
        "sounds good", "lgtm", "looks good", "run it"
    }
    if text in brief_tokens or len(text.split()) <= 4:
        return True
    return False


class TranscriptContextExtractor:
    """Extracts TaskContext from Antigravity's transcript logs."""

    def __init__(self, base_brain_dir: Optional[str] = None):
        if base_brain_dir:
            self.brain_dir = Path(base_brain_dir)
        else:
            self.brain_dir = Path(os.path.expanduser("~/.gemini/antigravity/brain"))

    def extract_context(
        self,
        conversation_id: str,
        step_idx: int = -1,
        max_bytes: int = 65536,
    ) -> TaskContext:
        """Reads the tail of transcript.jsonl to build TaskContext (<1ms)."""
        ctx = TaskContext(conversation_id=conversation_id, step_idx=step_idx)
        if not conversation_id:
            return ctx

        transcript_file = (
            self.brain_dir
            / conversation_id
            / ".system_generated"
            / "logs"
            / "transcript.jsonl"
        )
        if not transcript_file.is_file():
            return ctx

        user_prompts: List[str] = []
        recent_tools: List[str] = []
        recent_files: List[str] = []

        try:
            with open(transcript_file, "rb") as f:
                f.seek(0, 2)
                file_size = f.tell()

                def parse_chunk(start_offset: int, length: int) -> None:
                    f.seek(start_offset)
                    chunk_str = f.read(length).decode("utf-8", errors="ignore")
                    lines = chunk_str.splitlines()
                    for line in reversed(lines):
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except Exception:
                            continue

                        msg_type = data.get("type")
                        content = data.get("content") or ""

                        if msg_type == "USER_INPUT" and content:
                            cleaned = clean_user_prompt(content)
                            if cleaned and cleaned not in user_prompts:
                                user_prompts.append(cleaned)
                                if len(user_prompts) >= 3:
                                    break

                        elif msg_type == "PLANNER_RESPONSE":
                            tool_calls = data.get("tool_calls") or []
                            for tc in tool_calls:
                                t_name = tc.get("name")
                                if t_name and len(recent_tools) < 6 and t_name not in recent_tools:
                                    recent_tools.append(t_name)
                                t_args = tc.get("args") or {}
                                f_path = t_args.get("TargetFile") or t_args.get("AbsolutePath")
                                if f_path and len(recent_files) < 4:
                                    base = f_path.rstrip("/").split("/")[-1]
                                    if base and base not in recent_files:
                                        recent_files.append(base)

                # 1. Fast read from tail
                read_size = min(max_bytes, file_size)
                parse_chunk(file_size - read_size, read_size)

                # 2. If no user prompts found in tail, expand backwards up to 5MB or full file
                if not user_prompts and file_size > read_size:
                    expanded_size = min(file_size, 5 * 1024 * 1024)
                    parse_chunk(file_size - expanded_size, expanded_size)

            if user_prompts:
                ctx.user_prompt = user_prompts[0]
                # Determine primary goal
                if is_affirmative_or_brief(ctx.user_prompt) and len(user_prompts) > 1:
                    ctx.primary_goal = user_prompts[1]
                else:
                    ctx.primary_goal = ctx.user_prompt

            ctx.recent_tools = list(reversed(recent_tools))
            ctx.recent_files = list(reversed(recent_files))

        except Exception:
            pass

        return ctx
