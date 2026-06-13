"""运行时玩家对话状态。"""

import asyncio
from dataclasses import dataclass
from uuid import UUID

from pydantic_ai.messages import ModelMessage

DEFAULT_PLAYER_KEY = "__anonymous__"
DEFAULT_CONVERSATION_ID = "default"

SessionKey = tuple[UUID, str, str]
SessionLockKey = tuple[UUID, str]


@dataclass
class ConversationMetadata:
    """运行时对话元数据。"""

    conversation_id: str
    short_id: int
    title: str | None = None
    title_status: str = "pending"


def normalize_conversation_id(conversation_id: str | None) -> str:
    """标准化对话 ID，空值回落到默认对话。"""
    value = (conversation_id or DEFAULT_CONVERSATION_ID).strip()
    return value or DEFAULT_CONVERSATION_ID


def make_session_key(
    connection_id: UUID,
    player_name: str | None,
    conversation_id: str | None = None,
) -> SessionKey:
    """生成对话历史键。player_name 为空时回落到匿名键。"""
    player = player_name or DEFAULT_PLAYER_KEY
    return (connection_id, player, normalize_conversation_id(conversation_id))


def make_session_lock_key(
    connection_id: UUID,
    player_name: str | None,
) -> SessionLockKey:
    """生成会话锁键。player_name 为空时回落到匿名键。"""
    player = player_name or DEFAULT_PLAYER_KEY
    return (connection_id, player)


class ConversationSessionStore:
    """按 (connection_id, player_name, conversation_id) 管理运行时对话状态。"""

    def __init__(self) -> None:
        self._conversation_histories: dict[SessionKey, list[ModelMessage]] = {}
        self._conversation_generations: dict[SessionKey, int] = {}
        self._conversation_metadata: dict[SessionKey, ConversationMetadata] = {}
        self._conversation_short_ids: dict[SessionLockKey, dict[int, str]] = {}
        self._next_conversation_short_id: dict[SessionLockKey, int] = {}
        self._active_conversation_ids: dict[SessionLockKey, str] = {}
        self._session_locks: dict[SessionLockKey, asyncio.Lock] = {}

    def get_session_lock(
        self,
        connection_id: UUID,
        player_name: str | None,
    ) -> asyncio.Lock:
        """获取 (连接, 玩家) 级锁；同一玩家跨对话串行，跨玩家并行。"""
        key = make_session_lock_key(connection_id, player_name)
        return self._session_locks.setdefault(key, asyncio.Lock())

    def get_active_conversation_id(
        self,
        connection_id: UUID,
        player_name: str | None,
    ) -> str:
        """获取玩家当前活动运行时对话 ID。"""
        return self._active_conversation_ids.get(
            make_session_lock_key(connection_id, player_name),
            DEFAULT_CONVERSATION_ID,
        )

    def set_active_conversation_id(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None,
    ) -> str:
        """设置玩家当前活动运行时对话 ID。"""
        normalized = normalize_conversation_id(conversation_id)
        self._active_conversation_ids[make_session_lock_key(connection_id, player_name)] = normalized
        return normalized

    def ensure_conversation_metadata(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None = None,
    ) -> ConversationMetadata:
        """确保对话元数据存在，并分配玩家内稳定短 ID。"""
        key = make_session_key(connection_id, player_name, conversation_id)
        if metadata := self._conversation_metadata.get(key):
            return metadata

        lock_key = make_session_lock_key(connection_id, player_name)
        short_ids = self._conversation_short_ids.setdefault(lock_key, {})
        normalized_conversation_id = key[2]

        existing_short_id = next(
            (
                short_id
                for short_id, mapped_conversation_id in short_ids.items()
                if mapped_conversation_id == normalized_conversation_id
            ),
            None,
        )
        if existing_short_id is None:
            existing_short_id = self._next_conversation_short_id.get(lock_key, 1)
            short_ids[existing_short_id] = normalized_conversation_id
            self._next_conversation_short_id[lock_key] = existing_short_id + 1

        metadata = ConversationMetadata(
            conversation_id=normalized_conversation_id,
            short_id=existing_short_id,
        )
        self._conversation_metadata[key] = metadata
        return metadata

    def resolve_conversation_short_id(
        self,
        connection_id: UUID,
        player_name: str | None,
        short_id_text: str,
    ) -> str | None:
        """解析玩家内对话短 ID，支持 #2 和 2。"""
        value = short_id_text.strip()
        if value.startswith("#"):
            value = value[1:]
        if not value.isdigit():
            return None

        short_ids = self._conversation_short_ids.get(
            make_session_lock_key(connection_id, player_name),
            {},
        )
        return short_ids.get(int(value))

    def set_conversation_title(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None,
        title: str,
    ) -> ConversationMetadata:
        """设置对话标题并标记标题可用。"""
        metadata = self.ensure_conversation_metadata(
            connection_id,
            player_name,
            conversation_id,
        )
        metadata.title = title
        metadata.title_status = "ready"
        return metadata

    def mark_conversation_title_generating(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None,
    ) -> bool:
        """标记对话标题生成中；已有标题或正在生成时跳过。"""
        metadata = self.ensure_conversation_metadata(
            connection_id,
            player_name,
            conversation_id,
        )
        if metadata.title or metadata.title_status == "generating":
            return False
        metadata.title_status = "generating"
        return True

    def mark_conversation_title_failed(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None,
    ) -> None:
        """标题生成失败时回写状态，仅从生成中状态转为 failed。"""
        metadata = self.ensure_conversation_metadata(
            connection_id,
            player_name,
            conversation_id,
        )
        if metadata.title_status == "generating":
            metadata.title_status = "failed"

    def get_conversation_metadata(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None = None,
    ) -> ConversationMetadata:
        """获取对话元数据，不存在时自动创建。"""
        return self.ensure_conversation_metadata(
            connection_id,
            player_name,
            conversation_id,
        )

    def list_player_conversation_metadata(
        self,
        connection_id: UUID,
        player_name: str | None,
    ) -> list[ConversationMetadata]:
        """列出某玩家在当前连接下的运行时对话元数据。"""
        player = player_name or DEFAULT_PLAYER_KEY
        conversation_ids: set[str] = set()

        for cid, pname, conversation_id in self._conversation_histories.keys():
            if cid == connection_id and pname == player:
                conversation_ids.add(conversation_id)

        for cid, pname, conversation_id in self._conversation_metadata.keys():
            if cid == connection_id and pname == player:
                conversation_ids.add(conversation_id)

        for cid, pname in self._session_locks.keys():
            if cid == connection_id and pname == player:
                conversation_ids.add(DEFAULT_CONVERSATION_ID)

        metadata_items = [
            self.ensure_conversation_metadata(connection_id, player, conversation_id)
            for conversation_id in conversation_ids
        ]
        return sorted(metadata_items, key=lambda metadata: metadata.short_id)

    def get_conversation_generation(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None = None,
    ) -> int:
        """获取对话桶版本，用于避免过期请求覆盖后续管理命令。"""
        key = make_session_key(connection_id, player_name, conversation_id)
        return self._conversation_generations.get(key, 0)

    def ensure_conversation(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None = None,
    ) -> None:
        """确保对话桶存在，便于后续管理命令更新版本。"""
        key = make_session_key(connection_id, player_name, conversation_id)
        self.ensure_conversation_metadata(connection_id, player_name, conversation_id)
        self._conversation_histories.setdefault(key, [])
        self._conversation_generations.setdefault(key, 0)

    def conversation_exists(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None = None,
    ) -> bool:
        """判断对话桶是否已存在，即使历史为空也算存在。"""
        key = make_session_key(connection_id, player_name, conversation_id)
        return key in self._conversation_histories

    def get_conversation_history(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None = None,
    ) -> list[ModelMessage]:
        """获取 (连接, 玩家, 对话) 维度的对话历史副本。"""
        key = make_session_key(connection_id, player_name, conversation_id)
        history = self._conversation_histories.get(key, [])
        return list(history)

    def set_conversation_history(
        self,
        connection_id: UUID,
        player_name: str | None,
        history: list[ModelMessage],
        conversation_id: str | None = None,
        expected_generation: int | None = None,
    ) -> bool:
        """更新 (连接, 玩家, 对话) 维度的对话历史。"""
        key = make_session_key(connection_id, player_name, conversation_id)
        self.ensure_conversation_metadata(connection_id, player_name, conversation_id)
        current_generation = self._conversation_generations.get(key, 0)
        if expected_generation is not None and current_generation != expected_generation:
            return False
        self._conversation_histories[key] = list(history)
        self._conversation_generations[key] = current_generation + 1
        return True

    def clear_conversation_history(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None = None,
    ) -> None:
        """清理 (连接, 玩家, 对话) 维度的对话历史。"""
        key = make_session_key(connection_id, player_name, conversation_id)
        self._conversation_histories.pop(key, None)
        self._conversation_generations[key] = self._conversation_generations.get(key, 0) + 1

    def clear_player_conversation_histories(
        self,
        connection_id: UUID,
        player_name: str | None,
    ) -> None:
        """清理某玩家在当前连接下的全部运行时对话历史。"""
        player = player_name or DEFAULT_PLAYER_KEY
        history_keys = [
            key for key in self._conversation_histories
            if key[0] == connection_id and key[1] == player
        ]
        generation_keys = [
            key for key in self._conversation_generations
            if key[0] == connection_id and key[1] == player
        ]
        for key in set(history_keys + generation_keys):
            self._conversation_histories.pop(key, None)
            self._conversation_generations[key] = self._conversation_generations.get(key, 0) + 1

        metadata_keys = [
            key for key in self._conversation_metadata
            if key[0] == connection_id and key[1] == player
        ]
        for key in metadata_keys:
            self._conversation_metadata.pop(key, None)

        lock_key = make_session_lock_key(connection_id, player_name)
        self._conversation_short_ids.pop(lock_key, None)
        self._next_conversation_short_id.pop(lock_key, None)
        self._active_conversation_ids.pop(lock_key, None)

    def list_player_conversations(
        self,
        connection_id: UUID,
        player_name: str | None,
    ) -> list[tuple[str, int]]:
        """列出某玩家在当前连接下的运行时对话及消息数。"""
        player = player_name or DEFAULT_PLAYER_KEY
        conversations: dict[str, int] = {}
        for cid, pname, conversation_id in self._conversation_histories.keys():
            if cid == connection_id and pname == player:
                conversations[conversation_id] = len(
                    self._conversation_histories[(cid, pname, conversation_id)]
                )
        for cid, pname in self._session_locks.keys():
            if cid == connection_id and pname == player:
                conversations.setdefault(DEFAULT_CONVERSATION_ID, 0)
        return sorted(conversations.items())

    def list_session_players(self, connection_id: UUID) -> list[str]:
        """列出某连接下所有有过历史/锁的玩家名（不含匿名占位）。"""
        names: set[str] = set()
        for cid, player, _conversation_id in self._conversation_histories.keys():
            if cid == connection_id and player != DEFAULT_PLAYER_KEY:
                names.add(player)
        for cid, player in self._session_locks.keys():
            if cid == connection_id and player != DEFAULT_PLAYER_KEY:
                names.add(player)
        return sorted(names)

    def clear_connection_sessions(self, connection_id: UUID) -> None:
        """清理某连接下全部玩家的历史和锁。"""
        history_keys = [key for key in self._conversation_histories if key[0] == connection_id]
        for key in history_keys:
            self._conversation_histories.pop(key, None)

        generation_keys = [key for key in self._conversation_generations if key[0] == connection_id]
        for key in generation_keys:
            self._conversation_generations.pop(key, None)

        lock_keys = [key for key in self._session_locks if key[0] == connection_id]
        for key in lock_keys:
            self._session_locks.pop(key, None)
            self._active_conversation_ids.pop(key, None)

        metadata_keys = [key for key in self._conversation_metadata if key[0] == connection_id]
        for key in metadata_keys:
            self._conversation_metadata.pop(key, None)

        short_id_keys = [key for key in self._conversation_short_ids if key[0] == connection_id]
        for key in short_id_keys:
            self._conversation_short_ids.pop(key, None)
            self._next_conversation_short_id.pop(key, None)

    @property
    def active_conversation_count(self) -> int:
        """当前有历史桶的运行时对话数量。"""
        return len(self._conversation_histories)
