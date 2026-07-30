"""PydanticAI Agent 核心定义"""

import asyncio
import re
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Protocol, cast

from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)
from pydantic_ai.models import Model
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.usage import UsageLimits

from config.logging import get_logger
from config.settings import Settings, get_settings
from models.agent import AgentDependencies, StreamEvent
from services.agent.context import build_context_history_processor
from services.agent.harness.execution import build_harness_capability
from services.agent.tool_results import PLAYER_ERROR_ADVICE, ErrorKind
from services.agent.tools import register_agent_tools
from services.agent.prompt import build_dynamic_prompt

logger = get_logger(__name__)


class StreamModeSettings(Protocol):
    stream_sentence_mode: bool
    request_limit: int
    tool_calls_limit: int
    input_tokens_limit: int | None
    output_tokens_limit: int | None
    total_tokens_limit: int | None
    run_timeout: float
    max_tool_concurrency: int
    context_output_reserve_tokens: int


class RunBudgetSettings(Protocol):
    request_limit: int
    tool_calls_limit: int
    input_tokens_limit: int | None
    output_tokens_limit: int | None
    total_tokens_limit: int | None
    run_timeout: float
    max_tool_concurrency: int
    context_output_reserve_tokens: int

    def get_provider_config(self, provider_name: str | None = None) -> Any:
        ...


def _resolve_instrumentation(settings: Any) -> Any:
    """可配置的 PydanticAI instrumentation；默认关闭 exporter。

    即使关闭，structlog 事件仍通过 deps.run_id 形成因果链。
    开启时强制 include_content=False，禁止把完整正文写入 trace。
    """
    enabled = bool(getattr(settings, "pydantic_ai_instrumentation", False))
    if not enabled:
        return False
    try:
        from pydantic_ai.models.instrumented import InstrumentationSettings

        return InstrumentationSettings(include_content=False, include_binary_content=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "pydantic_ai_instrumentation_unavailable",
            error=str(exc),
        )
        return False


# pydantic-ai 1.94 中实现了 Model.count_tokens 的 provider。
# OpenAIChatModel / OllamaModel / FunctionModel / TestModel 会直接 NotImplementedError。
# deepseek 与 openai 均走 OpenAIChatModel，不能开启 count_tokens_before_request。
_PROVIDERS_SUPPORTING_COUNT_TOKENS = frozenset({"anthropic"})


def provider_supports_count_tokens(provider_name: str | None) -> bool:
    """当前运行时的 Model 是否支持请求前 count_tokens。"""
    if not provider_name:
        return False
    return provider_name.lower() in _PROVIDERS_SUPPORTING_COUNT_TOKENS


def build_usage_limits(settings: Any, provider_name: str | None = None) -> UsageLimits:
    """从 Settings 构建 PydanticAI UsageLimits。

    重要：PydanticAI 的 ``input_tokens`` / ``total_tokens`` 是**整轮 agent run 的累计值**
    （每次模型请求的 usage 相加），不是单次 prompt 的窗口占用。

    因此：
    - **单次请求**的上下文窗口保护由 ``ContextBuilder`` history_processor 负责；
    - ``input_tokens_limit`` / ``total_tokens_limit`` 仅在用户**显式配置**时作为
      run 级预算硬上限传入；**不要**用 ``context_window`` 自动派生，否则多步
      工具循环会在累计输入达到窗口大小时被误杀（即便每一步都未超窗）。
    - ``request_limit`` / ``tool_calls_limit`` 仍始终生效，用于限制本轮步数。
    - 模型支持时 ``count_tokens_before_request`` 可与显式 token 上限配合；
      OpenAI / DeepSeek / Ollama 的 Model 未实现 count_tokens，会自动关闭。
    """
    request_limit = int(getattr(settings, "request_limit", 8) or 8)
    tool_calls_limit = int(getattr(settings, "tool_calls_limit", 8) or 8)
    # 仅透传显式配置；None 表示不设 run 级 token 硬上限（由 ContextBuilder 管窗口）
    input_tokens_limit = getattr(settings, "input_tokens_limit", None)
    output_tokens_limit = getattr(settings, "output_tokens_limit", None)
    total_tokens_limit = getattr(settings, "total_tokens_limit", None)

    want_count_tokens = bool(getattr(settings, "count_tokens_before_request", True))
    supports_count_tokens = provider_supports_count_tokens(provider_name)
    count_tokens_before_request = want_count_tokens and supports_count_tokens
    if want_count_tokens and not supports_count_tokens:
        logger.debug(
            "count_tokens_before_request_disabled_for_provider",
            provider=provider_name,
            reason="model_does_not_implement_count_tokens",
        )

    return UsageLimits(
        request_limit=request_limit,
        tool_calls_limit=tool_calls_limit,
        input_tokens_limit=input_tokens_limit,
        output_tokens_limit=output_tokens_limit,
        total_tokens_limit=total_tokens_limit,
        count_tokens_before_request=count_tokens_before_request,
    )


def format_usage_limit_player_message(exc: UsageLimitExceeded) -> str:
    """把 UsageLimitExceeded 拆成可操作的玩家文案（区分 request/tool/token）。"""
    msg = str(exc)
    lower = msg.lower()
    if "tool_calls_limit" in lower:
        return (
            "本轮工具调用次数已达上限，请缩短任务、拆成多条指令，"
            "或稍后再试。"
        )
    if "request_limit" in lower:
        return (
            "本轮模型请求步数已达上限（多轮工具循环过长），请缩短任务"
            "或拆成多条指令后重试。"
        )
    if "output_tokens_limit" in lower:
        return "本轮模型输出过长已达上限，请缩短问题或分步提问。"
    if "total_tokens_limit" in lower:
        return (
            "本轮累计 token 已达配置上限（含多步工具循环），"
            "请缩短问题、清空上下文，或拆成更小的步骤后重试。"
        )
    if "input_tokens_limit" in lower:
        return (
            "本轮累计输入 token 已达配置上限（多步工具会把每次请求的输入相加），"
            "请缩短问题、清空或压缩上下文，或拆成更小的步骤后重试。"
        )
    return "已达到本轮请求预算上限，请缩短问题、拆成多步，或稍后再试。"


def get_run_timeout(settings: Any) -> float:
    return float(getattr(settings, "run_timeout", 90.0) or 90.0)


def player_facing_error(error_kind: ErrorKind, fallback: str | None = None) -> str:
    return PLAYER_ERROR_ADVICE.get(error_kind, fallback or PLAYER_ERROR_ADVICE["INTERNAL"])


def classify_run_exception(exc: BaseException) -> tuple[ErrorKind, str, str]:
    """返回 (error_kind, player_message, diagnostic_summary)。"""
    if isinstance(exc, asyncio.CancelledError):
        return "CANCELLED", player_facing_error("CANCELLED"), "CancelledError"
    if isinstance(exc, asyncio.TimeoutError):
        return "TRANSIENT", player_facing_error("TRANSIENT"), "run_timeout"
    if isinstance(exc, UsageLimitExceeded):
        return "DENIED", format_usage_limit_player_message(exc), str(exc)

    # 懒导入避免 core ↔ context 循环依赖
    from services.agent.context import ContextOversizedError

    if isinstance(exc, ContextOversizedError):
        msg = str(exc).strip() or "上下文超出预算，请缩短问题或清空上下文后重试。"
        return "DENIED", msg, str(exc)

    detail = _extract_exception_details(exc)
    lower = detail.lower()
    if "timeout" in lower or "deadline exceeded" in lower:
        return "TRANSIENT", player_facing_error("TRANSIENT"), detail
    return "INTERNAL", player_facing_error("INTERNAL"), detail


def _salvage_messages_from_run(run: Any) -> tuple[list[ModelMessage], list[ModelMessage], Any | None]:
    """从未正常结束的 AgentRun 尽量提取 all/new messages 与 usage。"""
    all_messages: list[ModelMessage] = []
    new_messages: list[ModelMessage] = []
    usage: Any | None = None
    if run is None:
        return all_messages, new_messages, usage
    try:
        raw_all = run.all_messages() if callable(getattr(run, "all_messages", None)) else None
        if isinstance(raw_all, list):
            all_messages = list(raw_all)
    except Exception:  # noqa: BLE001
        pass
    try:
        raw_new = run.new_messages() if callable(getattr(run, "new_messages", None)) else None
        if isinstance(raw_new, list):
            new_messages = list(raw_new)
        elif all_messages:
            new_messages = list(all_messages)
    except Exception:  # noqa: BLE001
        if all_messages:
            new_messages = list(all_messages)
    try:
        usage_attr = getattr(run, "usage", None)
        usage = usage_attr() if callable(usage_attr) else usage_attr
    except Exception:  # noqa: BLE001
        usage = None
    return all_messages, new_messages, usage


def _build_error_event(
    ctx: "_HandlerContext",
    deps: AgentDependencies,
    *,
    error_kind: ErrorKind,
    player_msg: str,
    diagnostic: str,
) -> StreamEvent:
    """构造 error StreamEvent；若已 salvage 到部分消息则一并放入 metadata。"""
    metadata: dict[str, Any] = {
        "error_kind": error_kind,
        "diagnostic_summary": diagnostic,
        "run_id": deps.run_id,
        "conversation_id": deps.conversation_id,
        "player_name": deps.player_name,
    }
    if ctx.all_messages:
        metadata["all_messages"] = ctx.all_messages
        metadata["new_messages"] = ctx.new_messages
        metadata["new_messages_serialized"] = serialize_model_messages(ctx.new_messages)
        metadata["tool_events"] = ctx.tool_events or _extract_tool_events_from_messages(
            ctx.new_messages
        )
        metadata["usage"] = ctx.serialized_usage
        metadata["salvage_partial_run"] = True
    return StreamEvent(
        event_type="error",
        content=player_msg,
        sequence=ctx.sequence,
        metadata=metadata,
    )


def _is_generator_exit_cleanup_error(exc: BaseException) -> bool:
    """识别 agent.iter 在 GeneratorExit 清理时产生的 RuntimeError。"""
    if isinstance(exc, GeneratorExit):
        return True
    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        return "generator exit" in msg or "generatorexit" in msg
    return False


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
    - MCP 故障后的持久降级
    """

    def __init__(self):
        self._agent: Agent[AgentDependencies, str | DeferredToolRequests] | None = None
        self._fallback_agent: Agent[AgentDependencies, str | DeferredToolRequests] | None = None
        self._mcp_toolsets: list[Any] = []
        self._initialized = False
        self._settings: Settings | None = None
        self._mcp_available: bool = True  # MCP 是否可用；失败后由 MCPManager 健康状态决定后续装配

    def _create_agent(
        self,
        toolsets: list[Any] | None = None,
    ) -> Agent[AgentDependencies, str | DeferredToolRequests]:
        """
        创建 Agent 实例

        Args:
            toolsets: MCP 工具集列表
        """
        settings = self._settings or get_settings()
        default_provider_config = settings.get_provider_config(settings.default_provider)
        harness_cap = build_harness_capability(settings)
        history_processor = build_context_history_processor(settings)
        instrument = _resolve_instrumentation(settings)
        agent: Agent[AgentDependencies, str | DeferredToolRequests] = Agent(
            f"{settings.default_provider}:{default_provider_config.model}",  # 默认模型，运行时可覆盖
            deps_type=AgentDependencies,
            output_type=[str, DeferredToolRequests],
            defer_model_check=True,
            retries=settings.agent_retries,
            toolsets=toolsets,
            max_concurrency=getattr(settings, "max_tool_concurrency", 4),
            capabilities=[harness_cap],
            history_processors=[history_processor],
            instrument=instrument,
        )

        @agent.system_prompt
        async def dynamic_system_prompt(ctx: RunContext[AgentDependencies]) -> str:
            """动态系统提示词 - 使用 PromptManager 构建"""
            return await build_dynamic_prompt(ctx)

        # 注册基础工具
        register_agent_tools(agent, settings=self._settings)

        return agent

    async def initialize(
        self,
        settings: Settings | None = None,
        mcp_toolsets: list[Any] | None = None,
    ) -> None:
        """
        初始化 Agent 管理器

        Args:
            settings: 应用配置，不传则使用全局配置
            mcp_toolsets: 外部传入的 MCP 工具集（推荐由 MCPManager 管理）
        """
        if self._initialized:
            return

        self._settings = settings or get_settings()

        # 使用外部传入的 MCP 工具集，或从配置加载（兼容旧逻辑）
        if mcp_toolsets is not None:
            self._mcp_toolsets = mcp_toolsets
            logger.info(
                "chat_agent_using_external_mcp",
                mcp_toolsets_count=len(self._mcp_toolsets),
            )
        else:
            # 兼容旧逻辑：从配置加载 MCP 工具集
            from services.agent.mcp import load_mcp_toolsets
            self._mcp_toolsets = load_mcp_toolsets(self._settings)
            logger.info(
                "chat_agent_loading_mcp_from_config",
                mcp_toolsets_count=len(self._mcp_toolsets),
            )

        # 创建带有 MCP 工具集的 Agent
        # 如果 MCP 工具集为空，Agent 仍然可以正常工作（优雅降级）
        self._agent = self._create_agent(toolsets=self._mcp_toolsets if self._mcp_toolsets else None)

        self._initialized = True
        logger.info(
            "chat_agent_initialized",
            mcp_toolsets_count=len(self._mcp_toolsets),
        )

    def get_agent(self) -> Agent[AgentDependencies, str | DeferredToolRequests]:
        """
        获取 Agent 实例

        如果尚未初始化，会自动进行懒初始化（包含 MCP 工具）

        Returns:
            Agent 实例
        """
        if self._agent is None:
            # 懒初始化：自动加载 MCP 工具集
            if not self._initialized:
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

    def reset(self) -> None:
        """重置 Agent 管理器状态，供运行时关闭后重新初始化。"""
        self._agent = None
        self._fallback_agent = None
        self._mcp_toolsets = []
        self._settings = None
        self._initialized = False
        self._mcp_available = True

    def refresh_mcp_toolsets(self, toolsets: list[Any]) -> None:
        """用最新 MCP 工具集恢复 primary Agent。"""
        self._mcp_toolsets = toolsets
        self._mcp_available = True
        self._fallback_agent = None
        self._agent = self._create_agent(toolsets=self._mcp_toolsets if self._mcp_toolsets else None)
        self._initialized = True
        logger.info(
            "chat_agent_mcp_toolsets_refreshed",
            mcp_toolsets_count=len(self._mcp_toolsets),
        )

    def mark_mcp_failed(self) -> None:
        """
        标记 MCP 为不可用状态

        当 MCP 连接超时或其他致命错误时调用此方法，
        后续所有请求将直接使用无 MCP 的降级 Agent。
        """
        if self._mcp_available:
            self._mcp_available = False
            logger.warning(
                "mcp_marked_as_unavailable",
                message="后续请求将使用无 MCP 的降级 Agent",
            )

    @property
    def is_mcp_available(self) -> bool:
        """MCP 是否可用"""
        return self._mcp_available

    def get_fallback_agent(self) -> Agent[AgentDependencies, str | DeferredToolRequests]:
        """获取无 MCP 工具集的降级 Agent 实例。"""
        if self._fallback_agent is None:
            logger.info("creating_fallback_agent_without_mcp")
            self._fallback_agent = self._create_agent(toolsets=None)
        return self._fallback_agent

    def get_active_agent(
        self,
        agent: Agent[AgentDependencies, str | DeferredToolRequests] | None = None,
        *,
        connection_id: str | None = None,
        mode: str = "stream",
    ) -> Agent[AgentDependencies, str | DeferredToolRequests]:
        if agent is not None:
            return agent
        # 后续新请求应优先装配 MCPManager 健康 server；此处仅在全局不可用时降级。
        if not self._mcp_available:
            logger.info(
                "mcp_already_failed_using_fallback",
                connection_id=connection_id,
                mode=mode,
            )
            return self.get_fallback_agent()
        return self.get_agent()


def get_agent_manager() -> ChatAgentManager:
    """获取 Agent 管理器单例"""
    from services.agent.runtime import get_agent_runtime

    return get_agent_runtime().get_agent_manager()


# 为了向后兼容，保留 chat_agent 变量（推荐使用 get_agent_manager）
# 通过懒加载方式获取 Agent，确保 MCP 工具被正确加载


def _get_legacy_chat_agent() -> Agent[AgentDependencies, str | DeferredToolRequests]:
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


def _non_stream_batch_max_chars() -> int:
    """非流式模式按句子分批的最大字符数（从 Settings.flow_control 读取）。"""
    try:
        return get_settings().flow_control.non_stream_batch_max_chars
    except Exception:
        return 150


def _non_stream_send_delay() -> float:
    """非流式模式每个包之间的发送延迟（秒，从 Settings.flow_control 读取）。"""
    try:
        return get_settings().flow_control.non_stream_send_delay
    except Exception:
        return 0.1


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


def _is_mcp_timeout_error(exc: BaseException) -> bool:
    """
    检查异常是否是 MCP 连接超时错误

    MCP 超时错误的特征：
    - 包含 TimeoutError
    - 错误信息中包含 "deadline exceeded" 或 "timeout"
    - 堆栈中包含 mcp 或 pydantic_ai.mcp 相关模块

    Args:
        exc: 异常对象

    Returns:
        是否是 MCP 超时错误
    """
    import traceback

    error_str = str(exc).lower()
    error_detail = _extract_exception_details(exc).lower()

    # 获取完整的堆栈跟踪信息
    tb_str = ""
    try:
        tb_str = traceback.format_exc().lower()
    except Exception:
        pass

    # 检查是否是超时错误 - 更宽松的检查
    is_timeout = (
        "timeout" in error_detail
        or "timeout" in error_str
        or "timeouterror" in error_detail
        or "deadline exceeded" in error_detail
        or "deadline exceeded" in error_str
        or "cancelled" in error_detail
        or "cancelled" in error_str
    )

    # 检查是否涉及 MCP（在错误详情、错误字符串或堆栈跟踪中）
    is_mcp_related = (
        "mcp" in error_detail
        or "mcp" in error_str
        or "mcp" in tb_str
        or "pydantic_ai.mcp" in error_detail
        or "pydantic_ai.mcp" in tb_str
        or "stdio_client" in error_detail
        or "stdio_client" in tb_str
        or "mcpserverstdio" in tb_str
    )

    # 如果异常类型是 ExceptionGroup 且包含 TimeoutError，很可能是 MCP 问题
    # 因为 MCP 使用 TaskGroup 来管理连接
    is_exception_group_with_timeout = (
        isinstance(exc, ExceptionGroup)
        and "timeout" in error_detail
    )

    logger.debug(
        "mcp_timeout_check",
        is_timeout=is_timeout,
        is_mcp_related=is_mcp_related,
        is_exception_group_with_timeout=is_exception_group_with_timeout,
        error_type=type(exc).__name__,
        error_detail_preview=error_detail[:200] if error_detail else "",
    )

    return (is_timeout and is_mcp_related) or is_exception_group_with_timeout


def _extract_exception_details(exc: BaseException) -> str:
    """
    提取异常详情，包括 ExceptionGroup 中的子异常。

    Python 3.11+ 的 TaskGroup 会抛出 ExceptionGroup，
    需要递归提取其中的真正错误信息。

    参考 pydantic-ai 文档: https://github.com/pydantic/pydantic-ai/blob/main/docs/models/overview.md
    """
    # 检查是否是 ExceptionGroup / BaseExceptionGroup (Python 3.11+)
    # 使用更可靠的检查方式
    if isinstance(exc, BaseExceptionGroup):
        sub_errors = []
        for sub_exc in exc.exceptions:
            sub_errors.append(_extract_exception_details(sub_exc))
        return f"{type(exc).__name__}: [{', '.join(sub_errors)}]"

    # 兼容 Python < 3.11 的 ExceptionGroup (通过 exceptiongroup 包)
    if hasattr(exc, "exceptions") and isinstance(getattr(exc, "exceptions", None), tuple):
        sub_errors = []
        for sub_exc in exc.exceptions:  # type: ignore[union-attr]
            sub_errors.append(_extract_exception_details(sub_exc))
        return f"{type(exc).__name__}: [{', '.join(sub_errors)}]"

    return f"{type(exc).__name__}: {str(exc)}"


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


def serialize_model_messages(messages: list[ModelMessage] | None) -> list[dict[str, Any]]:
    """将 ModelRequest/ModelResponse 序列化为 JSON-safe 字典列表。

    使用 pydantic_core.to_jsonable_python 保留 role/kind、part 顺序、
    tool-call ID、finish reason、provider/model 元数据与 usage。
    不序列化 streaming delta 或 HTTP raw body。
    """
    if not messages:
        return []
    try:
        from pydantic_core import to_jsonable_python

        raw = to_jsonable_python(messages)
        if isinstance(raw, list):
            return [item if isinstance(item, dict) else {"value": str(item)} for item in raw]
    except Exception:  # noqa: BLE001
        pass
    out: list[dict[str, Any]] = []
    for msg in messages:
        if hasattr(msg, "model_dump"):
            try:
                out.append(msg.model_dump(mode="json"))  # type: ignore[union-attr]
                continue
            except Exception:  # noqa: BLE001
                pass
        if is_dataclass(msg):
            try:
                out.append(asdict(msg))
                continue
            except Exception:  # noqa: BLE001
                pass
        out.append({"value": str(msg)})
    return out


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
            if isinstance(value, DeferredToolRequests):
                return ""
            if isinstance(value, str):
                return value
            return str(value)
    return ""


def _extract_deferred_requests(result: Any) -> DeferredToolRequests | None:
    """若 run 输出为 DeferredToolRequests 则返回，否则 None。"""
    output = getattr(result, "output", None)
    if isinstance(output, DeferredToolRequests):
        return output
    data = getattr(result, "data", None)
    if isinstance(data, DeferredToolRequests):
        return data
    return None


def _build_approval_required_event(
    ctx: _HandlerContext,
    deps: AgentDependencies,
    deferred: DeferredToolRequests,
    result: Any,
) -> StreamEvent:
    """构造审批暂停事件，供 Worker 写入 PendingApprovalStore 并提示玩家。"""
    new_messages = _extract_new_messages(result)
    return StreamEvent(
        event_type="approval_required",
        content="工具调用需要玩家审批",
        sequence=ctx.sequence,
        metadata={
            "deferred_requests": deferred,
            "all_messages": _extract_all_messages(result),
            # 原始对象供 Worker/history；序列化副本供 trace 边界消费
            "new_messages": new_messages,
            "new_messages_serialized": serialize_model_messages(new_messages),
            "tool_events": ctx.tool_events,
            "usage": ctx.serialized_usage,
            "run_id": deps.run_id,
            "conversation_id": deps.conversation_id,
            "player_name": deps.player_name,
            "connection_id": str(deps.connection_id),
        },
    )


def _mark_mcp_failure_from_exception(exc: BaseException, diagnostic: str) -> None:
    """仅标记有证据关联的 MCP server；无证据时不盲目禁用全部。"""
    try:
        from services.agent.mcp import get_mcp_manager

        mcp_manager = get_mcp_manager()
    except Exception:
        return

    detail = diagnostic
    try:
        detail = f"{diagnostic}\n{_extract_exception_details(exc)}"
    except Exception:
        pass

    matched = False
    for server_name in list(mcp_manager.servers.keys()):
        if server_name and server_name in detail:
            mcp_manager.mark_server_failed(server_name, diagnostic)
            matched = True

    if matched:
        # 用健康工具集刷新后续装配；不递归重放当前 run
        try:
            from services.agent.runtime import get_agent_runtime

            runtime = get_agent_runtime()
            settings = getattr(mcp_manager, "_settings", None)
            if settings is not None:
                runtime.refresh_mcp_tools(settings)
        except Exception:
            pass
        return

    # 无明确 server 证据时：只记录，不批量 mark 全部 server
    logger.warning(
        "mcp_failure_without_server_evidence",
        error=diagnostic,
        servers=list(mcp_manager.servers.keys()),
        # run_id 在调用侧日志补充；此处无 deps
    )


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


def _extract_reasoning_from_messages(messages: list[ModelMessage]) -> list[str]:
    """从结果消息中提取思考内容（ThinkingPart），用于非流式模式回填 reasoning 事件。"""
    reasoning_chunks: list[str] = []
    for message in messages:
        parts = getattr(message, "parts", None)
        if not parts:
            continue
        for part in parts:
            if isinstance(part, ThinkingPart) and part.content:
                reasoning_chunks.append(part.content)
    return reasoning_chunks


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


def _iter_sentence_batches(text: str, max_chars: int | None = None) -> Iterator[str]:
    """按句子完整性分批，避免单次 WebSocket 发送过大。"""
    max_chars = max_chars if max_chars is not None else _non_stream_batch_max_chars()
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
    # 思考内容单独缓冲，按完整句子下发；与正文 buffer 隔离，避免互相串句。
    reasoning_buffer: str = ""
    sent_text: str = ""
    emitted_content: bool = False
    all_messages: list[ModelMessage] = field(default_factory=list)
    new_messages: list[ModelMessage] = field(default_factory=list)
    serialized_usage: dict[str, Any] | None = None
    tool_events: list[dict[str, Any]] = field(default_factory=list)  # 记录工具调用事件
    tool_results: dict[str, str] = field(default_factory=dict)  # 记录工具返回结果，key=tool_call_id
    tool_call_names: dict[str, str] = field(default_factory=dict)  # tool_call_id -> tool_name


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
        await asyncio.sleep(_non_stream_send_delay())
    return event


async def _send_reasoning_event(
    ctx: _HandlerContext,
    content: str,
) -> StreamEvent:
    """发送推理（思考）事件并更新上下文。

    思考内容不进 sentence_buffer，避免与正文文本混淆。
    """
    event = StreamEvent(
        event_type="reasoning",
        content=content,
        sequence=ctx.sequence,
    )
    ctx.sequence += 1
    return event


def _append_reasoning_and_extract(ctx: _HandlerContext, chunk: str) -> list[str]:
    """将思考增量写入独立缓冲，并提取已完成的句子。"""
    if not chunk:
        return []
    ctx.reasoning_buffer += chunk
    sentences, ctx.reasoning_buffer = _extract_complete_sentences(ctx.reasoning_buffer)
    return sentences


async def _flush_reasoning_buffer(ctx: _HandlerContext) -> AsyncIterator[StreamEvent]:
    """冲刷未完成的思考缓冲（思考结束或 run 结束时调用）。"""
    if ctx.reasoning_buffer.strip():
        yield await _send_reasoning_event(ctx, ctx.reasoning_buffer)
    ctx.reasoning_buffer = ""


async def stream_response_handler(
    prompt: str | None,
    deps: AgentDependencies,
    model: Model | str | None = None,
    message_history: list[ModelMessage] | None = None,
    ctx: _HandlerContext | None = None,
    agent: Agent[AgentDependencies, str | DeferredToolRequests] | None = None,
    *,
    deferred_tool_results: Any | None = None,
) -> AsyncIterator[StreamEvent]:
    """
    流式响应处理器（基于 agent.iter() 官方推荐方式）

    处理流程：
    1. 使用 agent.iter() 遍历执行节点
    2. 在 ModelRequestNode 中流式输出文本
    3. 在 CallToolsNode 中处理工具调用事件
    4. 工具调用由 Pydantic AI 自动执行
    5. MCP 故障返回结构化错误，不在当前 run 内递归重放

    Args:
        prompt: 用户输入；审批恢复时可为 None（使用 message_history + deferred_tool_results）
        deps: Agent 依赖
        model: 可选的模型覆盖
        message_history: 消息历史
        ctx: 处理器上下文
        agent: 可选的 Agent 实例，不传则使用管理器中的 Agent
        deferred_tool_results: 审批恢复时传入的 DeferredToolResults

    Yields:
        StreamEvent: 流式事件
    """
    if ctx is None:
        ctx = _HandlerContext()

    agent_manager = get_agent_manager()

    active_agent = agent_manager.get_active_agent(
        agent,
        connection_id=str(deps.connection_id),
        mode="stream",
    )

    usage_limits = build_usage_limits(deps.settings, deps.provider)
    run_timeout = get_run_timeout(deps.settings)
    # 审批事件必须在 agent.iter() 上下文干净退出后再 yield。
    # 若在 async with 内 yield+return，消费者停止迭代时会注入 GeneratorExit，
    # pydantic-ai 的 iter 清理会变成 RuntimeError("coroutine ignored GeneratorExit")，
    # 进而拖垮 worker。
    approval_event: StreamEvent | None = None
    # 持有 run 引用，异常时 salvage 已产生的消息（UsageLimitExceeded 等中断路径）
    active_run: Any | None = None

    try:
        async with asyncio.timeout(run_timeout):
            run_kwargs: dict[str, Any] = {
                "deps": deps,
                "model": model,
                "message_history": message_history,
                "usage_limits": usage_limits,
            }
            if deferred_tool_results is not None:
                run_kwargs["deferred_tool_results"] = deferred_tool_results
            # 使用 agent.iter() 进行细粒度节点控制
            async with active_agent.iter(
                prompt,
                **run_kwargs,
            ) as run:
                active_run = run
                async for node in run:
                    # 处理用户提示节点
                    if Agent.is_user_prompt_node(node):
                        logger.debug(
                            "agent_user_prompt",
                            prompt=node.user_prompt,
                            connection_id=str(deps.connection_id),
                            run_id=deps.run_id,
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
                                        reasoning_buffer=ctx.reasoning_buffer,
                                    )
                                    part = event.part
                                    # ThinkingPart：进入独立思考缓冲，按完整句子作为 reasoning 输出
                                    if isinstance(part, ThinkingPart) and part.content:
                                        logger.debug(
                                            "agent_part_start_thinking",
                                            index=event.index,
                                            content=part.content,
                                            connection_id=str(deps.connection_id),
                                        )
                                        for sentence in _append_reasoning_and_extract(
                                            ctx, part.content
                                        ):
                                            yield await _send_reasoning_event(
                                                ctx, sentence
                                            )
                                    # TextPart：先冲刷思考缓冲，再按完整句子发送正文
                                    elif isinstance(part, TextPart) and part.content:
                                        async for reasoning_event in _flush_reasoning_buffer(
                                            ctx
                                        ):
                                            yield reasoning_event
                                        logger.debug(
                                            "agent_part_start_content",
                                            index=event.index,
                                            content=part.content,
                                            connection_id=str(deps.connection_id),
                                        )
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
                                    delta = event.delta
                                    # ThinkingPartDelta：进入独立思考缓冲，按完整句子输出
                                    if isinstance(delta, ThinkingPartDelta):
                                        chunk = delta.content_delta
                                        if chunk:
                                            logger.debug(
                                                "agent_thinking_delta",
                                                chunk=chunk,
                                                index=event.index,
                                                connection_id=str(deps.connection_id),
                                                reasoning_buffer_before=ctx.reasoning_buffer,
                                            )
                                            for sentence in _append_reasoning_and_extract(
                                                ctx, chunk
                                            ):
                                                yield await _send_reasoning_event(
                                                    ctx, sentence
                                                )
                                    # TextPartDelta：先冲刷思考缓冲，再缓冲并按完整句子发送正文
                                    elif isinstance(delta, TextPartDelta):
                                        async for reasoning_event in _flush_reasoning_buffer(
                                            ctx
                                        ):
                                            yield reasoning_event
                                        chunk = delta.content_delta
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
                        # 进入工具阶段前冲刷思考缓冲，保证思考先于工具显示
                        async for reasoning_event in _flush_reasoning_buffer(ctx):
                            yield reasoning_event
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
                                    ctx.tool_call_names[event.part.tool_call_id] = event.part.tool_name
                                    logger.info(
                                        "agent_tool_call",
                                        tool=event.part.tool_name,
                                        args=event.part.args,
                                        tool_call_id=event.part.tool_call_id,
                                        connection_id=str(deps.connection_id),
                                        run_id=deps.run_id,
                                        player_name=deps.player_name,
                                    )
                                    # 发送工具调用事件
                                    yield StreamEvent(
                                        event_type="tool_call",
                                        content=event.part.tool_name,
                                        sequence=ctx.sequence,
                                        metadata={
                                            "tool_name": event.part.tool_name,
                                            "tool_call_id": event.part.tool_call_id,
                                            "args": event.part.args,
                                            "run_id": deps.run_id,
                                            "player_name": deps.player_name,
                                        },
                                    )
                                    ctx.sequence += 1

                                # 工具返回事件
                                elif isinstance(event, FunctionToolResultEvent):
                                    result_content = str(event.result.content)
                                    ctx.tool_results[event.tool_call_id] = result_content
                                    logger.info(
                                        "agent_tool_result",
                                        tool_call_id=event.tool_call_id,
                                        tool_name=ctx.tool_call_names.get(event.tool_call_id),
                                        result_preview=result_content[:100],
                                        result_length=len(result_content),
                                        connection_id=str(deps.connection_id),
                                        run_id=deps.run_id,
                                        player_name=deps.player_name,
                                    )
                                    # 发送工具返回事件
                                    yield StreamEvent(
                                        event_type="tool_result",
                                        content=result_content,
                                        sequence=ctx.sequence,
                                        metadata={
                                            "tool_call_id": event.tool_call_id,
                                            "tool_name": ctx.tool_call_names.get(event.tool_call_id),
                                            "run_id": deps.run_id,
                                            "player_name": deps.player_name,
                                        },
                                    )
                                    ctx.sequence += 1

                    # 处理结束节点
                    elif Agent.is_end_node(node):
                        # 仅在整个执行结束时再冲刷尾部 buffer，避免跨节点半句提前下发
                        async for reasoning_event in _flush_reasoning_buffer(ctx):
                            yield reasoning_event
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
                            deferred = _extract_deferred_requests(run.result)
                            if deferred is not None:
                                # 先记下审批事件，退出 agent.iter 后再 yield
                                approval_event = _build_approval_required_event(
                                    ctx, deps, deferred, run.result
                                )
                                break
                        logger.debug(
                            "agent_run_complete",
                            connection_id=str(deps.connection_id),
                            run_id=deps.run_id,
                            tool_events_count=len(ctx.tool_events),
                        )

    except asyncio.CancelledError:
        raise
    except Exception as e:
        # agent.iter 在 GeneratorExit 清理失败时可能抛出 RuntimeError；
        # 若审批事件已就绪，视为成功暂停，不再当错误上报。
        if approval_event is not None and _is_generator_exit_cleanup_error(e):
            logger.debug(
                "stream_approval_cleanup_suppressed",
                error=str(e),
                connection_id=str(deps.connection_id),
                run_id=deps.run_id,
            )
        else:
            error_kind, player_msg, diagnostic = classify_run_exception(e)

            # MCP 故障：只标记有证据关联的 server，不在本 run 内递归重放
            if _is_mcp_timeout_error(e):
                logger.warning(
                    "mcp_connection_timeout_no_replay",
                    error=diagnostic,
                    connection_id=str(deps.connection_id),
                    run_id=deps.run_id,
                )
                _mark_mcp_failure_from_exception(e, diagnostic)
                error_kind = "TRANSIENT"
                player_msg = player_facing_error("TRANSIENT")

            # 中断前 salvage 已产生的工具/模型消息，供 worker 落历史与 trace
            if not ctx.all_messages and active_run is not None:
                salvaged_all, salvaged_new, salvaged_usage = _salvage_messages_from_run(
                    active_run
                )
                if salvaged_all:
                    ctx.all_messages = salvaged_all
                    ctx.new_messages = salvaged_new or salvaged_all
                    ctx.serialized_usage = _serialize_usage(salvaged_usage)
                    if not ctx.tool_events:
                        ctx.tool_events = _extract_tool_events_from_messages(
                            ctx.new_messages
                        )
                    logger.info(
                        "stream_partial_run_salvaged",
                        connection_id=str(deps.connection_id),
                        run_id=deps.run_id,
                        all_messages=len(ctx.all_messages),
                        new_messages=len(ctx.new_messages),
                        error_kind=error_kind,
                    )

            logger.error(
                "stream_response_handler_error",
                error=diagnostic,
                error_kind=error_kind,
                connection_id=str(deps.connection_id),
                run_id=deps.run_id,
                salvaged_messages=len(ctx.all_messages),
                exc_info=True,  # 保留完整堆栈
            )
            yield _build_error_event(
                ctx,
                deps,
                error_kind=error_kind,
                player_msg=player_msg,
                diagnostic=diagnostic,
            )
            return

    if approval_event is not None:
        yield approval_event
        return


async def non_stream_response_handler(
    prompt: str | None,
    deps: AgentDependencies,
    model: Model | str | None = None,
    message_history: list[ModelMessage] | None = None,
    ctx: _HandlerContext | None = None,
    agent: Agent[AgentDependencies, str | DeferredToolRequests] | None = None,
    *,
    deferred_tool_results: Any | None = None,
) -> AsyncIterator[StreamEvent]:
    """
    非流式响应处理器

    处理流程：
    1. 使用 run 获取完整响应（工具调用由框架自动处理）
    2. 使用 _extract_complete_sentences 匹配完整句子
    3. 逐一发送完整句子（带延迟以避免 MC 崩溃）

    Args:
        prompt: 用户输入；审批恢复时可为 None
        deps: Agent 依赖
        model: 可选的模型覆盖
        message_history: 消息历史
        ctx: 处理器上下文
        agent: 可选的 Agent 实例，不传则使用管理器中的 Agent
        deferred_tool_results: 审批恢复时传入的 DeferredToolResults

    Yields:
        StreamEvent: 流式事件
    """
    if ctx is None:
        ctx = _HandlerContext()

    agent_manager = get_agent_manager()

    active_agent = agent_manager.get_active_agent(
        agent,
        connection_id=str(deps.connection_id),
        mode="non_stream",
    )

    usage_limits = build_usage_limits(deps.settings, deps.provider)
    run_timeout = get_run_timeout(deps.settings)

    try:
        async with asyncio.timeout(run_timeout):
            run_kwargs: dict[str, Any] = {
                "deps": deps,
                "model": model,
                "message_history": message_history,
                "usage_limits": usage_limits,
            }
            if deferred_tool_results is not None:
                run_kwargs["deferred_tool_results"] = deferred_tool_results
            result = await active_agent.run(
                prompt,
                **run_kwargs,
            )

        # 提取处理结果
        ctx.new_messages = _extract_new_messages(result)
        ctx.all_messages = _extract_all_messages(result)
        ctx.serialized_usage = _serialize_usage(_extract_result_usage(result))
        ctx.tool_events = _extract_tool_events_from_messages(ctx.new_messages)

        deferred = _extract_deferred_requests(result)
        if deferred is not None:
            yield _build_approval_required_event(ctx, deps, deferred, result)
            return

        # 从结果消息中提取思考内容（ThinkingPart），在正文之前按完整句子作为 reasoning 事件输出
        for reasoning_chunk in _extract_reasoning_from_messages(ctx.new_messages):
            for batch in _iter_sentence_batches(reasoning_chunk):
                yield await _send_reasoning_event(ctx, batch)

        # 提取完整响应文本
        full_response = _extract_result_output(result)
        if not full_response:
            return

        # 使用 _extract_complete_sentences 提取完整句子
        # 为了保持 max_chars 限制，使用 _iter_sentence_batches
        for chunk in _iter_sentence_batches(full_response):
            yield await _send_content_event(ctx, chunk, add_delay=True)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        error_kind, player_msg, diagnostic = classify_run_exception(e)

        if _is_mcp_timeout_error(e):
            logger.warning(
                "mcp_connection_timeout_no_replay_non_stream",
                error=diagnostic,
                connection_id=str(deps.connection_id),
                run_id=deps.run_id,
            )
            _mark_mcp_failure_from_exception(e, diagnostic)
            error_kind = "TRANSIENT"
            player_msg = player_facing_error("TRANSIENT")

        logger.error(
            "non_stream_response_handler_error",
            error=diagnostic,
            error_kind=error_kind,
            connection_id=str(deps.connection_id),
            run_id=deps.run_id,
            salvaged_messages=len(ctx.all_messages),
            exc_info=True,
        )
        yield _build_error_event(
            ctx,
            deps,
            error_kind=error_kind,
            player_msg=player_msg,
            diagnostic=diagnostic,
        )


async def stream_chat(
    prompt: str | None,
    deps: AgentDependencies,
    model: Model | str | None = None,
    message_history: list[ModelMessage] | None = None,
    agent: Agent[AgentDependencies, str | DeferredToolRequests] | None = None,
    *,
    deferred_tool_results: Any | None = None,
) -> AsyncIterator[StreamEvent]:
    """
    流式聊天处理主调度函数

    根据 stream_sentence_mode 配置选择对应的处理器。
    工具调用由 Pydantic AI 框架自动处理，无需手动回退。

    Args:
        prompt: 用户输入；审批恢复时可为 None
        deps: Agent 依赖
        model: 可选的模型覆盖
        message_history: 消息历史
        agent: 可选的 Agent 实例，不传则使用管理器中的 Agent
        deferred_tool_results: 审批恢复时传入的 DeferredToolResults

    Yields:
        StreamEvent: 流式事件
    """
    use_stream_mode = cast(StreamModeSettings, deps.settings).stream_sentence_mode
    ctx = _HandlerContext()

    try:
        # 根据模式选择处理器
        if use_stream_mode:
            # 流式模式处理
            async for event in stream_response_handler(
                prompt,
                deps,
                model,
                message_history,
                ctx,
                agent,
                deferred_tool_results=deferred_tool_results,
            ):
                if event.event_type == "error":
                    yield event
                    return
                if event.event_type == "approval_required":
                    yield event
                    return
                yield event
        else:
            # 非流式模式处理
            async for event in non_stream_response_handler(
                prompt,
                deps,
                model,
                message_history,
                ctx,
                agent,
                deferred_tool_results=deferred_tool_results,
            ):
                if event.event_type == "error":
                    yield event
                    return
                if event.event_type == "approval_required":
                    yield event
                    return
                yield event

        logger.debug(
            "chat_complete",
            connection_id=str(deps.connection_id),
            run_id=deps.run_id,
            player_name=deps.player_name,
            conversation_id=deps.conversation_id,
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
                "new_messages": ctx.new_messages,
                "new_messages_serialized": serialize_model_messages(ctx.new_messages),
                "tool_events": ctx.tool_events,
                "run_id": deps.run_id,
                "conversation_id": deps.conversation_id,
                "player_name": deps.player_name,
            },
        )

    except asyncio.CancelledError:
        raise
    except Exception as e:
        error_kind, player_msg, diagnostic = classify_run_exception(e)
        logger.error(
            "stream_chat_error",
            error=diagnostic,
            error_kind=error_kind,
            connection_id=str(deps.connection_id),
            run_id=deps.run_id,
            exc_info=True,
        )
        yield _build_error_event(
            ctx,
            deps,
            error_kind=error_kind,
            player_msg=player_msg,
            diagnostic=diagnostic,
        )
