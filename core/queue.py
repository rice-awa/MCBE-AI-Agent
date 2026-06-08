"""消息队列 - 实现 WS 和 Agent 之间的异步解耦"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic_ai.messages import ModelMessage

from config.logging import get_logger

T = TypeVar("T")
logger = get_logger(__name__)

# 全局占位玩家名：当请求未携带玩家身份时使用，确保仍能落到独立的桶里。
DEFAULT_PLAYER_KEY = "__anonymous__"

# 默认对话名：未显式新建/切换对话时使用。
DEFAULT_CONVERSATION_ID = "default"

# 对话历史键: (connection_id, player_name, conversation_id)
SessionKey = tuple[UUID, str, str]
# 会话锁键: (connection_id, player_name)
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


@dataclass(order=True)
class QueueItem(Generic[T]):
    """队列项包装，支持优先级"""

    priority: int = field(compare=True)
    connection_id: UUID = field(compare=False)
    payload: T = field(compare=False)
    sequence: int = field(default=0, compare=True)  # 同优先级时的顺序


class MessageBroker:
    """
    消息代理 - 管理 WebSocket 和 Agent 之间的通信

    架构:
    - 请求队列 (共享): WS Handler -> Agent Worker
    - 响应队列 (每连接): Agent Worker -> WS Handler
    - 对话历史: 按 (连接, 玩家, 对话) 复合键分桶
    - 串行锁: 按 (连接, 玩家) 复合键分桶

    这种设计确保:
    1. LLM 请求不阻塞 WS 消息处理
    2. 响应正确路由到对应的连接
    3. 同一连接下不同玩家互不串扰：历史独立、可以并行处理
    4. 支持多 Worker 并发处理
    """

    def __init__(self, max_size: int = 100):
        # 请求队列: WS -> Agent (优先级队列)
        self._request_queue: asyncio.PriorityQueue[QueueItem] = asyncio.PriorityQueue(
            maxsize=max_size
        )
        # 响应队列: Agent -> WS (每个连接一个；下行不区分玩家，由 Addon 端按 player 路由)
        self._response_queues: dict[UUID, asyncio.Queue[Any]] = {}
        # 对话历史: 按 (连接, 玩家, 对话) 存储 pydantic-ai message_history
        self._conversation_histories: dict[SessionKey, list[ModelMessage]] = {}
        self._conversation_generations: dict[SessionKey, int] = {}
        self._conversation_metadata: dict[SessionKey, ConversationMetadata] = {}
        self._conversation_short_ids: dict[SessionLockKey, dict[int, str]] = {}
        self._next_conversation_short_id: dict[SessionLockKey, int] = {}
        # 会话锁: 保证同一 (连接, 玩家) 的请求按顺序处理；
        # 不同玩家可在同一连接上并行。
        self._session_locks: dict[SessionLockKey, asyncio.Lock] = {}
        # 序列号计数器
        self._sequence = 0
        # 锁
        self._lock = asyncio.Lock()

    async def submit_request(self, connection_id: UUID, payload: Any, priority: int = 0) -> None:
        """
        提交 LLM 请求（非阻塞）

        Args:
            connection_id: 连接 ID
            payload: 请求数据
            priority: 优先级（数字越小优先级越高）
        """
        async with self._lock:
            self._sequence += 1
            sequence = self._sequence

        item = QueueItem(
            priority=priority,
            connection_id=connection_id,
            payload=payload,
            sequence=sequence,
        )

        try:
            self._request_queue.put_nowait(item)
            logger.debug(
                "request_submitted",
                connection_id=str(connection_id),
                priority=priority,
                queue_size=self._request_queue.qsize(),
            )
        except asyncio.QueueFull:
            logger.warning(
                "request_queue_full",
                connection_id=str(connection_id),
                queue_size=self._request_queue.qsize(),
            )
            raise

    async def get_request(self) -> QueueItem:
        """
        获取待处理请求（Agent Worker 使用）

        Returns:
            队列项，包含连接 ID 和请求数据
        """
        item = await self._request_queue.get()
        logger.debug(
            "request_retrieved",
            connection_id=str(item.connection_id),
            priority=item.priority,
        )
        return item

    def request_done(self) -> None:
        """标记请求处理完成"""
        self._request_queue.task_done()

    def register_connection(self, connection_id: UUID) -> asyncio.Queue[Any]:
        """
        注册连接的响应队列

        Args:
            connection_id: 连接 ID

        Returns:
            该连接的响应队列
        """
        if connection_id in self._response_queues:
            logger.warning(
                "connection_already_registered",
                connection_id=str(connection_id),
            )
            return self._response_queues[connection_id]

        queue: asyncio.Queue[Any] = asyncio.Queue()
        self._response_queues[connection_id] = queue
        logger.info(
            "connection_registered",
            connection_id=str(connection_id),
            total_connections=len(self._response_queues),
        )
        return queue

    def unregister_connection(self, connection_id: UUID) -> None:
        """
        注销连接

        Args:
            connection_id: 连接 ID
        """
        queue = self._response_queues.pop(connection_id, None)
        # 清理该连接下所有玩家的会话状态
        self.clear_connection_sessions(connection_id)

        if queue:
            # 清空队列中的待处理消息
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            logger.info(
                "connection_unregistered",
                connection_id=str(connection_id),
                remaining_connections=len(self._response_queues),
            )

    async def send_response(self, connection_id: UUID, response: Any) -> bool:
        """
        发送响应到指定连接

        Args:
            connection_id: 连接 ID
            response: 响应数据

        Returns:
            是否成功发送
        """
        if queue := self._response_queues.get(connection_id):
            await queue.put(response)
            logger.debug(
                "response_sent",
                connection_id=str(connection_id),
                response_type=type(response).__name__,
            )
            return True
        else:
            logger.warning(
                "response_to_unknown_connection",
                connection_id=str(connection_id),
            )
            return False

    def has_connection(self, connection_id: UUID) -> bool:
        """检查连接是否仍注册。"""
        return connection_id in self._response_queues

    def get_response_queue(self, connection_id: UUID) -> asyncio.Queue[Any] | None:
        """获取指定连接的响应队列"""
        return self._response_queues.get(connection_id)

    def get_session_lock(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None = None,
    ) -> asyncio.Lock:
        """获取 (连接, 玩家) 级锁；同一玩家跨对话串行，跨玩家并行。"""
        key = make_session_lock_key(connection_id, player_name)
        return self._session_locks.setdefault(key, asyncio.Lock())

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

        metadata_keys = [key for key in self._conversation_metadata if key[0] == connection_id]
        for key in metadata_keys:
            self._conversation_metadata.pop(key, None)

        short_id_keys = [key for key in self._conversation_short_ids if key[0] == connection_id]
        for key in short_id_keys:
            self._conversation_short_ids.pop(key, None)
            self._next_conversation_short_id.pop(key, None)

    @property
    def pending_requests(self) -> int:
        """待处理请求数量"""
        return self._request_queue.qsize()

    @property
    def active_connections(self) -> int:
        """活跃连接数量"""
        return len(self._response_queues)

    def get_stats(self) -> dict[str, int]:
        """获取队列统计信息"""
        return {
            "pending_requests": self.pending_requests,
            "active_connections": self.active_connections,
            "active_conversations": len(self._conversation_histories),
        }
