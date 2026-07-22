"""Minecraft 协议消息模型"""

import json
import re
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

_TELLRAW_TARGET_UNQUOTED_RE = re.compile(
    r"^(?:@[a-z](?:\[[A-Za-z0-9_.,=!:-]*\])?|[A-Za-z0-9_.-]+)$"
)
_SCRIPT_EVENT_MESSAGE_ID_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")


def sanitize_tellraw_target(target: str) -> str:
    """Return a command-safe tellraw target without allowing command injection."""
    target = target.strip() or "@a"
    if any(ord(char) < 0x20 or char == "\x7f" for char in target):
        raise ValueError("tellraw target contains control characters")
    if _TELLRAW_TARGET_UNQUOTED_RE.fullmatch(target):
        return target
    escaped = target.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def validate_scriptevent_message_id(message_id: str) -> str:
    """Validate a scriptevent message id before embedding it in commandLine."""
    if not _SCRIPT_EVENT_MESSAGE_ID_RE.fullmatch(message_id):
        raise ValueError(
            "invalid scriptevent message_id; expected namespace:path matching "
            "^[a-z0-9_.-]+:[a-z0-9_./-]+$"
        )
    return message_id


def _sanitize_tellraw_target(target: str) -> str:
    return sanitize_tellraw_target(target)


def sanitize_tellraw_text(message: str) -> str:
    return message.replace('"', '\\"').replace(":", "：").replace("%", "\\%")


class MinecraftHeader(BaseModel):
    """Minecraft WebSocket 消息头"""

    requestId: str = Field(default_factory=lambda: str(uuid4()))
    messagePurpose: Literal["subscribe", "commandRequest", "commandResponse", "event"] = "commandRequest"
    version: int = 1
    EventName: str | None = None
    eventName: str | None = None  # 事件名称（小写）


class MinecraftOrigin(BaseModel):
    """命令来源"""

    type: Literal["player", "say"] = "player"


class MinecraftCommandBody(BaseModel):
    """命令请求体"""

    origin: MinecraftOrigin = Field(default_factory=MinecraftOrigin)
    commandLine: str
    version: int = 17039360


class MinecraftSubscribeBody(BaseModel):
    """订阅请求体"""

    eventName: str


class MinecraftMessage(BaseModel):
    """通用 Minecraft WebSocket 消息"""

    header: MinecraftHeader
    body: dict[str, Any]


class MinecraftCommand(BaseModel):
    """Minecraft 命令消息"""

    header: MinecraftHeader = Field(
        default_factory=lambda: MinecraftHeader(
            messagePurpose="commandRequest",
            EventName="commandRequest",
        )
    )
    body: MinecraftCommandBody

    @classmethod
    def create_tellraw(
        cls,
        message: str,
        color: str = "§a",
        target: str = "@a",
    ) -> "MinecraftCommand":
        """创建 tellraw 命令"""
        rawtext = json.dumps(
            {"rawtext": [{"text": f"{color}{message}"}]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        safe_target = sanitize_tellraw_target(target)
        command_line = f"tellraw {safe_target} {rawtext}"
        return cls(
            body=MinecraftCommandBody(
                commandLine=command_line,
            )
        )

    @classmethod
    def create_scriptevent(cls, content: str, message_id: str = "server:data") -> "MinecraftCommand":
        """创建 scriptevent 命令"""
        safe_message_id = validate_scriptevent_message_id(message_id)
        return cls(
            body=MinecraftCommandBody(
                commandLine=f"scriptevent {safe_message_id} {content}",
            )
        )

    @classmethod
    def create_raw(cls, command: str) -> "MinecraftCommand":
        """创建原始命令"""
        return cls(
            body=MinecraftCommandBody(commandLine=command)
        )


class MinecraftSubscribe(BaseModel):
    """Minecraft 事件订阅消息"""

    header: MinecraftHeader = Field(
        default_factory=lambda: MinecraftHeader(
            messagePurpose="subscribe",
            EventName="commandRequest",
        )
    )
    body: MinecraftSubscribeBody

    @classmethod
    def player_message(cls) -> "MinecraftSubscribe":
        """订阅玩家消息事件"""
        return cls(body=MinecraftSubscribeBody(eventName="PlayerMessage"))


class PlayerMessageEvent(BaseModel):
    """玩家消息事件"""

    sender: str
    message: str
    type: str | None = None
    receiver: str | None = None

    @classmethod
    def from_event_body(cls, body: dict[str, Any]) -> "PlayerMessageEvent":
        """从事件体解析"""
        return cls(
            sender=body.get("sender", ""),
            message=body.get("message", ""),
            type=body.get("type"),
            receiver=body.get("receiver"),
        )
