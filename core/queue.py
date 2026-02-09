"""消息队列 - 实现 WS 和 Agent 之间的异步解耦"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic_ai.messages import ModelMessage

from config.logging import get_logger

T = TypeVar("T")
logger = get_logger(__name__)


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

    这种设计确保:
    1. LLM 请求不阻塞 WS 消息处理
    2. 响应正确路由到对应的连接
    3. 支持多 Worker 并发处理
    """

    def __init__(self, max_size: int = 100):
        # 请求队列: WS -> Agent (优先级队列)
        self._request_queue: asyncio.PriorityQueue[QueueItem] = asyncio.PriorityQueue(
            maxsize=max_size
        )
        # 响应队列: Agent -> WS (每个连接一个)
        self._response_queues: dict[UUID, asyncio.Queue[Any]] = {}
        # 对话历史: 按连接存储 pydantic-ai message_history
        self._conversation_histories: dict[UUID, list[ModelMessage]] = {}
        # 连接锁: 保证同一连接的请求按顺序处理
        self._connection_locks: dict[UUID, asyncio.Lock] = {}
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
        self._connection_locks.setdefault(connection_id, asyncio.Lock())
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
        self.clear_conversation_history(connection_id)
        self._connection_locks.pop(connection_id, None)

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

    def get_response_queue(self, connection_id: UUID) -> asyncio.Queue[Any] | None:
        """获取指定连接的响应队列"""
        return self._response_queues.get(connection_id)

    def get_connection_lock(self, connection_id: UUID) -> asyncio.Lock:
        """获取连接级锁，确保同一连接请求串行处理"""
        return self._connection_locks.setdefault(connection_id, asyncio.Lock())

    def get_conversation_history(self, connection_id: UUID) -> list[ModelMessage]:
        """获取连接的对话历史副本"""
        history = self._conversation_histories.get(connection_id, [])
        return list(history)

    def set_conversation_history(
        self,
        connection_id: UUID,
        history: list[ModelMessage],
    ) -> None:
        """更新连接的对话历史"""
        self._conversation_histories[connection_id] = list(history)

    def clear_conversation_history(self, connection_id: UUID) -> None:
        """清理连接的对话历史"""
        self._conversation_histories.pop(connection_id, None)

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
