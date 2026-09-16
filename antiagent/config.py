"""Configuration loader and schema for AntiAgent."""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from antiagent.constants import (
    DEFAULT_PROFILE,
    PROFILE_AUTONOMOUS,
    PROFILE_BALANCED,
    PROFILE_PARANOID,
)


@dataclass
class AntiAgentConfig:
    """AntiAgent configuration options."""

    # Safety profile: "balanced", "paranoid", or "autonomous"
    profile: str = DEFAULT_PROFILE

    # Auto-approve strictly read-only inspection commands (e.g. ls, git status, cat)
    auto_approve_reads: bool = True

    # Auto-approve routine dev build/test commands within workspace (e.g. npm test, pytest)
    auto_approve_dev_commands: bool = True

    # "Approve for Me" context-aware auto-review engine (evaluates intent coherence & vulnerabilities)
    auto_review: bool = True

    # LLM Supervisor provider: "native" (Antigravity Zero-API Mode), "gemini", "openai", "ollama", or "offline"
    provider: str = "native"

    # Model name to use for the AI reviewer
    model: str = ""

    # API key override (if empty, checks GEMINI_API_KEY / OPENAI_API_KEY env vars)
    api_key: Optional[str] = None

    # Base URL for Ollama or custom OpenAI-compatible endpoint
    endpoint_url: Optional[str] = None

    # Custom regex rules
    custom_allow_patterns: List[str] = field(default_factory=list)
    custom_deny_patterns: List[str] = field(default_factory=list)

    # Audit logging
    audit_enabled: bool = True
    audit_log_path: Optional[str] = None

    # Onboarding completion status
    onboarding_completed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AntiAgentConfig":
        """Instantiate config from dictionary, ignoring unexpected keys."""
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


def get_global_config_dir() -> Path:
    """Get the global configuration directory (~/.antiagent)."""
    cfg_dir = Path(os.path.expanduser("~/.antiagent"))
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir


def get_default_audit_log_path() -> str:
    """Get default audit log path in ~/.antiagent/audit.log."""
    return str(get_global_config_dir() / "audit.log")


def load_config(workspace_dir: Optional[str] = None) -> AntiAgentConfig:
    """Load configuration with precedence:
    1. Environment variables
    2. Workspace configuration (.antiagent.json in workspace)
    3. Global configuration (~/.antiagent/config.json)
    4. Defaults
    """
    config_dict: Dict[str, Any] = {}

    # 1. Global config
    global_file = get_global_config_dir() / "config.json"
    if global_file.is_file():
        try:
            with open(global_file, "r", encoding="utf-8") as f:
                config_dict.update(json.load(f))
        except Exception:
            pass

    # 2. Workspace config
    if workspace_dir:
        ws_file = Path(workspace_dir) / ".antiagent.json"
        if ws_file.is_file():
            try:
                with open(ws_file, "r", encoding="utf-8") as f:
                    config_dict.update(json.load(f))
            except Exception:
                pass

    # Default audit log if not specified
    if not config_dict.get("audit_log_path"):
        config_dict["audit_log_path"] = get_default_audit_log_path()

    cfg = AntiAgentConfig.from_dict(config_dict)

    # 3. Environment variable overrides
    env_profile = os.getenv("ANTIAGENT_PROFILE")
    if env_profile in (PROFILE_BALANCED, PROFILE_PARANOID, PROFILE_AUTONOMOUS):
        cfg.profile = env_profile

    env_provider = os.getenv("ANTIAGENT_PROVIDER")
    if env_provider:
        cfg.provider = env_provider.lower()
    else:
        # Detect available provider automatically if not specified
        if os.getenv("GEMINI_API_KEY"):
            cfg.provider = "gemini"
            if not cfg.model:
                cfg.model = "gemini-2.5-flash"
        elif os.getenv("OPENAI_API_KEY"):
            cfg.provider = "openai"
            if not cfg.model:
                cfg.model = "gpt-4o-mini"

    if os.getenv("ANTIAGENT_MODEL"):
        cfg.model = os.getenv("ANTIAGENT_MODEL")

    if os.getenv("ANTIAGENT_AUTO_APPROVE_READS"):
        cfg.auto_approve_reads = os.getenv("ANTIAGENT_AUTO_APPROVE_READS").lower() in ("true", "1", "yes")

    return cfg


def save_global_config(config: AntiAgentConfig) -> Path:
    """Save configuration to ~/.antiagent/config.json."""
    cfg_dir = get_global_config_dir()
    cfg_file = cfg_dir / "config.json"
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, indent=2)
    return cfg_file


def save_workspace_config(config: AntiAgentConfig, workspace_dir: str) -> Path:
    """Save configuration to <workspace>/.antiagent.json."""
    ws_file = Path(workspace_dir) / ".antiagent.json"
    with open(ws_file, "w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, indent=2)
    return ws_file
