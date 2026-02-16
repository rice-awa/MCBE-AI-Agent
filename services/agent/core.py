"""PydanticAI Agent 核心定义"""

import asyncio
import re
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

from pydantic_ai import (
    Agent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    RunContext,
    TextPartDelta,
)
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model

from config.logging import get_logger
from config.settings import Settings, get_settings
from models.agent import AgentDependencies, StreamEvent
from services.agent.tools import register_agent_tools
from services.agent.prompt import build_dynamic_prompt

logger = get_logger(__name__)

TOOL_USAGE_GUIDE = """
你可以使用工具与 Minecraft 交互。
- 当用户要求"执行命令/给物品/发送消息/发标题/查询 Wiki"等可操作任务时，优先调用对应工具执行，而不是只解释步骤。
- 不要在有对应工具时直接说"我做不到"；若执行失败，要返回失败原因与下一步建议。
- 对于纯问答类问题，可直接回答。
""".strip()


class ChatAgentManager:
    """
    聊天 Agent 管理器

    负责：
    - 管理 Agent 实例的生命周期
    - 延迟加载 MCP 工具集
    - 提供 Agent 实例给业务代码使用
    """

    def __init__(self):
        self._agent: Agent[AgentDependencies, str] | None = None
        self._mcp_toolsets: list[Any] = []
        self._initialized = False
        self._settings: Settings | None = None

    def _create_agent(
        self,
        toolsets: list[Any] | None = None,
    ) -> Agent[AgentDependencies, str]:
        """
        创建 Agent 实例

        Args:
            toolsets: MCP 工具集列表
        """
        agent = Agent[AgentDependencies, str](
            "deepseek:deepseek-chat",  # 默认模型，运行时可覆盖
            deps_type=AgentDependencies,
            output_type=str,
            retries=2,
            toolsets=toolsets,
        )

        @agent.system_prompt
        async def dynamic_system_prompt(ctx: RunContext[AgentDependencies]) -> str:
            """动态系统提示词 - 使用 PromptManager 构建"""
            return await build_dynamic_prompt(ctx)

        # 注册基础工具
        register_agent_tools(agent)

        return agent

    async def initialize(self, settings: Settings | None = None) -> None:
        """
        初始化 Agent 管理器

        Args:
            settings: 应用配置，不传则使用全局配置
        """
        if self._initialized:
            return

        self._settings = settings or get_settings()

        # 加载 MCP 工具集
        from services.agent.mcp import load_mcp_toolsets

        self._mcp_toolsets = load_mcp_toolsets(self._settings)

        # 创建带有 MCP 工具集的 Agent
        self._agent = self._create_agent(toolsets=self._mcp_toolsets if self._mcp_toolsets else None)

        self._initialized = True
        logger.info(
            "chat_agent_initialized",
            mcp_toolsets_count=len(self._mcp_toolsets),
        )

    def get_agent(self) -> Agent[AgentDependencies, str]:
        """
        获取 Agent 实例

        如果尚未初始化，会自动进行懒初始化（包含 MCP 工具）

        Returns:
            Agent 实例
        """
        if self._agent is None:
            # 懒初始化：自动加载 MCP 工具集
            if not self._initialized:
                import inspect

                # 检查是否在异步上下文中
                try:
                    loop = asyncio.get_running_loop()
                    # 如果在异步上下文中，使用 ensure_future 延迟初始化
                    # 但为简化逻辑，这里直接同步初始化（第一次调用时）
                except RuntimeError:
                    pass

                # 执行懒初始化
                self._settings = get_settings()
                from services.agent.mcp import load_mcp_toolsets

                self._mcp_toolsets = load_mcp_toolsets(self._settings)
                self._agent = self._create_agent(toolsets=self._mcp_toolsets if self._mcp_toolsets else None)
                self._initialized = True
                logger.info("chat_agent_created_lazy", mcp_toolsets_count=len(self._mcp_toolsets))

        return self._agent

    @property
    def mcp_toolsets(self) -> list[Any]:
        """获取 MCP 工具集列表"""
        return self._mcp_toolsets

    @property
    def is_initialized(self) -> bool:
        """是否已初始化"""
        return self._initialized


# 全局 Agent 管理器实例
_agent_manager: ChatAgentManager | None = None


def get_agent_manager() -> ChatAgentManager:
    """获取 Agent 管理器单例"""
    global _agent_manager
    if _agent_manager is None:
        _agent_manager = ChatAgentManager()
    return _agent_manager


# 为了向后兼容，保留 chat_agent 变量（推荐使用 get_agent_manager）
# 通过懒加载方式获取 Agent，确保 MCP 工具被正确加载


def _get_legacy_chat_agent() -> Agent[AgentDependencies, str]:
    """获取兼容性 Agent 实例（懒加载）"""
    return get_agent_manager().get_agent()


class _LegacyChatAgentProxy:
    """兼容性代理，支持直接访问 Agent 属性和方法"""

    def __getattr__(self, name: str):
        return getattr(_get_legacy_chat_agent(), name)

    def __call__(self, *args, **kwargs):
        return _get_legacy_chat_agent()(*args, **kwargs)


# 使用代理对象，保持向后兼容
chat_agent = _LegacyChatAgentProxy()  # type: ignore[assignment]


SENTENCE_END_PATTERN = re.compile(r"[。！？\n.!?]+")
NON_STREAM_BATCH_MAX_CHARS = 150  # 减小批次大小，避免 MC 崩溃
NON_STREAM_SEND_DELAY = 0.1  # 非流式模式下每个包之间的延迟（秒）


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


def _extract_tool_events_from_messages(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """从消息列表中提取工具调用事件（用于非流式模式回填元数据）。"""
    tool_events: list[dict[str, Any]] = []

    for message in messages:
        parts = getattr(message, "parts", None)
        if not parts:
            continue

        for part in parts:
            if getattr(part, "part_kind", None) != "tool-call":
                continue

            tool_name = getattr(part, "tool_name", None)
            if not tool_name:
                continue

            tool_events.append(
                {
                    "tool_name": tool_name,
                    "tool_call_id": getattr(part, "tool_call_id", None),
                    "args": getattr(part, "args", None),
                }
            )

    return tool_events


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
    tool_events: list[dict[str, Any]] = field(default_factory=list)  # 记录工具调用事件


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
    agent: Agent[AgentDependencies, str] | None = None,
) -> AsyncIterator[StreamEvent]:
    """
    流式响应处理器（基于 agent.iter() 官方推荐方式）

    处理流程：
    1. 使用 agent.iter() 遍历执行节点
    2. 在 ModelRequestNode 中流式输出文本
    3. 在 CallToolsNode 中处理工具调用事件
    4. 工具调用由 Pydantic AI 自动执行

    Args:
        prompt: 用户输入
        deps: Agent 依赖
        model: 可选的模型覆盖
        message_history: 消息历史
        ctx: 处理器上下文
        agent: 可选的 Agent 实例，不传则使用管理器中的 Agent

    Yields:
        StreamEvent: 流式事件
    """
    if ctx is None:
        ctx = _HandlerContext()

    # 使用传入的 agent 或从管理器获取
    active_agent = agent or get_agent_manager().get_agent()

    try:
        # 使用 agent.iter() 进行细粒度节点控制
        async with active_agent.iter(
            prompt,
            deps=deps,
            model=model,
            message_history=message_history,
        ) as run:
            async for node in run:
                # 处理用户提示节点
                if Agent.is_user_prompt_node(node):
                    logger.debug(
                        "agent_user_prompt",
                        prompt=node.user_prompt,
                        connection_id=str(deps.connection_id),
                    )

                # 处理模型请求节点 - 流式输出文本
                elif Agent.is_model_request_node(node):
                    async with node.stream(run.ctx) as request_stream:
                        async for event in request_stream:
                            # 处理部分开始事件
                            if isinstance(event, PartStartEvent):
                                logger.debug(
                                    "agent_part_start",
                                    index=event.index,
                                    connection_id=str(deps.connection_id),
                                    current_buffer=ctx.sentence_buffer,
                                )
                                # 重要：PartStartEvent.part 可能包含初始文本内容！
                                # 如果是 TextPart 且有内容，需要处理
                                part = event.part
                                if hasattr(part, "content") and part.content:
                                    logger.debug(
                                        "agent_part_start_content",
                                        index=event.index,
                                        content=part.content,
                                        connection_id=str(deps.connection_id),
                                    )
                                    # 将 PartStartEvent 中的初始内容添加到 buffer
                                    ctx.sentence_buffer += part.content
                                    sentences, ctx.sentence_buffer = (
                                        _extract_complete_sentences(
                                            ctx.sentence_buffer
                                        )
                                    )
                                    for sentence in sentences:
                                        yield await _send_content_event(
                                            ctx, sentence
                                        )

                            # 处理增量事件
                            elif isinstance(event, PartDeltaEvent):
                                if isinstance(event.delta, TextPartDelta):
                                    # 文本增量 - 缓冲并按完整句子发送
                                    chunk = event.delta.content_delta
                                    logger.debug(
                                        "agent_text_delta",
                                        chunk=chunk,
                                        index=event.index,
                                        connection_id=str(deps.connection_id),
                                        buffer_before=ctx.sentence_buffer,
                                    )
                                    if chunk:
                                        ctx.sentence_buffer += chunk
                                        sentences, ctx.sentence_buffer = (
                                            _extract_complete_sentences(
                                                ctx.sentence_buffer
                                            )
                                        )
                                        logger.debug(
                                            "agent_sentences_extracted",
                                            sentences=sentences,
                                            buffer_after=ctx.sentence_buffer,
                                            connection_id=str(deps.connection_id),
                                        )
                                        for sentence in sentences:
                                            yield await _send_content_event(
                                                ctx, sentence
                                            )

                # 处理工具调用节点 - 工具由框架自动执行
                elif Agent.is_call_tools_node(node):
                    async with node.stream(run.ctx) as handle_stream:
                        async for event in handle_stream:
                            # 工具调用事件
                            if isinstance(event, FunctionToolCallEvent):
                                tool_info = {
                                    "tool_name": event.part.tool_name,
                                    "tool_call_id": event.part.tool_call_id,
                                    "args": event.part.args,
                                }
                                ctx.tool_events.append(tool_info)
                                logger.info(
                                    "agent_tool_call",
                                    tool=event.part.tool_name,
                                    args=event.part.args,
                                    tool_call_id=event.part.tool_call_id,
                                    connection_id=str(deps.connection_id),
                                )

                            # 工具返回事件
                            elif isinstance(event, FunctionToolResultEvent):
                                logger.info(
                                    "agent_tool_result",
                                    tool_call_id=event.tool_call_id,
                                    result_preview=str(event.result.content)[:100],
                                    connection_id=str(deps.connection_id),
                                )

                # 处理结束节点
                elif Agent.is_end_node(node):
                    # 仅在整个执行结束时再冲刷尾部 buffer，避免跨节点半句提前下发
                    if ctx.sentence_buffer.strip():
                        yield await _send_content_event(ctx, ctx.sentence_buffer)
                        ctx.sentence_buffer = ""

                    # 从最终结果中提取信息
                    if run.result is not None:
                        ctx.all_messages = _extract_all_messages(run.result)
                        ctx.new_messages = _extract_new_messages(run.result)
                        ctx.serialized_usage = _serialize_usage(
                            _extract_result_usage(run.result)
                        )
                    logger.debug(
                        "agent_run_complete",
                        connection_id=str(deps.connection_id),
                        tool_events_count=len(ctx.tool_events),
                    )

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
    agent: Agent[AgentDependencies, str] | None = None,
) -> AsyncIterator[StreamEvent]:
    """
    非流式响应处理器

    处理流程：
    1. 使用 run 获取完整响应（工具调用由框架自动处理）
    2. 使用 _extract_complete_sentences 匹配完整句子
    3. 逐一发送完整句子（带延迟以避免 MC 崩溃）

    Args:
        prompt: 用户输入
        deps: Agent 依赖
        model: 可选的模型覆盖
        message_history: 消息历史
        ctx: 处理器上下文
        agent: 可选的 Agent 实例，不传则使用管理器中的 Agent

    Yields:
        StreamEvent: 流式事件
    """
    if ctx is None:
        ctx = _HandlerContext()

    # 使用传入的 agent 或从管理器获取
    active_agent = agent or get_agent_manager().get_agent()

    try:
        result = await active_agent.run(
            prompt,
            deps=deps,
            model=model,
            message_history=message_history,
        )

        # 提取处理结果
        ctx.new_messages = _extract_new_messages(result)
        ctx.all_messages = _extract_all_messages(result)
        ctx.serialized_usage = _serialize_usage(_extract_result_usage(result))
        ctx.tool_events = _extract_tool_events_from_messages(ctx.new_messages)

        # 提取完整响应文本
        full_response = _extract_result_output(result)
        if not full_response:
            return

        # 使用 _extract_complete_sentences 提取完整句子
        # 为了保持 max_chars 限制，使用 _iter_sentence_batches
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


async def stream_chat(
    prompt: str,
    deps: AgentDependencies,
    model: Model | str | None = None,
    message_history: list[ModelMessage] | None = None,
    agent: Agent[AgentDependencies, str] | None = None,
) -> AsyncIterator[StreamEvent]:
    """
    流式聊天处理主调度函数

    根据 stream_sentence_mode 配置选择对应的处理器。
    工具调用由 Pydantic AI 框架自动处理，无需手动回退。

    Args:
        prompt: 用户输入
        deps: Agent 依赖
        model: 可选的模型覆盖
        message_history: 消息历史
        agent: 可选的 Agent 实例，不传则使用管理器中的 Agent

    Yields:
        StreamEvent: 流式事件
    """
    use_stream_mode = deps.settings.stream_sentence_mode
    ctx = _HandlerContext()

    try:
        # 根据模式选择处理器
        if use_stream_mode:
            # 流式模式处理
            async for event in stream_response_handler(
                prompt, deps, model, message_history, ctx, agent
            ):
                if event.event_type == "error":
                    yield event
                    return
                yield event
        else:
            # 非流式模式处理
            async for event in non_stream_response_handler(
                prompt, deps, model, message_history, ctx, agent
            ):
                if event.event_type == "error":
                    yield event
                    return
                yield event

        logger.debug(
            "chat_complete",
            connection_id=str(deps.connection_id),
            tool_events_count=len(ctx.tool_events),
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
                "tool_events": ctx.tool_events,
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
