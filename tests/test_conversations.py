"""Comprehensive unit and integration tests for Unified Antigravity Conversation Browser
and Native agy Context Management integration.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from antiagent.dashboard.server import DashboardRequestHandler
from antiagent.engine.conversations import (
    AgyCapabilities,
    AmbiguousConversationError,
    ConversationContextState,
    ConversationMessage,
    ConversationStore,
    ConversationSummary,
    SOURCE_CLI,
    SOURCE_DESKTOP,
    SOURCE_IDE,
    VALID_SOURCES,
    clear_capabilities_cache,
    detect_agy_capabilities,
    get_conversation_store,
    is_compaction_event,
    reset_conversation_store,
    sanitize_tool_args,
    validate_conversation_id,
)
from antiagent.engine.doctor import run_doctor_check


class TestConversationStoreBasics(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.desktop_dir = Path(self.temp_dir) / "desktop"
        self.ide_dir = Path(self.temp_dir) / "ide"
        self.cli_dir = Path(self.temp_dir) / "cli"

        self.desktop_dir.mkdir(parents=True, exist_ok=True)
        self.ide_dir.mkdir(parents=True, exist_ok=True)
        self.cli_dir.mkdir(parents=True, exist_ok=True)

        self.store = ConversationStore(
            desktop_root=str(self.desktop_dir),
            ide_root=str(self.ide_dir),
            cli_root=str(self.cli_dir),
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mock_session(self, root: Path, conv_id: str, prompt: str = "Test prompt", turns: int = 2, with_compaction: bool = False):
        log_dir = root / "brain" / conv_id / ".system_generated" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        transcript = log_dir / "transcript.jsonl"

        lines = [
            json.dumps({
                "type": "USER_INPUT",
                "content": f"<USER_REQUEST>{prompt}</USER_REQUEST>",
                "timestamp": "2026-09-28T10:00:00Z",
            }),
            json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": "I will assist you.",
                "thinking": "Secret internal CoT that must never be exposed.",
                "model": "gemini-2.5-pro",
                "tool_calls": [
                    {
                        "name": "run_command",
                        "id": "call_1",
                        "args": {"CommandLine": "git status", "token": "ghp_secrettoken123456789"},
                    }
                ],
                "timestamp": "2026-09-28T10:00:05Z",
            }),
        ]

        if with_compaction:
            lines.append(json.dumps({
                "type": "CHECKPOINT",
                "content": "# Resuming from a compaction at step 5\n- Summary of previous state.",
                "timestamp": "2026-09-28T10:01:00Z",
            }))
            lines.append(json.dumps({
                "type": "USER_INPUT",
                "content": "continue the work",
                "timestamp": "2026-09-28T10:01:10Z",
            }))
            lines.append(json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": "Continuing now.",
                "thinking": "More hidden internal reasoning.",
                "timestamp": "2026-09-28T10:01:15Z",
            }))

        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return transcript

    def test_multi_source_discovery(self):
        self._create_mock_session(self.desktop_dir, "desktop-conv-1", "Build desktop frontend")
        self._create_mock_session(self.ide_dir, "ide-conv-2", "Fix IDE bug")
        self._create_mock_session(self.cli_dir, "cli-conv-3", "Run CLI deployment")

        # Discover all
        convs, total = self.store.list_conversations(source="all")
        self.assertEqual(total, 3)
        self.assertEqual(len(convs), 3)

        sources = {c.source for c in convs}
        self.assertEqual(sources, {SOURCE_DESKTOP, SOURCE_IDE, SOURCE_CLI})

        # Test per-source listing
        desktop_convs, d_total = self.store.list_conversations(source="desktop")
        self.assertEqual(d_total, 1)
        self.assertEqual(desktop_convs[0].conversation_id, "desktop-conv-1")

        ide_convs, i_total = self.store.list_conversations(source="ide")
        self.assertEqual(i_total, 1)
        self.assertEqual(ide_convs[0].conversation_id, "ide-conv-2")

        cli_convs, c_total = self.store.list_conversations(source="cli")
        self.assertEqual(c_total, 1)
        self.assertEqual(cli_convs[0].conversation_id, "cli-conv-3")

        # Sources summary
        summary = self.store.get_sources_summary()
        self.assertEqual(summary["desktop"]["count"], 1)
        self.assertEqual(summary["ide"]["count"], 1)
        self.assertEqual(summary["cli"]["count"], 1)
        self.assertEqual(summary["total"], 3)

    def test_sqlite_summaries_metadata_integration(self):
        # Create SQLite conversation_summaries.db in desktop root
        db_path = self.desktop_dir / "conversation_summaries.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE summaries (
                conversation_id TEXT PRIMARY KEY,
                title TEXT,
                model_name TEXT,
                workspace_path TEXT,
                turn_count INTEGER
            )
        """)
        conn.execute(
            "INSERT INTO summaries VALUES (?, ?, ?, ?, ?)",
            ("desktop-conv-rich", "Scaffold Backend Architecture", "gemini-2.5-pro", "/Users/alice/app", 15)
        )
        conn.commit()
        conn.close()

        self._create_mock_session(self.desktop_dir, "desktop-conv-rich", "Scaffold backend")

        convs, total = self.store.list_conversations(source="desktop")
        self.assertEqual(total, 1)
        c = convs[0]
        self.assertEqual(c.title, "Scaffold Backend Architecture")
        self.assertEqual(c.model_name, "gemini-2.5-pro")
        self.assertEqual(c.workspace_path, "/Users/alice/app")

    def test_search_and_workspace_filter(self):
        self._create_mock_session(self.desktop_dir, "conv-auth", "Implement OAuth2 login")
        self._create_mock_session(self.desktop_dir, "conv-db", "Migrate Postgres database")

        results, total = self.store.list_conversations(search="oauth")
        self.assertEqual(total, 1)
        self.assertEqual(results[0].conversation_id, "conv-auth")

        results, total = self.store.list_conversations(search="postgres")
        self.assertEqual(total, 1)
        self.assertEqual(results[0].conversation_id, "conv-db")

        results, total = self.store.list_conversations(search="nonexistent")
        self.assertEqual(total, 0)

    def test_prefix_id_matching(self):
        full_id = "long-uuid-12345678-abcd"
        self._create_mock_session(self.desktop_dir, full_id, "Prefix test prompt")

        # Query using 8-character short prefix
        lookup = self.store.get_conversation("long-uui")
        self.assertIsNotNone(lookup)
        source, summary = lookup
        self.assertEqual(source, SOURCE_DESKTOP)
        self.assertEqual(summary.conversation_id, full_id)

        # Messages query using prefix
        messages, total, ctx = self.store.get_conversation_messages("long-uui", source=SOURCE_DESKTOP)
        self.assertGreater(len(messages), 0)
        self.assertEqual(messages[0].content, "Prefix test prompt")


class TestSecurityAndRedaction(unittest.TestCase):
    def test_validate_conversation_id(self):
        # Valid IDs
        validate_conversation_id("154e7714-8bf2-48f2-a386-44844fe8f97d")
        validate_conversation_id("session_12345")
        validate_conversation_id("abcABC123")

        # Invalid IDs
        with self.assertRaises(ValueError):
            validate_conversation_id("")
        with self.assertRaises(ValueError):
            validate_conversation_id("../traversal")
        with self.assertRaises(ValueError):
            validate_conversation_id("/absolute/path")
        with self.assertRaises(ValueError):
            validate_conversation_id("id with spaces")
        with self.assertRaises(ValueError):
            validate_conversation_id("id;rm -rf /")
        with self.assertRaises(ValueError):
            validate_conversation_id("id\0nullbyte")

    def test_path_traversal_rejection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(desktop_root=temp_dir)
            with self.assertRaises(ValueError):
                store.get_conversation("../outside", source="desktop")
            with self.assertRaises(ValueError):
                store.get_conversation_messages("../../etc/passwd", source="desktop")

    def test_symlink_escape_rejection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "root"
            outside = Path(temp_dir) / "outside"
            root.mkdir()
            outside.mkdir()

            outside_file = outside / "secret.txt"
            outside_file.write_text("classified", encoding="utf-8")

            # Create symlink inside root pointing outside
            symlink_dir = root / "symlink_dir"
            try:
                os.symlink(str(outside), str(symlink_dir))
            except OSError:
                self.skipTest("Symlinks not permitted in environment")

            store = ConversationStore(desktop_root=str(root))
            with self.assertRaises(ValueError):
                store._resolve_safe_path(str(root), "symlink_dir/secret.txt")

    def test_sanitize_tool_args_redaction(self):
        args = {
            "CommandLine": "curl -H 'Authorization: Bearer sk-secret12345' https://api.example.com",
            "password": "supersecretpassword",
            "api_key": "my-api-key",
            "token": "ghp_1234567890abcdefghijklmnopqrstuvwxyz",
            "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC...\n-----END PRIVATE KEY-----",
            "nested": {
                "secret": "db-password",
                "normal": "normal-value",
                "array": [
                    {"credential": "creds"},
                    "plain-item",
                ]
            }
        }

        sanitized = sanitize_tool_args(args)

        self.assertEqual(sanitized["password"], "[REDACTED]")
        self.assertEqual(sanitized["api_key"], "[REDACTED]")
        self.assertEqual(sanitized["token"], "[REDACTED]")
        self.assertEqual(sanitized["private_key"], "[REDACTED]")
        self.assertEqual(sanitized["nested"]["secret"], "[REDACTED]")
        self.assertEqual(sanitized["nested"]["normal"], "normal-value")
        self.assertEqual(sanitized["nested"]["array"][0]["credential"], "[REDACTED]")
        self.assertEqual(sanitized["nested"]["array"][1], "plain-item")

        # Bearer token in string should be redacted
        self.assertNotIn("sk-secret12345", sanitized["CommandLine"])
        self.assertIn("[REDACTED]", sanitized["CommandLine"])


class TestCompactionAndCoTProtection(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.desktop_dir = Path(self.temp_dir) / "desktop"
        self.desktop_dir.mkdir(parents=True, exist_ok=True)
        self.store = ConversationStore(desktop_root=str(self.desktop_dir))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_hidden_cot_never_exposed(self):
        conv_id = "test-cot-hide"
        log_dir = self.desktop_dir / "brain" / conv_id / ".system_generated" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        transcript = log_dir / "transcript.jsonl"

        secret_cot = "SECRET_CHAIN_OF_THOUGHT_REASONING_NOT_VISIBLE_TO_USER"
        visible_content = "Here is the solution to your issue."

        lines = [
            json.dumps({
                "type": "USER_INPUT",
                "content": "<USER_REQUEST>How do I configure ssl?</USER_REQUEST>",
            }),
            json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": visible_content,
                "thinking": secret_cot,
                "tool_calls": [],
            }),
        ]
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

        messages, total, ctx = self.store.get_conversation_messages(conv_id, source="desktop")
        self.assertEqual(len(messages), 2)
        agent_msg = messages[1]

        self.assertEqual(agent_msg.content, visible_content)
        # Ensure thinking is not present in content or anywhere in dict
        msg_dict = agent_msg.to_dict()
        self.assertNotIn(secret_cot, json.dumps(msg_dict))

        # Markdown Export should not leak CoT
        md_export = self.store.export_conversation(conv_id, source="desktop", format="markdown")
        self.assertNotIn(secret_cot, md_export)
        self.assertIn(visible_content, md_export)

        # JSON Export should not leak CoT
        json_export = self.store.export_conversation(conv_id, source="desktop", format="json")
        self.assertNotIn(secret_cot, json_export)
        self.assertIn(visible_content, json_export)

    def test_compaction_event_detection_and_context_state(self):
        conv_id = "test-compaction"
        log_dir = self.desktop_dir / "brain" / conv_id / ".system_generated" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        transcript = log_dir / "transcript.jsonl"

        lines = [
            json.dumps({
                "type": "USER_INPUT",
                "content": "<USER_REQUEST>Start huge refactor</USER_REQUEST>",
            }),
            json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": "Starting refactor.",
                "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": "/app/main.py"}}],
            }),
            # Native Antigravity Compaction Checkpoint
            json.dumps({
                "type": "CHECKPOINT",
                "content": "# Resuming from a compaction...\n- Refactored main module\n- Prepared router",
            }),
            json.dumps({
                "type": "USER_INPUT",
                "content": "Now add tests",
            }),
            json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": "Tests added successfully.",
                "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pytest"}}],
            }),
        ]
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

        messages, total, ctx = self.store.get_conversation_messages(conv_id, source="desktop")

        # Compaction message should be normalized as type="compaction"
        compaction_msgs = [m for m in messages if m.type == "compaction"]
        self.assertEqual(len(compaction_msgs), 1)
        self.assertIn("Resuming from a compaction", compaction_msgs[0].content)

        # Context state
        self.assertEqual(ctx.compaction_count, 1)
        self.assertTrue(ctx.has_compaction)
        self.assertTrue(ctx.auto_compaction_enabled)
        self.assertTrue(ctx.managed_by_agy)
        self.assertIn("Refactored main module", ctx.latest_compaction_summary)
        self.assertEqual(ctx.tool_calls_count, 2)
        self.assertEqual(ctx.turn_count, 2)

    def test_malformed_jsonl_lines_handled_gracefully(self):
        conv_id = "test-corrupt-jsonl"
        log_dir = self.desktop_dir / "brain" / conv_id / ".system_generated" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        transcript = log_dir / "transcript.jsonl"

        # Corrupted partial writes mixed with valid lines
        lines = [
            "{\"type\": \"USER_INPUT\", \"content\": \"Valid first prompt\"}",
            "{\"partial_json_broken_line",
            "",
            "   ",
            "not a json line at all",
            "{\"type\": \"PLANNER_RESPONSE\", \"content\": \"Valid response\"}",
        ]
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

        messages, total, ctx = self.store.get_conversation_messages(conv_id, source="desktop")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].content, "Valid first prompt")
        self.assertEqual(messages[1].content, "Valid response")


class TestAgyCapabilitiesAndResume(unittest.TestCase):
    def setUp(self):
        clear_capabilities_cache()

    def tearDown(self):
        clear_capabilities_cache()

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_detect_agy_capabilities_available(self, mock_run, mock_which):
        mock_which.return_value = "/usr/local/bin/agy"

        def run_mock(cmd, **kwargs):
            if "--version" in cmd:
                return MagicMock(returncode=0, stdout="agy 0.4.2 (Antigravity CLI)\n", stderr="")
            if "--help" in cmd:
                return MagicMock(returncode=0, stdout="Usage: agy [OPTIONS]\n  --resume <id> Resume session\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_mock

        caps = detect_agy_capabilities(bypass_cache=True)
        self.assertTrue(caps.available)
        self.assertEqual(caps.version, "0.4.2")
        self.assertEqual(caps.path, "/usr/local/bin/agy")
        self.assertTrue(caps.supports_resume)

    @patch("shutil.which")
    @patch("pathlib.Path.is_file")
    def test_detect_agy_capabilities_not_found(self, mock_is_file, mock_which):
        mock_which.return_value = None
        mock_is_file.return_value = False

        caps = detect_agy_capabilities(bypass_cache=True)
        self.assertFalse(caps.available)
        self.assertIsNone(caps.path)

    def test_resume_desktop_session_refuses_direct_cli(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(desktop_root=temp_dir)
            log_dir = Path(temp_dir) / "brain" / "d-conv-1" / ".system_generated" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "transcript.jsonl").write_text("{}\n", encoding="utf-8")

            res = store.resume_conversation("d-conv-1", source="desktop")
            self.assertFalse(res["ok"])
            self.assertFalse(res["supported"])
            self.assertIn("Desktop", res["instructions"])

    @patch("antiagent.engine.conversations.detect_agy_capabilities")
    @patch("subprocess.Popen")
    def test_resume_cli_session_with_agy(self, mock_popen, mock_detect):
        mock_detect.return_value = AgyCapabilities(
            available=True,
            path="/usr/local/bin/agy",
            version="0.4.2",
            resume_supported=True,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(cli_root=temp_dir)
            log_dir = Path(temp_dir) / "brain" / "c-conv-1" / ".system_generated" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "transcript.jsonl").write_text("{}\n", encoding="utf-8")

            res = store.resume_conversation("c-conv-1", source="cli", launch=True)
            self.assertTrue(res["ok"])
            self.assertEqual(res["command"], "agy --resume c-conv-1")
            mock_popen.assert_called_once()


class TestDashboardConversationApiIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()
        cls.desktop_dir = Path(cls.test_dir) / "desktop"
        cls.ide_dir = Path(cls.test_dir) / "ide"
        cls.cli_dir = Path(cls.test_dir) / "cli"

        cls.desktop_dir.mkdir(parents=True, exist_ok=True)
        cls.ide_dir.mkdir(parents=True, exist_ok=True)
        cls.cli_dir.mkdir(parents=True, exist_ok=True)

        os.environ["ANTIAGENT_DESKTOP_DIR"] = str(cls.desktop_dir)
        os.environ["ANTIAGENT_IDE_DIR"] = str(cls.ide_dir)
        os.environ["ANTIAGENT_CLI_DIR"] = str(cls.cli_dir)
        os.environ["ANTIAGENT_TESTING"] = "1"
        reset_conversation_store()
        clear_capabilities_cache()

        # Populate a sample desktop session
        log_dir = cls.desktop_dir / "brain" / "conv-test-live" / ".system_generated" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps({"type": "USER_INPUT", "content": "<USER_REQUEST>Build live dashboard</USER_REQUEST>"}),
            json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": "Dashboard built successfully.",
                "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/tmp/dash.html"}}],
            }),
            json.dumps({"type": "CHECKPOINT", "content": "# Resuming from a compaction...\n- Finished html"}),
        ]
        (log_dir / "transcript.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

        DashboardRequestHandler.workspace_path = cls.test_dir
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("ANTIAGENT_TESTING", None)
        os.environ.pop("ANTIAGENT_DESKTOP_DIR", None)
        os.environ.pop("ANTIAGENT_IDE_DIR", None)
        os.environ.pop("ANTIAGENT_CLI_DIR", None)
        reset_conversation_store()
        clear_capabilities_cache()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_api_conversations_list(self):
        url = f"http://127.0.0.1:{self.port}/api/conversations"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertIn("conversations", data)
            self.assertIn("sources", data)
            self.assertIn("agy_capabilities", data)
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["conversations"][0]["conversation_id"], "conv-test-live")

    def test_api_conversations_detail(self):
        url = f"http://127.0.0.1:{self.port}/api/conversations/detail?id=conv-test-live&source=desktop"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertIn("conversation", data)
            self.assertIn("messages", data)
            self.assertIn("context_state", data)

            ctx = data["context_state"]
            self.assertTrue(ctx["has_compaction"])
            self.assertEqual(ctx["compaction_count"], 1)

    def test_api_conversations_detail_unknown(self):
        url = f"http://127.0.0.1:{self.port}/api/conversations/detail?id=unknown-id&source=desktop"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url)
        self.assertEqual(ctx.exception.code, 404)

    def test_api_conversations_detail_traversal_blocked(self):
        url = f"http://127.0.0.1:{self.port}/api/conversations/detail?id=../secret&source=desktop"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url)
        self.assertEqual(ctx.exception.code, 400)

    def test_api_conversations_context(self):
        url = f"http://127.0.0.1:{self.port}/api/conversations/context?id=conv-test-live&source=desktop"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertIn("context_state", data)
            self.assertTrue(data["context_state"]["auto_compaction_enabled"])

    def test_api_conversations_export_markdown(self):
        url = f"http://127.0.0.1:{self.port}/api/conversations/export"
        payload = json.dumps({
            "conversation_id": "conv-test-live",
            "source": "desktop",
            "format": "markdown",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertIn("conv-test-live", data["content"])

    def test_api_conversations_export_json(self):
        url = f"http://127.0.0.1:{self.port}/api/conversations/export"
        payload = json.dumps({
            "conversation_id": "conv-test-live",
            "source": "desktop",
            "format": "json",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            parsed = json.loads(data["content"])
            self.assertEqual(parsed["summary"]["conversation_id"], "conv-test-live")


class TestDoctorSourceDiagnostics(unittest.TestCase):
    def test_doctor_reports_structured_sources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            d_root = Path(temp_dir) / "desktop"
            i_root = Path(temp_dir) / "ide"
            c_root = Path(temp_dir) / "cli"
            d_root.mkdir()
            i_root.mkdir()
            c_root.mkdir()

            os.environ["ANTIAGENT_DESKTOP_DIR"] = str(d_root)
            os.environ["ANTIAGENT_IDE_DIR"] = str(i_root)
            os.environ["ANTIAGENT_CLI_DIR"] = str(c_root)
            reset_conversation_store()

            try:
                # Add 2 desktop sessions, 1 ide session
                for cid in ("d1", "d2"):
                    log = d_root / "brain" / cid / ".system_generated" / "logs"
                    log.mkdir(parents=True)
                    (log / "transcript.jsonl").write_text("{}\n")
                log_ide = i_root / "brain" / "i1" / ".system_generated" / "logs"
                log_ide.mkdir(parents=True)
                (log_ide / "transcript.jsonl").write_text("{}\n")

                doc = run_doctor_check(workspace_path=temp_dir)
                sessions = doc["antigravity_sessions"]

                self.assertEqual(sessions["count"], 3)
                self.assertIn("sources", sessions)
                self.assertEqual(sessions["sources"]["desktop"]["count"], 2)
                self.assertTrue(sessions["sources"]["desktop"]["available"])
                self.assertEqual(sessions["sources"]["ide"]["count"], 1)
                self.assertTrue(sessions["sources"]["ide"]["available"])
                self.assertEqual(sessions["sources"]["cli"]["count"], 0)
                self.assertTrue(sessions["sources"]["cli"]["available"])
            finally:
                os.environ.pop("ANTIAGENT_DESKTOP_DIR", None)
                os.environ.pop("ANTIAGENT_IDE_DIR", None)
                os.environ.pop("ANTIAGENT_CLI_DIR", None)
                reset_conversation_store()


class TestAmbiguityHandling(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.d_root = Path(self.temp_dir) / "desktop"
        self.i_root = Path(self.temp_dir) / "ide"
        self.c_root = Path(self.temp_dir) / "cli"
        self.d_root.mkdir()
        self.i_root.mkdir()
        self.c_root.mkdir()
        self.store = ConversationStore(
            desktop_root=str(self.d_root),
            ide_root=str(self.i_root),
            cli_root=str(self.c_root),
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cross_source_ambiguity_raises_exception_with_sources(self):
        shared_id = "shared-uuid-9999"
        # Create session in desktop
        d_log = self.d_root / "brain" / shared_id / ".system_generated" / "logs"
        d_log.mkdir(parents=True)
        (d_log / "transcript.jsonl").write_text('{"type": "USER_INPUT", "content": "Desktop request"}\n')

        # Create session with same ID in IDE
        i_log = self.i_root / "brain" / shared_id / ".system_generated" / "logs"
        i_log.mkdir(parents=True)
        (i_log / "transcript.jsonl").write_text('{"type": "USER_INPUT", "content": "IDE request"}\n')

        # 1. Unqualified lookup must raise AmbiguousConversationError
        with self.assertRaises(AmbiguousConversationError) as ctx:
            self.store.get_conversation(shared_id)
        self.assertEqual(ctx.exception.conversation_id, shared_id)
        self.assertEqual(ctx.exception.sources, ["desktop", "ide"])

        # 2. Explicit source resolves cleanly
        lookup_d = self.store.get_conversation(shared_id, source="desktop")
        self.assertIsNotNone(lookup_d)
        self.assertEqual(lookup_d[0], "desktop")

        lookup_i = self.store.get_conversation(shared_id, source="ide")
        self.assertIsNotNone(lookup_i)
        self.assertEqual(lookup_i[0], "ide")

        # 3. allow_ambiguous=True returns without raising
        lookup_any = self.store.get_conversation(shared_id, allow_ambiguous=True)
        self.assertIsNotNone(lookup_any)


class TestPrefixSearchDeduplication(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.d_root = Path(self.temp_dir) / "desktop"
        self.d_root.mkdir()
        self.store = ConversationStore(desktop_root=str(self.d_root))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_deduplicates_brain_dir_and_db_file(self):
        cid = "550e8400-e29b-41d4-a716-446655440000"

        # Create brain directory with transcript
        brain_log = self.d_root / "brain" / cid / ".system_generated" / "logs"
        brain_log.mkdir(parents=True)
        transcript = brain_log / "transcript.jsonl"
        transcript.write_text('{"type": "USER_INPUT", "content": "Hello world"}\n')

        # Create conversations/*.db with same UUID
        conv_dir = self.d_root / "conversations"
        conv_dir.mkdir(parents=True)
        (conv_dir / f"{cid}.db").write_text("dummy-sqlite-data")

        # List conversations - must contain only 1 item and no trailing .db in ID
        convs, total = self.store.list_conversations(source="desktop")
        self.assertEqual(total, 1)
        self.assertEqual(convs[0].conversation_id, cid)
        self.assertFalse(convs[0].conversation_id.endswith(".db"))

        # Prefix search by short prefix
        lookup = self.store.get_conversation("550e8400", source="desktop")
        self.assertIsNotNone(lookup)
        src, summary = lookup
        self.assertEqual(summary.conversation_id, cid)
        self.assertIsNotNone(summary.transcript_path)
        self.assertTrue(summary.transcript_path.endswith("transcript.jsonl"))


class TestMessageContractAndRole(unittest.TestCase):
    def test_message_to_dict_role_and_tool_calls(self):
        user_msg = ConversationMessage(
            sequence=0,
            timestamp="2026-09-28T10:00:00Z",
            type="user",
            visible_text="Write a test suite",
        )
        u_dict = user_msg.to_dict()
        self.assertEqual(u_dict["role"], "user")
        self.assertEqual(u_dict["content"], "Write a test suite")
        self.assertEqual(u_dict["tool_calls"], [])

        asst_msg = ConversationMessage(
            sequence=1,
            timestamp="2026-09-28T10:00:05Z",
            type="assistant",
            visible_text="I am writing the tests.",
        )
        a_dict = asst_msg.to_dict()
        self.assertEqual(a_dict["role"], "assistant")
        self.assertEqual(a_dict["content"], "I am writing the tests.")
        self.assertEqual(a_dict["tool_calls"], [])

        tool_msg = ConversationMessage(
            sequence=2,
            timestamp="2026-09-28T10:00:10Z",
            type="tool",
            visible_text="",
            tool_name="run_command",
            tool_args={"CommandLine": "pytest"},
            sanitized_tool_summary="Execute command: pytest",
            status="SUCCESS",
        )
        t_dict = tool_msg.to_dict()
        self.assertEqual(t_dict["role"], "assistant")
        self.assertEqual(len(t_dict["tool_calls"]), 1)
        self.assertEqual(t_dict["tool_calls"][0]["tool_name"], "run_command")
        self.assertEqual(t_dict["tool_calls"][0]["status"], "SUCCESS")
        self.assertEqual(t_dict["tool_calls"][0]["args"]["CommandLine"], "pytest")

    def test_assistant_cot_tags_strictly_stripped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            d_root = Path(temp_dir) / "desktop"
            d_root.mkdir()
            store = ConversationStore(desktop_root=str(d_root))

            cid = "cot-tag-test"
            log_dir = d_root / "brain" / cid / ".system_generated" / "logs"
            log_dir.mkdir(parents=True)
            raw = (
                '<thought>Hidden chain of thought plan</thought>'
                'Visible user response.'
                '<thinking>Another hidden thought</thinking>'
            )
            (log_dir / "transcript.jsonl").write_text(
                json.dumps({"type": "PLANNER_RESPONSE", "content": raw}) + "\n"
            )

            messages, total, ctx = store.get_conversation_messages(cid, source="desktop")
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].content, "Visible user response.")
            self.assertNotIn("Hidden chain of thought", messages[0].content)
            self.assertNotIn("Another hidden thought", messages[0].content)


class TestDashboardAmbiguityAndEdgeCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.d_root = Path(cls.temp_dir) / "desktop"
        cls.i_root = Path(cls.temp_dir) / "ide"
        cls.d_root.mkdir()
        cls.i_root.mkdir()

        # Shared session ID for ambiguity test
        shared_id = "ambig-conv-409"
        for root in (cls.d_root, cls.i_root):
            log = root / "brain" / shared_id / ".system_generated" / "logs"
            log.mkdir(parents=True)
            (log / "transcript.jsonl").write_text('{"type": "USER_INPUT", "content": "Test"}\n')

        os.environ["ANTIAGENT_DESKTOP_DIR"] = str(cls.d_root)
        os.environ["ANTIAGENT_IDE_DIR"] = str(cls.i_root)
        reset_conversation_store()

        class DirectTestHandler(DashboardRequestHandler):
            def _validate_host(self) -> bool:
                return True
            def _validate_csrf(self) -> bool:
                return True

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DirectTestHandler)
        cls.port = cls.server.server_address[1]
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        os.environ.pop("ANTIAGENT_DESKTOP_DIR", None)
        os.environ.pop("ANTIAGENT_IDE_DIR", None)
        reset_conversation_store()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_missing_id_returns_400(self):
        url = f"http://127.0.0.1:{self.port}/api/conversations/detail"
        req = urllib.request.Request(url)
        try:
            urllib.request.urlopen(req)
            self.fail("Expected HTTP 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_unknown_id_returns_404(self):
        url = f"http://127.0.0.1:{self.port}/api/conversations/detail?id=does-not-exist-at-all"
        req = urllib.request.Request(url)
        try:
            urllib.request.urlopen(req)
            self.fail("Expected HTTP 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_ambiguous_id_returns_409(self):
        url = f"http://127.0.0.1:{self.port}/api/conversations/detail?id=ambig-conv-409"
        req = urllib.request.Request(url)
        try:
            urllib.request.urlopen(req)
            self.fail("Expected HTTP 409")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 409)
            body = json.loads(e.read().decode("utf-8"))
            self.assertTrue(body.get("ambiguous"))
            self.assertEqual(sorted(body.get("sources", [])), ["desktop", "ide"])

    def test_ambiguous_id_resolved_with_source_returns_200(self):
        url = f"http://127.0.0.1:{self.port}/api/conversations/detail?id=ambig-conv-409&source=desktop"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertEqual(data["conversation"]["source"], "desktop")

    def test_resume_ambiguous_without_source_returns_409(self):
        url = f"http://127.0.0.1:{self.port}/api/conversations/resume"
        payload = json.dumps({"conversation_id": "ambig-conv-409"}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req)
            self.fail("Expected HTTP 409")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 409)
            body = json.loads(e.read().decode("utf-8"))
            self.assertTrue(body.get("ambiguous"))


class TestCLISessionCommands(unittest.TestCase):
    def test_cli_doctor_runs_without_crash(self):
        import io
        import sys
        from antiagent.cli import run_doctor

        out = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = out
            run_doctor()
        finally:
            sys.stdout = old_stdout

        output_str = out.getvalue()
        self.assertIn("Antigravity Sessions", output_str)
        self.assertIn("Antigravity Mode Compatibility", output_str)

    def test_cli_conversations_ambiguity_warning(self):
        import argparse
        import io
        import sys
        from antiagent.cli import handle_conversations_cli

        with tempfile.TemporaryDirectory() as temp_dir:
            d_root = Path(temp_dir) / "desktop"
            i_root = Path(temp_dir) / "ide"
            d_root.mkdir()
            i_root.mkdir()

            shared_id = "cli-ambig-session"
            for root in (d_root, i_root):
                log = root / "brain" / shared_id / ".system_generated" / "logs"
                log.mkdir(parents=True)
                (log / "transcript.jsonl").write_text('{"type": "USER_INPUT", "content": "test"}\n')

            os.environ["ANTIAGENT_DESKTOP_DIR"] = str(d_root)
            os.environ["ANTIAGENT_IDE_DIR"] = str(i_root)
            reset_conversation_store()

            try:
                out = io.StringIO()
                old_stdout = sys.stdout
                try:
                    sys.stdout = out
                    args = argparse.Namespace(
                        conv_command="show",
                        conversation_id=shared_id,
                        source=None,
                        limit=50,
                    )
                    with self.assertRaises(SystemExit) as cm:
                        handle_conversations_cli(args)
                    self.assertEqual(cm.exception.code, 1)
                finally:
                    sys.stdout = old_stdout

                output_str = out.getvalue()
                self.assertIn("exists in multiple sources", output_str)
                self.assertIn("Please specify --source", output_str)
            finally:
                os.environ.pop("ANTIAGENT_DESKTOP_DIR", None)
                os.environ.pop("ANTIAGENT_IDE_DIR", None)
                reset_conversation_store()

    def test_cli_conversations_list_text_and_json(self):
        import argparse
        import io
        import sys
        from antiagent.cli import handle_conversations_cli

        with tempfile.TemporaryDirectory() as temp_dir:
            d_root = Path(temp_dir) / "desktop"
            d_root.mkdir()
            log = d_root / "brain" / "cli-list-conv" / ".system_generated" / "logs"
            log.mkdir(parents=True)
            (log / "transcript.jsonl").write_text(
                json.dumps({"type": "USER_INPUT", "content": "<USER_REQUEST>Build microservice</USER_REQUEST>"}) + "\n"
            )

            os.environ["ANTIAGENT_DESKTOP_DIR"] = str(d_root)
            reset_conversation_store()

            try:
                # Text output
                out = io.StringIO()
                old_stdout = sys.stdout
                try:
                    sys.stdout = out
                    args = argparse.Namespace(
                        conv_command="list",
                        source="desktop",
                        workspace=None,
                        search=None,
                        limit=10,
                        json=False,
                    )
                    handle_conversations_cli(args)
                finally:
                    sys.stdout = old_stdout

                text_out = out.getvalue()
                self.assertIn("cli-list", text_out)
                self.assertIn("Build microservice", text_out)

                # JSON output
                out_json = io.StringIO()
                try:
                    sys.stdout = out_json
                    args.json = True
                    handle_conversations_cli(args)
                finally:
                    sys.stdout = old_stdout

                json_parsed = json.loads(out_json.getvalue())
                self.assertTrue(json_parsed["ok"])
                self.assertEqual(json_parsed["total"], 1)
                self.assertEqual(json_parsed["conversations"][0]["conversation_id"], "cli-list-conv")
            finally:
                os.environ.pop("ANTIAGENT_DESKTOP_DIR", None)
                reset_conversation_store()

    def test_cli_conversations_show_text_and_json(self):
        import argparse
        import io
        import sys
        from antiagent.cli import handle_conversations_cli

        with tempfile.TemporaryDirectory() as temp_dir:
            d_root = Path(temp_dir) / "desktop"
            d_root.mkdir()
            log = d_root / "brain" / "cli-show-conv" / ".system_generated" / "logs"
            log.mkdir(parents=True)
            lines = [
                json.dumps({"type": "USER_INPUT", "content": "Deploy database"}),
                json.dumps({
                    "type": "PLANNER_RESPONSE",
                    "content": "Deploying now.",
                    "thinking": "hidden reasoning",
                    "tool_calls": [{"name": "run_command", "args": {"CommandLine": "docker compose up"}}],
                }),
            ]
            (log / "transcript.jsonl").write_text("\n".join(lines) + "\n")

            os.environ["ANTIAGENT_DESKTOP_DIR"] = str(d_root)
            reset_conversation_store()

            try:
                out = io.StringIO()
                old_stdout = sys.stdout
                try:
                    sys.stdout = out
                    args = argparse.Namespace(
                        conv_command="show",
                        conversation_id="cli-show-conv",
                        source="desktop",
                        tail=None,
                        tools=True,
                        json=False,
                    )
                    handle_conversations_cli(args)
                finally:
                    sys.stdout = old_stdout

                output = out.getvalue()
                self.assertIn("Deploy database", output)
                self.assertIn("Deploying now.", output)
                self.assertIn("docker compose up", output)
                self.assertNotIn("hidden reasoning", output)

                # Test JSON format
                out_json = io.StringIO()
                try:
                    sys.stdout = out_json
                    args.json = True
                    handle_conversations_cli(args)
                finally:
                    sys.stdout = old_stdout

                data = json.loads(out_json.getvalue())
                self.assertEqual(data["summary"]["conversation_id"], "cli-show-conv")
                self.assertEqual(len(data["messages"]), 3)
                self.assertNotIn("hidden reasoning", json.dumps(data))
            finally:
                os.environ.pop("ANTIAGENT_DESKTOP_DIR", None)
                reset_conversation_store()

    def test_cli_conversations_export_stdout_and_file(self):
        import argparse
        import io
        import sys
        from antiagent.cli import handle_conversations_cli

        with tempfile.TemporaryDirectory() as temp_dir:
            d_root = Path(temp_dir) / "desktop"
            d_root.mkdir()
            log = d_root / "brain" / "cli-export-conv" / ".system_generated" / "logs"
            log.mkdir(parents=True)
            (log / "transcript.jsonl").write_text('{"type": "USER_INPUT", "content": "Export me"}\n')

            os.environ["ANTIAGENT_DESKTOP_DIR"] = str(d_root)
            reset_conversation_store()

            try:
                # Export to stdout (markdown)
                out = io.StringIO()
                old_stdout = sys.stdout
                try:
                    sys.stdout = out
                    args = argparse.Namespace(
                        conv_command="export",
                        conversation_id="cli-export-conv",
                        source="desktop",
                        format="markdown",
                        output=None,
                    )
                    handle_conversations_cli(args)
                finally:
                    sys.stdout = old_stdout

                self.assertIn("# ", out.getvalue())
                self.assertIn("Export me", out.getvalue())

                # Export to file
                export_file = Path(temp_dir) / "exported.md"
                out_f = io.StringIO()
                try:
                    sys.stdout = out_f
                    args.output = str(export_file)
                    handle_conversations_cli(args)
                finally:
                    sys.stdout = old_stdout

                self.assertTrue(export_file.is_file())
                self.assertIn("Export me", export_file.read_text(encoding="utf-8"))
            finally:
                os.environ.pop("ANTIAGENT_DESKTOP_DIR", None)
                reset_conversation_store()

    def test_cli_conversations_resume_desktop_gives_instructions(self):
        import argparse
        import io
        import sys
        from antiagent.cli import handle_conversations_cli

        with tempfile.TemporaryDirectory() as temp_dir:
            d_root = Path(temp_dir) / "desktop"
            d_root.mkdir()
            log = d_root / "brain" / "cli-resume-conv" / ".system_generated" / "logs"
            log.mkdir(parents=True)
            (log / "transcript.jsonl").write_text('{"type": "USER_INPUT", "content": "Resume test"}\n')

            os.environ["ANTIAGENT_DESKTOP_DIR"] = str(d_root)
            reset_conversation_store()

            try:
                out = io.StringIO()
                old_stdout = sys.stdout
                try:
                    sys.stdout = out
                    args = argparse.Namespace(
                        conv_command="resume",
                        conversation_id="cli-resume-conv",
                        source="desktop",
                    )
                    with self.assertRaises(SystemExit) as cm:
                        handle_conversations_cli(args)
                    self.assertEqual(cm.exception.code, 1)
                finally:
                    sys.stdout = old_stdout

                output = out.getvalue()
                self.assertIn("Direct CLI resume is not supported for Desktop", output)
            finally:
                os.environ.pop("ANTIAGENT_DESKTOP_DIR", None)
                reset_conversation_store()

    def test_cli_remote_conversations(self):
        import argparse
        import io
        import sys
        from antiagent.cli import handle_remote_cli
        from antiagent.engine.remote_sessions import CommandResult, RemoteHost

        mock_host = RemoteHost(
            name="test-box",
            ssh_host="10.0.0.1",
            user="ubuntu",
            port=22,
        )

        mock_payload = json.dumps({
            "ok": True,
            "total": 1,
            "conversations": [
                {
                    "conversation_id": "rem-uuid-1234",
                    "source": "cli",
                    "title": "Remote deployment",
                }
            ],
        })

        with patch("antiagent.engine.remote_sessions.RemoteHostRegistry.get_host", return_value=mock_host), \
             patch("antiagent.engine.remote_sessions.SSHClient.run_command") as mock_exec:
            mock_exec.return_value = CommandResult(
                argv=["ssh", "test-box"],
                returncode=0,
                stdout=mock_payload,
                stderr="",
                duration_ms=25,
            )

            out = io.StringIO()
            old_stdout = sys.stdout
            try:
                sys.stdout = out
                args = argparse.Namespace(
                    remote_command="conversations",
                    machine="test-box",
                    remote_conv_cmd="list",
                    source="all",
                    limit=20,
                    json=False,
                )
                handle_remote_cli(args)
            finally:
                sys.stdout = old_stdout

            output = out.getvalue()
            self.assertIn("Remote Antigravity Conversations on 'test-box'", output)
            self.assertIn("rem-uuid", output)
            self.assertIn("Remote deployment", output)


if __name__ == "__main__":
    unittest.main()


