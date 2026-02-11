"""Minecraft 协议处理"""

import json
from typing import Any

from models.minecraft import (
    MinecraftMessage,
    MinecraftSubscribe,
    MinecraftCommand,
    PlayerMessageEvent,
)
from models.messages import ChatRequest
from services.websocket.connection import ConnectionState
from config.logging import get_logger
from config.settings import get_settings

logger = get_logger(__name__)

# 获取配置
_settings = get_settings()
_minecraft_config = _settings.minecraft

# 命令前缀定义（从配置读取）
COMMANDS = _minecraft_config.commands


class MinecraftProtocolHandler:
    """Minecraft WebSocket 协议处理器"""

    @staticmethod
    def create_subscribe_message() -> str:
        """创建订阅玩家消息事件的消息"""
        subscribe = MinecraftSubscribe.player_message()
        return subscribe.model_dump_json(exclude_none=True)

    @staticmethod
    def create_welcome_message(
        connection_id: str,
        model: str,
        provider: str,
        context_enabled: bool,
    ) -> str:
        """创建欢迎消息"""
        config = _minecraft_config
        # 找到帮助命令的前缀
        help_prefix = next(
            (prefix for prefix, cmd in config.commands.items() if cmd == "help"),
            "帮助"
        )
        context_status = (
            config.context_enabled_text if context_enabled else config.context_disabled_text
        )
        return config.welcome_message_template.format(
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

    @staticmethod
    def parse_command(message: str) -> tuple[str | None, str]:
        """
        解析命令

        Args:
            message: 玩家消息

        Returns:
            (命令类型, 内容) 元组
        """
        for cmd_prefix, cmd_type in COMMANDS.items():
            if message.startswith(cmd_prefix):
                content = message[len(cmd_prefix) :].strip()
                return cmd_type, content

        return None, message

    @staticmethod
    def create_chat_request(
        state: ConnectionState,
        content: str,
        provider: str | None = None,
        delivery: str | None = None,
    ) -> ChatRequest:
        """
        创建聊天请求

        Args:
            state: 连接状态
            content: 聊天内容
            provider: 可选的 LLM 提供商

        Returns:
            ChatRequest 对象
        """
        return ChatRequest(
            connection_id=state.id,
            content=content,
            player_name=state.player_name,
            use_context=state.context_enabled,
            provider=provider or state.current_provider,
            delivery=delivery or "tellraw",
        )

    @staticmethod
    def get_help_text() -> str:
        """获取帮助文本（根据命令前缀动态生成）"""
        config = _minecraft_config
        lines = ["可用命令:"]

        # 根据 commands 顺序生成帮助文本
        for prefix, cmd_type in config.commands.items():
            if cmd_type == "login":
                continue  # 登录命令不显示在帮助中
            if cmd_type in config.command_help:
                desc, usage = config.command_help[cmd_type]
                if usage:
                    lines.append(f"• {prefix} {usage} - {desc}")
                else:
                    lines.append(f"• {prefix} - {desc}")

        return "\n".join(lines)

    @staticmethod
    def create_error_message(error: str) -> str:
        """创建错误消息"""
        config = _minecraft_config
        return MinecraftCommand.create_tellraw(
            f"{config.error_prefix}{error}", color=config.error_color
        ).model_dump_json(exclude_none=True)

    @staticmethod
    def create_info_message(info: str) -> str:
        """创建信息消息"""
        config = _minecraft_config
        return MinecraftCommand.create_tellraw(
            f"{config.info_prefix}{info}", color=config.info_color
        ).model_dump_json(exclude_none=True)

    @staticmethod
    def create_success_message(message: str) -> str:
        """创建成功消息"""
        config = _minecraft_config
        return MinecraftCommand.create_tellraw(
            f"{config.success_prefix}{message}", color=config.success_color
        ).model_dump_json(exclude_none=True)
