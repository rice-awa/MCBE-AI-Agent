"""消息队列 - 实现 WS 和 Agent 之间的异步解耦"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar
from uuid import UUID

from pydantic_ai.messages import ModelMessage

from config.logging import get_logger
from core.session import (
    DEFAULT_CONVERSATION_ID,
    DEFAULT_PLAYER_KEY,
    ConversationMetadata,
    ConversationSessionStore,
    SessionKey,
    SessionLockKey,
    make_session_key,
    make_session_lock_key,
    normalize_conversation_id,
)

if TYPE_CHECKING:
    from services.agent.trace import TraceContext

__all__ = [
    "DEFAULT_CONVERSATION_ID",
    "DEFAULT_PLAYER_KEY",
    "ConversationMetadata",
    "MessageBroker",
    "QueueItem",
    "SessionKey",
    "SessionLockKey",
    "make_session_key",
    "make_session_lock_key",
    "normalize_conversation_id",
]

T = TypeVar("T")
logger = get_logger(__name__)


@dataclass(order=True)
class QueueItem(Generic[T]):
    """队列项包装，支持优先级"""

    priority: int = field(compare=True)
    connection_id: UUID = field(compare=False)
    payload: T = field(compare=False)
    sequence: int = field(default=0, compare=True)  # 同优先级时的顺序
    # 入站 trace 上下文；无 correlation 字段时保持 None（向后兼容）
    trace_context: "TraceContext | None" = field(default=None, compare=False)
    enqueued_at_ns: int = field(default=0, compare=False)


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
        # 运行时对话状态：按 (连接, 玩家, 对话) 管理历史、元数据、短 ID 和锁。
        self.sessions = ConversationSessionStore()
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

        trace_context = self._trace_context_from_payload(connection_id, payload)
        item = QueueItem(
            priority=priority,
            connection_id=connection_id,
            payload=payload,
            sequence=sequence,
            trace_context=trace_context,
            enqueued_at_ns=time.time_ns(),
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

    @staticmethod
    def _trace_context_from_payload(
        connection_id: UUID,
        payload: Any,
    ) -> "TraceContext | None":
        """从 ChatRequest 的 correlation 字段构建 TraceContext；缺失则返回 None。"""
        from models.messages import ChatRequest
        from services.agent.trace import TraceContext

        if not isinstance(payload, ChatRequest):
            return None

        trace_id = payload.trace_id or payload.run_id
        attempt_id = payload.attempt_id
        if not trace_id or not attempt_id:
            return None

        run_id = payload.run_id or trace_id
        return TraceContext(
            trace_id=trace_id,
            run_id=run_id,
            attempt_id=attempt_id,
            message_id=str(payload.id),
            connection_id=str(connection_id),
            player_name=payload.player_name or DEFAULT_PLAYER_KEY,
            conversation_id=payload.conversation_id or DEFAULT_CONVERSATION_ID,
        )

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
        return self.sessions.get_session_lock(connection_id, player_name)

    def get_active_conversation_id(
        self,
        connection_id: UUID,
        player_name: str | None,
    ) -> str:
        """获取玩家当前活动运行时对话 ID。"""
        return self.sessions.get_active_conversation_id(connection_id, player_name)

    def set_active_conversation_id(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None,
    ) -> str:
        """设置玩家当前活动运行时对话 ID。"""
        return self.sessions.set_active_conversation_id(
            connection_id,
            player_name,
            conversation_id,
        )

    def ensure_conversation_metadata(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None = None,
    ) -> ConversationMetadata:
        """确保对话元数据存在，并分配玩家内稳定短 ID。"""
        return self.sessions.ensure_conversation_metadata(
            connection_id,
            player_name,
            conversation_id,
        )

    def resolve_conversation_short_id(
        self,
        connection_id: UUID,
        player_name: str | None,
        short_id_text: str,
    ) -> str | None:
        """解析玩家内对话短 ID，支持 #2 和 2。"""
        return self.sessions.resolve_conversation_short_id(
            connection_id,
            player_name,
            short_id_text,
        )

    def set_conversation_title(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None,
        title: str,
    ) -> ConversationMetadata:
        """设置对话标题并标记标题可用。"""
        return self.sessions.set_conversation_title(
            connection_id,
            player_name,
            conversation_id,
            title,
        )

    def set_conversation_title_if_connected(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None,
        title: str,
    ) -> ConversationMetadata | None:
        """仅当连接仍注册时设置对话标题，避免清理后重建元数据。"""
        if not self.has_connection(connection_id):
            return None
        return self.sessions.set_conversation_title(
            connection_id,
            player_name,
            conversation_id,
            title,
        )

    def mark_conversation_title_generating(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None,
    ) -> bool:
        """标记对话标题生成中；已有标题或正在生成时跳过。"""
        return self.sessions.mark_conversation_title_generating(
            connection_id,
            player_name,
            conversation_id,
        )

    def mark_conversation_title_failed(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None,
    ) -> None:
        """标题生成失败时回写状态，仅从生成中状态转为 failed。"""
        self.sessions.mark_conversation_title_failed(
            connection_id,
            player_name,
            conversation_id,
        )

    def get_conversation_metadata(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None = None,
    ) -> ConversationMetadata:
        """获取对话元数据，不存在时自动创建。"""
        return self.sessions.get_conversation_metadata(
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
        return self.sessions.list_player_conversation_metadata(connection_id, player_name)

    def get_conversation_generation(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None = None,
    ) -> int:
        """获取对话历史 revision；普通历史写入会递增，兼容既有 API。"""
        return self.sessions.get_conversation_generation(
            connection_id,
            player_name,
            conversation_id,
        )

    def get_conversation_invalidation_epoch(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None = None,
    ) -> int:
        """获取对话失效 epoch；仅管理操作递增，用于 stale guard。"""
        return self.sessions.get_conversation_invalidation_epoch(
            connection_id,
            player_name,
            conversation_id,
        )

    def bump_conversation_invalidation_epoch(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None = None,
    ) -> int:
        """递增指定对话的失效 epoch。"""
        return self.sessions.bump_conversation_invalidation_epoch(
            connection_id,
            player_name,
            conversation_id,
        )

    def ensure_conversation(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None = None,
    ) -> None:
        """确保对话桶存在，便于后续管理命令更新版本。"""
        self.sessions.ensure_conversation(connection_id, player_name, conversation_id)

    def conversation_exists(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None = None,
    ) -> bool:
        """判断对话桶是否已存在，即使历史为空也算存在。"""
        return self.sessions.conversation_exists(connection_id, player_name, conversation_id)

    def get_conversation_history(
        self,
        connection_id: UUID,
        player_name: str | None = None,
        conversation_id: str | None = None,
    ) -> list[ModelMessage]:
        """获取 (连接, 玩家, 对话) 维度的对话历史副本。"""
        return self.sessions.get_conversation_history(
            connection_id,
            player_name,
            conversation_id,
        )

    def set_conversation_history(
        self,
        connection_id: UUID,
        player_name: str | None | list[ModelMessage],
        history: list[ModelMessage] | None = None,
        conversation_id: str | None = None,
        expected_generation: int | None = None,
        expected_invalidation_epoch: int | None = None,
    ) -> bool:
        """更新 (连接, 玩家, 对话) 维度的对话历史。"""
        if history is None and isinstance(player_name, list):
            history = player_name
            player_name = None
        return self.sessions.set_conversation_history(
            connection_id,
            player_name,
            list(history or []),
            conversation_id,
            expected_generation,
            expected_invalidation_epoch,
        )

    def clear_conversation_history(
        self,
        connection_id: UUID,
        player_name: str | None = None,
        conversation_id: str | None = None,
    ) -> None:
        """清理 (连接, 玩家, 对话) 维度的对话历史。"""
        self.sessions.clear_conversation_history(connection_id, player_name, conversation_id)

    def clear_player_conversation_histories(
        self,
        connection_id: UUID,
        player_name: str | None,
    ) -> None:
        """清理某玩家在当前连接下的全部运行时对话历史。"""
        self.sessions.clear_player_conversation_histories(connection_id, player_name)

    def list_player_conversations(
        self,
        connection_id: UUID,
        player_name: str | None,
    ) -> list[tuple[str, int]]:
        """列出某玩家在当前连接下的运行时对话及消息数。"""
        return self.sessions.list_player_conversations(connection_id, player_name)

    def list_session_players(self, connection_id: UUID) -> list[str]:
        """列出某连接下所有有过历史/锁的玩家名（不含匿名占位）。"""
        return self.sessions.list_session_players(connection_id)

    def clear_connection_sessions(self, connection_id: UUID) -> None:
        """清理某连接下全部玩家的历史和锁。"""
        self.sessions.clear_connection_sessions(connection_id)

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
            "active_conversations": self.sessions.active_conversation_count,
        }
