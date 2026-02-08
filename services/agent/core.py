"""PydanticAI Agent 核心定义"""

import re
from dataclasses import asdict, is_dataclass
from typing import Any, AsyncIterator

from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model

from models.agent import AgentDependencies, StreamEvent
from config.logging import get_logger
from services.agent.tools import register_agent_tools

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


register_agent_tools(chat_agent)


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
    use_sentence_mode = deps.settings.stream_sentence_mode
    
    # 句子模式：缓冲器和正则匹配
    if use_sentence_mode:
        sentence_end_pattern = re.compile(r'[。！？\n.!?]+')
        buffer = ""

    try:
        # 使用 run_stream 进行流式处理
        async with chat_agent.run_stream(
            prompt,
            deps=deps,
            model=model,
        ) as result:
            # 流式输出文本内容
            async for chunk in result.stream_text(delta=True):
                if use_sentence_mode:
                    # 句子模式：缓冲并按完整句子发送
                    buffer += chunk
                    
                    # 查找所有完整句子
                    sentences = []
                    last_end = 0
                    for match in sentence_end_pattern.finditer(buffer):
                        # 提取句子（包含结束符）
                        sentence = buffer[last_end : match.end()]
                        sentences.append(sentence)
                        last_end = match.end()
                    
                    # 发送完整句子
                    for sentence in sentences:
                        if sentence.strip():  # 跳过纯空白句子
                            yield StreamEvent(
                                event_type="content",
                                content=sentence,
                                sequence=sequence,
                            )
                            sequence += 1
                    
                    # 保留未完成的部分
                    buffer = buffer[last_end:]
                else:
                    # 实时模式：直接发送每个 chunk
                    yield StreamEvent(
                        event_type="content",
                        content=chunk,
                        sequence=sequence,
                    )
                    sequence += 1

        # 句子模式：发送剩余缓冲内容（如果有）
        if use_sentence_mode and buffer.strip():
            yield StreamEvent(
                event_type="content",
                content=buffer,
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
