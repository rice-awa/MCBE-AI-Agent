"""PydanticAI Agent 核心定义"""

import asyncio
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field, is_dataclass
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
NON_STREAM_BATCH_MAX_CHARS = 200  # 减小批次大小，避免 MC 崩溃
NON_STREAM_SEND_DELAY = 0.05  # 非流式模式下每个包之间的延迟（秒）
MAX_TOOL_FALLBACK_ATTEMPTS = 3
TOOL_CHAIN_CONTINUE_PROMPT = "请继续完成工具调用链并给出最终回复。"


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


def _extract_new_messages(result: Any) -> list[ModelMessage]:
    """兼容不同 pydantic-ai 版本，提取 new_messages（增量消息）。"""
    new_messages_attr = getattr(result, "new_messages", None)
    if callable(new_messages_attr):
        messages = new_messages_attr()
        if isinstance(messages, list):
            return messages
    # 回退到 all_messages
    return _extract_all_messages(result)


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


def _append_sent_text(sent_text: str, new_text: str) -> str:
    if not new_text:
        return sent_text
    return f"{sent_text}{new_text}"


def _diff_fallback_text(fallback_text: str, sent_text: str) -> str:
    if not sent_text:
        return fallback_text
    if fallback_text.startswith(sent_text):
        return fallback_text[len(sent_text) :]
    return ""


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


@dataclass
class _HandlerContext:
    """处理器上下文，用于跟踪处理状态"""

    sequence: int = 0
    sentence_buffer: str = ""
    sent_text: str = ""
    emitted_content: bool = False
    all_messages: list[ModelMessage] = field(default_factory=list)
    new_messages: list[ModelMessage] = field(default_factory=list)
    serialized_usage: dict[str, Any] | None = None
    tool_calls: int = 0
    tool_returns: int = 0


async def _send_content_event(
    ctx: _HandlerContext,
    content: str,
    add_delay: bool = False,
) -> StreamEvent:
    """发送内容事件并更新上下文"""
    event = StreamEvent(
        event_type="content",
        content=content,
        sequence=ctx.sequence,
    )
    ctx.emitted_content = True
    ctx.sent_text = _append_sent_text(ctx.sent_text, content)
    ctx.sequence += 1
    if add_delay:
        await asyncio.sleep(NON_STREAM_SEND_DELAY)
    return event


async def stream_response_handler(
    prompt: str,
    deps: AgentDependencies,
    model: Model | str | None = None,
    message_history: list[ModelMessage] | None = None,
    ctx: _HandlerContext | None = None,
) -> AsyncIterator[StreamEvent]:
    """
    流式响应处理器

    处理流程：
    1. 使用 run_stream 获取流式 chunks
    2. 提取内容并添加到缓冲区
    3. 使用 _extract_complete_sentences 匹配完整句子
    4. 逐一发送完整句子

    Args:
        prompt: 用户输入
        deps: Agent 依赖
        model: 可选的模型覆盖
        message_history: 消息历史
        ctx: 处理器上下文（用于工具链回退时延续状态）

    Yields:
        StreamEvent: 流式事件
    """
    if ctx is None:
        ctx = _HandlerContext()

    try:
        async with chat_agent.run_stream(
            prompt,
            deps=deps,
            model=model,
            message_history=message_history,
        ) as result:
            # 流式输出文本内容
            async for chunk in result.stream_text(delta=True):
                # 缓冲并按完整句子发送
                ctx.sentence_buffer += chunk
                sentences, ctx.sentence_buffer = _extract_complete_sentences(
                    ctx.sentence_buffer
                )

                for sentence in sentences:
                    yield await _send_content_event(ctx, sentence)

            # 发送缓冲区剩余内容
            if ctx.sentence_buffer.strip():
                yield await _send_content_event(ctx, ctx.sentence_buffer)
                ctx.sentence_buffer = ""

            # 提取处理结果
            ctx.new_messages = _extract_new_messages(result)
            ctx.all_messages = _extract_all_messages(result)
            ctx.serialized_usage = _serialize_usage(_extract_result_usage(result))
            ctx.tool_calls, ctx.tool_returns = _count_tool_parts(ctx.all_messages)

    except Exception as e:
        logger.error(
            "stream_response_handler_error",
            error=str(e),
            connection_id=str(deps.connection_id),
        )
        yield StreamEvent(
            event_type="error",
            content=f"流式响应处理错误: {str(e)}",
            sequence=ctx.sequence,
        )


async def non_stream_response_handler(
    prompt: str,
    deps: AgentDependencies,
    model: Model | str | None = None,
    message_history: list[ModelMessage] | None = None,
    ctx: _HandlerContext | None = None,
) -> AsyncIterator[StreamEvent]:
    """
    非流式响应处理器

    处理流程：
    1. 使用 run 获取完整响应
    2. 使用 _extract_complete_sentences 匹配完整句子
    3. 逐一发送完整句子（带延迟以避免 MC 崩溃）

    Args:
        prompt: 用户输入
        deps: Agent 依赖
        model: 可选的模型覆盖
        message_history: 消息历史
        ctx: 处理器上下文（用于工具链回退时延续状态）

    Yields:
        StreamEvent: 流式事件
    """
    if ctx is None:
        ctx = _HandlerContext()

    try:
        result = await chat_agent.run(
            prompt,
            deps=deps,
            model=model,
            message_history=message_history,
        )

        # 提取处理结果
        ctx.new_messages = _extract_new_messages(result)
        ctx.all_messages = _extract_all_messages(result)
        ctx.serialized_usage = _serialize_usage(_extract_result_usage(result))
        ctx.tool_calls, ctx.tool_returns = _count_tool_parts(ctx.all_messages)

        # 提取完整响应文本
        full_response = _extract_result_output(result)
        if not full_response:
            return

        # 使用 _extract_complete_sentences 提取完整句子
        # 为了保持 max_chars 限制，使用 _iter_sentence_batches
        # （它内部也使用 _extract_complete_sentences）
        for chunk in _iter_sentence_batches(full_response):
            yield await _send_content_event(ctx, chunk, add_delay=True)

    except Exception as e:
        logger.error(
            "non_stream_response_handler_error",
            error=str(e),
            connection_id=str(deps.connection_id),
        )
        yield StreamEvent(
            event_type="error",
            content=f"非流式响应处理错误: {str(e)}",
            sequence=ctx.sequence,
        )


async def _handle_tool_chain_fallback(
    ctx: _HandlerContext,
    deps: AgentDependencies,
    model: Model | str | None,
    message_history: list[ModelMessage] | None,
    use_stream_mode: bool,
) -> AsyncIterator[StreamEvent]:
    """
    处理工具链不完整的情况

    某些模型（如 DeepSeek）可能在流式模式下返回 tool_call 但未完成工具链执行。
    发现 tool_call/return 数不一致时，使用非流式 run 完成链路：
    用户指令 -> tool_call -> tool_response -> 再次请求模型 -> 最终响应。
    """
    fallback_history = (message_history or []) + ctx.new_messages
    fallback_prompt = TOOL_CHAIN_CONTINUE_PROMPT
    attempt = 0

    while attempt < MAX_TOOL_FALLBACK_ATTEMPTS and ctx.tool_calls > ctx.tool_returns:
        attempt += 1
        logger.warning(
            "tool_chain_incomplete_on_stream",
            connection_id=str(deps.connection_id),
            tool_calls=ctx.tool_calls,
            tool_returns=ctx.tool_returns,
            attempt=attempt,
        )

        fallback_result = await chat_agent.run(
            fallback_prompt,
            deps=deps,
            model=model,
            message_history=fallback_history,
        )

        fallback_new_messages = _extract_new_messages(fallback_result)
        ctx.all_messages = _extract_all_messages(fallback_result)
        ctx.serialized_usage = _serialize_usage(_extract_result_usage(fallback_result))
        ctx.tool_calls, ctx.tool_returns = _count_tool_parts(ctx.all_messages)

        fallback_output = _extract_result_output(fallback_result)
        fallback_delta = _diff_fallback_text(fallback_output, ctx.sent_text)

        if fallback_output.strip() and not fallback_delta:
            logger.warning(
                "tool_fallback_output_mismatch",
                connection_id=str(deps.connection_id),
                sent_text_length=len(ctx.sent_text),
                fallback_length=len(fallback_output),
            )

        # 发送增量文本（避免重复已发送的内容）
        if fallback_delta.strip():
            if use_stream_mode:
                # 流式模式：使用 _extract_complete_sentences
                sentences, tail = _extract_complete_sentences(fallback_delta)
                if tail.strip():
                    sentences.append(tail)

                for sentence in sentences:
                    yield await _send_content_event(ctx, sentence)
            else:
                # 非流式模式：使用 _iter_sentence_batches（带延迟）
                for chunk in _iter_sentence_batches(fallback_delta):
                    yield await _send_content_event(ctx, chunk, add_delay=True)

        if ctx.tool_calls <= ctx.tool_returns:
            break

        # 累积新的消息到历史
        fallback_history = fallback_history + fallback_new_messages


async def stream_chat(
    prompt: str,
    deps: AgentDependencies,
    model: Model | str | None = None,
    message_history: list[ModelMessage] | None = None,
) -> AsyncIterator[StreamEvent]:
    """
    流式聊天处理主调度函数

    根据 stream_sentence_mode 配置选择对应的处理器，
    并统一处理工具链回退逻辑。

    Args:
        prompt: 用户输入
        deps: Agent 依赖
        model: 可选的模型覆盖
        message_history: 消息历史

    Yields:
        StreamEvent: 流式事件
    """
    use_stream_mode = deps.settings.stream_sentence_mode
    ctx = _HandlerContext()
    fallback_used = False

    try:
        # 根据模式选择处理器
        if use_stream_mode:
            # 流式模式处理
            async for event in stream_response_handler(
                prompt, deps, model, message_history, ctx
            ):
                if event.event_type == "error":
                    yield event
                    return
                yield event
        else:
            # 非流式模式处理
            async for event in non_stream_response_handler(
                prompt, deps, model, message_history, ctx
            ):
                if event.event_type == "error":
                    yield event
                    return
                yield event

        # 处理工具链不完整的情况
        if ctx.tool_calls > ctx.tool_returns:
            fallback_used = True
            async for event in _handle_tool_chain_fallback(
                ctx, deps, model, message_history, use_stream_mode
            ):
                if event.event_type == "error":
                    yield event
                    return
                yield event

        logger.debug(
            "tool_chain_status",
            connection_id=str(deps.connection_id),
            tool_calls=ctx.tool_calls,
            tool_returns=ctx.tool_returns,
            fallback_used=fallback_used,
        )

        # 发送完成事件
        yield StreamEvent(
            event_type="content",
            content="",
            sequence=ctx.sequence,
            metadata={
                "is_complete": True,
                "usage": ctx.serialized_usage,
                "all_messages": ctx.all_messages,
                "tool_calls": ctx.tool_calls,
                "tool_returns": ctx.tool_returns,
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
            sequence=ctx.sequence,
        )
