"""Agent 相关模型"""

import json

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol
from uuid import UUID, uuid4

import httpx

if TYPE_CHECKING:
    from services.agent.tool_results import CommandResult
    from services.agent.trace import TraceContext, TraceRecorder


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


# 游戏内工具消息默认截断长度（完整参数/结果仍记入日志）
DEFAULT_TOOL_CALL_DISPLAY_MAX_LENGTH = 120
DEFAULT_TOOL_ARG_VALUE_MAX_LENGTH = 20
DEFAULT_TOOL_RESULT_DISPLAY_MAX_LENGTH = 80


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """
    截断文本，保留前 max_length 个字符并添加后缀

    Args:
        text: 原始文本
        max_length: 最大长度（不含 suffix）
        suffix: 截断后添加的后缀

    Returns:
        截断后的文本
    """
    if max_length <= 0:
        return suffix if text else text
    if len(text) <= max_length:
        return text
    return text[:max_length] + suffix


def _format_tool_arg_value(
    value: Any,
    *,
    max_length: int = DEFAULT_TOOL_ARG_VALUE_MAX_LENGTH,
) -> str:
    """将工具参数值格式化为适合游戏内显示的短文本。"""
    if isinstance(value, str):
        display = value
    else:
        try:
            display = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            display = repr(value)
    return truncate_text(display, max_length)


def format_tool_call_message(
    tool_name: str,
    args: dict[str, Any] | str | None = None,
    *,
    max_length: int = DEFAULT_TOOL_CALL_DISPLAY_MAX_LENGTH,
    max_arg_value_length: int = DEFAULT_TOOL_ARG_VALUE_MAX_LENGTH,
    max_args: int = 3,
) -> str:
    """
    格式化游戏内工具调用消息（过长截断为 ...；完整参数由调用方写日志）。

    Args:
        tool_name: 工具名称
        args: 工具参数（字典或 JSON 字符串）
        max_length: 整条游戏内消息的最大长度
        max_arg_value_length: 单个参数值的最大显示长度
        max_args: 最多展示的参数个数

    Returns:
        格式化后的游戏内消息
    """
    # 处理 args 可能是字符串的情况
    if args is not None:
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                # 如果不是有效的 JSON，直接显示原始字符串（再整体截断）
                raw = f"{MCPrefix.TOOL_CALL}{tool_name}({args})"
                return truncate_text(raw, max_length)

        if isinstance(args, dict) and args:
            # 提取关键参数进行显示
            items = list(args.items())
            key_args = []
            for key, value in items[:max_args]:
                key_args.append(f"{key}={_format_tool_arg_value(value, max_length=max_arg_value_length)}")
            if len(items) > max_args:
                key_args.append("...")
            args_str = ", ".join(key_args)
            message = f"{MCPrefix.TOOL_CALL}{tool_name}({args_str})"
            return truncate_text(message, max_length)

    return f"{MCPrefix.TOOL_CALL}{tool_name}"


def format_tool_result_message(
    tool_name: str,
    result: str,
    max_length: int = DEFAULT_TOOL_RESULT_DISPLAY_MAX_LENGTH,
) -> str:
    """
    格式化游戏内工具返回消息（过长截断为 ...；完整结果由调用方写日志）。

    Args:
        tool_name: 工具名称
        result: 工具返回结果
        max_length: 结果最大显示长度

    Returns:
        格式化后的游戏内消息
    """
    truncated = truncate_text(result, max_length)
    return f"{MCPrefix.TOOL_CALL}{tool_name}: {truncated}"


@dataclass
class ContextInfo:
    """上下文使用信息"""

    message_count: int  # 消息数量
    estimated_tokens: int  # 估计的 token 数
    max_tokens: int | None  # 模型最大上下文


class AddonBridgeClient(Protocol):
    """Addon 桥接客户端协议。"""

    async def request(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        """向 addon 请求能力结果。"""


@dataclass
class AgentDependencies:
    """Agent 运行时依赖注入"""

    connection_id: UUID
    player_name: str
    settings: Any
    http_client: httpx.AsyncClient
    send_to_game: Callable[[str], Awaitable[None]]
    run_command: Callable[[str], Awaitable["CommandResult"]]
    addon_bridge: AddonBridgeClient | None = None
    provider: str | None = None  # 当前使用的 LLM 提供商
    # 获取上下文使用信息的回调，返回 (消息数, 估计token数, 最大token数)
    get_context_info: Callable[[], ContextInfo | None] | None = None
    # 单次 run 身份：关联模型请求、工具调用与完成事件
    run_id: str = field(default_factory=lambda: str(uuid4()))
    # attempt / TraceContext / TraceRecorder：每个已接受请求由 Worker 注入。
    attempt_id: str | None = None
    conversation_id: str | None = None
    # 会话级自动批准（来自 ChatRequest；策略引擎据此跳过审批提示）
    auto_approve_tools: bool = False
    # Trace 相关：缺失时 harness/worker 以 fail-soft 跳过记录
    trace_context: "TraceContext | None" = None
    trace_recorder: "TraceRecorder | None" = None


@dataclass
class AgentResponse:
    """Agent 响应结果"""

    content: str
    reasoning: str | None = None
    tool_events: list[dict[str, Any]] | None = None
    token_usage: dict[str, int] | None = None
    is_complete: bool = True
    run_id: str | None = None
    conversation_id: str | None = None
    player_name: str | None = None


@dataclass
class StreamEvent:
    """流式事件"""

    event_type: str  # "reasoning", "content", "tool_call", "tool_result", "error"
    content: str
    sequence: int
    metadata: dict[str, Any] | None = None
