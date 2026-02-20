"""Agent 相关模型"""

import json

from dataclasses import dataclass
from typing import Any, Callable, Awaitable
from uuid import UUID

import httpx

from config.settings import Settings


# Minecraft 颜色代码常量
class MCColor:
    """Minecraft 颜色代码常量"""

    GREEN = "§a"  # LLM 主要输出内容
    YELLOW = "§e"  # 工具调用信息
    GRAY = "§7"  # 思考内容
    RED = "§c"  # 错误信息
    WHITE = "§f"  # 默认白色
    AQUA = "§b"  # 信息提示
    GOLD = "§6"  # 强调信息
    DARK_GRAY = "§8"  # 次要信息


# 消息前缀常量
class MCPrefix:
    """消息前缀常量"""

    TOOL_CALL = "● "  # 工具调用前缀
    THINKING = "✻ "  # 思考内容前缀
    ERROR = "✖ "  # 错误前缀
    SUCCESS = "✓ "  # 成功前缀


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """
    截断文本，保留前 max_length 个字符并添加后缀

    Args:
        text: 原始文本
        max_length: 最大长度
        suffix: 截断后添加的后缀

    Returns:
        截断后的文本
    """
    if len(text) <= max_length:
        return text
    return text[:max_length] + suffix


def format_tool_call_message(tool_name: str, args: dict[str, Any] | str | None = None) -> str:
    """
    格式化工具调用消息

    Args:
        tool_name: 工具名称
        args: 工具参数（字典或 JSON 字符串）

    Returns:
        格式化后的消息
    """
    # 处理 args 可能是字符串的情况
    if args is not None:
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                # 如果不是有效的 JSON，直接显示原始字符串
                return f"{MCPrefix.TOOL_CALL}{tool_name}({args})"
        
        if isinstance(args, dict) and args:
            # 提取关键参数进行显示
            key_args = []
            for key, value in list(args.items())[:3]:  # 最多显示3个参数
                if isinstance(value, str) and len(value) > 20:
                    value = value[:20] + "..."
                key_args.append(f"{key}={repr(value)}")
            args_str = ", ".join(key_args)
            return f"{MCPrefix.TOOL_CALL}{tool_name}({args_str})"
    
    return f"{MCPrefix.TOOL_CALL}{tool_name}"


def format_tool_result_message(tool_name: str, result: str, max_length: int = 80) -> str:
    """
    格式化工具返回消息

    Args:
        tool_name: 工具名称
        result: 工具返回结果
        max_length: 结果最大显示长度

    Returns:
        格式化后的消息
    """
    truncated = truncate_text(result, max_length)
    return f"{MCPrefix.TOOL_CALL}{tool_name}: {truncated}"


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

    event_type: str  # "reasoning", "content", "tool_call", "tool_result", "error"
    content: str
    sequence: int
    metadata: dict[str, Any] | None = None
