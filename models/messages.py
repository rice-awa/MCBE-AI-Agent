"""WebSocket 消息模型"""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from core.session import DEFAULT_CONVERSATION_ID


class BaseMessage(BaseModel):
    """消息基类"""

    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=datetime.now)
    connection_id: UUID


class ChatRequest(BaseMessage):
    """聊天请求"""

    type: Literal["chat"] = "chat"
    content: str
    player_name: str | None = None
    use_context: bool = True
    provider: str | None = None  # 可选指定 LLM 提供商
    delivery: Literal["tellraw", "scriptevent"] = "tellraw"
    conversation_id: str = DEFAULT_CONVERSATION_ID
    # 单次 run 身份，由入队侧或 Worker 生成；贯穿工具/完成事件。
    run_id: str | None = None
    # 历史 revision，保留兼容 API；普通聊天写回会递增。
    conversation_generation: int = 0
    # 管理操作失效 epoch；clear/switch/new/restore/switch_model 等运行时状态变更会递增。
    conversation_invalidation_epoch: int = 0
    broadcast_ai_chat: bool = False
    # 审批恢复：不把批准文本作为新 prompt，而是用原 messages + deferred results 恢复。
    resume_approval_id: str | None = None
    deferred_tool_results: dict | None = None
    resume_message_history: list | None = None


class ChatResponse(BaseMessage):
    """聊天响应（完整）"""

    type: Literal["response"] = "response"
    content: str
    reasoning: str | None = None
    is_complete: bool = False
    token_usage: dict | None = None


class StreamChunk(BaseMessage):
    """流式响应块"""

    type: Literal["stream_chunk"] = "stream_chunk"
    chunk_type: Literal[
        "reasoning", "content", "error",
        "thinking_start", "thinking_end",
        "tool_call", "tool_result",
        "approval_required",
    ]
    content: str
    sequence: int
    delivery: Literal["tellraw", "scriptevent"] = "tellraw"
    player_name: str | None = None
    target: str | None = None
    # 工具相关元数据
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result_preview: str | None = None


class SystemNotification(BaseMessage):
    """系统通知"""

    type: Literal["notification"] = "notification"
    level: Literal["info", "warning", "error"]
    message: str
    player_name: str | None = None


class ErrorMessage(BaseMessage):
    """错误消息"""

    type: Literal["error"] = "error"
    error_code: str
    message: str
    details: dict | None = None


class CommandRequest(BaseMessage):
    """命令请求"""

    type: Literal["command"] = "command"
    command_type: Literal[
        "login",
        "save",
        "context",
        "conversation",
        "run_command",
        "switch_model",
        "help",
        "ai_broadcast",
        "tool_approval",
    ]
    content: str | None = None


class CommandResponse(BaseMessage):
    """命令响应"""

    type: Literal["command_response"] = "command_response"
    success: bool
    message: str
    data: dict | None = None
