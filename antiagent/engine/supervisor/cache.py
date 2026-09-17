"""Simple TTL decision cache for supervisor verdicts."""

import hashlib
import json
import time
from typing import Dict, Optional, Tuple


class DecisionCache:
    """In-memory cache for tool evaluation verdicts."""

    def __init__(self, ttl_seconds: int = 300):
        self.ttl = ttl_seconds
        # key -> (decision, reason, expiry)
        self._store: Dict[str, Tuple[str, str, float]] = {}

    def _hash_key(
        self, tool_name: str, tool_args: dict, context_summary: Optional[str] = None
    ) -> str:
        try:
            args_str = json.dumps(tool_args, sort_keys=True, default=str)
        except Exception:
            args_str = str(sorted(str(k) for k in tool_args.keys()))
        ctx_part = context_summary or ""
        serialized = f"{tool_name}:{args_str}:{ctx_part}"
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(
        self, tool_name: str, tool_args: dict, context_summary: Optional[str] = None
    ) -> Optional[Tuple[str, str]]:
        key = self._hash_key(tool_name, tool_args, context_summary=context_summary)
        if key in self._store:
            decision, reason, expiry = self._store[key]
            if time.time() < expiry:
                return decision, f"{reason} (cached)"
            del self._store[key]
        return None

    def put(
        self,
        tool_name: str,
        tool_args: dict,
        decision: str,
        reason: str,
        context_summary: Optional[str] = None,
    ) -> None:
        key = self._hash_key(tool_name, tool_args, context_summary=context_summary)
        self._store[key] = (decision, reason, time.time() + self.ttl)

    def clear(self) -> None:
        self._store.clear()
