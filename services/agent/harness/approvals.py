"""进程内待审批工具调用存储（单进程恢复）。"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import DeferredToolRequests


DEFAULT_APPROVAL_TTL_SECONDS = 120.0


@dataclass
class PendingApproval:
    """一次需要玩家确认的工具调用。"""

    approval_id: str
    connection_id: str
    player_name: str
    conversation_id: str
    run_id: str
    tool_call_id: str
    tool_name: str
    normalized_args: dict[str, Any]
    args_summary: str
    args_hash: str
    policy_version: str
    messages: list[ModelMessage]
    requests: DeferredToolRequests
    provider: str | None
    delivery: str
    use_context: bool
    broadcast_ai_chat: bool
    created_at: float
    expires_at: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at


class PendingApprovalStore:
    """按 (connection_id, player_name, conversation_id, approval_id) 索引的进程内审批表。"""

    def __init__(self, *, default_ttl_seconds: float = DEFAULT_APPROVAL_TTL_SECONDS) -> None:
        self._default_ttl = float(default_ttl_seconds)
        self._items: dict[tuple[str, str, str, str], PendingApproval] = {}
        self._lock = threading.RLock()

    @staticmethod
    def make_key(
        connection_id: str,
        player_name: str,
        conversation_id: str,
        approval_id: str,
    ) -> tuple[str, str, str, str]:
        return (str(connection_id), str(player_name), str(conversation_id), str(approval_id))

    def generate_approval_id(self) -> str:
        return secrets.token_hex(4)

    def put(self, pending: PendingApproval) -> PendingApproval:
        key = self.make_key(
            pending.connection_id,
            pending.player_name,
            pending.conversation_id,
            pending.approval_id,
        )
        with self._lock:
            self._purge_expired_unlocked()
            self._items[key] = pending
        return pending

    def get(
        self,
        connection_id: str,
        player_name: str,
        conversation_id: str,
        approval_id: str,
    ) -> PendingApproval | None:
        key = self.make_key(connection_id, player_name, conversation_id, approval_id)
        with self._lock:
            self._purge_expired_unlocked()
            return self._items.get(key)

    def pop(
        self,
        connection_id: str,
        player_name: str,
        conversation_id: str,
        approval_id: str,
    ) -> PendingApproval | None:
        key = self.make_key(connection_id, player_name, conversation_id, approval_id)
        with self._lock:
            self._purge_expired_unlocked()
            return self._items.pop(key, None)

    def get_for_owner(
        self,
        *,
        connection_id: str,
        player_name: str,
        conversation_id: str,
        approval_id: str,
    ) -> tuple[PendingApproval | None, str | None]:
        """返回 (pending, reject_reason)。reject_reason 非空表示不可用。"""
        pending = self.get(connection_id, player_name, conversation_id, approval_id)
        if pending is None:
            # 可能存在但 owner 不匹配
            with self._lock:
                for key, item in self._items.items():
                    if key[3] == approval_id:
                        if key[0] != str(connection_id) or key[1] != str(player_name):
                            return None, "跨玩家或跨连接审批被拒绝"
                        if key[2] != str(conversation_id):
                            return None, "跨对话审批被拒绝"
                        break
            return None, "未找到该审批或已处理"
        if pending.is_expired():
            self.pop(connection_id, player_name, conversation_id, approval_id)
            return None, "审批已过期"
        return pending, None

    def clear_connection(self, connection_id: str) -> int:
        cid = str(connection_id)
        with self._lock:
            keys = [k for k in self._items if k[0] == cid]
            for key in keys:
                del self._items[key]
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired_unlocked()
            return len(self._items)

    def _purge_expired_unlocked(self, now: float | None = None) -> None:
        current = now if now is not None else time.time()
        expired = [k for k, v in self._items.items() if v.expires_at <= current]
        for key in expired:
            del self._items[key]
