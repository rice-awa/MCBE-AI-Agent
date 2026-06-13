"""Minecraft 协议处理"""

import json
from dataclasses import dataclass
from typing import Any

from models.minecraft import (
    MinecraftMessage,
    MinecraftSubscribe,
    MinecraftCommand,
    PlayerMessageEvent,
)
from models.messages import ChatRequest
from services.websocket.connection import ConnectionState
from services.websocket.command import CommandRegistry
from config.logging import get_logger
from config.settings import MinecraftConfig

logger = get_logger(__name__)


@dataclass(frozen=True)
class TellrawMessage:
    """结构化 tellraw 消息：携带原文与颜色，由 _send_ws_payload 统一分片。

    避免在发送层反向解析已序列化的 commandLine JSON。
    """

    text: str
    color: str


class MinecraftProtocolHandler:
    """Minecraft WebSocket 协议处理器"""

    def __init__(self, config: MinecraftConfig):
        self.config = config
        self.command_registry = CommandRegistry(config.commands)

    @staticmethod
    def create_subscribe_message() -> str:
        """创建订阅玩家消息事件的消息"""
        subscribe = MinecraftSubscribe.player_message()
        return subscribe.model_dump_json(exclude_none=True)

    def create_welcome_message(
        self,
        connection_id: str,
        model: str,
        provider: str,
        context_enabled: bool,
    ) -> str:
        """创建欢迎消息"""
        # 找到帮助命令的前缀
        help_prefix = self.command_registry.get_command_prefix("help") or "帮助"
        context_status = (
            self.config.context_enabled_text if context_enabled else self.config.context_disabled_text
        )
        return self.config.welcome_message_template.format(
            connection_id=connection_id[:8],
            provider=provider,
            model=model,
            context_status=context_status,
            help_command=help_prefix,
        )

    @staticmethod
    def parse_player_message(data: dict[str, Any]) -> PlayerMessageEvent | None:
        """
        解析玩家消息事件

        Args:
            data: WebSocket 消息数据

        Returns:
            PlayerMessageEvent 或 None
        """
        try:
            header = data.get("header", {})
            event_name = header.get("eventName")

            if event_name != "PlayerMessage":
                return None

            body = data.get("body", {})
            return PlayerMessageEvent.from_event_body(body)

        except Exception as e:
            logger.error(
                "parse_player_message_error",
                error=str(e),
                data=data,
            )
            return None

    def parse_command(self, message: str) -> tuple[str | None, str]:
        """
        解析命令

        Args:
            message: 玩家消息

        Returns:
            (命令类型, 内容) 元组
        """
        # 使用 CommandRegistry 进行命令解析（支持别名）
        return self.command_registry.resolve(message)

    @staticmethod
    def create_chat_request(
        state: ConnectionState,
        content: str,
        provider: str | None = None,
        delivery: str | None = None,
        player_name: str | None = None,
        conversation_id: str = "default",
    ) -> ChatRequest:
        """
        创建聊天请求

        Args:
            state: 连接状态
            content: 聊天内容
            provider: 可选的 LLM 提供商
            delivery: 下行通道
            player_name: 本次消息真实发送者；缺省时回退到 state.player_name
            conversation_id: 调用方按玩家状态解析出的当前对话 ID

        Returns:
            ChatRequest 对象
        """
        sender = player_name or state.player_name
        # 多人场景下优先取该玩家的 PlayerSession 设置
        session = state.get_player_session(sender)
        return ChatRequest(
            connection_id=state.id,
            content=content,
            player_name=sender,
            use_context=session.context_enabled,
            provider=provider or session.current_provider,
            delivery=delivery or "tellraw",
            conversation_id=conversation_id,
        )

    def get_help_text(self) -> str:
        """获取帮助文本（根据命令前缀动态生成）"""
        lines = ["可用命令:"]

        # 使用 CommandRegistry 获取命令列表
        for prefix, cmd_type, aliases in self.command_registry.list_all_commands():
            if cmd_type == "login":
                continue  # 登录命令不显示在帮助中

            cmd_config = self.command_registry.get_command_config(prefix)
            if cmd_config is None:
                cmd_desc, usage = self.config.get_command_description(cmd_type)
            else:
                cmd_desc, usage = cmd_config.description, cmd_config.usage
            if usage:
                lines.append(f"• {prefix} {usage} - {cmd_desc}")
            else:
                lines.append(f"• {prefix} - {cmd_desc}")

        return "\n".join(lines)

    def create_error_message(self, error: str) -> "TellrawMessage":
        """创建错误消息（结构化，由发送层统一分片）。"""
        return TellrawMessage(
            text=f"{self.config.error_prefix}{error}",
            color=self.config.error_color,
        )

    def create_info_message(self, info: str) -> "TellrawMessage":
        """创建信息消息（结构化，由发送层统一分片）。"""
        return TellrawMessage(
            text=f"{self.config.info_prefix}{info}",
            color=self.config.info_color,
        )

    def create_success_message(self, message: str) -> "TellrawMessage":
        """创建成功消息（结构化，由发送层统一分片）。"""
        return TellrawMessage(
            text=f"{self.config.success_prefix}{message}",
            color=self.config.success_color,
        )
