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

logger = get_logger(__name__)

# 命令前缀定义
COMMANDS = {
    "#登录": "login",
    "AGENT 聊天": "chat",
    "AGENT 脚本": "chat_script",
    "AGENT 保存": "save",
    "AGENT 上下文": "context",
    "运行命令": "run_command",
    "切换模型": "switch_model",
    "帮助": "help",
}


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
        message = f"""-----------
成功连接 MCBE AI Agent v2.0
连接 ID: {connection_id[:8]}...
当前模型: {provider}/{model}
上下文: {'启用' if context_enabled else '关闭'}
-----------
使用 "帮助" 查看可用命令
        """.strip()
        return message

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
        """获取帮助文本"""
        return """
可用命令:
• AGENT 聊天 <内容> - 与 AI 对话
• AGENT 脚本 <内容> - 使用脚本事件发送
• AGENT 上下文 <启用/关闭/状态> - 管理上下文
• 切换模型 <provider> - 切换 LLM (deepseek/openai/anthropic/ollama)
• AGENT 保存 - 保存对话历史
• 运行命令 <命令> - 执行游戏命令
• 帮助 - 显示此帮助
        """.strip()

    @staticmethod
    def create_error_message(error: str) -> str:
        """创建错误消息"""
        return MinecraftCommand.create_tellraw(
            f"❌ 错误: {error}", color="§c"
        ).model_dump_json(exclude_none=True)

    @staticmethod
    def create_info_message(info: str) -> str:
        """创建信息消息"""
        return MinecraftCommand.create_tellraw(
            f"ℹ️ {info}", color="§b"
        ).model_dump_json(exclude_none=True)

    @staticmethod
    def create_success_message(message: str) -> str:
        """创建成功消息"""
        return MinecraftCommand.create_tellraw(
            f"✅ {message}", color="§a"
        ).model_dump_json(exclude_none=True)
