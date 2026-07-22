"""Cache for preflight-normalized absolute targets before approval recovery."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from services.agent.harness.approvals import DEFAULT_APPROVAL_TTL_SECONDS


@dataclass
class PreflightCacheEntry:
    run_id: str
    tool_call_id: str
    original_args_hash: str
    canonical_args: dict[str, Any]
    execute_args: dict[str, Any] = field(default_factory=dict)
    approval_metadata: dict[str, Any] = field(default_factory=dict)
    preflight_payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    connection_id: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (str(self.run_id), str(self.tool_call_id), str(self.original_args_hash))


class PreflightCache:
    """In-process preflight result cache with approval-aligned TTL."""

    def __init__(self, *, ttl_seconds: float = DEFAULT_APPROVAL_TTL_SECONDS) -> None:
        self._ttl = float(ttl_seconds)
        self._items: dict[tuple[str, str, str], PreflightCacheEntry] = {}
        self._lock = threading.RLock()

    def put(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        original_args_hash: str,
        canonical_args: dict[str, Any],
        execute_args: dict[str, Any] | None = None,
        approval_metadata: dict[str, Any] | None = None,
        preflight_payload: dict[str, Any] | None = None,
        connection_id: str | None = None,
    ) -> PreflightCacheEntry:
        entry = PreflightCacheEntry(
            run_id=str(run_id or ""),
            tool_call_id=str(tool_call_id or ""),
            original_args_hash=str(original_args_hash or ""),
            canonical_args=dict(canonical_args or {}),
            execute_args=dict(execute_args or {}),
            approval_metadata=dict(approval_metadata or {}),
            preflight_payload=dict(preflight_payload or {}),
            connection_id=str(connection_id) if connection_id is not None else None,
        )
        with self._lock:
            self._purge_unlocked()
            self._items[entry.key] = entry
        return entry

    def get(
        self,
        run_id: str,
        tool_call_id: str,
        original_args_hash: str,
    ) -> PreflightCacheEntry | None:
        key = (str(run_id or ""), str(tool_call_id or ""), str(original_args_hash or ""))
        with self._lock:
            self._purge_unlocked()
            return self._items.get(key)

    def clear_connection(self, connection_id: str) -> int:
        cid = str(connection_id)
        with self._lock:
            keys = [k for k, v in self._items.items() if v.connection_id == cid]
            for key in keys:
                del self._items[key]
            return len(keys)

    def clear_expired(self, now: float | None = None) -> int:
        with self._lock:
            before = len(self._items)
            self._purge_unlocked(now)
            return before - len(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            self._purge_unlocked()
            return len(self._items)

    def _purge_unlocked(self, now: float | None = None) -> None:
        current = now if now is not None else time.time()
        expired = [k for k, v in self._items.items() if current - v.created_at >= self._ttl]
        for key in expired:
            del self._items[key]


_GLOBAL_PREFLIGHT = PreflightCache()


def get_preflight_cache() -> PreflightCache:
    return _GLOBAL_PREFLIGHT


def reset_preflight_cache() -> None:
    _GLOBAL_PREFLIGHT.clear()


def clear_preflight_for_connection(connection_id: str) -> int:
    return _GLOBAL_PREFLIGHT.clear_connection(connection_id)
