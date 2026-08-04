"""Cache for preflight-normalized absolute targets before approval recovery."""

from __future__ import annotations

import asyncio
import secrets
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
    plan_id: str = ""
    tool_name: str = ""
    executed: bool = False
    execution_result: Any = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (str(self.run_id), str(self.tool_call_id), str(self.original_args_hash))


class PreflightCache:
    """In-process preflight result cache with approval-aligned TTL.

    双索引：``(run_id, tool_call_id, original_args_hash)`` 用于同轮幂等查询，
    ``plan_id`` 用于审批恢复（gateway → worker → 执行器）。
    """

    def __init__(self, *, ttl_seconds: float = DEFAULT_APPROVAL_TTL_SECONDS) -> None:
        self._ttl = float(ttl_seconds)
        self._items: dict[tuple[str, str, str], PreflightCacheEntry] = {}
        self._by_plan_id: dict[str, PreflightCacheEntry] = {}
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
        plan_id: str = "",
        tool_name: str = "",
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
            plan_id=str(plan_id or "").strip() or secrets.token_hex(16),
            tool_name=str(tool_name or ""),
        )
        with self._lock:
            self._purge_unlocked()
            self._items[entry.key] = entry
            self._by_plan_id[entry.plan_id] = entry
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

    def get_by_plan_id(self, plan_id: str) -> PreflightCacheEntry | None:
        """按 plan_id 取预检计划；过期条目在访问时清除。"""
        pid = str(plan_id or "").strip()
        if not pid:
            return None
        with self._lock:
            self._purge_unlocked()
            return self._by_plan_id.get(pid)

    def clear_connection(self, connection_id: str) -> int:
        cid = str(connection_id)
        with self._lock:
            keys = [k for k, v in self._items.items() if v.connection_id == cid]
            for key in keys:
                entry = self._items.pop(key, None)
                if entry is not None and entry.plan_id:
                    self._by_plan_id.pop(entry.plan_id, None)
            return len(keys)

    def clear_expired(self, now: float | None = None) -> int:
        with self._lock:
            before = len(self._items)
            self._purge_unlocked(now)
            return before - len(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._by_plan_id.clear()

    def __len__(self) -> int:
        with self._lock:
            self._purge_unlocked()
            return len(self._items)

    def _purge_unlocked(self, now: float | None = None) -> None:
        current = now if now is not None else time.time()
        expired = [k for k, v in self._items.items() if current - v.created_at >= self._ttl]
        for key in expired:
            entry = self._items.pop(key, None)
            if entry is not None and entry.plan_id:
                self._by_plan_id.pop(entry.plan_id, None)


_GLOBAL_PREFLIGHT = PreflightCache()


def get_preflight_cache() -> PreflightCache:
    return _GLOBAL_PREFLIGHT


def reset_preflight_cache() -> None:
    _GLOBAL_PREFLIGHT.clear()
    with _plan_execution_locks_guard:
        _plan_execution_locks.clear()


def clear_preflight_for_connection(connection_id: str) -> int:
    return _GLOBAL_PREFLIGHT.clear_connection(connection_id)


# --- Per-plan execution serialization ---------------------------------------
#
# ``execute_block_plan`` 的幂等（``executed`` / ``execution_result`` 读-查-写）
# 不能靠 ``PreflightCache._lock``（threading.RLock）跨 ``await`` 持锁：同一事件
# 循环里另一个协程会在 ``lock.acquire()`` 上阻塞整个循环（死锁）。每个 plan_id
# 一把 asyncio.Lock，让并发恢复串行化，第二个协程在拿到锁后重新检查
# ``executed`` 并直接返回缓存结果。

_PLAN_EXECUTION_LOCKS_MAX = 4096
_plan_execution_locks: dict[str, asyncio.Lock] = {}
_plan_execution_locks_guard = threading.Lock()


def get_plan_execution_lock(plan_id: str) -> asyncio.Lock:
    """Return the per-plan asyncio lock serializing plan execution.

    Bounded registry: when the cap is reached only dead locks (no holder and no
    waiters — ``locked()`` is False) are evicted, so eviction can never race an
    in-flight execution.
    """
    pid = str(plan_id or "").strip()
    with _plan_execution_locks_guard:
        lock = _plan_execution_locks.get(pid)
        if lock is None:
            if len(_plan_execution_locks) >= _PLAN_EXECUTION_LOCKS_MAX:
                dead = [k for k, v in _plan_execution_locks.items() if not v.locked()]
                for key in dead:
                    del _plan_execution_locks[key]
            lock = asyncio.Lock()
            _plan_execution_locks[pid] = lock
        return lock
