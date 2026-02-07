"""PydanticAI Agent 核心定义"""

from dataclasses import asdict, is_dataclass
from typing import Any, AsyncIterator

from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model

from models.agent import AgentDependencies, StreamEvent
from config.logging import get_logger

logger = get_logger(__name__)

# 创建主聊天 Agent
chat_agent = Agent[AgentDependencies, str](
    "deepseek:deepseek-chat",  # 默认模型，运行时可覆盖
    deps_type=AgentDependencies,
    output_type=str,
    retries=2,
)


@chat_agent.system_prompt
async def dynamic_system_prompt(ctx: RunContext[AgentDependencies]) -> str:
    """动态系统提示词"""
    base_prompt = ctx.deps.settings.system_prompt
    player_info = f"\n\n当前玩家: {ctx.deps.player_name}"
    return base_prompt + player_info


@chat_agent.tool
async def run_minecraft_command(
    ctx: RunContext[AgentDependencies],
    command: str,
) -> str:
    """
    执行 Minecraft 命令

    Args:
        ctx: 运行上下文
        command: 要执行的命令（不包括前导斜杠）

    Returns:
        执行结果描述
    """
    logger.info(
        "agent_tool_call",
        tool="run_minecraft_command",
        command=command,
        connection_id=str(ctx.deps.connection_id),
    )

    try:
        await ctx.deps.run_command(command)
        return f"已执行命令: /{command}"
    except Exception as e:
        logger.error(
            "agent_tool_error",
            tool="run_minecraft_command",
            error=str(e),
        )
        return f"命令执行失败: {str(e)}"


@chat_agent.tool
async def send_game_message(
    ctx: RunContext[AgentDependencies],
    message: str,
) -> str:
    """
    向游戏发送消息

    Args:
        ctx: 运行上下文
        message: 要发送的消息

    Returns:
        发送结果
    """
    logger.info(
        "agent_tool_call",
        tool="send_game_message",
        connection_id=str(ctx.deps.connection_id),
    )

    try:
        await ctx.deps.send_to_game(message)
        return "消息已发送到游戏"
    except Exception as e:
        logger.error(
            "agent_tool_error",
            tool="send_game_message",
            error=str(e),
        )
        return f"消息发送失败: {str(e)}"


def _serialize_usage(usage: Any | None) -> dict[str, Any] | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    if hasattr(usage, "model_dump"):
        return usage.model_dump()  # type: ignore[no-any-return]
    if hasattr(usage, "dict"):
        return usage.dict()  # type: ignore[no-any-return]
    if is_dataclass(usage):
        return asdict(usage)
    return {"value": str(usage)}


async def stream_chat(
    prompt: str,
    deps: AgentDependencies,
    model: Model | str | None = None,
) -> AsyncIterator[StreamEvent]:
    """
    流式聊天处理

    Args:
        prompt: 用户输入
        deps: Agent 依赖
        model: 可选的模型覆盖

    Yields:
        StreamEvent: 流式事件
    """
    sequence = 0

    try:
        # 使用 run_stream 进行流式处理
        async with chat_agent.run_stream(
            prompt,
            deps=deps,
            model=model,
        ) as result:
            # 流式输出文本内容
            async for chunk in result.stream_text(delta=True):
                yield StreamEvent(
                    event_type="content",
                    content=chunk,
                    sequence=sequence,
                )
                sequence += 1

        # 发送完成事件
        yield StreamEvent(
            event_type="content",
            content="",
            sequence=sequence,
            metadata={
                "is_complete": True,
                "usage": _serialize_usage(result.usage()),
            },
        )

    except Exception as e:
        logger.error(
            "stream_chat_error",
            error=str(e),
            connection_id=str(deps.connection_id),
        )
        yield StreamEvent(
            event_type="error",
            content=f"聊天处理错误: {str(e)}",
            sequence=sequence,
        )
