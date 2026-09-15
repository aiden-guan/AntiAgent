"""Simple TTL decision cache for supervisor verdicts."""

import hashlib
import time
from typing import Dict, Optional, Tuple


class DecisionCache:
    """In-memory cache for tool evaluation verdicts."""

    def __init__(self, ttl_seconds: int = 300):
        self.ttl = ttl_seconds
        # key -> (decision, reason, expiry)
        self._store: Dict[str, Tuple[str, str, float]] = {}

    def _hash_key(self, tool_name: str, tool_args: dict) -> str:
        serialized = f"{tool_name}:{sorted(tool_args.items())}"
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(self, tool_name: str, tool_args: dict) -> Optional[Tuple[str, str]]:
        key = self._hash_key(tool_name, tool_args)
        if key in self._store:
            decision, reason, expiry = self._store[key]
            if time.time() < expiry:
                return decision, f"{reason} (cached)"
            del self._store[key]
        return None

    def put(self, tool_name: str, tool_args: dict, decision: str, reason: str) -> None:
        key = self._hash_key(tool_name, tool_args)
        self._store[key] = (decision, reason, time.time() + self.ttl)

    def clear(self) -> None:
        self._store.clear()
