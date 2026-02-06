"""Minecraft 协议消息模型"""

from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MinecraftHeader(BaseModel):
    """Minecraft WebSocket 消息头"""

    requestId: str = Field(default_factory=lambda: str(uuid4()))
    messagePurpose: Literal["subscribe", "commandRequest", "event"] = "commandRequest"
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

    header: MinecraftHeader = Field(default_factory=lambda: MinecraftHeader(messagePurpose="commandRequest"))
    body: MinecraftCommandBody

    @classmethod
    def create_tellraw(cls, message: str, color: str = "§a") -> "MinecraftCommand":
        """创建 tellraw 命令"""
        # 转义特殊字符
        safe_message = (
            message.replace('"', '\\"')
            .replace(":", "：")
            .replace("%", "\\%")
        )
        command_line = f'tellraw @a {{"rawtext":[{{"text":"{color}{safe_message}"}}]}}'
        return cls(
            body=MinecraftCommandBody(
                origin=MinecraftOrigin(type="say"),
                commandLine=command_line,
            )
        )

    @classmethod
    def create_scriptevent(cls, content: str, message_id: str = "server:data") -> "MinecraftCommand":
        """创建 scriptevent 命令"""
        return cls(
            body=MinecraftCommandBody(
                commandLine=f"scriptevent {message_id} {content}",
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
