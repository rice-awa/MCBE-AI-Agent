"""Agent Worker - 从队列消费请求并处理"""

import asyncio
import copy
import dataclasses
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolApproved, ToolDenied
from core.queue import MessageBroker
from services.agent.core import stream_chat, _extract_exception_details, player_facing_error, classify_run_exception
from services.agent.context import ensure_tool_message_pairs
from services.agent.harness.approvals import PendingApproval
from services.agent.harness.audit import (
    enqueue_validation_failure_audit,
    extract_tool_validation_failures,
)
from services.agent.harness.execution import summarize_args_for_player
from services.agent.providers import ProviderRegistry
from services.agent.runtime import get_agent_runtime
from services.agent.title import generate_conversation_title
from services.agent.tool_results import CommandResult
from models.constants import DEFAULT_PLAYER_DISPLAY_NAME
from models.messages import ChatRequest, StreamChunk, SystemNotification
from models.agent import (
    AgentDependencies,
    ContextInfo,
    MCColor,
    MCPrefix,
    truncate_text,
    format_tool_call_message,
    format_tool_result_message,
)
from config.settings import Settings
from config.logging import get_logger
from services.agent.trace import TraceContext, get_trace_recorder

logger = get_logger(__name__)


# ── 单次执行结果类型 ─────────────────────────────────────────────
# 方案四：将单次 MCBE Chat Agent 执行从 AgentWorker 生命周期中独立。
# AgentWorker 只提交上下文并接收 ExecutionResult，由内部模块协作完成
# 准备/执行/恢复/输出转换/失败挽救/历史提交/收尾。


@dataclass
class ExecutionResult:
    """单次 Agent 执行的结果，由 _execute_single_request 返回。

    AgentWorker 根据 status 决定后续动作：
    - success: 提交历史，生成标题，终态追踪
    - approval_pending: 审批挂起，不提交历史（下游恢复时处理）
    - partial: mid-run 失败，已落盘部分结果
    - timeout / cancelled / exception / disconnected: 非终态，按需 cleanup
    """

    status: Literal[
        "success",
        "approval_pending",
        "partial",
        "timeout",
        "cancelled",
        "exception",
        "disconnected",
    ]
    """执行终态"""

    response_text: str = ""
    """完整响应文本（仅 success 时有效）"""

    reasoning_text: str = ""
    """推理内容文本"""

    event_count: int = 0
    """流式事件总数"""

    duration_ms: int = 0
    """执行耗时（毫秒）"""

    all_messages: list[ModelMessage] | None = None
    """本次执行产生的完整消息列表（success/partial 时可用于保存历史）"""

    new_messages: list[ModelMessage] | None = None
    """本次执行新增的消息列表"""

    usage: dict | None = None
    """token 用量"""

    error_kind: str | None = None
    """错误分类"""

    error_summary: str | None = None
    """玩家可见错误摘要"""

    diagnostic_summary: str | None = None
    """诊断用错误详情"""

    approval_id: str | None = None
    """审批挂起 ID（仅 approval_pending 时有效）"""

    validation_messages: list | None = None
    """审批恢复时需要的原始消息"""

    trace_context: TraceContext | None = None
    """本次执行的追踪上下文"""

    tool_events_count: int = 0
    """工具调用事件数"""

    provider: str | None = None
    """使用的 provider 名称"""

    stream_target: str | None = None
    """流式消息目标"""

    # ── 终态收尾标记 ──
    history_updated: bool = False
    """历史是否已提交（success 路径内自动提交）"""

    title_generation_triggered: bool = False
    """是否已触发对话标题生成"""

    terminal_emitted: bool = False
    """终态追踪事件是否已发出（避免重复 emit）"""

    sequence: int = 0
    """最后发送的消息序号"""

    salvage_partial_run: bool = False
    """是否已落盘部分运行结果"""


class AgentWorker:
    """
    Agent 工作协程 - 从队列消费请求并处理

    架构:
    - 从 MessageBroker 的请求队列获取任务
    - 调用 PydanticAI Agent 进行流式处理
    - 将响应发送到对应连接的响应队列
    """

    def __init__(
        self,
        broker: MessageBroker,
        settings: Settings,
        worker_id: int = 0,
        addon: "AddonBridgeService | None" = None,
    ):
        self.broker = broker
        self.settings = settings
        self.worker_id = worker_id
        self._addon = addon
        self._running = False
        self._http_client: httpx.AsyncClient | None = None
        self._task: asyncio.Task | None = None
        self._title_tasks: set[asyncio.Task] = set()
        self.run_title_generation_inline = False

    async def start(self) -> None:
        """启动 Worker"""
        if self._running:
            logger.warning("worker_already_running", worker_id=self.worker_id)
            return

        self._running = True
        self._http_client = httpx.AsyncClient(timeout=self.settings.worker_http_timeout)

        logger.info("worker_started", worker_id=self.worker_id)

        # 创建后台任务
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """停止 Worker"""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._title_tasks:
            title_tasks = list(self._title_tasks)
            for task in title_tasks:
                task.cancel()
            await asyncio.gather(*title_tasks, return_exceptions=True)
            self._title_tasks.clear()

        if self._http_client:
            await self._http_client.aclose()

        logger.info("worker_stopped", worker_id=self.worker_id)

    async def _run(self) -> None:
        """Worker 主循环"""
        while self._running:
            item = None
            try:
                # 从队列获取请求（带超时，避免无限期阻塞）
                try:
                    item = await asyncio.wait_for(
                        self.broker.get_request(),
                        timeout=self.settings.worker_poll_timeout
                    )
                except asyncio.TimeoutError:
                    # 超时后继续循环，检查 _running 状态
                    continue

                # 处理请求
                await self._process_request(item)

            except asyncio.CancelledError:
                logger.info("worker_cancelled", worker_id=self.worker_id)
                # 若已取出 item 但尚未 request_done，在 finally 中补齐
                raise
            except Exception as e:
                logger.error(
                    "worker_error",
                    worker_id=self.worker_id,
                    error=str(e),
                    exc_info=True,
                )
                # 继续运行，不因单个错误而停止
            finally:
                # 正常、异常、取消路径均完成 queue task，避免悬挂
                if item is not None:
                    try:
                        self.broker.request_done()
                    except Exception as done_error:
                        logger.error(
                            "worker_request_done_failed",
                            worker_id=self.worker_id,
                            error=str(done_error),
                        )

    async def _process_request(self, item: any) -> None:
        """
        处理单个请求

        Args:
            item: QueueItem 包含 connection_id 和 payload (ChatRequest)
        """
        request: ChatRequest = item.payload
        connection_id: UUID = item.connection_id
        trace_context = getattr(item, "trace_context", None)
        enqueued_at_ns = int(getattr(item, "enqueued_at_ns", 0) or 0)

        # 同一 (连接, 玩家) 串行；不同玩家可并行，避免上下文乱序又不互相阻塞。
        session_lock = self.broker.get_session_lock(
            connection_id, request.player_name, request.conversation_id
        )
        async with session_lock:
            await self._process_request_locked(
                request,
                connection_id,
                trace_context=trace_context,
                enqueued_at_ns=enqueued_at_ns,
            )

    def _resolve_trace_context(
        self,
        request: ChatRequest,
        connection_id: UUID,
        *,
        trace_context: TraceContext | None = None,
    ) -> TraceContext | None:
        """优先使用 QueueItem.trace_context；否则从 ChatRequest correlation 重建。"""
        if trace_context is not None:
            return trace_context
        trace_id = request.trace_id or request.run_id
        attempt_id = request.attempt_id
        if not trace_id or not attempt_id:
            return None
        from models.constants import DEFAULT_CONVERSATION_ID, DEFAULT_PLAYER_KEY

        return TraceContext(
            trace_id=trace_id,
            run_id=request.run_id or trace_id,
            attempt_id=attempt_id,
            message_id=str(request.id),
            connection_id=str(connection_id),
            player_name=request.player_name or DEFAULT_PLAYER_KEY,
            conversation_id=request.conversation_id or DEFAULT_CONVERSATION_ID,
        )

    def _emit_lifecycle(
        self,
        event_name: str,
        context: TraceContext | None,
        *,
        status: str = "info",
        duration_ms: int | None = None,
        attributes: dict | None = None,
        payload: dict | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        """Fail-soft lifecycle emit via process TraceRecorder."""
        if context is None:
            return
        try:
            recorder = get_trace_recorder(self.settings)
            attrs = dict(attributes or {})
            # Free-text diagnostics only when content mode is on (match harness)
            if not getattr(recorder, "include_content", False):
                attrs.pop("diagnostic_summary", None)
            recorder.emit(
                event_name,
                context,
                status=status,
                duration_ms=duration_ms,
                attributes=attrs or None,
                payload=payload,
                tool_call_id=tool_call_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("trace_emit_failed", event=event_name, error=str(exc))

    def _record_model_pairs_from_messages(
        self,
        context: TraceContext | None,
        messages: list | None,
        *,
        usage: dict | None = None,
        provider: str | None = None,
    ) -> None:
        """从 new_messages 按 request/response 对写入 model.request.completed。"""
        if context is None or not messages:
            return
        try:
            from services.agent.core import serialize_model_messages
            from services.agent.trace import get_trace_recorder

            recorder = get_trace_recorder(self.settings)
            serialized = serialize_model_messages(messages)
            # 若已是 dict 列表（测试 fixture）直接使用
            if messages and isinstance(messages[0], dict):
                serialized = list(messages)  # type: ignore[arg-type]

            pair: list[dict] = []
            pairs: list[list[dict]] = []
            for msg in serialized:
                kind = msg.get("kind") if isinstance(msg, dict) else None
                if kind == "request":
                    if pair:
                        pairs.append(pair)
                    pair = [msg]
                elif kind == "response":
                    pair.append(msg)
                    pairs.append(pair)
                    pair = []
                else:
                    pair.append(msg)
            if pair:
                pairs.append(pair)

            for idx, group in enumerate(pairs):
                finish_reason = None
                for item in group:
                    if isinstance(item, dict) and item.get("kind") == "response":
                        finish_reason = item.get("finish_reason")
                recorder.record_model_messages(
                    context,
                    messages=group,
                    usage=usage if idx == len(pairs) - 1 else None,
                    provider=provider,
                    finish_reason=finish_reason,
                    attributes={"pair_index": idx},
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("trace_model_pairs_failed", error=str(exc))

    def _record_validation_failures_from_messages(
        self,
        *,
        messages: list | None,
        run_id: str,
        deps: AgentDependencies,
        trace_context: TraceContext | None,
        recorder: Any,
        seen: set[tuple[str, str, str]],
    ) -> None:
        """审计并 Trace 当前 run 的工具参数校验失败。

        该入口同时服务成功终态和 salvage error；消息级 extractor 保持纯函数，
        这里负责把结果投影到两个有副作用的观察面。失败发生在工具执行前，
        因此绝不补发 execution.started 或 external_state_unknown。
        """
        if not messages:
            return
        try:
            failures = extract_tool_validation_failures(messages, run_id=run_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("tool_validation_failure_extract_failed", error=str(exc))
            return

        for failure in failures:
            key = (
                run_id,
                str(failure.get("tool_call_id") or ""),
                str(failure.get("retry_timestamp") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            tool_call_id = str(failure.get("tool_call_id") or "") or None
            audit_ctx = SimpleNamespace(deps=deps, tool_call_id=tool_call_id)
            enqueue_validation_failure_audit(
                failure,
                settings=self.settings,
                ctx=audit_ctx,
                run_id=run_id,
            )
            self._emit_validation_failure_trace(
                recorder,
                trace_context,
                failure,
            )

    def _emit_validation_failure_trace(
        self,
        recorder: Any,
        context: TraceContext | None,
        failure: dict,
    ) -> None:
        if recorder is None or context is None:
            return
        try:
            attributes = {
                "tool_name": str(failure.get("tool_name") or "unknown"),
                "tool_call_id": str(failure.get("tool_call_id") or ""),
                "error_kind": "INVALID_ARGUMENT",
                "execution_stage": "validation",
                "validation_error_type": str(
                    failure.get("error_type") or "validation_error"
                ),
                "validation_error_locations": list(
                    failure.get("error_locations") or []
                ),
                "parameters": dict(failure.get("parameters") or {}),
            }
            payload = None
            if getattr(recorder, "include_content", False):
                payload = {"error_message": failure.get("validation_content")}
            recorder.emit(
                "tool.validation.failed",
                context,
                status="failed",
                tool_call_id=str(failure.get("tool_call_id") or "") or None,
                attributes=attributes,
                payload=payload,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("trace_tool_validation_failed", error=str(exc))

    async def _process_request_locked(
        self,
        request: ChatRequest,
        connection_id: UUID,
        conversation_generation: int | None = None,
        *,
        trace_context: TraceContext | None = None,
        enqueued_at_ns: int = 0,
    ) -> None:
        """处理单个请求（已持有会话锁）— 方案四重构版。

        AgentWorker 负责：
        1. 设置上下文（历史加载、依赖构建、广播）
        2. 调用 _execute_single_request 执行
        3. 基于 ExecutionResult 决定收尾动作（提交历史、生成标题、终态追踪）

        单次执行核心逻辑在 _execute_single_request 中。
        """
        if conversation_generation is None:
            conversation_generation = request.conversation_generation
        conversation_invalidation_epoch = request.conversation_invalidation_epoch

        # 确保 run 有稳定身份
        run_id = request.run_id or str(uuid4())
        if not request.run_id:
            request.run_id = run_id
        if not request.trace_id:
            request.trace_id = run_id
        if not request.attempt_id:
            request.attempt_id = str(uuid4())

        resolved_context = self._resolve_trace_context(
            request, connection_id, trace_context=trace_context
        )
        recorder = get_trace_recorder(self.settings)
        validation_failures_seen: set[tuple[str, str, str]] = set()

        # queue.dequeued + agent.attempt.started / resumed
        dequeue_ms = None
        if enqueued_at_ns > 0:
            dequeue_ms = max(0, int((time.time_ns() - enqueued_at_ns) / 1_000_000))
        self._emit_lifecycle(
            "queue.dequeued",
            resolved_context,
            status="info",
            duration_ms=dequeue_ms,
            attributes={"worker_id": self.worker_id},
        )
        if request.resume_approval_id and request.deferred_tool_results is not None:
            self._emit_lifecycle(
                "agent.attempt.resumed",
                resolved_context,
                status="resumed",
                attributes={
                    "worker_id": self.worker_id,
                    "resume_approval_id": request.resume_approval_id,
                    "provider": request.provider or self.settings.default_provider,
                },
            )
        else:
            self._emit_lifecycle(
                "agent.attempt.started",
                resolved_context,
                status="started",
                attributes={
                    "worker_id": self.worker_id,
                    "provider": request.provider or self.settings.default_provider,
                },
            )

        logger.info(
            "processing_chat_request",
            worker_id=self.worker_id,
            connection_id=str(connection_id),
            player=request.player_name,
            conversation_id=request.conversation_id,
            run_id=run_id,
            content_length=len(request.content),
        )

        # ── 历史加载与依赖准备 ──
        message_history: list[ModelMessage] | None = None
        deferred_tool_results: DeferredToolResults | None = None
        resume_prompt: str | None = request.content
        cleared_count = 0

        if request.resume_approval_id and request.deferred_tool_results is not None:
            resume_prompt = None
            raw_history = request.resume_message_history or []
            message_history = list(raw_history)
            deferred_tool_results = self._coerce_deferred_tool_results(request.deferred_tool_results)
        elif request.use_context:
            raw_history = self.broker.get_conversation_history(
                connection_id, request.player_name, request.conversation_id
            )
            message_history, cleared_count = self._sanitize_loaded_history(
                connection_id=connection_id,
                request=request,
                raw_history=raw_history,
                run_id=run_id,
                conversation_invalidation_epoch=conversation_invalidation_epoch,
            )
        if (
            message_history is not None
            and request.resume_approval_id
            and request.deferred_tool_results is not None
        ):
            deferred_call_ids = self._deferred_tool_result_call_ids(
                deferred_tool_results
            )
            message_history, removed_calls, removed_responses = self._sanitize_tool_history(
                message_history,
                preserve_call_ids=deferred_call_ids,
            )
            if removed_calls or removed_responses:
                logger.info(
                    "chat_history_orphan_tool_parts_removed",
                    worker_id=self.worker_id,
                    connection_id=str(connection_id),
                    player=request.player_name,
                    conversation_id=request.conversation_id,
                    run_id=run_id,
                    removed_orphan_tool_calls=removed_calls,
                    removed_orphan_tool_responses=removed_responses,
                )
        else:
            removed_calls = removed_responses = 0
        if message_history is not None and request.use_context:
            logger.debug(
                "chat_history_loaded",
                worker_id=self.worker_id,
                connection_id=str(connection_id),
                player=request.player_name,
                conversation_id=request.conversation_id,
                run_id=run_id,
                history_message_count=len(message_history),
                cleared_reasoning_content_count=cleared_count,
            )

        # 构建依赖
        deps = AgentDependencies(
            connection_id=connection_id,
            player_name=request.player_name or DEFAULT_PLAYER_DISPLAY_NAME,
            settings=self.settings,
            http_client=self._http_client,
            send_to_game=self._create_send_callback(connection_id),
            run_command=self._create_command_callback(connection_id),
            addon_bridge=self._create_addon_bridge_client(connection_id),
            provider=request.provider or self.settings.default_provider,
            get_context_info=self._make_context_info_fn(
                connection_id, request, run_id
            ),
            run_id=run_id,
            attempt_id=request.attempt_id,
            conversation_id=request.conversation_id,
            auto_approve_tools=bool(request.auto_approve_tools),
            trace_context=resolved_context,
            trace_recorder=recorder,
        )

        stream_target = "@a" if request.broadcast_ai_chat else request.player_name
        if request.broadcast_ai_chat:
            await self.broker.send_response(
                connection_id,
                SystemNotification(
                    connection_id=connection_id,
                    level="info",
                    message=f"AI正在为{request.player_name or DEFAULT_PLAYER_DISPLAY_NAME}思考",
                    player_name="@a",
                ),
            )

        # ── 执行前压缩 ──
        provider_name = request.provider or self.settings.default_provider
        run_identifier = dict(
            connection_id=connection_id,
            player_name=request.player_name,
            conversation_id=request.conversation_id,
            run_id=run_id,
        )
        message_history = await self._maybe_compress_before_run(
            request=request,
            connection_id=connection_id,
            provider_name=provider_name,
            message_history=message_history,
            run_id=run_id,
            conversation_invalidation_epoch=conversation_invalidation_epoch,
            cleared_count=cleared_count,
            run_identifier=run_identifier,
        )

        # ── 执行单次请求 ──
        result = await self._execute_single_request(
            resume_prompt=resume_prompt,
            deps=deps,
            provider_name=provider_name,
            message_history=message_history,
            deferred_tool_results=deferred_tool_results,
            request=request,
            connection_id=connection_id,
            stream_target=stream_target,
            resolved_context=resolved_context,
            recorder=recorder,
            validation_failures_seen=validation_failures_seen,
            conversation_invalidation_epoch=conversation_invalidation_epoch,
            run_identifier=run_identifier,
        )

        # ── 收尾：基于 ExecutionResult 决定动作 ──
        if result.status == "success":
            # 成功路径：历史已在 _execute_single_request 的 is_complete 中提交
            if not result.terminal_emitted and resolved_context is not None:
                self._record_model_pairs_from_messages(
                    resolved_context,
                    result.new_messages,
                    usage=result.usage,
                    provider=provider_name,
                )
                try:
                    recorder.record_final_response(
                        resolved_context,
                        content=result.response_text,
                        duration_ms=result.duration_ms,
                        status="completed",
                        attributes={
                            "chunk_count": result.event_count,
                            "tool_events_count": result.tool_events_count,
                        },
                    )
                except Exception as exc:
                    logger.debug("trace_final_response_failed", error=str(exc))
        elif result.status == "approval_pending":
            if not result.terminal_emitted:
                self._emit_lifecycle(
                    "trace.suspended",
                    resolved_context,
                    status="suspended",
                    attributes={
                        "reason": "approval_required",
                        "worker_id": self.worker_id,
                    },
                )
        elif result.status == "partial":
            # mid-run 失败，已有部分历史落盘
            if not result.terminal_emitted and resolved_context is not None:
                self._record_model_pairs_from_messages(
                    resolved_context,
                    result.new_messages,
                    usage=result.usage,
                    provider=provider_name,
                )
                self._emit_lifecycle(
                    "trace.failed",
                    resolved_context,
                    status="failed",
                    attributes={
                        "error_kind": result.error_kind,
                        "diagnostic_summary": result.diagnostic_summary,
                        "salvage_partial_run": result.salvage_partial_run,
                    },
                )
        elif result.status in ("timeout", "cancelled"):
            if not result.terminal_emitted:
                self._emit_lifecycle(
                    "trace.cancelled" if result.status == "cancelled" else "trace.failed",
                    resolved_context,
                    status=result.status,
                    attributes={"worker_id": self.worker_id},
                )
            if result.status == "timeout":
                await self._send_error_chunk(
                    connection_id,
                    request.player_name,
                    result.error_summary or "请求超时",
                    result.sequence,
                    target=stream_target,
                    error_kind=result.error_kind,
                    run_id=run_id,
                    trace_id=request.trace_id or run_id,
                    attempt_id=request.attempt_id,
                )
        elif result.status == "exception":
            if not result.terminal_emitted:
                self._emit_lifecycle(
                    "trace.failed",
                    resolved_context,
                    status="failed",
                    attributes={
                        "error_kind": result.error_kind,
                        "diagnostic_summary": result.diagnostic_summary,
                    },
                )
        elif result.status == "disconnected":
            if not result.terminal_emitted:
                self._emit_lifecycle(
                    "trace.cancelled",
                    resolved_context,
                    status="cancelled",
                    attributes={"reason": "connection_disconnected"},
                )

    async def _execute_single_request(
        self,
        *,
        resume_prompt: str | None,
        deps: AgentDependencies,
        provider_name: str,
        message_history: list[ModelMessage] | None,
        deferred_tool_results: DeferredToolResults | None,
        request: ChatRequest,
        connection_id: UUID,
        stream_target: str | None,
        resolved_context: TraceContext | None,
        recorder: Any,
        validation_failures_seen: set[tuple[str, str, str]],
        conversation_invalidation_epoch: int | None,
        run_identifier: dict,
    ) -> ExecutionResult:
        """执行单次 Agent 请求的核心管道。

        职责（单次执行深模块）：
        - 获取模型实例
        - 运行流式事件循环
        - 收集响应/推理内容
        - 处理 tool_call/tool_result/approval_required/error 事件
        - 在 is_complete 时提交历史、触发标题生成
        - 异常/取消/超时处理（包括 MCP 超时标记）
        - 返回 ExecutionResult 供 AgentWorker 收尾

        AgentWorker 不参与此模块的内部逻辑。
        """
        run_id = run_identifier["run_id"]
        start_time = time.monotonic()
        sequence = 0
        event_count = 0
        response_parts: list[str] = []
        reasoning_parts: list[str] = []
        reasoning_started = False
        thinking_end_sent = False
        enable_reasoning_output = self.settings.enable_reasoning_output
        terminal_emitted = False
        history_updated = False
        title_generation_triggered = False
        salvage_partial_run = False

        from services.agent.core import _is_mcp_timeout_error
        mcp_manager = get_agent_runtime().get_mcp_manager(self.settings)
        # 获取模型
        try:
            provider_config = self.settings.get_provider_config(provider_name)
            model = ProviderRegistry.get_model(provider_config)

            logger.debug(
                "using_provider",
                provider=provider_name,
                model=provider_config.model,
                run_id=run_id,
            )
        except Exception as e:
            logger.error(
                "provider_error",
                provider=provider_name,
                error=str(e),
                run_id=run_id,
            )
            await self._send_error_chunk(
                connection_id,
                request.player_name,
                player_facing_error("INTERNAL"),
                0,
                target=stream_target,
                error_kind="INTERNAL",
                run_id=run_id,
                trace_id=request.trace_id or run_id,
                attempt_id=request.attempt_id,
            )
            return ExecutionResult(
                status="exception",
                error_kind="INTERNAL",
                error_summary=player_facing_error("INTERNAL"),
                diagnostic_summary=str(e)[:200],
                trace_context=resolved_context,
                terminal_emitted=False,
                provider=provider_name,
                stream_target=stream_target,
            )

        try:
            async for event in stream_chat(
                resume_prompt,
                deps,
                model,
                message_history=message_history,
                deferred_tool_results=deferred_tool_results,
            ):
                event_count += 1
                if event.event_type == "content" and event.content:
                    response_parts.append(event.content)
                elif event.event_type == "reasoning" and event.content:
                    reasoning_parts.append(event.content)

                elif event.event_type == "tool_call":
                    tool_name = event.metadata.get("tool_name", "unknown") if event.metadata else "unknown"
                    tool_args = event.metadata.get("args") if event.metadata else None
                    if isinstance(tool_args, str):
                        import json
                        try:
                            tool_args = json.loads(tool_args)
                        except json.JSONDecodeError:
                            tool_args = {"raw": tool_args}
                    tool_msg = format_tool_call_message(tool_name, tool_args)
                    logger.info(
                        "worker_tool_call_display",
                        tool=tool_name,
                        args=tool_args,
                        display=tool_msg,
                        connection_id=str(connection_id),
                        player_name=request.player_name,
                    )
                    tool_chunk = StreamChunk(
                        connection_id=connection_id,
                        chunk_type="tool_call",
                        content=tool_msg,
                        sequence=sequence,
                        delivery=request.delivery,
                        player_name=request.player_name,
                        target=stream_target,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        **self._chunk_correlation(request),
                    )
                    await self.broker.send_response(connection_id, tool_chunk)
                    sequence += 1

                elif event.event_type == "tool_result":
                    tool_name = event.metadata.get("tool_name") if event.metadata else None
                    result_content = event.content
                    logger.info(
                        "worker_tool_result_display",
                        tool=tool_name,
                        result=result_content,
                        result_length=len(result_content or ""),
                        connection_id=str(connection_id),
                        player_name=request.player_name,
                    )
                    if self.settings.tool_response_verbose:
                        tool_result_msg = format_tool_result_message(
                            tool_name or "tool",
                            result_content,
                        )
                        result_chunk = StreamChunk(
                            connection_id=connection_id,
                            chunk_type="tool_result",
                            content=tool_result_msg,
                            sequence=sequence,
                            delivery=request.delivery,
                            player_name=request.player_name,
                            target=stream_target,
                            tool_name=tool_name,
                            tool_result_preview=truncate_text(result_content, 80),
                            **self._chunk_correlation(request),
                        )
                        await self.broker.send_response(connection_id, result_chunk)
                        sequence += 1

                elif event.event_type == "approval_required":
                    validation_messages = (
                        event.metadata.get("new_messages")
                        if event.metadata
                        else None
                    )
                    if not isinstance(validation_messages, list):
                        validation_messages = (
                            event.metadata.get("all_messages")
                            if event.metadata
                            else None
                        )
                    self._record_validation_failures_from_messages(
                        messages=validation_messages
                        if isinstance(validation_messages, list)
                        else None,
                        run_id=run_id,
                        deps=deps,
                        trace_context=resolved_context,
                        recorder=recorder,
                        seen=validation_failures_seen,
                    )
                    if resolved_context is not None and event.metadata:
                        new_messages = event.metadata.get("new_messages_serialized")
                        if not isinstance(new_messages, list):
                            new_messages = event.metadata.get("new_messages")
                        usage = event.metadata.get("usage")
                        usage_dict = usage if isinstance(usage, dict) else None
                        self._record_model_pairs_from_messages(
                            resolved_context,
                            new_messages if isinstance(new_messages, list) else None,
                            usage=usage_dict,
                            provider=provider_name,
                        )
                    await self._handle_approval_required(
                        request=request,
                        connection_id=connection_id,
                        event=event,
                        sequence=sequence,
                        stream_target=stream_target,
                        trace_context=resolved_context,
                    )
                    return ExecutionResult(
                        status="approval_pending",
                        response_text="".join(response_parts),
                        reasoning_text="".join(reasoning_parts),
                        event_count=event_count,
                        duration_ms=int((time.monotonic() - start_time) * 1000),
                        terminal_emitted=terminal_emitted,
                        trace_context=resolved_context,
                        provider=provider_name,
                        stream_target=stream_target,
                        sequence=sequence,
                    )

                if event.metadata and event.metadata.get("is_complete"):
                    validation_messages = event.metadata.get("new_messages")
                    if not isinstance(validation_messages, list):
                        validation_messages = event.metadata.get("all_messages")
                    self._record_validation_failures_from_messages(
                        messages=validation_messages
                        if isinstance(validation_messages, list)
                        else None,
                        run_id=run_id,
                        deps=deps,
                        trace_context=resolved_context,
                        recorder=recorder,
                        seen=validation_failures_seen,
                    )
                    all_messages = event.metadata.get("all_messages")
                    tool_events = event.metadata.get("tool_events")
                    tool_events_count = len(tool_events) if tool_events else 0
                    usage = event.metadata.get("usage")
                    usage_dict = usage if isinstance(usage, dict) else None
                    new_messages = event.metadata.get("new_messages_serialized")
                    if not isinstance(new_messages, list):
                        new_messages = event.metadata.get("new_messages")

                    if isinstance(all_messages, list):
                        if self.broker.get_response_queue(connection_id) is not None:
                            trimmed_history = self._trim_history(
                                all_messages,
                                self.settings.max_history_turns,
                            )
                            trimmed_history, cleared_count_val = self._strip_reasoning_content(
                                trimmed_history
                            )
                            history_updated = self.broker.set_conversation_history(
                                connection_id,
                                request.player_name,
                                trimmed_history,
                                request.conversation_id,
                                expected_invalidation_epoch=conversation_invalidation_epoch,
                            )
                            if history_updated:
                                logger.debug(
                                    "chat_history_updated",
                                    worker_id=self.worker_id,
                                    connection_id=str(connection_id),
                                    player=request.player_name,
                                    conversation_id=request.conversation_id,
                                    history_message_count=len(trimmed_history),
                                    cleared_reasoning_content_count=cleared_count_val,
                                )

                                if (
                                    self._count_user_prompts(trimmed_history) == 1
                                    and self.broker.mark_conversation_title_generating(
                                        connection_id,
                                        request.player_name,
                                        request.conversation_id,
                                    )
                                ):
                                    await self._schedule_title_generation(
                                        connection_id,
                                        request.player_name,
                                        request.conversation_id,
                                        request.content,
                                        model,
                                    )
                                    title_generation_triggered = True

                                from core.conversation import get_conversation_manager

                                conv_manager = get_conversation_manager(self.broker, self.settings)
                                compressed, msg = await conv_manager.check_and_compress(
                                    connection_id,
                                    request.player_name,
                                    force=False,
                                    conversation_id=request.conversation_id,
                                    provider_name=provider_name,
                                )
                                if compressed:
                                    await self.broker.send_response(
                                        connection_id,
                                        SystemNotification(
                                            connection_id=connection_id,
                                            level="info",
                                            message=f"对话历史已自动压缩，{msg}",
                                            player_name=request.player_name,
                                        ),
                                    )
                                    logger.debug(
                                        "auto_compression_triggered",
                                        worker_id=self.worker_id,
                                        connection_id=str(connection_id),
                                        player=request.player_name,
                                        message=msg,
                                    )
                            else:
                                logger.info(
                                    "chat_history_stale_write_skipped",
                                    worker_id=self.worker_id,
                                    connection_id=str(connection_id),
                                    player=request.player_name,
                                    conversation_id=request.conversation_id,
                                    run_id=run_id,
                                )

                    response_text = "".join(response_parts)
                    reasoning_text = "".join(reasoning_parts)
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    logger.info(
                        "chat_response_complete",
                        worker_id=self.worker_id,
                        connection_id=str(connection_id),
                        player=request.player_name,
                        conversation_id=request.conversation_id,
                        run_id=run_id,
                        response_length=len(response_text),
                        reasoning_length=len(reasoning_text),
                        chunk_count=event_count,
                        duration_ms=duration_ms,
                        usage=usage_dict,
                        tool_events=tool_events,
                        tool_events_count=tool_events_count,
                    )

                    if response_text:
                        await self.broker.send_response(connection_id, {
                            "type": "ai_response_sync",
                            "player_name": request.player_name or DEFAULT_PLAYER_DISPLAY_NAME,
                            "role": "assistant",
                            "text": response_text,
                        })

                    return ExecutionResult(
                        status="success",
                        response_text=response_text,
                        reasoning_text=reasoning_text,
                        event_count=event_count,
                        duration_ms=duration_ms,
                        all_messages=all_messages if isinstance(all_messages, list) else None,
                        new_messages=new_messages if isinstance(new_messages, list) else None,
                        usage=usage_dict,
                        trace_context=resolved_context,
                        tool_events_count=tool_events_count,
                        provider=provider_name,
                        stream_target=stream_target,
                        history_updated=history_updated,
                        title_generation_triggered=title_generation_triggered,
                        terminal_emitted=terminal_emitted,
                        sequence=sequence,
                    )

                elif event.event_type == "error":
                    response_text = "".join(response_parts)
                    error_kind = (
                        event.metadata.get("error_kind") if event.metadata else None
                    ) or "INTERNAL"
                    diagnostic_summary = (
                        event.metadata.get("diagnostic_summary")
                        if event.metadata
                        else None
                    )
                    logger.error(
                        "chat_response_error",
                        worker_id=self.worker_id,
                        connection_id=str(connection_id),
                        run_id=run_id,
                        error_kind=error_kind,
                        diagnostic_summary=diagnostic_summary,
                        response_length=len(response_text),
                        salvaged=bool(
                            event.metadata and event.metadata.get("all_messages")
                        ),
                    )

                    validation_messages = event.metadata.get("new_messages")
                    if not isinstance(validation_messages, list):
                        validation_messages = event.metadata.get("all_messages")
                    self._record_validation_failures_from_messages(
                        messages=validation_messages
                        if isinstance(validation_messages, list)
                        else None,
                        run_id=run_id,
                        deps=deps,
                        trace_context=resolved_context,
                        recorder=recorder,
                        seen=validation_failures_seen,
                    )

                    if (
                        request.use_context
                        and event.metadata
                        and isinstance(event.metadata.get("all_messages"), list)
                        and self.broker.get_response_queue(connection_id) is not None
                    ):
                        await self._persist_partial_run_history(
                            connection_id=connection_id,
                            request=request,
                            all_messages=event.metadata["all_messages"],
                            player_error_text=event.content or "",
                            conversation_invalidation_epoch=conversation_invalidation_epoch,
                        )
                        salvage_partial_run = True

                    new_messages = event.metadata.get("new_messages_serialized")
                    if not isinstance(new_messages, list):
                        new_messages = event.metadata.get("new_messages")
                    usage = event.metadata.get("usage")
                    usage_dict = usage if isinstance(usage, dict) else None

                    chunk = StreamChunk(
                        connection_id=connection_id,
                        chunk_type=event.event_type,
                        content=event.content,
                        sequence=sequence,
                        delivery=request.delivery,
                        player_name=request.player_name,
                        target=stream_target,
                        **self._chunk_correlation(request),
                    )
                    await self.broker.send_response(connection_id, chunk)
                    sequence += 1

                    return ExecutionResult(
                        status="partial",
                        response_text=response_text,
                        reasoning_text="".join(reasoning_parts),
                        event_count=event_count,
                        duration_ms=int((time.monotonic() - start_time) * 1000),
                        all_messages=(
                            event.metadata.get("all_messages")
                            if isinstance(event.metadata.get("all_messages"), list)
                            else None
                        ),
                        new_messages=new_messages if isinstance(new_messages, list) else None,
                        usage=usage_dict,
                        error_kind=error_kind,
                        error_summary=event.content or "",
                        diagnostic_summary=diagnostic_summary,
                        trace_context=resolved_context,
                        provider=provider_name,
                        stream_target=stream_target,
                        salvage_partial_run=salvage_partial_run,
                        terminal_emitted=terminal_emitted,
                        sequence=sequence,
                    )

                elif event.event_type in ("content", "reasoning"):
                    if event.content:
                        if (
                            event.event_type == "reasoning"
                            and not reasoning_started
                            and enable_reasoning_output
                        ):
                            reasoning_started = True
                            start_chunk = StreamChunk(
                                connection_id=connection_id,
                                chunk_type="thinking_start",
                                content="",
                                sequence=sequence,
                                delivery=request.delivery,
                                player_name=request.player_name,
                                target=stream_target,
                                **self._chunk_correlation(request),
                            )
                            await self.broker.send_response(connection_id, start_chunk)
                            sequence += 1

                        if (
                            event.event_type == "content"
                            and reasoning_started
                            and not thinking_end_sent
                            and enable_reasoning_output
                        ):
                            thinking_end_sent = True
                            end_chunk = StreamChunk(
                                connection_id=connection_id,
                                chunk_type="thinking_end",
                                content="",
                                sequence=sequence,
                                delivery=request.delivery,
                                player_name=request.player_name,
                                target=stream_target,
                                **self._chunk_correlation(request),
                            )
                            await self.broker.send_response(connection_id, end_chunk)
                            sequence += 1

                        should_send = (
                            enable_reasoning_output
                            if event.event_type == "reasoning"
                            else True
                        )
                        if should_send:
                            chunk = StreamChunk(
                                connection_id=connection_id,
                                chunk_type=event.event_type,
                                content=event.content,
                                sequence=sequence,
                                delivery=request.delivery,
                                player_name=request.player_name,
                                target=stream_target,
                                **self._chunk_correlation(request),
                            )
                            await self.broker.send_response(connection_id, chunk)
                            sequence += 1

        except asyncio.CancelledError:
            logger.info(
                "stream_processing_cancelled",
                worker_id=self.worker_id,
                connection_id=str(connection_id),
                run_id=run_id,
            )
            return ExecutionResult(
                status="cancelled",
                response_text="".join(response_parts),
                reasoning_text="".join(reasoning_parts),
                event_count=event_count,
                duration_ms=int((time.monotonic() - start_time) * 1000),
                trace_context=resolved_context,
                terminal_emitted=terminal_emitted,
                provider=provider_name,
                stream_target=stream_target,
                sequence=sequence,
            )
        except Exception as e:
            error_kind, player_msg, diagnostic = classify_run_exception(e)

            if _is_mcp_timeout_error(e):
                logger.warning(
                    "mcp_timeout_detected_in_worker",
                    worker_id=self.worker_id,
                    connection_id=str(connection_id),
                    run_id=run_id,
                    error=diagnostic,
                )
                if mcp_manager is not None:
                    detail = diagnostic
                    matched = False
                    for server_name in list(mcp_manager.servers.keys()):
                        if server_name and server_name in detail:
                            mcp_manager.mark_server_failed(
                                server_name, f"连接超时: {diagnostic}"
                            )
                            matched = True
                    if matched:
                        from services.agent.runtime import get_agent_runtime

                        get_agent_runtime().refresh_mcp_tools(self.settings)

            logger.error(
                "stream_processing_error",
                worker_id=self.worker_id,
                connection_id=str(connection_id),
                run_id=run_id,
                error_kind=error_kind,
                error=diagnostic,
                exc_info=True,
            )
            await self._send_error_chunk(
                connection_id,
                request.player_name,
                player_msg,
                sequence,
                target=stream_target,
                error_kind=error_kind,
                run_id=run_id,
                trace_id=request.trace_id or run_id,
                attempt_id=request.attempt_id,
            )
            return ExecutionResult(
                status="exception",
                response_text="".join(response_parts),
                reasoning_text="".join(reasoning_parts),
                event_count=event_count,
                duration_ms=int((time.monotonic() - start_time) * 1000),
                error_kind=error_kind,
                error_summary=player_msg,
                diagnostic_summary=diagnostic,
                trace_context=resolved_context,
                terminal_emitted=terminal_emitted,
                provider=provider_name,
                stream_target=stream_target,
                sequence=sequence,
            )

        # 流正常结束（无 is_complete 事件且无响应内容）：作为超时处理
        end_text = "".join(response_parts)
        if end_text:
            # 有响应内容但没收到 is_complete：视为成功，避免误报超时
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.info(
                "stream_finished_without_is_complete",
                worker_id=self.worker_id,
                connection_id=str(connection_id),
                run_id=run_id,
                response_length=len(end_text),
            )
            return ExecutionResult(
                status="success",
                response_text=end_text,
                reasoning_text="".join(reasoning_parts),
                event_count=event_count,
                duration_ms=duration_ms,
                trace_context=resolved_context,
                terminal_emitted=terminal_emitted,
                provider=provider_name,
                stream_target=stream_target,
                sequence=sequence,
            )
        return ExecutionResult(
            status="timeout",
            event_count=event_count,
            duration_ms=int((time.monotonic() - start_time) * 1000),
            error_kind="TIMEOUT",
            error_summary="请求超时",
            trace_context=resolved_context,
            terminal_emitted=terminal_emitted,
            provider=provider_name,
            stream_target=stream_target,
            sequence=sequence,
        )

    def _make_context_info_fn(
        self,
        connection_id: UUID,
        request: ChatRequest,
        run_id: str,
    ):
        """构建获取上下文信息的回调。"""
        def get_context_info() -> ContextInfo | None:
            if not request.use_context:
                return None
            history = self.broker.get_conversation_history(
                connection_id, request.player_name, request.conversation_id
            )
            message_count = len(history) if history else 0
            from services.agent.context import estimate_history_tokens
            estimated_tokens = estimate_history_tokens(history) if history else 0
            provider_name = request.provider or self.settings.default_provider
            provider_config = self.settings.get_provider_config(provider_name)
            max_tokens = provider_config.context_window
            return ContextInfo(
                message_count=message_count,
                estimated_tokens=estimated_tokens,
                max_tokens=max_tokens,
            )
        return get_context_info

    async def _maybe_compress_before_run(
        self,
        *,
        request: ChatRequest,
        connection_id: UUID,
        provider_name: str,
        message_history: list[ModelMessage] | None,
        run_id: str,
        conversation_invalidation_epoch: int | None,
        cleared_count: int,
        run_identifier: dict,
    ) -> list[ModelMessage] | None:
        """执行前压缩对话历史（审批恢复路径不压缩）。"""
        if not request.use_context or (
            request.resume_approval_id and request.deferred_tool_results is not None
        ):
            return message_history

        from core.conversation import get_conversation_manager

        conv_manager = get_conversation_manager(self.broker, self.settings)
        compressed, compress_msg = await conv_manager.check_and_compress(
            connection_id,
            request.player_name,
            force=False,
            conversation_id=request.conversation_id,
            provider_name=provider_name,
        )
        if compressed:
            raw_history = self.broker.get_conversation_history(
                connection_id, request.player_name, request.conversation_id
            )
            message_history, cleared_count = self._sanitize_loaded_history(
                connection_id=connection_id,
                request=request,
                raw_history=raw_history,
                run_id=run_id,
                conversation_invalidation_epoch=conversation_invalidation_epoch,
            )
            await self.broker.send_response(
                connection_id,
                SystemNotification(
                    connection_id=connection_id,
                    level="info",
                    message=f"对话历史已自动压缩，{compress_msg}",
                    player_name=request.player_name,
                ),
            )
            logger.debug(
                "pre_request_compression_triggered",
                worker_id=self.worker_id,
                connection_id=str(connection_id),
                player=request.player_name,
                message=compress_msg,
                history_message_count=len(message_history),
                cleared_reasoning_content_count=cleared_count,
            )
        return message_history

    async def _generate_title_for_conversation(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None,
        first_user_message: str,
        model: object,
    ) -> None:
        """为首轮完成后的对话生成标题。"""
        try:
            title = await generate_conversation_title(first_user_message, model)
            metadata = self.broker.set_conversation_title_if_connected(
                connection_id,
                player_name,
                conversation_id,
                title,
            )
            if metadata is None:
                return
            logger.info(
                "conversation_title_generated",
                worker_id=self.worker_id,
                connection_id=str(connection_id),
                player=player_name,
                conversation_id=conversation_id,
                title=title,
            )
        except Exception as e:
            if self.broker.has_connection(connection_id):
                self.broker.mark_conversation_title_failed(
                    connection_id,
                    player_name,
                    conversation_id,
                )
            logger.warning(
                "conversation_title_generation_failed",
                worker_id=self.worker_id,
                connection_id=str(connection_id),
                player=player_name,
                conversation_id=conversation_id,
                error=str(e),
                exc_info=True,
            )

    async def _schedule_title_generation(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None,
        first_user_message: str,
        model: object,
    ) -> None:
        """调度对话标题生成；测试可切换为内联执行。"""
        if self.run_title_generation_inline:
            await self._generate_title_for_conversation(
                connection_id,
                player_name,
                conversation_id,
                first_user_message,
                model,
            )
            return

        task = asyncio.create_task(
            self._generate_title_for_conversation(
                connection_id,
                player_name,
                conversation_id,
                first_user_message,
                model,
            )
        )
        self._title_tasks.add(task)
        task.add_done_callback(self._title_tasks.discard)

    @staticmethod
    def _count_user_prompts(history: list[ModelMessage]) -> int:
        """统计历史中包含用户提示部分的消息数量。"""
        return sum(
            1
            for message in history
            if any(
                getattr(part, "part_kind", None) == "user-prompt"
                for part in getattr(message, "parts", [])
            )
        )

    async def _persist_partial_run_history(
        self,
        *,
        connection_id: UUID,
        request: ChatRequest,
        all_messages: list[ModelMessage],
        player_error_text: str,
        conversation_invalidation_epoch: int | None,
    ) -> None:
        """mid-run 失败时落盘已产生消息 + 错误说明，供下轮 LLM 继续。"""
        if not all_messages:
            return

        history, removed_calls, removed_responses = self._sanitize_tool_history(
            list(all_messages)
        )
        note = (player_error_text or "").strip()
        if note:
            # 避免与已有尾部 assistant 文本重复
            last = history[-1] if history else None
            last_text = ""
            if isinstance(last, ModelResponse):
                for part in getattr(last, "parts", []) or []:
                    if getattr(part, "part_kind", None) == "text":
                        last_text += str(getattr(part, "content", "") or "")
            if note not in last_text:
                history.append(
                    ModelResponse(
                        parts=[
                            TextPart(
                                content=(
                                    f"[系统] 本轮执行中断：{note}"
                                    " 已完成的工具结果见上文；请基于现状继续或向玩家说明。"
                                )
                            )
                        ]
                    )
                )

        trimmed_history = self._trim_history(history, self.settings.max_history_turns)
        trimmed_history, cleared_count = self._strip_reasoning_content(trimmed_history)
        (
            trimmed_history,
            trimmed_removed_calls,
            trimmed_removed_responses,
        ) = self._sanitize_tool_history(trimmed_history)
        removed_calls += trimmed_removed_calls
        removed_responses += trimmed_removed_responses
        try:
            history_updated = self.broker.set_conversation_history(
                connection_id,
                request.player_name,
                trimmed_history,
                request.conversation_id,
                expected_invalidation_epoch=conversation_invalidation_epoch,
            )
        except TypeError:
            # 旧 broker 签名兼容
            history_updated = self.broker.set_conversation_history(
                connection_id,
                request.player_name,
                trimmed_history,
                request.conversation_id,
            )

        if history_updated:
            logger.info(
                "chat_partial_history_persisted",
                worker_id=self.worker_id,
                connection_id=str(connection_id),
                player=request.player_name,
                conversation_id=request.conversation_id,
                history_message_count=len(trimmed_history),
                cleared_reasoning_content_count=cleared_count,
                removed_orphan_tool_calls=removed_calls,
                removed_orphan_tool_responses=removed_responses,
            )
            # 失败路径也做一次压缩检查（与成功路径一致），避免超大 partial 历史
            try:
                conv_manager = get_agent_runtime().get_conversation_manager(self.broker, self.settings)
                provider_name = request.provider or self.settings.default_provider
                compressed, msg = await conv_manager.check_and_compress(
                    connection_id,
                    request.player_name,
                    force=False,
                    conversation_id=request.conversation_id,
                    provider_name=provider_name,
                )
                if compressed:
                    await self.broker.send_response(
                        connection_id,
                        SystemNotification(
                            connection_id=connection_id,
                            level="info",
                            message=f"对话历史已自动压缩，{msg}",
                            player_name=request.player_name,
                        ),
                    )
                    logger.info(
                        "auto_compression_triggered_after_partial",
                        worker_id=self.worker_id,
                        connection_id=str(connection_id),
                        player=request.player_name,
                        message=msg,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "partial_history_compress_failed",
                    error=str(exc),
                    connection_id=str(connection_id),
                )
        else:
            logger.info(
                "chat_partial_history_stale_write_skipped",
                worker_id=self.worker_id,
                connection_id=str(connection_id),
                player=request.player_name,
                conversation_id=request.conversation_id,
            )

    def _sanitize_loaded_history(
        self,
        *,
        connection_id: UUID,
        request: ChatRequest,
        raw_history: list[ModelMessage],
        run_id: str,
        conversation_invalidation_epoch: int | None,
    ) -> tuple[list[ModelMessage], int]:
        """清理已存历史，并在当前 epoch 下回写清洗结果。"""
        message_history, cleared_count = self._strip_reasoning_content(raw_history)
        message_history, removed_calls, removed_responses = self._sanitize_tool_history(
            message_history
        )
        if not removed_calls and not removed_responses:
            return message_history, cleared_count

        logger.info(
            "chat_history_orphan_tool_parts_removed",
            worker_id=self.worker_id,
            connection_id=str(connection_id),
            player=request.player_name,
            conversation_id=request.conversation_id,
            run_id=run_id,
            removed_orphan_tool_calls=removed_calls,
            removed_orphan_tool_responses=removed_responses,
        )
        history_updated = self.broker.set_conversation_history(
            connection_id,
            request.player_name,
            message_history,
            request.conversation_id,
            expected_invalidation_epoch=conversation_invalidation_epoch,
        )
        if not history_updated:
            logger.info(
                "chat_history_orphan_tool_parts_stale_write_skipped",
                worker_id=self.worker_id,
                connection_id=str(connection_id),
                player=request.player_name,
                conversation_id=request.conversation_id,
                run_id=run_id,
                removed_orphan_tool_calls=removed_calls,
                removed_orphan_tool_responses=removed_responses,
            )
        return message_history, cleared_count

    @staticmethod
    def _sanitize_tool_history(
        messages: list[ModelMessage],
        *,
        preserve_call_ids: set[str] | None = None,
    ) -> tuple[list[ModelMessage], int, int]:
        """清理工具级孤立 part，并返回移除的 call/response 数量。"""

        def count_parts(history: list[ModelMessage]) -> tuple[int, int]:
            calls = 0
            responses = 0
            for message in history:
                for part in getattr(message, "parts", []) or []:
                    if isinstance(part, ToolCallPart):
                        calls += 1
                    elif isinstance(part, ToolReturnPart) or (
                        isinstance(part, RetryPromptPart) and part.tool_name is not None
                    ):
                        responses += 1
            return calls, responses

        before_calls, before_responses = count_parts(messages)
        cleaned = ensure_tool_message_pairs(
            messages,
            preserve_call_ids=preserve_call_ids,
        )
        after_calls, after_responses = count_parts(cleaned)
        return cleaned, before_calls - after_calls, before_responses - after_responses

    @staticmethod
    def _deferred_tool_result_call_ids(
        deferred_tool_results: DeferredToolResults | None,
    ) -> set[str]:
        """返回当前 resume 结果明确引用的 call IDs，不扩张为全部历史孤儿。"""
        if deferred_tool_results is None:
            return set()
        result: set[str] = set()
        for field_name in ("approvals", "calls"):
            values = getattr(deferred_tool_results, field_name, None)
            if not isinstance(values, dict):
                continue
            for call_id in values:
                normalized = str(call_id)
                if normalized.strip():
                    result.add(normalized)
        return result

    @staticmethod
    def _trim_history(
        messages: list[ModelMessage],
        max_turns: int,
    ) -> list[ModelMessage]:
        """按"用户轮次"裁剪历史，保留最近 N 轮对话。

        注意：确保工具调用链的完整性，不从中间切断 tool-call/tool-return 对。
        """
        if max_turns <= 0:
            return []

        if not messages:
            return []

        # 从后向前查找用户轮次
        user_turns = 0
        cut_idx = 0

        for idx in range(len(messages) - 1, -1, -1):
            message = messages[idx]
            if isinstance(message, ModelRequest):
                has_user_prompt = any(
                    getattr(part, "part_kind", None) == "user-prompt"
                    for part in message.parts
                )
                if has_user_prompt:
                    user_turns += 1
                    if user_turns == max_turns:
                        # 找到第 N 个用户轮次的起始位置
                        cut_idx = idx
                        break

        # 如果没有找到足够的轮次，保留全部
        if user_turns < max_turns:
            return list(messages)

        # 确保不从 tool-call/tool-return 链中间切断
        # 向前查找，确保包含完整的工具调用链
        while cut_idx > 0:
            prev_message = messages[cut_idx - 1]
            # 检查前一个消息是否包含未完成的工具调用
            if isinstance(prev_message, ModelResponse):
                has_tool_call = any(
                    getattr(part, "part_kind", None) == "tool-call"
                    for part in prev_message.parts
                )
                if has_tool_call:
                    # 需要包含这个响应，因为当前请求可能是对其的 tool-return
                    cut_idx -= 1
                    continue

            # 检查是否是系统提示词
            if isinstance(prev_message, ModelRequest):
                has_system_prompt = any(
                    getattr(part, "part_kind", None) == "system-prompt"
                    for part in prev_message.parts
                )
                if has_system_prompt:
                    cut_idx -= 1
                    continue

            break

        return list(messages[cut_idx:])

    @classmethod
    def _strip_reasoning_content(
        cls,
        messages: list[ModelMessage],
    ) -> tuple[list[ModelMessage], int]:
        """清空历史中的推理内容（ThinkingPart/content 与 reasoning_content），减少后续请求带宽。"""
        sanitized_messages: list[ModelMessage] = []
        cleared_count = 0

        for message in messages:
            sanitized_message, message_cleared = cls._sanitize_model_message(message)
            if sanitized_message is None:
                sanitized_message = copy.deepcopy(message)
                message_cleared = cls._clear_reasoning_content_in_object(sanitized_message)

            sanitized_messages.append(sanitized_message)
            cleared_count += message_cleared

        return sanitized_messages, cleared_count

    @classmethod
    def _sanitize_model_message(
        cls,
        message: ModelMessage,
    ) -> tuple[ModelMessage | None, int]:
        """优先走轻量路径处理标准 ModelMessage，避免整条历史深拷贝。"""
        if not isinstance(message, ModelResponse):
            return None, 0

        updated_parts: list[object] | None = None
        cleared_count = 0

        for index, part in enumerate(message.parts):
            updated_part = part

            if isinstance(part, ThinkingPart) and part.content:
                updated_part = dataclasses.replace(part, content="")
                cleared_count += 1

            if hasattr(updated_part, "reasoning_content"):
                reasoning_content = getattr(updated_part, "reasoning_content", None)
                if isinstance(reasoning_content, str) and reasoning_content:
                    if dataclasses.is_dataclass(updated_part):
                        updated_part = dataclasses.replace(updated_part, reasoning_content="")
                    else:
                        updated_part = copy.copy(updated_part)
                        setattr(updated_part, "reasoning_content", "")
                    cleared_count += 1

            if updated_part is not part:
                if updated_parts is None:
                    updated_parts = list(message.parts)
                updated_parts[index] = updated_part

        if updated_parts is None:
            return message, 0

        return dataclasses.replace(message, parts=updated_parts), cleared_count

    @classmethod
    def _clear_reasoning_content_in_object(
        cls,
        obj: object,
        visited: set[int] | None = None,
    ) -> int:
        """递归清空对象图中的 reasoning_content 字段。"""
        if visited is None:
            visited = set()

        obj_id = id(obj)
        if obj_id in visited:
            return 0
        visited.add(obj_id)

        cleared_count = 0

        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "reasoning_content" and isinstance(value, str) and value:
                    obj[key] = ""
                    cleared_count += 1
                else:
                    cleared_count += cls._clear_reasoning_content_in_object(value, visited)
            return cleared_count

        if isinstance(obj, list):
            for item in obj:
                cleared_count += cls._clear_reasoning_content_in_object(item, visited)
            return cleared_count

        if isinstance(obj, tuple):
            for item in obj:
                cleared_count += cls._clear_reasoning_content_in_object(item, visited)
            return cleared_count

        if hasattr(obj, "reasoning_content"):
            value = getattr(obj, "reasoning_content", None)
            if isinstance(value, str) and value:
                try:
                    setattr(obj, "reasoning_content", "")
                    cleared_count += 1
                except (AttributeError, TypeError, ValueError):
                    pass

        if hasattr(obj, "__dict__"):
            for value in vars(obj).values():
                cleared_count += cls._clear_reasoning_content_in_object(value, visited)

        return cleared_count

    def _create_send_callback(self, connection_id: UUID):
        """创建发送消息到游戏的回调"""

        async def send_to_game(message: str) -> None:
            # 发送到响应队列，由 WebSocket Handler 处理
            await self.broker.send_response(
                connection_id,
                {"type": "game_message", "content": message},
            )

        return send_to_game

    def _create_command_callback(self, connection_id: UUID):
        """创建执行命令的回调，返回结构化 CommandResult。"""

        async def run_command(command: str) -> CommandResult:
            # 发送到响应队列，由 WebSocket Handler 处理并等待命令结果
            loop = asyncio.get_running_loop()
            future: asyncio.Future[str] = loop.create_future()
            sent = await self.broker.send_response(
                connection_id,
                {
                    "type": "run_command",
                    "command": command,
                    "result_future": future,
                },
            )

            if not sent:
                return CommandResult.connection_unavailable(
                    "连接不存在",
                    diagnostic_summary="broker.send_response returned False",
                )

            try:
                raw = await asyncio.wait_for(future, timeout=self.settings.run_command_timeout)
            except asyncio.TimeoutError:
                return CommandResult.timeout_unknown(
                    "命令执行超时: 未收到游戏侧 commandResponse",
                    diagnostic_summary="asyncio.TimeoutError waiting for commandResponse",
                )
            except asyncio.CancelledError:
                raise

            text = str(raw) if raw is not None else ""
            if text.startswith("命令执行失败: 连接") or text.startswith("命令执行失败: WebSocket"):
                return CommandResult.connection_unavailable(
                    text,
                    diagnostic_summary=text,
                )
            if text.startswith("命令执行失败"):
                return CommandResult.failed(text, diagnostic_summary=text)
            return CommandResult.ok(text or "命令执行成功")

        return run_command

    def _create_addon_bridge_client(self, connection_id: UUID):
        """创建 addon 桥接客户端。

        Returns ``None`` when no ``AddonBridgeService`` was injected (unit tests
        or hosts without addon tooling). Runtime ``cli.py serve`` always injects
        the shared service from ``HostGatewayServer``.

        Addon 层仍消费字符串结果；从 CommandResult 映射。发送侧失败（帧过大等）
        必须抛出，避免 SDK 侧空等 bridge timeout 并被误映射为 STATE_UNKNOWN。
        """
        if self._addon is None:
            return None

        async def send_command_for_addon(command: str) -> str:
            result = await self._create_command_callback(connection_id)(command)
            if result.is_success:
                return result.output

            diagnostic = result.diagnostic_summary or result.output or result.status
            # Pre-mutation host failures: raise so map_bridge_exception can classify
            # as LIMIT_EXCEEDED instead of waiting for a bridge response that never
            # comes (and then reporting STATE_UNKNOWN).
            text = result.output or ""
            if result.status == "failed" and (
                "raw command too long" in text
                or "FrameTooLarge" in text
                or ("commandLine" in text and "too long" in text)
                or "bridge request never left host" in text
            ):
                raise RuntimeError(
                    f"bridge request never left host: {diagnostic}"
                ) from None
            if result.status == "connection_unavailable":
                raise ConnectionError(
                    f"bridge outbound failed before send: {diagnostic}"
                ) from None
            if result.status == "timeout_unknown":
                # WS commandResponse timeout is distinct from bridge RESP timeout;
                # still unknown because the game may have accepted the scriptevent.
                return f"命令执行超时: {result.output}"
            return f"命令执行失败: {result.output}"

        return self._addon.create_client(
            connection_id=connection_id,
            send_command=send_command_for_addon,
        )

    @staticmethod
    def _chunk_correlation(request: ChatRequest) -> dict[str, str | None]:
        """从 ChatRequest 提取 StreamChunk correlation（不含正文）。"""
        return {
            "trace_id": request.trace_id or request.run_id,
            "attempt_id": request.attempt_id,
            "conversation_id": request.conversation_id,
        }

    async def _send_error_chunk(
        self,
        connection_id: UUID,
        player_name: str | None,
        error: str,
        sequence: int,
        target: str | None = None,
        *,
        error_kind: str | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        attempt_id: str | None = None,
    ) -> None:
        """发送错误消息块（玩家可见文案，不含堆栈）。"""
        chunk = StreamChunk(
            connection_id=connection_id,
            chunk_type="error",
            content=error if error.startswith("错误") else f"错误: {error}",
            sequence=sequence,
            player_name=player_name,
            target=target,
            trace_id=trace_id or run_id,
            attempt_id=attempt_id,
        )
        await self.broker.send_response(connection_id, chunk)

    def _coerce_deferred_tool_results(self, payload: dict | DeferredToolResults) -> DeferredToolResults:
        if isinstance(payload, DeferredToolResults):
            return payload
        results = DeferredToolResults()
        approvals = payload.get("approvals") if isinstance(payload, dict) else None
        calls = payload.get("calls") if isinstance(payload, dict) else None
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        if isinstance(approvals, dict):
            for tool_call_id, value in approvals.items():
                if value is True:
                    results.approvals[tool_call_id] = True
                elif value is False:
                    results.approvals[tool_call_id] = False
                elif isinstance(value, dict) and value.get("kind") == "tool-denied":
                    results.approvals[tool_call_id] = ToolDenied(
                        message=str(value.get("message") or "已拒绝")
                    )
                elif isinstance(value, dict) and value.get("kind") == "tool-approved":
                    plan_id = value.get("plan_id")
                    override_args = value.get("override_args")
                    if isinstance(plan_id, str) and plan_id.strip():
                        # 恢复契约：只携带 plan_id；验证与执行都从预检缓存还原
                        results.approvals[tool_call_id] = ToolApproved(
                            override_args={"plan_id": plan_id.strip()}
                        )
                    elif isinstance(override_args, dict):
                        results.approvals[tool_call_id] = ToolApproved(override_args=override_args)
                    else:
                        raise ValueError("tool-approved requires plan_id or override_args")
                else:
                    results.approvals[tool_call_id] = value
        if isinstance(calls, dict):
            results.calls.update(calls)
        if isinstance(metadata, dict):
            results.metadata.update(metadata)
        return results

    async def _handle_approval_required(
        self,
        *,
        request: ChatRequest,
        connection_id: UUID,
        event,
        sequence: int,
        stream_target: str | None,
        trace_context: TraceContext | None = None,
    ) -> None:
        """把 DeferredToolRequests 落盘到 PendingApprovalStore，并提示玩家审批。"""
        import time as _time

        metadata = event.metadata or {}
        deferred = metadata.get("deferred_requests")
        if not isinstance(deferred, DeferredToolRequests):
            await self._send_error_chunk(
                connection_id,
                request.player_name,
                "内部错误：缺少待审批工具调用",
                sequence,
                target=stream_target,
                error_kind="INTERNAL",
                run_id=request.run_id,
                trace_id=request.trace_id or request.run_id,
                attempt_id=request.attempt_id,
            )
            return

        messages = metadata.get("all_messages") or []
        store = get_agent_runtime().get_pending_approval_store(self.settings)
        ttl = float(getattr(self.settings, "approval_ttl", 120.0) or 120.0)
        now = _time.time()
        player_name = request.player_name or DEFAULT_PLAYER_DISPLAY_NAME
        approval_ids: list[str] = []

        pending_calls = list(deferred.approvals) + list(deferred.calls)
        if not pending_calls:
            await self._send_error_chunk(
                connection_id,
                request.player_name,
                "没有需要审批的工具调用",
                sequence,
                target=stream_target,
                error_kind="INTERNAL",
                run_id=request.run_id,
                trace_id=request.trace_id or request.run_id,
                attempt_id=request.attempt_id,
            )
            return

        # 同一 DeferredToolRequests 作为一批：须全部决策后才 resume
        batch_id = store.generate_batch_id()
        preassigned_ids = [store.generate_approval_id() for _ in pending_calls]
        sibling_ids = list(preassigned_ids)

        # Fail closed before any store write: block tools need plan_id (recovery contract).
        for call in pending_calls:
            if call.tool_name not in {"inspect_block", "place_block", "fill_block"}:
                continue
            meta = deferred.metadata.get(call.tool_call_id, {}) if deferred.metadata else {}
            plan_id = meta.get("plan_id") if isinstance(meta, dict) else None
            if not isinstance(plan_id, str) or not plan_id.strip():
                logger.error(
                    "block_approval_missing_plan_id",
                    tool_name=call.tool_name,
                    tool_call_id=call.tool_call_id,
                    run_id=request.run_id,
                    player_name=player_name,
                )
                await self._send_error_chunk(
                    connection_id,
                    request.player_name,
                    "方块工具审批参数契约无效，未进入审批队列",
                    sequence,
                    target=stream_target,
                    error_kind="INTERNAL",
                    run_id=request.run_id,
                    trace_id=request.trace_id or request.run_id,
                    attempt_id=request.attempt_id,
                )
                return

        for approval_id, call in zip(preassigned_ids, pending_calls):
            meta = deferred.metadata.get(call.tool_call_id, {}) if deferred.metadata else {}
            normalized_args = meta.get("normalized_args")
            if not isinstance(normalized_args, dict):
                args = call.args if isinstance(call.args, dict) else {}
                normalized_args = args
            args_hash = str(meta.get("args_hash") or "")
            execute_args = meta.get("execute_args")
            if call.tool_name not in {"inspect_block", "place_block", "fill_block"} and not isinstance(
                execute_args, dict
            ):
                execute_args = normalized_args
            execution_args_hash = str(meta.get("execution_args_hash") or "")
            args_summary = str(
                meta.get("args_summary")
                or summarize_args_for_player(call.tool_name, normalized_args)
            )
            policy_version = str(meta.get("policy_version") or getattr(self.settings, "tool_policy_version", "unknown"))
            pending = PendingApproval(
                approval_id=approval_id,
                connection_id=str(connection_id),
                player_name=player_name,
                conversation_id=request.conversation_id,
                run_id=request.run_id or str(metadata.get("run_id") or ""),
                tool_call_id=call.tool_call_id,
                expected_tool_call_id=call.tool_call_id,
                tool_name=call.tool_name,
                normalized_args=normalized_args,
                execute_args=execute_args,
                args_summary=args_summary,
                args_hash=args_hash,
                execution_args_hash=execution_args_hash,
                plan_id=str(meta.get("plan_id") or "") if isinstance(meta, dict) else "",
                policy_version=policy_version,
                messages=list(messages),
                requests=deferred,
                provider=request.provider,
                delivery=request.delivery,
                use_context=request.use_context,
                broadcast_ai_chat=request.broadcast_ai_chat,
                created_at=now,
                expires_at=now + ttl,
                batch_id=batch_id,
                sibling_approval_ids=sibling_ids,
                metadata={
                    "risk": meta.get("risk"),
                    "reason": meta.get("reason"),
                    "trace_id": (trace_context.trace_id if trace_context else request.trace_id),
                    "attempt_id": (trace_context.attempt_id if trace_context else request.attempt_id),
                    "message_id": (trace_context.message_id if trace_context else str(request.id)),
                    "approval_metadata": meta.get("approval_metadata", {}),
                },
            )
            store.put(pending)
            approval_ids.append(approval_id)

            if trace_context is not None:
                # tool.proposed is already emitted by harness call_tool; worker
                # only writes approval.requested (+ related attributes).
                try:
                    recorder = get_trace_recorder(self.settings)
                    approval_attrs: dict = {
                        "approval_id": approval_id,
                        "batch_id": batch_id,
                        "tool_name": call.tool_name,
                        "policy_version": policy_version,
                        "ttl_seconds": ttl,
                    }
                    # Free-text args_summary only when content mode is on
                    if getattr(recorder, "include_content", False):
                        approval_attrs["args_summary"] = args_summary
                    recorder.emit(
                        "approval.requested",
                        trace_context,
                        status="info",
                        tool_call_id=call.tool_call_id,
                        attributes=approval_attrs,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("trace_approval_requested_failed", error=str(exc))

            batch_hint = ""
            if len(sibling_ids) > 1:
                batch_hint = (
                    f"\n批次: {batch_id}（共 {len(sibling_ids)} 项，"
                    f"需全部决策后才会继续执行）"
                )
            prompt = (
                f"工具审批请求 [{approval_id}]\n"
                f"工具: {call.tool_name}\n"
                f"参数: {args_summary}\n"
                f"原因: {meta.get('reason') or '需要确认'}"
                f"{batch_hint}\n"
                f"请执行: AGENT 同意  或  AGENT 拒绝"
                f"（也可指定 id: AGENT 同意 {approval_id}；"
                f"AGENT 同意 对话 / AGENT 同意 永远 可跳过后续审批）"
            )
            chunk = StreamChunk(
                connection_id=connection_id,
                chunk_type="approval_required",
                content=prompt,
                sequence=sequence,
                delivery=request.delivery,
                player_name=request.player_name,
                target=stream_target or request.player_name,
                tool_name=call.tool_name,
                **self._chunk_correlation(request),
            )
            await self.broker.send_response(connection_id, chunk)
            sequence += 1

        logger.info(
            "tool_approval_pending",
            worker_id=self.worker_id,
            connection_id=str(connection_id),
            player=player_name,
            conversation_id=request.conversation_id,
            approval_ids=approval_ids,
            batch_id=batch_id,
            run_id=request.run_id,
        )
