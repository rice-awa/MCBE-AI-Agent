"""Agent 相关模型"""

from dataclasses import dataclass
from typing import Any, Callable, Awaitable
from uuid import UUID

import httpx

from config.settings import Settings


@dataclass
class ContextInfo:
    """上下文使用信息"""

    message_count: int  # 消息数量
    estimated_tokens: int  # 估计的 token 数
    max_tokens: int | None  # 模型最大上下文


@dataclass
class AgentDependencies:
    """Agent 运行时依赖注入"""

    connection_id: UUID
    player_name: str
    settings: Settings
    http_client: httpx.AsyncClient
    send_to_game: Callable[[str], Awaitable[None]]
    run_command: Callable[[str], Awaitable[str]]
    provider: str | None = None  # 当前使用的 LLM 提供商
    # 获取上下文使用信息的回调，返回 (消息数, 估计token数, 最大token数)
    get_context_info: Callable[[], ContextInfo | None] | None = None


@dataclass
class AgentResponse:
    """Agent 响应结果"""

    content: str
    reasoning: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    token_usage: dict[str, int] | None = None
    is_complete: bool = True


@dataclass
class StreamEvent:
    """流式事件"""

    event_type: str  # "reasoning", "content", "tool_call", "error"
    content: str
    sequence: int
    metadata: dict[str, Any] | None = None
