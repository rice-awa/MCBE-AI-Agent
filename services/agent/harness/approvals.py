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
    """一次需要玩家确认的工具调用。

    同一轮 DeferredToolRequests 中的多个工具共享 batch_id；
    必须等 batch 内全部决策齐后，才用完整 DeferredToolResults 恢复 run。
    """

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
    batch_id: str = ""
    sibling_approval_ids: list[str] = field(default_factory=list)
    decision: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.batch_id:
            self.batch_id = self.approval_id
        if not self.sibling_approval_ids:
            self.sibling_approval_ids = [self.approval_id]

    def is_expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at


class PendingApprovalStore:
    """按 (connection_id, player_name, conversation_id, approval_id) 索引的进程内审批表。

    多工具 defer 时按 batch 聚合决策：单个 `AGENT 同意|拒绝` 只记录一项；
    当 batch 内全部 approval_id 都有决策后，一次性弹出并返回完整结果。
    """

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

    def generate_batch_id(self) -> str:
        return secrets.token_hex(6)

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
            self._pop_batch_unlocked(
                connection_id=connection_id,
                player_name=player_name,
                conversation_id=conversation_id,
                batch_id=pending.batch_id,
            )
            return None, "审批已过期"
        return pending, None

    def list_pending_for_owner(
        self,
        *,
        connection_id: str,
        player_name: str,
        conversation_id: str | None = None,
    ) -> list[PendingApproval]:
        """列出该玩家（可选限定对话）下尚未决策且未过期的审批，按创建时间升序。"""
        with self._lock:
            self._purge_expired_unlocked()
            items = [
                item
                for key, item in self._items.items()
                if key[0] == str(connection_id)
                and key[1] == str(player_name)
                and (conversation_id is None or key[2] == str(conversation_id))
                and item.decision is None
                and not item.is_expired()
            ]
        items.sort(key=lambda item: item.created_at)
        return items

    def resolve_target_approval_id(
        self,
        *,
        connection_id: str,
        player_name: str,
        conversation_id: str,
        approval_id: str | None = None,
    ) -> tuple[str | None, str | None]:
        """解析要决策的 approval_id。

        - 显式 id：校验存在后返回
        - 省略 id：若仅有一批未决策审批，自动取该批最早一项；多批冲突则报错

        Returns:
            (resolved_id, reject_reason)
        """
        explicit = (approval_id or "").strip()
        if explicit:
            pending, reason = self.get_for_owner(
                connection_id=connection_id,
                player_name=player_name,
                conversation_id=conversation_id,
                approval_id=explicit,
            )
            if pending is None:
                return None, reason or "未找到该审批或已处理"
            return pending.approval_id, None

        pending_items = self.list_pending_for_owner(
            connection_id=connection_id,
            player_name=player_name,
            conversation_id=conversation_id,
        )
        if not pending_items:
            return None, "当前没有待审批的工具调用"
        batch_ids = {item.batch_id for item in pending_items}
        if len(batch_ids) > 1:
            return (
                None,
                "存在多个待审批批次，请使用 AGENT 同意|拒绝 <approval_id> 指定",
            )
        # 同批内按创建时间取最早未决策项
        undecided = [item for item in pending_items if item.decision is None]
        if not undecided:
            return None, "当前没有待审批的工具调用"
        return undecided[0].approval_id, None

    def record_decision(
        self,
        *,
        connection_id: str,
        player_name: str,
        conversation_id: str,
        approval_id: str,
        approved: bool,
    ) -> tuple[PendingApproval | None, str | None, list[PendingApproval] | None]:
        """记录单项决策。

        Returns:
            (pending, reject_reason, completed_batch)
            - reject_reason 非空：不可用
            - completed_batch 为 None：已记录，仍等待同 batch 其他项
            - completed_batch 为 list：batch 齐套，已从 store 弹出，可 resume
        """
        with self._lock:
            self._purge_expired_unlocked()
            key = self.make_key(connection_id, player_name, conversation_id, approval_id)
            pending = self._items.get(key)
            if pending is None:
                for existing_key, item in self._items.items():
                    if existing_key[3] == approval_id:
                        if existing_key[0] != str(connection_id) or existing_key[1] != str(player_name):
                            return None, "跨玩家或跨连接审批被拒绝", None
                        if existing_key[2] != str(conversation_id):
                            return None, "跨对话审批被拒绝", None
                        break
                return None, "未找到该审批或已处理", None

            if pending.is_expired():
                self._pop_batch_unlocked(
                    connection_id=connection_id,
                    player_name=player_name,
                    conversation_id=conversation_id,
                    batch_id=pending.batch_id,
                )
                return None, "审批已过期", None

            if pending.decision is not None:
                return None, "该审批已处理", None

            pending.decision = bool(approved)

            siblings = self._siblings_unlocked(
                connection_id=connection_id,
                player_name=player_name,
                conversation_id=conversation_id,
                batch_id=pending.batch_id,
                expected_ids=pending.sibling_approval_ids,
            )
            if siblings is None:
                # batch 成员缺失（可能部分过期被清）：整批失效
                self._pop_batch_unlocked(
                    connection_id=connection_id,
                    player_name=player_name,
                    conversation_id=conversation_id,
                    batch_id=pending.batch_id,
                )
                return None, "审批批次不完整或已过期", None

            if any(item.decision is None for item in siblings):
                return pending, None, None

            # 齐套：弹出整批
            completed = self._pop_batch_unlocked(
                connection_id=connection_id,
                player_name=player_name,
                conversation_id=conversation_id,
                batch_id=pending.batch_id,
            )
            return pending, None, completed

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

    def _siblings_unlocked(
        self,
        *,
        connection_id: str,
        player_name: str,
        conversation_id: str,
        batch_id: str,
        expected_ids: list[str],
    ) -> list[PendingApproval] | None:
        found: list[PendingApproval] = []
        for approval_id in expected_ids:
            key = self.make_key(connection_id, player_name, conversation_id, approval_id)
            item = self._items.get(key)
            if item is None or item.batch_id != batch_id:
                return None
            found.append(item)
        return found

    def _pop_batch_unlocked(
        self,
        *,
        connection_id: str,
        player_name: str,
        conversation_id: str,
        batch_id: str,
    ) -> list[PendingApproval]:
        popped: list[PendingApproval] = []
        keys = [
            key
            for key, item in self._items.items()
            if key[0] == str(connection_id)
            and key[1] == str(player_name)
            and key[2] == str(conversation_id)
            and item.batch_id == batch_id
        ]
        for key in keys:
            popped.append(self._items.pop(key))
        return popped

    def _purge_expired_unlocked(self, now: float | None = None) -> None:
        current = now if now is not None else time.time()
        expired_batches: set[tuple[str, str, str, str]] = set()
        for key, item in self._items.items():
            if item.expires_at <= current:
                expired_batches.add((key[0], key[1], key[2], item.batch_id))
        for connection_id, player_name, conversation_id, batch_id in expired_batches:
            self._pop_batch_unlocked(
                connection_id=connection_id,
                player_name=player_name,
                conversation_id=conversation_id,
                batch_id=batch_id,
            )
