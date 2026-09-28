"""Unified Antigravity Conversation Browser & Native Context Management.

Provides centralized discovery, fast indexing, normalized browsing,
redacted inspection, and native context compaction visibility across
Antigravity Desktop, IDE, and CLI session stores.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from antiagent.engine.context_extractor import clean_user_prompt

# Supported conversation sources
SOURCE_DESKTOP = "desktop"
SOURCE_IDE = "ide"
SOURCE_CLI = "cli"
VALID_SOURCES: Set[str] = {SOURCE_DESKTOP, SOURCE_IDE, SOURCE_CLI}

# Conversation ID pattern (alphanumeric, hyphens, underscores, dots; max 128 chars)
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-\.]{1,128}\Z")

# Sensitive keys that must be redacted in tool arguments
SENSITIVE_KEY_SUBSTRINGS = (
    "password",
    "passwd",
    "token",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "private_key",
    "privatekey",
    "credential",
    "credentials",
)

# Regex patterns for sensitive tokens embedded in strings
_BEARER_RE = re.compile(r"(Bearer\s+)[a-zA-Z0-9_\-\.~+/=]{10,}", re.IGNORECASE)
_GH_TOKEN_RE = re.compile(r"(ghp_[a-zA-Z0-9]{20,}|gho_[a-zA-Z0-9]{20,}|github_pat_[a-zA-Z0-9_]{22,})")
_OPENAI_KEY_RE = re.compile(r"(sk-[a-zA-Z0-9_\-]{20,})")
_PEM_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")


def validate_conversation_id(conversation_id: str) -> None:
    """Ensure conversation ID contains no path traversal or forbidden characters."""
    if not conversation_id or not isinstance(conversation_id, str):
        raise ValueError("Conversation ID cannot be empty.")
    if "/" in conversation_id or "\\" in conversation_id or "\0" in conversation_id or ".." in conversation_id:
        raise ValueError(f"Invalid conversation ID '{conversation_id}': path traversal sequence detected.")
    if not _SAFE_ID_RE.match(conversation_id):
        raise ValueError(f"Invalid conversation ID '{conversation_id}': format must be alphanumeric with dashes/underscores.")


class AmbiguousConversationError(ValueError):
    """Raised when a conversation ID exists across multiple sources and no explicit source was specified."""

    def __init__(self, conversation_id: str, sources: List[str]):
        super().__init__(
            f"Conversation '{conversation_id}' was found in multiple sources ({', '.join(sorted(sources))}). "
            f"Please specify source explicitly."
        )
        self.conversation_id = conversation_id
        self.sources = sorted(sources)


def sanitize_tool_args(args: Any) -> Any:
    """Recursively redact passwords, tokens, API keys, and sensitive data from tool arguments."""
    if isinstance(args, dict):
        sanitized: Dict[str, Any] = {}
        for key, value in args.items():
            k_lower = str(key).lower().replace("-", "_")
            if any(sub in k_lower for sub in SENSITIVE_KEY_SUBSTRINGS):
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = sanitize_tool_args(value)
        return sanitized
    elif isinstance(args, list):
        return [sanitize_tool_args(item) for item in args]
    elif isinstance(args, str):
        # Redact known credential patterns
        s = _BEARER_RE.sub(r"\1[REDACTED]", args)
        s = _GH_TOKEN_RE.sub("[REDACTED]", s)
        s = _OPENAI_KEY_RE.sub("[REDACTED]", s)
        s = _PEM_PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", s)
        return s
    return args


def build_tool_summary(name: str, args: Dict[str, Any]) -> str:
    """Generate a concise, sanitized one-line summary for a tool call."""
    if not isinstance(args, dict):
        return name

    sanitized = sanitize_tool_args(args)
    if name == "run_command":
        cmd = str(sanitized.get("CommandLine", ""))
        return f"$ {cmd}" if cmd else "run_command"
    elif name in ("view_file", "write_to_file", "replace_file_content"):
        path = sanitized.get("TargetFile") or sanitized.get("AbsolutePath") or ""
        return f"{name} {path}" if path else name
    elif name in ("list_dir", "find_by_name"):
        d = sanitized.get("DirectoryPath") or sanitized.get("SearchDirectory") or ""
        pat = sanitized.get("Pattern", "")
        if pat:
            return f"{name} {d} ({pat})"
        return f"{name} {d}" if d else name
    elif name == "grep_search":
        q = sanitized.get("Query", "")
        p = sanitized.get("SearchPath", "")
        return f"grep '{q}' in {p}"
    
    # Generic representation with compact args
    items = []
    for k, v in list(sanitized.items())[:3]:
        v_str = str(v)
        if len(v_str) > 30:
            v_str = v_str[:27] + "..."
        items.append(f"{k}={v_str}")
    return f"{name}({', '.join(items)})" if items else name


def is_compaction_event(data: Dict[str, Any]) -> bool:
    """Detect whether a transcript JSON record represents an explicit native context compaction."""
    msg_type = str(data.get("type", "")).upper()
    event_type = str(data.get("event_type", "")).upper()
    if msg_type in ("CHECKPOINT", "COMPACTION", "CONTEXT_RESET", "SESSION_RESET", "SESSION_CONTINUATION"):
        return True
    if event_type in ("CHECKPOINT", "COMPACTION", "CONTEXT_RESET", "SESSION_RESET", "SESSION_CONTINUATION"):
        return True
    if data.get("compaction") is True or data.get("is_compaction") is True:
        return True
    return False


@dataclass
class ConversationSummary:
    """Metadata summary of a discovered Antigravity conversation."""

    conversation_id: str
    source: str
    title: str = ""
    preview: str = ""
    last_user_prompt: str = ""
    workspace_paths: List[str] = field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    transcript_path: Optional[str] = None
    artifact_directory: Optional[str] = None
    transcript_size_bytes: int = 0
    message_count: int = 0
    user_turn_count: int = 0
    tool_call_count: int = 0
    compaction_count: int = 0
    model_name: Optional[str] = None
    healthy: bool = True
    error: Optional[str] = None

    @property
    def workspace_path(self) -> str:
        return self.workspace_paths[0] if self.workspace_paths else ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["turn_count"] = self.user_turn_count
        d["file_size_bytes"] = self.transcript_size_bytes
        d["workspace_path"] = self.workspace_path
        d["first_prompt"] = self.last_user_prompt or self.preview
        return d


@dataclass
class ConversationMessage:
    """Normalized, user-visible message from a conversation transcript."""

    sequence: int
    timestamp: Optional[str]
    type: str  # "user", "assistant", "tool", "checkpoint", "compaction", "system_event", "unknown"
    visible_text: str = ""
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    sanitized_tool_summary: Optional[str] = None
    status: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def content(self) -> str:
        return self.visible_text

    @property
    def role(self) -> str:
        return "user" if self.type == "user" else "assistant"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["content"] = self.visible_text
        d["role"] = self.role
        if self.type == "tool":
            d["tool_calls"] = [{
                "tool_name": self.tool_name,
                "sanitized_summary": self.sanitized_tool_summary,
                "args": self.tool_args or {},
                "status": self.status,
            }]
        else:
            d["tool_calls"] = []
        return d



@dataclass
class ConversationContextState:
    """Visibility into Antigravity native context management and compaction state."""

    conversation_id: str
    source: str
    context_used: Optional[int] = None
    context_limit: Optional[int] = None
    usage_ratio: Optional[float] = None
    auto_compaction_supported: bool = True
    compaction_count: Optional[int] = None
    last_compaction_at: Optional[str] = None
    state_source: str = "unavailable"
    approximate: bool = False
    latest_compaction_summary: Optional[str] = None
    tool_calls_count: int = 0
    turn_count: int = 0
    file_size_bytes: int = 0

    @property
    def has_compaction(self) -> bool:
        return bool(self.compaction_count and self.compaction_count > 0)

    @property
    def auto_compaction_enabled(self) -> bool:
        return self.auto_compaction_supported

    @property
    def managed_by_agy(self) -> bool:
        return True

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["has_compaction"] = self.has_compaction
        d["auto_compaction_enabled"] = self.auto_compaction_enabled
        d["managed_by_agy"] = self.managed_by_agy
        return d


@dataclass
class AgyCapabilities:
    """Detected runtime capabilities of the local Antigravity CLI (`agy`)."""

    available: bool = False
    path: Optional[str] = None
    version: Optional[str] = None
    resume_supported: bool = False
    desktop_import_supported: bool = False
    context_status_supported: bool = False
    manual_compact_supported: bool = False
    remote_control_supported: bool = False

    @property
    def supports_resume(self) -> bool:
        return self.resume_supported

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["supports_resume"] = self.resume_supported
        return d


# In-memory capability probe cache with TTL
_CAPABILITIES_CACHE: Optional[AgyCapabilities] = None
_CAPABILITIES_CACHE_TIME: float = 0.0
_CAPABILITIES_TTL_SECONDS = 60.0


def clear_capabilities_cache() -> None:
    """Reset cached agy capabilities (useful for tests and dynamic updates)."""
    global _CAPABILITIES_CACHE, _CAPABILITIES_CACHE_TIME
    _CAPABILITIES_CACHE = None
    _CAPABILITIES_CACHE_TIME = 0.0


def detect_agy_capabilities(agy_path_override: Optional[str] = None, bypass_cache: bool = False) -> AgyCapabilities:
    """Probes the local system for the `agy` executable and determines supported features."""
    global _CAPABILITIES_CACHE, _CAPABILITIES_CACHE_TIME

    now = time.time()
    if not bypass_cache and _CAPABILITIES_CACHE is not None and (now - _CAPABILITIES_CACHE_TIME) < _CAPABILITIES_TTL_SECONDS:
        return _CAPABILITIES_CACHE

    agy_bin = agy_path_override or os.getenv("AGY_PATH")
    if not agy_bin:
        agy_bin = shutil.which("agy")

    if not agy_bin:
        # Check standard fallback locations
        home = Path.home()
        candidates = [
            home / ".local" / "bin" / "agy",
            home / ".antigravity" / "bin" / "agy",
            home / ".gemini" / "antigravity" / "bin" / "agy",
            home / "bin" / "agy",
            Path("/usr/local/bin/agy"),
            Path("/opt/homebrew/bin/agy"),
        ]
        for c in candidates:
            if c.is_file() and os.access(c, os.X_OK):
                agy_bin = str(c)
                break

    if not agy_bin:
        caps = AgyCapabilities(available=False)
        _CAPABILITIES_CACHE = caps
        _CAPABILITIES_CACHE_TIME = now
        return caps

    # Probe version
    ver_str = None
    try:
        res = subprocess.run([agy_bin, "--version"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            first = res.stdout.strip().splitlines()
            if first:
                raw_ver = first[0].strip()
                import re
                m = re.search(r"(\d+\.\d+(?:\.\d+)?(?:-[0-9A-Za-z.-]+)?)", raw_ver)
                ver_str = m.group(1) if m else raw_ver
    except Exception:
        pass

    # Probe help output
    help_text = ""
    try:
        res = subprocess.run([agy_bin, "--help"], capture_output=True, text=True, timeout=3)
        help_text = (res.stdout + "\n" + res.stderr).lower()
    except Exception:
        pass

    resume_supported = "resume" in help_text or "--resume" in help_text or "-r " in help_text
    desktop_import = "import" in help_text or "import-desktop" in help_text
    context_status = "context" in help_text
    manual_compact = "compact" in help_text
    remote_control = "remote-control" in help_text or "--remote-control" in help_text

    caps = AgyCapabilities(
        available=True,
        path=agy_bin,
        version=ver_str,
        resume_supported=resume_supported,
        desktop_import_supported=desktop_import,
        context_status_supported=context_status,
        manual_compact_supported=manual_compact,
        remote_control_supported=remote_control,
    )
    _CAPABILITIES_CACHE = caps
    _CAPABILITIES_CACHE_TIME = now
    return caps


class ConversationStore:
    """Centralized conversation discovery, indexing, and normalization engine."""

    def __init__(
        self,
        roots_override: Optional[Dict[str, List[Path]]] = None,
        desktop_root: Optional[Union[str, Path]] = None,
        ide_root: Optional[Union[str, Path]] = None,
        cli_root: Optional[Union[str, Path]] = None,
    ):
        if roots_override is None:
            roots_override = {}
        else:
            roots_override = dict(roots_override)

        if desktop_root:
            roots_override[SOURCE_DESKTOP] = [Path(desktop_root)]
        if ide_root:
            roots_override[SOURCE_IDE] = [Path(ide_root)]
        if cli_root:
            roots_override[SOURCE_CLI] = [Path(cli_root)]

        self._roots_override = roots_override or None
        # Cache for indexed conversation summaries: key=(transcript_path, mtime, size)
        self._summary_cache: Dict[Tuple[str, float, int], ConversationSummary] = {}

    def get_roots(self, source: str) -> List[Path]:
        """Return candidate data directory roots for the requested source."""
        if source not in VALID_SOURCES:
            raise ValueError(f"Invalid conversation source '{source}'. Must be one of: {sorted(VALID_SOURCES)}")

        if self._roots_override and source in self._roots_override:
            return [p for p in self._roots_override[source] if p]

        home = Path.home()
        roots: List[Path] = []

        if source == SOURCE_DESKTOP:
            env_dir = os.getenv("ANTIAGENT_DESKTOP_DIR") or os.getenv("ANTIAGENT_ANTIGRAVITY_DIR")
            if env_dir:
                return [Path(env_dir)]
            roots.extend([
                home / ".gemini" / "antigravity",
                home / "Library" / "Application Support" / "Antigravity",
                home / ".config" / "Antigravity",
            ])
            if sys.platform == "win32":
                app_data = os.getenv("APPDATA")
                if app_data:
                    roots.append(Path(app_data) / "Antigravity")
        elif source == SOURCE_IDE:
            env_dir = os.getenv("ANTIAGENT_IDE_DIR")
            if env_dir:
                return [Path(env_dir)]
            roots.extend([
                home / ".gemini" / "antigravity-ide",
                home / "Library" / "Application Support" / "Antigravity IDE",
                home / ".antigravity-ide",
                home / ".config" / "Antigravity IDE",
            ])
            if sys.platform == "win32":
                app_data = os.getenv("APPDATA")
                if app_data:
                    roots.append(Path(app_data) / "Antigravity IDE")
        elif source == SOURCE_CLI:
            env_dir = os.getenv("ANTIAGENT_CLI_DIR") or os.getenv("AGY_DIR")
            if env_dir:
                return [Path(env_dir)]
            roots.extend([
                home / ".gemini" / "antigravity-cli",
                home / ".gemini" / "agy",
                home / ".config" / "agy",
                home / ".agy",
            ])

        return roots

    def get_primary_root(self, source: str) -> Optional[Path]:
        """Returns the first existing root for a source, or the default candidate."""
        for r in self.get_roots(source):
            if r.is_dir():
                return r
        roots = self.get_roots(source)
        return roots[0] if roots else None

    def get_sources_summary(self) -> Dict[str, Any]:
        """Return diagnostic source counts and availability for the Doctor engine."""
        summary: Dict[str, Any] = {}
        total = 0
        for src in (SOURCE_DESKTOP, SOURCE_IDE, SOURCE_CLI):
            convs = self.list_conversations_for_source(src, limit=1000)
            primary = self.get_primary_root(src)
            available = any(r.is_dir() for r in self.get_roots(src))
            cnt = len(convs)
            total += cnt
            summary[src] = {
                "count": cnt,
                "available": available,
                "path": str(primary) if primary else None,
            }
        summary["total"] = total
        return summary

    def _resolve_safe_path(self, base_dir: Union[str, Path], target_subpath: Union[str, Path]) -> Path:
        """Resolve a path safely, preventing directory traversal and symlink escapes."""
        base_resolved = Path(base_dir).resolve()
        target_resolved = (Path(base_dir) / target_subpath).resolve()
        try:
            target_resolved.relative_to(base_resolved)
        except ValueError:
            raise ValueError(f"Security: subpath '{target_subpath}' escapes base root '{base_resolved}'.")
        return target_resolved

    def _find_transcript_in_dir(self, conv_dir: Path) -> Optional[Path]:
        """Check known relative locations for transcript.jsonl within a conversation directory."""
        candidates = [
            conv_dir / ".system_generated" / "logs" / "transcript.jsonl",
            conv_dir / "transcript.jsonl",
            conv_dir / ".system_generated" / "logs" / "transcript_full.jsonl",
            conv_dir / "transcript_full.jsonl",
        ]
        for c in candidates:
            try:
                if c.is_file():
                    return c
            except Exception:
                pass
        return None

    def _read_summaries_from_sqlite(self, root: Path) -> Dict[str, Dict[str, Any]]:
        """Read cached conversation summaries from Antigravity's SQLite database if present."""
        db_path = root / "conversation_summaries.db"
        if not db_path.is_file():
            return {}

        results: Dict[str, Dict[str, Any]] = {}
        try:
            # Open in read-only URI mode to prevent any locking or modification
            uri = f"file:{db_path.resolve()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=2.0)
            cursor = conn.cursor()

            # Find table name: conversation_summaries or summaries
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cursor.fetchall()]
            target_table = "conversation_summaries" if "conversation_summaries" in tables else ("summaries" if "summaries" in tables else None)
            if not target_table:
                conn.close()
                return {}

            cursor.execute(f"PRAGMA table_info({target_table})")
            cols = [c[1] for c in cursor.fetchall()]
            title_col = "title" if "title" in cols else "preview"
            prev_col = "preview" if "preview" in cols else title_col
            step_col = "step_count" if "step_count" in cols else ("turn_count" if "turn_count" in cols else "NULL")
            mod_col = "last_modified_time" if "last_modified_time" in cols else "NULL"
            ws_col = "workspace_uris" if "workspace_uris" in cols else ("workspace_path" if "workspace_path" in cols else "NULL")
            model_col = "model_name" if "model_name" in cols else ("model" if "model" in cols else "NULL")

            cursor.execute(f"SELECT conversation_id, {title_col}, {prev_col}, {step_col}, {mod_col}, {ws_col}, {model_col} FROM {target_table}")
            for row in cursor.fetchall():
                cid, title, preview, step_count, last_mod, ws_uris, model = row
                ws_list: List[str] = []
                if ws_uris:
                    try:
                        parsed_ws = json.loads(ws_uris)
                        if isinstance(parsed_ws, list):
                            for u in parsed_ws:
                                clean_u = str(u).replace("file://", "")
                                ws_list.append(clean_u)
                        else:
                            ws_list.append(str(parsed_ws).replace("file://", ""))
                    except Exception:
                        ws_list.append(str(ws_uris).replace("file://", ""))
                clean_t = clean_user_prompt(title or "").strip()
                clean_p = clean_user_prompt(preview or "").strip()
                if re.match(r"^<[/a-zA-Z0-9_\-]+>$", clean_t):
                    clean_t = ""
                if re.match(r"^<[/a-zA-Z0-9_\-]+>$", clean_p):
                    clean_p = ""

                results[str(cid)] = {
                    "title": clean_t or clean_p or "",
                    "preview": clean_p or clean_t or "",
                    "step_count": step_count or 0,
                    "last_modified_time": str(last_mod) if last_mod else None,
                    "workspace_paths": ws_list,
                    "model_name": model if model else None,
                }
            conn.close()
        except Exception:
            pass
        return results

    def _index_conversation(
        self,
        source: str,
        conv_id: str,
        conv_dir: Path,
        db_metadata: Optional[Dict[str, Any]] = None,
    ) -> ConversationSummary:
        """Cheaply extract metadata summary by inspecting file stats and bounded head/tail."""
        validate_conversation_id(conv_id)
        transcript_file = self._find_transcript_in_dir(conv_dir)
        artifact_dir = conv_dir if conv_dir.is_dir() else None

        if not transcript_file or not transcript_file.is_file():
            # Check if conv_dir is in conversations/ and brain/ has the real session folder
            if conv_dir.parent.name == "conversations" or conv_dir.name == "conversations":
                base_root = conv_dir.parent if conv_dir.name == "conversations" else conv_dir.parent.parent
                brain_candidate = base_root / "brain" / conv_id
                if brain_candidate.is_dir():
                    tf = self._find_transcript_in_dir(brain_candidate)
                    if tf and tf.is_file():
                        transcript_file = tf
                        conv_dir = brain_candidate
                        artifact_dir = conv_dir

        if not transcript_file or not transcript_file.is_file():
            # Directory exists but no transcript file found (e.g. empty or binary-only session)
            st = conv_dir.stat() if conv_dir.exists() else None
            mtime_iso = (
                datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
                if st
                else None
            )
            title = db_metadata.get("title", "") if db_metadata else ""
            preview = db_metadata.get("preview", "") if db_metadata else ""
            ws = db_metadata.get("workspace_paths", []) if db_metadata else []
            return ConversationSummary(
                conversation_id=conv_id,
                source=source,
                title=title or f"Session {conv_id[:8]}",
                preview=preview,
                last_user_prompt="",
                workspace_paths=ws,
                created_at=mtime_iso,
                updated_at=mtime_iso,
                transcript_path=None,
                artifact_directory=str(artifact_dir) if artifact_dir else None,
                transcript_size_bytes=0,
                message_count=db_metadata.get("step_count", 0) if db_metadata else 0,
                model_name=db_metadata.get("model_name") if db_metadata else None,
                healthy=True,
            )

        st = transcript_file.stat()
        cache_key = (str(transcript_file), st.st_mtime, st.st_size)
        if cache_key in self._summary_cache:
            cached = self._summary_cache[cache_key]
            # Refresh source in case of duplicate IDs across distinct sources
            if cached.source == source:
                return cached

        # Fast head read (<8KB) to find first prompt, title, created_at, workspace
        first_prompt = ""
        first_created = None
        model_name = (db_metadata.get("model_name") if db_metadata else None) or None
        title = db_metadata.get("title", "") if db_metadata else ""
        preview = db_metadata.get("preview", "") if db_metadata else ""
        if title and re.match(r"^<[/a-zA-Z0-9_\-]+>$", title.strip()):
            title = ""
        if preview and re.match(r"^<[/a-zA-Z0-9_\-]+>$", preview.strip()):
            preview = ""
        ws_paths = list(db_metadata.get("workspace_paths", [])) if db_metadata else []

        try:
            with open(transcript_file, "rb") as f:
                head_bytes = f.read(8192)
                head_text = head_bytes.decode("utf-8", errors="ignore")
                for line in head_text.splitlines():
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                        if not first_created and obj.get("created_at"):
                            first_created = obj.get("created_at")
                        if not model_name:
                            m = obj.get("model_name") or obj.get("model")
                            if m:
                                model_name = str(m)
                        if not ws_paths:
                            w = obj.get("workspacePaths") or obj.get("workspace_paths") or obj.get("workspace")
                            if w:
                                if isinstance(w, list):
                                    ws_paths.extend([str(x).replace("file://", "") for x in w if x])
                                else:
                                    ws_paths.append(str(w).replace("file://", ""))
                            for tc in obj.get("tool_calls", []):
                                tc_args = tc.get("args", {})
                                cwd = tc_args.get("Cwd") or tc_args.get("SearchDirectory")
                                if cwd and str(cwd) not in ws_paths:
                                    ws_paths.append(str(cwd))
                        if obj.get("type") == "USER_INPUT" and obj.get("content"):
                            cleaned = clean_user_prompt(obj.get("content", ""))
                            if cleaned and not first_prompt:
                                first_prompt = cleaned
                                break
                    except Exception:
                        continue
        except Exception:
            pass

        # Fast tail read (<16KB) to find latest user prompt, step count approximation, and compactions
        last_prompt = ""
        last_created = None
        last_step_idx = 0
        compactions_found = 0

        try:
            with open(transcript_file, "rb") as f:
                f.seek(0, 2)
                fsize = f.tell()
                tail_size = min(fsize, 16384)
                f.seek(fsize - tail_size)
                tail_bytes = f.read(tail_size)
                tail_text = tail_bytes.decode("utf-8", errors="ignore")
                for line in reversed(tail_text.splitlines()):
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                        if not last_created and obj.get("created_at"):
                            last_created = obj.get("created_at")
                        if not last_step_idx and obj.get("step_index") is not None:
                            last_step_idx = int(obj.get("step_index"))
                        if is_compaction_event(obj):
                            compactions_found += 1
                        if obj.get("type") == "USER_INPUT" and obj.get("content") and not last_prompt:
                            last_prompt = clean_user_prompt(obj.get("content", ""))
                    except Exception:
                        continue
        except Exception:
            pass

        if not title:
            # Check for task.md in directory
            task_file = conv_dir / "task.md"
            if task_file.is_file():
                try:
                    for line in task_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                        if line.startswith("# "):
                            title = line.lstrip("# ").strip()
                            break
                except Exception:
                    pass

        if not title:
            title = first_prompt.splitlines()[0][:60] if first_prompt else f"Session {conv_id[:8]}"

        if not preview:
            preview = last_prompt.splitlines()[0][:90] if last_prompt else title

        updated_at = (
            last_created
            or (db_metadata.get("last_modified_time") if db_metadata else None)
            or datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        )
        created_at = (
            first_created
            or datetime.fromtimestamp(st.st_ctime, tz=timezone.utc).isoformat()
        )

        approx_messages = max(last_step_idx + 1, db_metadata.get("step_count", 0) if db_metadata else 0)

        summary = ConversationSummary(
            conversation_id=conv_id,
            source=source,
            title=title,
            preview=preview,
            last_user_prompt=last_prompt or first_prompt,
            workspace_paths=ws_paths,
            created_at=created_at,
            updated_at=updated_at,
            transcript_path=str(transcript_file),
            artifact_directory=str(artifact_dir) if artifact_dir else None,
            transcript_size_bytes=st.st_size,
            message_count=approx_messages,
            user_turn_count=max(1, approx_messages // 4),
            tool_call_count=max(0, (approx_messages * 3) // 4),
            compaction_count=compactions_found,
            model_name=model_name,
            healthy=True,
        )

        self._summary_cache[cache_key] = summary
        return summary

    def list_conversations_for_source(self, source: str, limit: int = 1000) -> List[ConversationSummary]:
        """Discover and index conversations from all candidate roots for a given source."""
        results: List[ConversationSummary] = []
        seen_ids: Set[str] = set()

        for root in self.get_roots(source):
            if not root.is_dir():
                continue

            db_meta = self._read_summaries_from_sqlite(root)
            conv_search_dirs = [root / "brain", root / "conversations"]

            for sdir in conv_search_dirs:
                if not sdir.is_dir():
                    continue
                try:
                    for child in sdir.iterdir():
                        if child.name.startswith(".") or child.name == "tempmediaStorage":
                            continue

                        cid = child.name
                        # Support standalone files like <id>.jsonl, <id>.db, <id>.pb
                        if child.is_file():
                            if child.suffix in (".jsonl", ".db", ".pb"):
                                cid = child.stem
                            else:
                                continue

                        if cid in seen_ids or not _SAFE_ID_RE.match(cid):
                            continue

                        # Verify path safety against root
                        try:
                            self._resolve_safe_path(root, child.relative_to(root))
                        except Exception:
                            continue

                        # For directories, verify it's a conversation directory
                        if child.is_dir():
                            has_transcript = bool(self._find_transcript_in_dir(child))
                            has_db_entry = cid in db_meta
                            has_sys_gen = (child / ".system_generated").is_dir()
                            if not (has_transcript or has_db_entry or has_sys_gen):
                                continue

                        seen_ids.add(cid)
                        meta_for_id = db_meta.get(cid)
                        conv_folder = (root / "brain" / cid) if (root / "brain" / cid).is_dir() else (child if child.is_dir() else child.parent)
                        summary = self._index_conversation(
                            source=source,
                            conv_id=cid,
                            conv_dir=conv_folder,
                            db_metadata=meta_for_id,
                        )
                        results.append(summary)
                        if len(results) >= limit:
                            break
                except Exception:
                    continue

        return results

    def list_conversations(
        self,
        source: str = "all",
        workspace: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        bypass_cache: bool = False,
    ) -> Tuple[List[ConversationSummary], int]:
        """Search, filter, and paginate conversations across requested sources."""
        if bypass_cache:
            self._summary_cache.clear()

        all_summaries: List[ConversationSummary] = []
        sources_to_query = [source] if source in VALID_SOURCES else [SOURCE_DESKTOP, SOURCE_IDE, SOURCE_CLI]

        for s in sources_to_query:
            all_summaries.extend(self.list_conversations_for_source(s))

        # Filter by workspace substring or path
        if workspace:
            ws_query = os.path.abspath(os.path.expanduser(workspace)).lower()
            filtered = []
            for item in all_summaries:
                match = False
                for p in item.workspace_paths:
                    if ws_query in os.path.abspath(p).lower() or ws_query in p.lower():
                        match = True
                        break
                if match:
                    filtered.append(item)
            all_summaries = filtered

        # Filter by free-text search (title, preview, prompt, ID)
        if search:
            q = search.lower().strip()
            all_summaries = [
                item for item in all_summaries
                if q in item.title.lower()
                or q in item.preview.lower()
                or q in item.last_user_prompt.lower()
                or q in item.conversation_id.lower()
            ]

        # Sort newest-first based on updated_at
        def _sort_key(item: ConversationSummary) -> str:
            return item.updated_at or item.created_at or ""

        all_summaries.sort(key=_sort_key, reverse=True)
        total = len(all_summaries)
        paginated = all_summaries[offset : offset + limit]
        return paginated, total

    def get_conversation(
        self,
        conversation_id: str,
        source: Optional[str] = None,
        allow_ambiguous: bool = False,
    ) -> Optional[Tuple[str, ConversationSummary]]:
        """Look up a conversation by ID. If source is specified, checks that source first.
        
        If source is omitted and the ID matches sessions in multiple distinct sources,
        raises AmbiguousConversationError unless allow_ambiguous is True.
        """
        validate_conversation_id(conversation_id)
        sources_to_check = [source] if source in VALID_SOURCES else [SOURCE_DESKTOP, SOURCE_IDE, SOURCE_CLI]

        matches: List[Tuple[str, ConversationSummary]] = []
        seen_keys: Set[Tuple[str, str]] = set()

        for s in sources_to_check:
            for root in self.get_roots(s):
                if not root.is_dir():
                    continue
                # Check brain/<id> or conversations/<id>
                candidates = [
                    root / "brain" / conversation_id,
                    root / "conversations" / f"{conversation_id}.jsonl",
                    root / "conversations" / f"{conversation_id}.db",
                    root / "conversations" / conversation_id,
                    root / conversation_id,
                ]
                for c in candidates:
                    if c.exists():
                        try:
                            self._resolve_safe_path(root, c.relative_to(root))
                            if (s, conversation_id) in seen_keys:
                                continue
                            db_meta = self._read_summaries_from_sqlite(root).get(conversation_id)
                            conv_folder = (root / "brain" / conversation_id) if (root / "brain" / conversation_id).is_dir() else (c if c.is_dir() else c.parent)
                            summary = self._index_conversation(s, conversation_id, conv_folder, db_meta)
                            matches.append((s, summary))
                            seen_keys.add((s, conversation_id))
                            break
                        except Exception:
                            continue
                if source and any(m[0] == source for m in matches):
                    break

        if not matches and len(conversation_id) >= 6:
            # Fall back to prefix matching (e.g. 8-char short IDs from table display)
            for s in sources_to_check:
                for root in self.get_roots(s):
                    if not root.is_dir():
                        continue
                    db_summaries = self._read_summaries_from_sqlite(root)
                    for search_dir in [root / "brain", root / "conversations"]:
                        if not search_dir.is_dir():
                            continue
                        try:
                            for entry in search_dir.iterdir():
                                if entry.name.startswith(".") or entry.name == "tempmediaStorage":
                                    continue
                                if entry.is_file() and entry.suffix not in (".jsonl", ".db", ".pb", ""):
                                    continue
                                full_id = entry.stem if entry.is_file() else entry.name
                                if not full_id.startswith(conversation_id):
                                    continue
                                if (s, full_id) in seen_keys:
                                    continue
                                if not _SAFE_ID_RE.match(full_id):
                                    continue
                                try:
                                    self._resolve_safe_path(root, entry.relative_to(root))
                                    db_meta = db_summaries.get(full_id)
                                    conv_folder = (root / "brain" / full_id) if (root / "brain" / full_id).is_dir() else (entry if entry.is_dir() else entry.parent)
                                    summary = self._index_conversation(s, full_id, conv_folder, db_meta)
                                    matches.append((s, summary))
                                    seen_keys.add((s, full_id))
                                    break
                                except Exception:
                                    pass
                        except Exception:
                            continue
                    if source and any(m[0] == source for m in matches):
                        break

        if not matches:
            return None

        # Check for ambiguity across distinct sources
        distinct_sources = sorted(list({m[0] for m in matches}))
        if len(distinct_sources) > 1 and not source:
            if not allow_ambiguous:
                raise AmbiguousConversationError(conversation_id, distinct_sources)
            # Pick most recently updated
            matches.sort(key=lambda m: m[1].updated_at or "", reverse=True)
            return matches[0]

        return matches[0]

    def get_conversation_messages(
        self,
        conversation_id: str,
        source: str,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[ConversationMessage], int, ConversationContextState]:
        """Stream and normalize transcript events into clean, user-visible messages."""
        validate_conversation_id(conversation_id)
        lookup = self.get_conversation(conversation_id, source=source)
        if not lookup:
            raise FileNotFoundError(f"Conversation '{conversation_id}' not found in source '{source}'.")

        _, summary = lookup
        transcript_path = summary.transcript_path
        if not transcript_path or not os.path.isfile(transcript_path):
            state = ConversationContextState(
                conversation_id=conversation_id,
                source=source,
                auto_compaction_supported=True,
                compaction_count=0,
                state_source="unavailable",
            )
            return [], 0, state

        messages: List[ConversationMessage] = []
        compaction_events: List[Dict[str, Any]] = []
        seq = 0

        try:
            with open(transcript_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        # Gracefully ignore malformed or incomplete JSON lines
                        continue

                    # Check for native compaction/checkpoint events
                    if is_compaction_event(data):
                        compaction_events.append(data)
                        content = data.get("content") or "Native context compaction / checkpoint completed."
                        # Summarize checkpoint prompt if verbose
                        c_lines = content.strip().splitlines()
                        summary_text = c_lines[0] if c_lines else "Context compaction"
                        messages.append(
                            ConversationMessage(
                                sequence=seq,
                                timestamp=data.get("created_at"),
                                type="compaction",
                                visible_text=summary_text,
                                status=data.get("status", "DONE"),
                                metadata={
                                    "step_index": data.get("step_index"),
                                    "event_type": data.get("type") or data.get("event_type"),
                                    "full_content": content[:1000],
                                },
                            )
                        )
                        seq += 1
                        continue

                    msg_type = data.get("type")
                    content = data.get("content") or ""

                    # 1. User messages
                    if msg_type == "USER_INPUT" and content:
                        clean_prompt = clean_user_prompt(content)
                        if clean_prompt:
                            messages.append(
                                ConversationMessage(
                                    sequence=seq,
                                    timestamp=data.get("created_at"),
                                    type="user",
                                    visible_text=clean_prompt,
                                    status=data.get("status", "DONE"),
                                )
                            )
                            seq += 1

                    # 2. Planner / Assistant output & tool calls
                    elif msg_type == "PLANNER_RESPONSE":
                        # CRITICAL: NEVER expose hidden reasoning (thinking). Only render content.
                        tool_calls = data.get("tool_calls") or []

                        # If there is direct assistant speech/prose, output it as assistant
                        if content and isinstance(content, str) and content.strip():
                            # Strictly filter out internal chain-of-thought tags
                            clean_text = re.sub(r"<(?:thought|thinking)>[\s\S]*?</(?:thought|thinking)>", "", content, flags=re.IGNORECASE).strip()
                            if clean_text:
                                messages.append(
                                    ConversationMessage(
                                        sequence=seq,
                                        timestamp=data.get("created_at"),
                                        type="assistant",
                                        visible_text=clean_text,
                                        status=data.get("status", "DONE"),
                                    )
                                )
                                seq += 1

                        # Render tool calls
                        for tc in tool_calls:
                            t_name = tc.get("name", "tool")
                            t_args = tc.get("args") or {}
                            sanitized = sanitize_tool_args(t_args)
                            summary_str = build_tool_summary(t_name, sanitized)
                            messages.append(
                                ConversationMessage(
                                    sequence=seq,
                                    timestamp=data.get("created_at"),
                                    type="tool",
                                    visible_text="",
                                    tool_name=t_name,
                                    tool_args=sanitized,
                                    sanitized_tool_summary=summary_str,
                                    status=data.get("status", "DONE"),
                                )
                            )
                            seq += 1

                    # 3. Generic system/tool events
                    elif msg_type in ("SYSTEM_MESSAGE", "GENERIC", "ERROR_MESSAGE"):
                        if content and not data.get("is_hidden"):
                            # If following a tool call, attach result status to that tool call
                            if msg_type == "GENERIC" and messages and messages[-1].type == "tool":
                                if "The command exited with code 0" in content:
                                    messages[-1].status = "SUCCESS"
                                elif "exited with code" in content:
                                    messages[-1].status = "FAILED"
                                messages[-1].metadata["result_snippet"] = content.strip()[:200]
                                continue

                            # Skip timestamp header lines in system preview if present
                            lines = [l.strip() for l in content.strip().splitlines() if l.strip()]
                            preview_text = ""
                            for l in lines:
                                if not l.startswith("Created At:") and not l.startswith("Completed At:"):
                                    preview_text = l[:200]
                                    break
                            if not preview_text and lines:
                                preview_text = lines[0][:200]

                            if preview_text:
                                messages.append(
                                    ConversationMessage(
                                        sequence=seq,
                                        timestamp=data.get("created_at"),
                                        type="system_event",
                                        visible_text=preview_text,
                                        status=data.get("status", "DONE"),
                                    )
                                )
                                seq += 1
        except Exception as e:
            # Tolerant against partial reads
            pass

        total_messages = len(messages)
        paginated_messages = messages[offset : offset + limit]

        # Context state calculation
        last_compaction_ts = compaction_events[-1].get("created_at") if compaction_events else None
        latest_summary = compaction_events[-1].get("content") if compaction_events else None
        tool_count = sum(1 for m in messages if m.type == "tool")
        user_turn_count = sum(1 for m in messages if m.type == "user")
        transcript_bytes = Path(transcript_path).stat().st_size if Path(transcript_path).exists() else 0

        context_state = ConversationContextState(
            conversation_id=conversation_id,
            source=source,
            auto_compaction_supported=True,
            compaction_count=len(compaction_events),
            last_compaction_at=last_compaction_ts,
            state_source="transcript_events" if compaction_events else "unavailable",
            approximate=False,
            latest_compaction_summary=latest_summary,
            tool_calls_count=tool_count,
            turn_count=user_turn_count,
            file_size_bytes=transcript_bytes,
        )

        return paginated_messages, total_messages, context_state

    def export_conversation(self, conversation_id: str, source: str, format: str = "markdown") -> str:
        """Export conversation transcript in clean Markdown or JSON format without hidden reasoning."""
        validate_conversation_id(conversation_id)
        lookup = self.get_conversation(conversation_id, source=source)
        if not lookup:
            raise FileNotFoundError(f"Conversation '{conversation_id}' not found in source '{source}'.")

        _, summary = lookup
        messages, total, ctx_state = self.get_conversation_messages(
            conversation_id, source=source, limit=10000, offset=0
        )

        if format.lower() == "json":
            export_dict = {
                "summary": summary.to_dict(),
                "context_state": ctx_state.to_dict(),
                "total_messages": total,
                "messages": [m.to_dict() for m in messages],
            }
            return json.dumps(export_dict, indent=2)

        # Default Markdown format
        lines = [
            f"# {summary.title or 'Antigravity Conversation'}",
            "",
            f"- **Conversation ID:** `{summary.conversation_id}`",
            f"- **Source:** `{summary.source}`",
            f"- **Workspaces:** {', '.join(summary.workspace_paths) if summary.workspace_paths else '(none)'}",
            f"- **Created:** {summary.created_at or '(unknown)'}",
            f"- **Updated:** {summary.updated_at or '(unknown)'}",
            f"- **Transcript Size:** {round(summary.transcript_size_bytes / 1024, 1)} KB",
            f"- **Native Compactions:** {ctx_state.compaction_count or 0}",
            "",
            "---",
            "",
        ]

        for m in messages:
            ts = f" *({m.timestamp[:19].replace('T', ' ')})*" if m.timestamp else ""
            if m.type == "user":
                lines.append(f"### 👤 User{ts}\n")
                lines.append(m.visible_text)
                lines.append("\n")
            elif m.type == "assistant":
                lines.append(f"### 🤖 Antigravity Assistant{ts}\n")
                lines.append(m.visible_text)
                lines.append("\n")
            elif m.type == "tool":
                lines.append(f"**⚡ Tool Call:** `{m.sanitized_tool_summary}`{ts}\n")
            elif m.type == "compaction":
                lines.append(f"> 📦 **Native Context Compaction**{ts}\n> {m.visible_text}\n")
            elif m.type == "system_event":
                lines.append(f"> ⚙️ *System Event: {m.visible_text}*{ts}\n")

        return "\n".join(lines)

    def get_context_state(self, conversation_id: str, source: str) -> ConversationContextState:
        """Query or derive native context window state and compaction history."""
        validate_conversation_id(conversation_id)
        caps = detect_agy_capabilities()

        # If native CLI context status is supported, try querying it
        if caps.available and caps.context_status_supported:
            agy_bin = shutil.which("agy") or os.getenv("AGY_PATH")
            if agy_bin:
                try:
                    res = subprocess.run(
                        [agy_bin, "context", conversation_id, "--json"],
                        capture_output=True,
                        text=True,
                        timeout=3,
                    )
                    if res.returncode == 0 and res.stdout.strip():
                        c_data = json.loads(res.stdout)
                        return ConversationContextState(
                            conversation_id=conversation_id,
                            source=source,
                            context_used=c_data.get("context_used"),
                            context_limit=c_data.get("context_limit"),
                            usage_ratio=c_data.get("usage_ratio"),
                            auto_compaction_supported=True,
                            compaction_count=c_data.get("compaction_count"),
                            last_compaction_at=c_data.get("last_compaction_at"),
                            state_source="agy_cli",
                            approximate=False,
                        )
                except Exception:
                    pass

        # Fallback to inspecting structured compaction events from transcript
        try:
            _, _, ctx_state = self.get_conversation_messages(conversation_id, source=source, limit=10, offset=0)
            return ctx_state
        except Exception:
            return ConversationContextState(
                conversation_id=conversation_id,
                source=source,
                auto_compaction_supported=True,
                compaction_count=0,
                state_source="unavailable",
                approximate=False,
            )

    def resume_conversation(
        self,
        conversation_id: str,
        source: str,
        launch: bool = True,
    ) -> Dict[str, Any]:
        """Safely resume a conversation using official Antigravity interfaces."""
        validate_conversation_id(conversation_id)
        if source not in VALID_SOURCES:
            raise ValueError(f"Invalid conversation source '{source}'. Must be one of: {sorted(VALID_SOURCES)}")

        lookup = self.get_conversation(conversation_id, source=source)
        if not lookup:
            return {
                "ok": False,
                "supported": False,
                "error": f"Conversation '{conversation_id}' was not found in source '{source}'.",
            }

        caps = detect_agy_capabilities()

        # 1. Desktop and IDE conversations
        if source in (SOURCE_DESKTOP, SOURCE_IDE):
            if not caps.desktop_import_supported:
                source_label = "Desktop" if source == SOURCE_DESKTOP else "IDE"
                return {
                    "ok": False,
                    "supported": False,
                    "source": source,
                    "conversation_id": conversation_id,
                    "error": f"Direct CLI resume is not supported for {source_label} conversations.",
                    "instructions": (
                        f"To continue this {source_label} session, open Google Antigravity {source_label} "
                        "and select the conversation from your recent chats."
                    ),
                }

        # 2. CLI conversations
        if source == SOURCE_CLI:
            if not caps.available:
                return {
                    "ok": False,
                    "supported": False,
                    "source": source,
                    "conversation_id": conversation_id,
                    "error": "Antigravity CLI ('agy') is not installed or available on this system.",
                    "instructions": "Install the Antigravity CLI ('agy') and ensure it is on your PATH to resume CLI conversations.",
                }
            if not caps.resume_supported:
                return {
                    "ok": False,
                    "supported": False,
                    "source": source,
                    "conversation_id": conversation_id,
                    "error": f"The installed 'agy' CLI ({caps.version or 'version unknown'}) does not support the 'resume' command.",
                    "instructions": "Please update your Antigravity CLI to a version supporting session resume.",
                }

            agy_bin = caps.path or shutil.which("agy") or os.getenv("AGY_PATH")
            if not agy_bin:
                return {
                    "ok": False,
                    "supported": False,
                    "error": "Could not determine agy executable location.",
                }

            # Safe argument-array execution (NEVER shell=True)
            cmd = [agy_bin, "resume", conversation_id]
            cmd_str = f"agy --resume {conversation_id}" if "agy" in agy_bin else " ".join(cmd)
            if not launch:
                return {
                    "ok": True,
                    "supported": True,
                    "source": source,
                    "conversation_id": conversation_id,
                    "command": cmd_str,
                    "command_args": cmd,
                }

            try:
                # Spawn or attach session
                proc = subprocess.Popen(cmd)
                return {
                    "ok": True,
                    "supported": True,
                    "source": source,
                    "conversation_id": conversation_id,
                    "pid": proc.pid,
                    "command": cmd_str,
                    "command_args": cmd,
                }
            except Exception as e:
                return {
                    "ok": False,
                    "supported": True,
                    "error": f"Failed to execute agy resume: {e}",
                }

        return {
            "ok": False,
            "supported": False,
            "error": f"Resume not supported for source '{source}'.",
        }



# Singleton conversation store
_GLOBAL_STORE: Optional[ConversationStore] = None


def reset_conversation_store() -> None:
    """Reset the singleton store instance (useful for test isolation)."""
    global _GLOBAL_STORE
    _GLOBAL_STORE = None


def get_conversation_store(reset: bool = False) -> ConversationStore:
    """Return the singleton instance of the ConversationStore."""
    global _GLOBAL_STORE
    if _GLOBAL_STORE is None or reset:
        _GLOBAL_STORE = ConversationStore()
    return _GLOBAL_STORE

