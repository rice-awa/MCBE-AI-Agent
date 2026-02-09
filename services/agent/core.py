"""PydanticAI Agent 核心定义"""

import re
from collections.abc import Iterator
from dataclasses import asdict, is_dataclass
from typing import Any, AsyncIterator

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model

from models.agent import AgentDependencies, StreamEvent
from config.logging import get_logger
from services.agent.tools import register_agent_tools

logger = get_logger(__name__)

TOOL_USAGE_GUIDE = """
你可以使用工具与 Minecraft 交互。
- 当用户要求“执行命令/给物品/发送消息/发标题/查询 Wiki”等可操作任务时，优先调用对应工具执行，而不是只解释步骤。
- 不要在有对应工具时直接说“我做不到”；若执行失败，要返回失败原因与下一步建议。
- 对于纯问答类问题，可直接回答。
""".strip()

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
    return f"{base_prompt}\n\n{TOOL_USAGE_GUIDE}{player_info}"


register_agent_tools(chat_agent)


SENTENCE_END_PATTERN = re.compile(r"[。！？\n.!?]+")
NON_STREAM_BATCH_MAX_CHARS = 800


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


def _extract_all_messages(result: Any) -> list[ModelMessage]:
    """兼容不同 pydantic-ai 版本，提取 all_messages。"""
    all_messages_attr = getattr(result, "all_messages", None)
    if callable(all_messages_attr):
        messages = all_messages_attr()
        if isinstance(messages, list):
            return messages
    return []


def _extract_result_usage(result: Any) -> Any | None:
    """兼容不同 pydantic-ai 版本，提取 usage。"""
    usage_attr = getattr(result, "usage", None)
    if callable(usage_attr):
        return usage_attr()
    return usage_attr


def _extract_result_output(result: Any) -> str:
    """兼容不同 pydantic-ai 版本，提取最终文本输出。"""
    for field_name in ("output", "data"):
        if hasattr(result, field_name):
            value = getattr(result, field_name)
            if value is None:
                continue
            if isinstance(value, str):
                return value
            return str(value)
    return ""


def _count_tool_parts(messages: list[ModelMessage]) -> tuple[int, int]:
    """统计消息链路中的 tool-call / tool-return 数量。"""
    tool_calls = 0
    tool_returns = 0

    for message in messages:
        parts = getattr(message, "parts", None)
        if not parts:
            continue

        for part in parts:
            part_kind = getattr(part, "part_kind", None)
            if part_kind == "tool-call":
                tool_calls += 1
            elif part_kind == "tool-return":
                tool_returns += 1

    return tool_calls, tool_returns


def _extract_complete_sentences(buffer: str) -> tuple[list[str], str]:
    """从缓冲区提取完整句子，并返回剩余未完成文本。"""
    sentences: list[str] = []
    last_end = 0

    for match in SENTENCE_END_PATTERN.finditer(buffer):
        sentence = buffer[last_end : match.end()]
        if sentence.strip():
            sentences.append(sentence)
        last_end = match.end()

    return sentences, buffer[last_end:]


def _iter_sentence_batches(text: str, max_chars: int = NON_STREAM_BATCH_MAX_CHARS) -> Iterator[str]:
    """按句子完整性分批，避免单次 WebSocket 发送过大。"""
    if not text:
        return

    sentences, tail = _extract_complete_sentences(text)
    if tail.strip():
        sentences.append(tail)

    batch = ""
    for sentence in sentences:
        # 极端情况下单句过长，退化为按长度切分，避免断链。
        if len(sentence) > max_chars:
            if batch.strip():
                yield batch
                batch = ""

            for start in range(0, len(sentence), max_chars):
                chunk = sentence[start : start + max_chars]
                if chunk.strip():
                    yield chunk
            continue

        if len(batch) + len(sentence) > max_chars and batch.strip():
            yield batch
            batch = sentence
            continue

        batch += sentence

    if batch.strip():
        yield batch


async def stream_chat(
    prompt: str,
    deps: AgentDependencies,
    model: Model | str | None = None,
    message_history: list[ModelMessage] | None = None,
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
    use_stream_mode = deps.settings.stream_sentence_mode
    sentence_buffer = ""
    full_response_parts: list[str] = []
    emitted_content = False
    all_messages: list[ModelMessage] = []
    serialized_usage: dict[str, Any] | None = None
    tool_calls = 0
    tool_returns = 0
    fallback_used = False

    try:
        # 使用 run_stream 进行流式处理
        async with chat_agent.run_stream(
            prompt,
            deps=deps,
            model=model,
            message_history=message_history,
        ) as result:
            # 流式输出文本内容
            async for chunk in result.stream_text(delta=True):
                full_response_parts.append(chunk)

                if use_stream_mode:
                    # 流式模式：缓冲并按完整句子发送
                    sentence_buffer += chunk
                    sentences, sentence_buffer = _extract_complete_sentences(sentence_buffer)

                    for sentence in sentences:
                        yield StreamEvent(
                            event_type="content",
                            content=sentence,
                            sequence=sequence,
                        )
                        emitted_content = True
                        sequence += 1

            all_messages = _extract_all_messages(result)
            serialized_usage = _serialize_usage(_extract_result_usage(result))

        tool_calls, tool_returns = _count_tool_parts(all_messages)

        # DeepSeek 流式场景下可能出现“返回了 tool_call，但未继续执行工具链”的情况。
        # 发现 tool_call/return 数不一致时，回退到非流式 run，确保链路完整：
        # 用户指令 -> tool_call -> tool_response -> 再次请求模型 -> 最终响应。
        if tool_calls > tool_returns:
            fallback_used = True
            logger.warning(
                "tool_chain_incomplete_on_stream",
                connection_id=str(deps.connection_id),
                tool_calls=tool_calls,
                tool_returns=tool_returns,
            )

            fallback_result = await chat_agent.run(
                prompt,
                deps=deps,
                model=model,
                message_history=message_history,
            )

            all_messages = _extract_all_messages(fallback_result)
            serialized_usage = _serialize_usage(_extract_result_usage(fallback_result))
            tool_calls, tool_returns = _count_tool_parts(all_messages)

            fallback_output = _extract_result_output(fallback_result)
            # 若前面已推送过内容，避免重复回复；仅用 fallback 来补齐工具执行链路与上下文。
            if fallback_output.strip() and not emitted_content:
                if use_stream_mode:
                    fallback_sentences, fallback_tail = _extract_complete_sentences(fallback_output)
                    if fallback_tail.strip():
                        fallback_sentences.append(fallback_tail)

                    for sentence in fallback_sentences:
                        yield StreamEvent(
                            event_type="content",
                            content=sentence,
                            sequence=sequence,
                        )
                        emitted_content = True
                        sequence += 1
                else:
                    for chunk in _iter_sentence_batches(fallback_output):
                        yield StreamEvent(
                            event_type="content",
                            content=chunk,
                            sequence=sequence,
                        )
                        emitted_content = True
                        sequence += 1
        else:
            if use_stream_mode and sentence_buffer.strip():
                yield StreamEvent(
                    event_type="content",
                    content=sentence_buffer,
                    sequence=sequence,
                )
                emitted_content = True
                sequence += 1

            if not use_stream_mode:
                full_response = "".join(full_response_parts)
                for chunk in _iter_sentence_batches(full_response):
                    yield StreamEvent(
                        event_type="content",
                        content=chunk,
                        sequence=sequence,
                    )
                    emitted_content = True
                    sequence += 1

        logger.debug(
            "tool_chain_status",
            connection_id=str(deps.connection_id),
            tool_calls=tool_calls,
            tool_returns=tool_returns,
            fallback_used=fallback_used,
        )

        # 发送完成事件
        yield StreamEvent(
            event_type="content",
            content="",
            sequence=sequence,
            metadata={
                "is_complete": True,
                "usage": serialized_usage,
                "all_messages": all_messages,
                "tool_calls": tool_calls,
                "tool_returns": tool_returns,
                "tool_fallback_used": fallback_used,
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
