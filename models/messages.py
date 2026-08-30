"""WebSocket 消息模型。

The broker response queue historically carried four unrelated dictionaries.
The gateway outbound value objects below close that boundary: producers must
declare the business player, conversation and correlation identity before the
response bridge can dispatch them.  ``Mapping`` compatibility is deliberately
limited to these value objects so old integrations that read ``message["type"]``
can migrate without bringing the magic-dict contract back into internal code.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any, Literal, cast
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
    # 迁移期 run_id 与 trace_id 相同，保留现有日志与幂等兼容。
    run_id: str | None = None
    # 一次玩家意图的稳定身份；审批恢复/重试保持不变。
    trace_id: str | None = None
    # 单次执行 attempt；审批恢复或重试时生成新值。
    attempt_id: str | None = None
    # 历史 revision，保留兼容 API；普通聊天写回会递增。
    conversation_generation: int = 0
    # 管理操作失效 epoch；clear/switch/new/restore/switch_model 等运行时状态变更会递增。
    conversation_invalidation_epoch: int = 0
    broadcast_ai_chat: bool = False
    # 会话级自动批准高风险工具（AGENT 同意 对话|永远 打开后写入）。
    auto_approve_tools: bool = False
    # 审批恢复：不把批准文本作为新 prompt，而是用原 messages + deferred results 恢复。
    resume_approval_id: str | None = None
    deferred_tool_results: dict[str, Any] | None = None
    resume_message_history: list[Any] | None = None


class ChatResponse(BaseMessage):
    """聊天响应（完整）"""

    type: Literal["response"] = "response"
    content: str
    reasoning: str | None = None
    is_complete: bool = False
    token_usage: dict[str, Any] | None = None


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
    tool_args: dict[str, Any] | None = None
    tool_result_preview: str | None = None
    # 交付侧 correlation（不含消息正文）；Task 3 桥接会使用。
    trace_id: str | None = None
    attempt_id: str | None = None
    conversation_id: str | None = None
    # 审批相关字段（chunk_type=="approval_required" 时携带）
    approval_id: str | None = None
    args_summary: str | None = None
    approval_reason: str | None = None
    batch_id: str | None = None
    batch_size: int | None = None
    batch_index: int | None = None


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
    details: dict[str, Any] | None = None


class CommandRequest(BaseMessage):
    """命令请求"""

    type: Literal["command"] = "command"
    command_type: Literal[
        "login",
        "save",
        "context",
        "continuous_mode",
        "conversation",
        "run_command",
        "switch_model",
        "help",
        "ai_broadcast",
        "tool_approve",
        "tool_deny",
    ]
    content: str | None = None


class CommandResponse(BaseMessage):
    """命令响应"""

    type: Literal["command_response"] = "command_response"
    success: bool
    message: str
    data: dict[str, Any] | None = None


def _require_identity(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


@dataclass(frozen=True, slots=True)
class _GatewayOutbound(Mapping[str, Any]):
    """Base mapping view for typed gateway outbound messages."""

    def __getitem__(self, key: str) -> Any:
        if key == "type":
            return self.message_type
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    def __iter__(self) -> Iterator[str]:
        yield "type"
        for item in fields(self):
            if item.name != "message_type":
                yield item.name

    def __len__(self) -> int:
        return 1 + sum(item.name != "message_type" for item in fields(self))

    @property
    def message_type(self) -> str:
        raise NotImplementedError

    def model_dump(self) -> dict[str, Any]:
        """Return the compatibility mapping as a regular dictionary."""

        return {key: self[key] for key in self}


@dataclass(frozen=True, slots=True)
class RunCommandOutbound(_GatewayOutbound):
    """One command request awaiting a Minecraft ``commandResponse``."""

    command: str
    result_future: Any
    player_name: str
    conversation_id: str
    correlation_id: str

    @property
    def message_type(self) -> str:
        return "run_command"

    def __post_init__(self) -> None:
        _require_identity(self.command, "command")
        _require_identity(self.player_name, "player_name")
        _require_identity(self.conversation_id, "conversation_id")
        _require_identity(self.correlation_id, "correlation_id")


TextResponseRole = Literal["user", "assistant", "approval"]


@dataclass(frozen=True, slots=True)
class TextResponseOutbound(_GatewayOutbound):
    """A typed ``mcbews:text_resp``/UI text response producer message."""

    player_name: str
    conversation_id: str
    correlation_id: str
    role: TextResponseRole
    text: str
    response_id: str = field(default_factory=lambda: str(uuid4()))
    title: str | None = None
    usage: dict[str, Any] | None = None

    @property
    def message_type(self) -> str:
        return "ai_response_sync"

    def __post_init__(self) -> None:
        _require_identity(self.player_name, "player_name")
        _require_identity(self.conversation_id, "conversation_id")
        _require_identity(self.correlation_id, "correlation_id")
        _require_identity(self.response_id, "response_id")
        if self.role not in {"user", "assistant", "approval"}:
            raise ValueError(f"unsupported text response role: {self.role}")
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")


@dataclass(frozen=True, slots=True)
class SessionResponseOutbound(_GatewayOutbound):
    """One atomic, correlated SDK session response."""

    player_name: str
    conversation_id: str
    correlation_id: str
    response: Any

    @property
    def message_type(self) -> str:
        return "session_resp"

    @property
    def request_id(self) -> str:
        return str(getattr(self.response, "request_id", self.correlation_id))

    @property
    def action(self) -> str:
        return str(getattr(self.response, "action", "status"))

    def __post_init__(self) -> None:
        _require_identity(self.player_name, "player_name")
        _require_identity(self.conversation_id, "conversation_id")
        _require_identity(self.correlation_id, "correlation_id")
        if self.response is None:
            raise ValueError("response must not be None")


@dataclass(frozen=True, slots=True)
class GameMessageOutbound(_GatewayOutbound):
    """A game-facing message produced by an Agent tool."""

    player_name: str
    conversation_id: str
    correlation_id: str
    content: str
    target: str | None = None

    @property
    def message_type(self) -> str:
        return "game_message"

    def __post_init__(self) -> None:
        _require_identity(self.player_name, "player_name")
        _require_identity(self.conversation_id, "conversation_id")
        _require_identity(self.correlation_id, "correlation_id")
        if not isinstance(self.content, str):
            raise ValueError("content must be a string")


GatewayOutbound = (
    RunCommandOutbound
    | TextResponseOutbound
    | SessionResponseOutbound
    | GameMessageOutbound
)


def coerce_legacy_gateway_outbound(item: Mapping[str, Any]) -> GatewayOutbound:
    """Convert one external legacy dict at the bridge boundary.

    Internal producers must construct the typed classes directly.  This helper
    exists only for old plugins/tests that still enqueue dictionaries and keeps
    their compatibility logic in one place.
    """

    from core.session import DEFAULT_CONVERSATION_ID
    from models.constants import DEFAULT_PLAYER_DISPLAY_NAME

    message_type = item.get("type")
    player_name = str(item.get("player_name") or DEFAULT_PLAYER_DISPLAY_NAME)
    conversation_id = str(item.get("conversation_id") or DEFAULT_CONVERSATION_ID)
    correlation_id = str(
        item.get("correlation_id")
        or item.get("response_id")
        or item.get("request_id")
        or uuid4()
    )
    if message_type == "run_command":
        return RunCommandOutbound(
            command=str(item.get("command") or ""),
            result_future=item.get("result_future"),
            player_name=player_name,
            conversation_id=conversation_id,
            correlation_id=correlation_id,
        )
    if message_type == "ai_response_sync":
        role = cast(TextResponseRole, item.get("role") or "assistant")
        return TextResponseOutbound(
            player_name=player_name,
            conversation_id=conversation_id,
            correlation_id=correlation_id,
            role=role,
            text=str(item.get("text") or ""),
            response_id=str(item.get("response_id") or correlation_id),
            title=item.get("title"),
            usage=item.get("usage"),
        )
    if message_type == "game_message":
        return GameMessageOutbound(
            player_name=player_name,
            conversation_id=conversation_id,
            correlation_id=correlation_id,
            content=str(item.get("content") or ""),
            target=item.get("target"),
        )
    if message_type == "session_resp":
        from mcbe_ws_sdk.profiles.mcbews_v1.models import (
            SessionAction,
            SessionError,
            SessionResponse,
        )

        action = cast(SessionAction, item.get("action") or "status")
        error = item.get("error")
        if isinstance(error, str):
            error = SessionError(code="SESSION_OPERATION_FAILED", message=error[:256])
        response = SessionResponse(
            request_id=str(item.get("request_id") or correlation_id),
            action=action,
            ok=bool(item.get("ok", False)),
            data=item.get("data"),
            error=error,
        )
        return SessionResponseOutbound(
            player_name=player_name,
            conversation_id=conversation_id,
            correlation_id=correlation_id,
            response=response,
        )
    raise ValueError(f"unknown gateway outbound type: {message_type!r}")


__all__ = [
    "BaseMessage",
    "ChatRequest",
    "ChatResponse",
    "CommandRequest",
    "CommandResponse",
    "ErrorMessage",
    "GameMessageOutbound",
    "GatewayOutbound",
    "RunCommandOutbound",
    "SessionResponseOutbound",
    "StreamChunk",
    "SystemNotification",
    "TextResponseOutbound",
    "coerce_legacy_gateway_outbound",
]
