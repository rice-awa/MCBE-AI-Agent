"""Typed conversation/session domain operations for the Host gateway.

Conversation management has two consumers: the in-game chat renderer and the
MCBEWS session response adapter.  This module keeps the operation and data
queries in one place; neither consumer needs to parse rendered text or infer
success from a Minecraft color code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from config.settings import Settings
from core.queue import MessageBroker, normalize_conversation_id
from core.session import DEFAULT_CONVERSATION_ID
from services.gateway.session_store import HostSessionStore, PlayerSession

CONVERSATION_ACTIONS = frozenset(
    {
        "new",
        "switch",
        "list",
        "status",
        "clear",
        "save",
        "restore",
        "saved",
        "delete",
        "compress",
    }
)


@dataclass(frozen=True, slots=True)
class ConversationOperationResult:
    """Stable result shared by chat and session adapters."""

    action: str
    ok: bool
    code: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(
        cls,
        action: str,
        message: str,
        *,
        data: dict[str, Any] | None = None,
        code: str = "OK",
    ) -> ConversationOperationResult:
        return cls(action=action, ok=True, code=code, message=message, data=data or {})

    @classmethod
    def failure(
        cls,
        action: str,
        message: str,
        *,
        code: str = "OPERATION_FAILED",
        data: dict[str, Any] | None = None,
    ) -> ConversationOperationResult:
        return cls(action=action, ok=False, code=code, message=message, data=data or {})


class ConversationOperations:
    """Own the Host conversation/session operation contract."""

    def __init__(
        self,
        broker: MessageBroker,
        settings: Settings,
        sessions: HostSessionStore | None = None,
        *,
        conversation_manager: Any | None = None,
    ) -> None:
        self.broker = broker
        self.settings = settings
        self.sessions = sessions
        self._conversation_manager = conversation_manager

    async def execute(
        self,
        connection_id: UUID,
        *,
        player_name: str,
        action: str,
        conversation_id: str | None = None,
        saved_session_id: str | None = None,
    ) -> ConversationOperationResult:
        """Execute one validated operation for an explicit business player."""

        normalized_action = str(action or "").strip().lower()
        actor = str(player_name or "").strip()
        if not actor:
            return ConversationOperationResult.failure(
                normalized_action or "unknown",
                "缺少 player_name",
                code="INVALID_PLAYER",
            )
        if normalized_action not in CONVERSATION_ACTIONS:
            return ConversationOperationResult.failure(
                normalized_action or "unknown",
                f"未知操作: {normalized_action}",
                code="UNSUPPORTED_ACTION",
            )

        try:
            if normalized_action == "new":
                return self._new(connection_id, actor, conversation_id)
            if normalized_action == "switch":
                return self._switch(connection_id, actor, conversation_id)
            if normalized_action == "list":
                return self._list(connection_id, actor)
            if normalized_action == "status":
                return self._status(connection_id, actor)
            if normalized_action == "clear":
                return self._clear(connection_id, actor, conversation_id)
            if normalized_action == "compress":
                return await self._compress(connection_id, actor, conversation_id)
            if normalized_action == "save":
                return await self._save(connection_id, actor, conversation_id)
            if normalized_action == "restore":
                return await self._restore(
                    connection_id, actor, conversation_id, saved_session_id
                )
            if normalized_action == "saved":
                return await self._saved(actor)
            if normalized_action == "delete":
                return await self._delete(actor, saved_session_id)
        except Exception as exc:  # noqa: BLE001 - domain boundary normalizes failures
            return ConversationOperationResult.failure(
                normalized_action,
                f"处理失败: {exc}",
                code="INTERNAL_ERROR",
            )

        # The action set above is exhaustive; keep a defensive result for type
        # checkers and for future additions that forget to add a branch.
        return ConversationOperationResult.failure(
            normalized_action,
            "未知操作",
            code="UNSUPPORTED_ACTION",
        )

    # Intentional aliases make the service pleasant for adapters and preserve
    # terminology used by older Host integrations.
    async def operate(self, connection_id: UUID, **kwargs: Any) -> ConversationOperationResult:
        return await self.execute(connection_id, **kwargs)

    async def run(self, connection_id: UUID, **kwargs: Any) -> ConversationOperationResult:
        return await self.execute(connection_id, **kwargs)

    def _player_session(self, connection_id: UUID, player_name: str) -> PlayerSession:
        if self.sessions is None:
            return PlayerSession(player_name=player_name)
        host = self.sessions.get(connection_id)
        if host is None:
            host = self.sessions.create(
                connection_id,
                authenticated=bool(getattr(self.settings, "dev_mode", False)),
                ai_broadcast_all=bool(
                    getattr(getattr(self.settings, "minecraft", None), "ai_broadcast_default", True)
                ),
            )
        return host.get_player_session(player_name)

    def _active(self, connection_id: UUID, player_name: str) -> str:
        return self.broker.get_active_conversation_id(connection_id, player_name)

    def _resolve_cid(self, conversation_id: str | None, active: str) -> str:
        return normalize_conversation_id(conversation_id or active)

    def _metadata(self, connection_id: UUID, player_name: str, conversation_id: str) -> Any:
        return self.broker.ensure_conversation_metadata(
            connection_id, player_name, conversation_id
        )

    def _conversation_data(
        self,
        connection_id: UUID,
        player_name: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        metadata = self._metadata(connection_id, player_name, conversation_id)
        history = self.broker.get_conversation_history(
            connection_id, player_name, conversation_id
        )
        return {
            "conversation_id": conversation_id,
            "short_id": metadata.short_id,
            "title": metadata.title or "",
            "message_count": len(history),
        }

    def _new(
        self,
        connection_id: UUID,
        player_name: str,
        requested_id: str | None,
    ) -> ConversationOperationResult:
        requested = str(requested_id or "").strip()
        # Session DTOs default cid to "default"; a new operation without an
        # explicit id must still create a fresh bucket.
        if requested and requested != DEFAULT_CONVERSATION_ID:
            new_id = normalize_conversation_id(requested)
            if self.broker.conversation_exists(connection_id, player_name, new_id):
                return ConversationOperationResult.failure(
                    "new",
                    f"对话 {new_id} 已存在，请使用 switch {new_id} 切换",
                    code="CONVERSATION_EXISTS",
                )
        else:
            new_id = self._generate_unique_id(connection_id, player_name)
        active = self._active(connection_id, player_name)
        self.broker.bump_conversation_invalidation_epoch(connection_id, player_name, active)
        self.broker.set_active_conversation_id(connection_id, player_name, new_id)
        self.broker.set_conversation_history(connection_id, player_name, [], new_id)
        data = self._conversation_data(connection_id, player_name, new_id)
        return ConversationOperationResult.success(
            "new", f"已新建并切换到对话: #{data['short_id']} {new_id}", data=data
        )

    def _switch(
        self,
        connection_id: UUID,
        player_name: str,
        target_id: str | None,
    ) -> ConversationOperationResult:
        raw = str(target_id or "").strip()
        if not raw or raw == DEFAULT_CONVERSATION_ID:
            return ConversationOperationResult.failure(
                "switch", "请指定要切换的对话 ID", code="INVALID_ARGUMENT"
            )
        resolved = self.broker.resolve_conversation_short_id(connection_id, player_name, raw)
        target = normalize_conversation_id(resolved or raw)
        active = self._active(connection_id, player_name)
        existing = self.broker.conversation_exists(connection_id, player_name, target)
        self.broker.bump_conversation_invalidation_epoch(connection_id, player_name, active)
        if target != active:
            self.broker.bump_conversation_invalidation_epoch(connection_id, player_name, target)
        if not existing:
            self.broker.set_conversation_history(connection_id, player_name, [], target)
        self.broker.set_active_conversation_id(connection_id, player_name, target)
        data = self._conversation_data(connection_id, player_name, target)
        message = (
            f"已切换到对话: #{data['short_id']} {target}（{self._count_turns(connection_id, player_name, target)}轮）"
            if existing
            else f"已创建并切换到新会话: #{data['short_id']} {target}"
        )
        return ConversationOperationResult.success("switch", message, data=data)

    def _list(self, connection_id: UUID, player_name: str) -> ConversationOperationResult:
        active = self._active(connection_id, player_name)
        live = dict(self.broker.list_player_conversations(connection_id, player_name))
        live.setdefault(active, len(self.broker.get_conversation_history(connection_id, player_name, active)))
        conversations: list[dict[str, Any]] = []
        for metadata in self.broker.list_player_conversation_metadata(connection_id, player_name):
            cid = metadata.conversation_id
            history = self.broker.get_conversation_history(connection_id, player_name, cid)
            conversations.append(
                {
                    "id": cid,
                    "short_id": metadata.short_id,
                    "title": metadata.title or "",
                    "message_count": len(history),
                    "is_active": cid == active,
                }
            )
        if not conversations:
            conversations.append(
                {
                    "id": active,
                    "short_id": self._metadata(connection_id, player_name, active).short_id,
                    "title": "",
                    "message_count": live.get(active, 0),
                    "is_active": True,
                }
            )
        return ConversationOperationResult.success(
            "list", f"当前连接内共有 {len(conversations)} 个对话", data={"conversations": conversations}
        )

    def _status(self, connection_id: UUID, player_name: str) -> ConversationOperationResult:
        active = self._active(connection_id, player_name)
        session = self._player_session(connection_id, player_name)
        data = self._conversation_data(connection_id, player_name, active)
        data.update(
            {
                "turns": self._count_turns(connection_id, player_name, active),
                "max_history_turns": int(getattr(self.settings, "max_history_turns", 0) or 0),
                "context_enabled": session.context_enabled,
                "title_status": self._metadata(connection_id, player_name, active).title_status,
            }
        )
        return ConversationOperationResult.success(
            "status", f"当前对话: {active}\n对话消息数: {data['message_count']}", data=data
        )

    def _clear(
        self,
        connection_id: UUID,
        player_name: str,
        conversation_id: str | None,
    ) -> ConversationOperationResult:
        active = self._active(connection_id, player_name)
        cid = self._resolve_cid(conversation_id, active)
        self.broker.clear_conversation_history(connection_id, player_name, cid)
        self.broker.set_conversation_history(connection_id, player_name, [], cid)
        return ConversationOperationResult.success(
            "clear", f"对话 {cid} 的历史已清除", data=self._conversation_data(connection_id, player_name, cid)
        )

    async def _compress(
        self,
        connection_id: UUID,
        player_name: str,
        conversation_id: str | None,
    ) -> ConversationOperationResult:
        active = self._active(connection_id, player_name)
        cid = self._resolve_cid(conversation_id, active)
        session = self._player_session(connection_id, player_name)
        manager = self._manager()
        compressed, message = await manager.check_and_compress(
            connection_id,
            player_name,
            force=True,
            conversation_id=cid,
            provider_name=session.current_provider or self.settings.default_provider,
        )
        return (
            ConversationOperationResult.success("compress", message, data=self._conversation_data(connection_id, player_name, cid))
            if compressed
            else ConversationOperationResult.failure("compress", message, code="NOT_COMPRESSED")
        )

    async def _save(
        self,
        connection_id: UUID,
        player_name: str,
        conversation_id: str | None,
    ) -> ConversationOperationResult:
        active = self._active(connection_id, player_name)
        cid = self._resolve_cid(conversation_id, active)
        session = self._player_session(connection_id, player_name)
        manager = self._manager()
        success, value = await manager.save_conversation(
            connection_id=connection_id,
            player_name=player_name,
            provider=session.current_provider or self.settings.default_provider,
            template=session.current_template,
            custom_variables=session.custom_variables,
            conversation_id=cid,
        )
        if not success:
            return ConversationOperationResult.failure("save", value, code="SAVE_FAILED")
        return ConversationOperationResult.success("save", f"对话已保存: {value}", data={"session_id": value})

    async def _restore(
        self,
        connection_id: UUID,
        player_name: str,
        conversation_id: str | None,
        saved_session_id: str | None,
    ) -> ConversationOperationResult:
        if not saved_session_id:
            return ConversationOperationResult.failure("restore", "缺少 sid 参数", code="INVALID_ARGUMENT")
        active = self._active(connection_id, player_name)
        cid = self._resolve_cid(conversation_id, active)
        success, message = await self._manager().restore_conversation(
            connection_id, saved_session_id, player_name=player_name, conversation_id=cid
        )
        if not success:
            return ConversationOperationResult.failure("restore", message, code="RESTORE_FAILED")
        return ConversationOperationResult.success(
            "restore", message, data={"session_id": saved_session_id, **self._conversation_data(connection_id, player_name, cid)}
        )

    async def _saved(self, player_name: str) -> ConversationOperationResult:
        saved = await self._manager().list_conversations(player_name=player_name)
        return ConversationOperationResult.success("saved", f"共有 {len(saved)} 个已保存会话", data={"saved": saved})

    async def _delete(self, player_name: str, saved_session_id: str | None) -> ConversationOperationResult:
        if not saved_session_id:
            return ConversationOperationResult.failure("delete", "缺少 sid 参数", code="INVALID_ARGUMENT")
        success, message = await self._manager().delete_conversation(saved_session_id, player_name=player_name)
        return (
            ConversationOperationResult.success("delete", message, data={"session_id": saved_session_id})
            if success
            else ConversationOperationResult.failure("delete", message, code="DELETE_FAILED")
        )

    def _manager(self) -> Any:
        if self._conversation_manager is None:
            from services.agent.runtime import get_agent_runtime

            self._conversation_manager = get_agent_runtime().get_conversation_manager(
                self.broker, self.settings
            )
        return self._conversation_manager

    def _count_turns(self, connection_id: UUID, player_name: str, conversation_id: str) -> int:
        history = self.broker.get_conversation_history(connection_id, player_name, conversation_id)
        turns = 0
        for message in history:
            if any(getattr(part, "part_kind", None) == "user-prompt" for part in getattr(message, "parts", [])):
                turns += 1
        return turns

    def _generate_unique_id(self, connection_id: UUID, player_name: str) -> str:
        for _ in range(20):
            candidate = "chat-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid4().hex[:6]
            if not self.broker.conversation_exists(connection_id, player_name, candidate):
                return candidate
        raise RuntimeError("无法生成唯一对话 ID")


__all__ = ["CONVERSATION_ACTIONS", "ConversationOperationResult", "ConversationOperations"]
