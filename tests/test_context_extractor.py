"""Unit tests for Bounded Task Context extraction from Antigravity transcripts."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from antiagent.engine.context_extractor import (
    TranscriptContextExtractor,
    clean_user_prompt,
    is_affirmative_or_brief,
)


class TestContextExtractor(unittest.TestCase):
    def test_clean_user_prompt(self):
        raw = """<USER_REQUEST>
Build a FastAPI web app with unit tests.
</USER_REQUEST>
<ADDITIONAL_METADATA>
The current local time is: 2026-09-15T15:00:00-07:00.
</ADDITIONAL_METADATA>"""
        cleaned = clean_user_prompt(raw)
        self.assertEqual(cleaned, "Build a FastAPI web app with unit tests.")

    def test_is_affirmative_or_brief(self):
        self.assertTrue(is_affirmative_or_brief("yes"))
        self.assertTrue(is_affirmative_or_brief("do it for me"))
        self.assertTrue(is_affirmative_or_brief("proceed!"))
        self.assertTrue(is_affirmative_or_brief("ok"))
        self.assertFalse(is_affirmative_or_brief("Please refactor the database connector module and test it"))

    def test_extract_context_with_transcript(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            conv_id = "test-conv-123"
            log_dir = Path(temp_dir) / conv_id / ".system_generated" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            transcript_file = log_dir / "transcript.jsonl"

            # Create mock transcript
            lines = [
                json.dumps({
                    "type": "USER_INPUT",
                    "content": "<USER_REQUEST>Scaffold a backend and add unit tests</USER_REQUEST>",
                }),
                json.dumps({
                    "type": "PLANNER_RESPONSE",
                    "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/proj/src/app.py"}}],
                }),
                json.dumps({
                    "type": "USER_INPUT",
                    "content": "do it for me",
                }),
            ]
            transcript_file.write_text("\n".join(lines), encoding="utf-8")

            extractor = TranscriptContextExtractor(base_brain_dir=temp_dir)
            ctx = extractor.extract_context(conv_id)

            self.assertEqual(ctx.user_prompt, "do it for me")
            self.assertEqual(ctx.primary_goal, "Scaffold a backend and add unit tests")
            self.assertIn("write_to_file", ctx.recent_tools)
            self.assertIn("app.py", ctx.recent_files)
            self.assertIn("User Goal: Scaffold a backend", ctx.to_summary())

    def test_missing_transcript_fallback(self):
        extractor = TranscriptContextExtractor(base_brain_dir="/nonexistent/path")
        ctx = extractor.extract_context("no-such-id")
        self.assertEqual(ctx.user_prompt, "")
        self.assertEqual(ctx.primary_goal, "")
        self.assertEqual(ctx.recent_tools, [])


    def test_extract_context_large_transcript_expansion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            conv_id = "test-conv-large"
            log_dir = Path(temp_dir) / conv_id / ".system_generated" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            transcript_file = log_dir / "transcript.jsonl"

            # 1. First line is user prompt
            lines = [json.dumps({
                "type": "USER_INPUT",
                "content": "<USER_REQUEST>Build native folder picker dialog</USER_REQUEST>",
            })]

            # 2. Pad transcript with >80KB of PLANNER_RESPONSE events
            pad = "x" * 1000
            for i in range(85):
                lines.append(json.dumps({
                    "type": "PLANNER_RESPONSE",
                    "step_index": i,
                    "content": f"Working on step {i} with payload {pad}",
                    "tool_calls": [{"name": "run_command", "args": {"CommandLine": "ls"}}],
                }))

            transcript_file.write_text("\n".join(lines), encoding="utf-8")
            self.assertGreater(transcript_file.stat().st_size, 80000)

            # 3. Test that extractor still finds the initial user prompt
            extractor = TranscriptContextExtractor(base_brain_dir=temp_dir)
            ctx = extractor.extract_context(conv_id)
            self.assertEqual(ctx.user_prompt, "Build native folder picker dialog")
            self.assertEqual(ctx.primary_goal, "Build native folder picker dialog")


if __name__ == "__main__":
    unittest.main()
