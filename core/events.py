"""事件定义"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any
from uuid import UUID, uuid4


class EventType(Enum):
    """事件类型枚举"""

    # 连接事件
    CONNECTION_OPENED = auto()
    CONNECTION_CLOSED = auto()
    CONNECTION_ERROR = auto()

    # 认证事件
    AUTH_SUCCESS = auto()
    AUTH_FAILED = auto()
    TOKEN_EXPIRED = auto()

    # 聊天事件
    CHAT_REQUEST = auto()
    CHAT_RESPONSE = auto()
    CHAT_STREAM_START = auto()
    CHAT_STREAM_CHUNK = auto()
    CHAT_STREAM_END = auto()
    CHAT_ERROR = auto()

    # 命令事件
    COMMAND_RECEIVED = auto()
    COMMAND_EXECUTED = auto()
    COMMAND_FAILED = auto()

    # Agent 事件
    AGENT_STARTED = auto()
    AGENT_COMPLETED = auto()
    AGENT_ERROR = auto()
    AGENT_TOOL_CALL = auto()

    # 系统事件
    SYSTEM_STARTUP = auto()
    SYSTEM_SHUTDOWN = auto()
    WORKER_STARTED = auto()
    WORKER_STOPPED = auto()


@dataclass
class Event:
    """事件基类"""

    event_type: EventType
    connection_id: UUID | None = None
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    event_id: UUID = field(default_factory=uuid4)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type.name,
            "connection_id": str(self.connection_id) if self.connection_id else None,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
        }


class EventBus:
    """简单的事件总线实现"""

    def __init__(self):
        self._handlers: dict[EventType, list[Any]] = {}

    def subscribe(self, event_type: EventType, handler: Any) -> None:
        """订阅事件"""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: EventType, handler: Any) -> None:
        """取消订阅"""
        if event_type in self._handlers:
            self._handlers[event_type].remove(handler)

    async def publish(self, event: Event) -> None:
        """发布事件"""
        handlers = self._handlers.get(event.event_type, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                # 记录错误但不中断其他处理器
                import asyncio
                from mcbe_ai_agent.config.logging import get_logger
                logger = get_logger(__name__)
                logger.error(
                    "event_handler_error",
                    event_type=event.event_type.name,
                    error=str(e),
                )


# 全局事件总线实例
import asyncio
event_bus = EventBus()
