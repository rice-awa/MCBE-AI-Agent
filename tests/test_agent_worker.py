"""AgentWorker 超时、queue 结束语义与预算契约单元测试。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from models.messages import ChatRequest
from services.agent.tool_results import CommandResult
from services.agent.worker import AgentWorker


def _make_settings(
    worker_http_timeout: int = 30,
    worker_poll_timeout: float = 2.0,
    run_command_timeout: float = 5.0,
    request_limit: int = 8,
    tool_calls_limit: int = 8,
    run_timeout: float = 90.0,
) -> MagicMock:
    s = MagicMock()
    s.worker_http_timeout = worker_http_timeout
    s.worker_poll_timeout = worker_poll_timeout
    s.run_command_timeout = run_command_timeout
    s.request_limit = request_limit
    s.tool_calls_limit = tool_calls_limit
    s.run_timeout = run_timeout
    s.default_provider = "ollama"
    s.enable_reasoning_output = False
    s.tool_response_verbose = False
    s.max_history_turns = 20
    return s


def test_worker_stores_http_timeout_settings():
    """Worker 初始化时应保留 settings 中的超时配置。"""
    settings = _make_settings(worker_http_timeout=30)
    broker = MagicMock()
    worker = AgentWorker(broker, settings)
    assert worker.settings.worker_http_timeout == 30
    assert worker.settings.worker_poll_timeout == 2.0
    assert worker.settings.run_command_timeout == 5.0


def test_worker_coerces_block_tool_approval_override_args() -> None:
    """Gateway's block approval payload must become PydanticAI's concrete override type."""
    from pydantic_ai.tools import ToolApproved

    worker = AgentWorker(MagicMock(), _make_settings())
    execute_args = {
        "type_id": "minecraft:stone",
        "mode": "fill",
        "coordinate_mode": "absolute",
        "dimension": "minecraft:overworld",
        "from_pos": {"x": 2, "y": 64, "z": 1},
        "to_pos": {"x": 4, "y": 64, "z": 3},
        "locked_targets": [{"x": 2, "y": 64, "z": 1}],
        "phase": "execute",
    }

    results = worker._coerce_deferred_tool_results(
        {"approvals": {"tc-fill": {"kind": "tool-approved", "override_args": execute_args}}}
    )

    approved = results.approvals["tc-fill"]
    assert isinstance(approved, ToolApproved)
    assert approved.override_args == execute_args


def test_worker_coerces_block_plan_id_approval_to_plan_id_override() -> None:
    """plan_id 恢复负载只产生 {plan_id} override，绝无 status/phase 注入。"""
    from pydantic_ai.tools import ToolApproved

    worker = AgentWorker(MagicMock(), _make_settings())
    results = worker._coerce_deferred_tool_results(
        {"approvals": {"tc-place": {"kind": "tool-approved", "plan_id": "pid-1"}}}
    )

    approved = results.approvals["tc-place"]
    assert isinstance(approved, ToolApproved)
    assert approved.override_args == {"plan_id": "pid-1"}


@pytest.mark.asyncio
async def test_worker_rejects_block_approval_without_plan_id() -> None:
    """方块工具审批 metadata 必须携带 plan_id；缺失则 fail-closed，不得进入审批队列。"""
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import DeferredToolRequests

    from services.agent.harness.approvals import PendingApprovalStore

    broker = MagicMock()
    broker.send_response = AsyncMock()
    worker = AgentWorker(broker, _make_settings())
    store = PendingApprovalStore(default_ttl_seconds=120.0)
    connection_id = uuid4()
    request = ChatRequest(
        id=uuid4(),
        connection_id=connection_id,
        player_name="Steve",
        conversation_id="conv-1",
        content="edit",
        run_id="run-missing-plan",
    )
    deferred = DeferredToolRequests(
        approvals=[
            ToolCallPart(
                tool_name="place_block",
                tool_call_id="tc-place",
                args={"pos": [1, 64, 1], "block": "stone", "expect": "air"},
            )
        ],
        metadata={
            "tc-place": {
                "normalized_args": {"pos": [1, 64, 1], "block": "stone", "expect": "air"},
                # deliberately omit plan_id
                "args_hash": "h",
                "args_summary": "place stone",
            }
        },
    )
    event = SimpleNamespace(
        metadata={
            "deferred_requests": deferred,
            "all_messages": [],
            "run_id": "run-missing-exec",
        }
    )

    with patch(
        "services.agent.worker.get_agent_runtime",
        return_value=SimpleNamespace(get_pending_approval_store=lambda _settings: store),
    ):
        await worker._handle_approval_required(
            request=request,
            connection_id=connection_id,
            event=event,
            sequence=1,
            stream_target="Steve",
        )

    assert len(store) == 0
    broker.send_response.assert_awaited()
    chunk = broker.send_response.await_args.args[1]
    assert chunk.chunk_type == "error"
    assert "契约无效" in chunk.content


@pytest.mark.asyncio
async def test_start_passes_http_timeout_to_httpx_client():
    """start() 应将 settings.worker_http_timeout 传给 httpx.AsyncClient。"""
    settings = _make_settings(worker_http_timeout=42)
    broker = MagicMock()
    worker = AgentWorker(broker, settings)

    with patch("services.agent.worker.httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.aclose = AsyncMock()
        await worker.start()

    mock_client_cls.assert_called_once_with(timeout=42)

    await worker.stop()


@pytest.mark.asyncio
async def test_run_uses_poll_timeout_from_settings():
    """_run() 应使用 settings.worker_poll_timeout 作为 wait_for 超时。"""
    settings = _make_settings(worker_poll_timeout=0.123)
    broker = MagicMock()
    broker.get_request = AsyncMock()
    worker = AgentWorker(broker, settings)

    captured: dict[str, float] = {}

    async def spy_wait_for(aw, timeout):
        captured["value"] = timeout
        # 捕获后立即停止循环，避免空转
        worker._running = False
        raise asyncio.TimeoutError()

    worker._running = True

    with patch("services.agent.worker.asyncio.wait_for", side_effect=spy_wait_for):
        await worker._run()

    assert captured["value"] == 0.123
    broker.request_done.assert_not_called()


@pytest.mark.asyncio
async def test_run_command_callback_uses_timeout_from_settings():
    """run_command 回调应使用 settings.run_command_timeout 作为 wait_for 超时。"""
    settings = _make_settings(run_command_timeout=7.5)
    broker = MagicMock()
    broker.send_response = AsyncMock(return_value=True)
    worker = AgentWorker(broker, settings)

    captured: dict[str, float] = {}

    async def spy_wait_for(future, timeout):
        captured["value"] = timeout
        return "ok"

    run_command = worker._create_command_callback(UUID(int=0))

    with patch("services.agent.worker.asyncio.wait_for", side_effect=spy_wait_for):
        result = await run_command("say hi")

    assert isinstance(result, CommandResult)
    assert result.is_success
    assert result.output == "ok"
    assert captured["value"] == 7.5


@pytest.mark.asyncio
async def test_run_command_callback_connection_unavailable():
    settings = _make_settings()
    broker = MagicMock()
    broker.send_response = AsyncMock(return_value=False)
    worker = AgentWorker(broker, settings)

    result = await worker._create_command_callback(UUID(int=0))("say hi")
    assert result.status == "connection_unavailable"
    assert not result.is_success


@pytest.mark.asyncio
async def test_run_command_callback_timeout_unknown():
    settings = _make_settings(run_command_timeout=0.01)
    broker = MagicMock()
    broker.send_response = AsyncMock(return_value=True)
    worker = AgentWorker(broker, settings)

    async def never_complete(future, timeout):
        raise asyncio.TimeoutError()

    with patch("services.agent.worker.asyncio.wait_for", side_effect=never_complete):
        result = await worker._create_command_callback(UUID(int=0))("say hi")

    assert result.status == "timeout_unknown"
    assert result.external_state_unknown is True


def test_settings_defaults_and_constraints():
    """Settings 字段应有正确默认值与约束。"""
    from config.settings import Settings

    # Settings() 会读本地 config.json；对可能被配置覆盖的字段显式固定期望值
    s = Settings(
        worker_http_timeout=60,
        worker_poll_timeout=1.0,
        run_command_timeout=10.0,
        request_limit=8,
        tool_calls_limit=8,
        run_timeout=90.0,
        max_tool_concurrency=4,
    )
    assert s.worker_http_timeout == 60
    assert s.worker_poll_timeout == 1.0
    assert s.run_command_timeout == 10.0
    assert s.request_limit == 8
    assert s.tool_calls_limit == 8
    assert s.run_timeout == 90.0
    assert s.max_tool_concurrency == 4

    with pytest.raises(Exception):
        Settings(worker_http_timeout=0)
    with pytest.raises(Exception):
        Settings(worker_poll_timeout=0)
    with pytest.raises(Exception):
        Settings(run_command_timeout=0)


class _QueueItem:
    def __init__(self, connection_id, payload):
        self.connection_id = connection_id
        self.payload = payload


@pytest.mark.asyncio
async def test_request_done_called_once_on_process_exception():
    """处理异常时 request_done() 仍恰好执行一次。"""
    settings = _make_settings()
    broker = MagicMock()
    connection_id = uuid4()
    request = ChatRequest(connection_id=connection_id, content="hi", player_name="Alex")
    item = _QueueItem(connection_id, request)

    call_count = {"n": 0}

    async def get_request_once():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return item
        # 第二次起让循环退出
        worker._running = False
        raise asyncio.TimeoutError()

    broker.get_request = AsyncMock(side_effect=get_request_once)
    broker.request_done = MagicMock()
    worker = AgentWorker(broker, settings)
    worker._running = True

    async def boom(_item):
        raise RuntimeError("process failed")

    with patch.object(worker, "_process_request", side_effect=boom):
        await worker._run()

    assert broker.request_done.call_count == 1


@pytest.mark.asyncio
async def test_request_done_called_once_on_cancel():
    """取消路径上 request_done() 恰好执行一次。"""
    settings = _make_settings()
    broker = MagicMock()
    connection_id = uuid4()
    request = ChatRequest(connection_id=connection_id, content="hi", player_name="Alex")
    item = _QueueItem(connection_id, request)

    got_item = asyncio.Event()

    async def get_request():
        got_item.set()
        return item

    broker.get_request = AsyncMock(side_effect=get_request)
    broker.request_done = MagicMock()
    worker = AgentWorker(broker, settings)
    worker._running = True

    async def hang(_item):
        # 模拟处理中被取消
        await asyncio.sleep(3600)

    with patch.object(worker, "_process_request", side_effect=hang):
        task = asyncio.create_task(worker._run())
        await got_item.wait()
        await asyncio.sleep(0)  # 让 _process_request 启动
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert broker.request_done.call_count == 1


@pytest.mark.asyncio
async def test_usage_limit_exceeded_does_not_continue_tools(monkeypatch):
    """预算超限时完成/错误事件不包含继续工具调用。"""
    from models.agent import StreamEvent
    from pydantic_ai.exceptions import UsageLimitExceeded

    settings = _make_settings(request_limit=1, tool_calls_limit=1)
    broker = MagicMock()
    broker.get_session_lock = MagicMock(return_value=asyncio.Lock())
    broker.get_conversation_history = MagicMock(return_value=[])
    broker.send_response = AsyncMock(return_value=True)
    broker.get_response_queue = MagicMock(return_value=object())
    worker = AgentWorker(broker, settings)

    tool_executed = {"n": 0}

    async def fake_stream_chat(*_args, **_kwargs):
        # 预算超限：直接 yield 错误，不产生 tool_call
        yield StreamEvent(
            event_type="error",
            content="已达到本轮请求预算上限，请缩短问题或稍后再试。",
            sequence=0,
            metadata={
                "error_kind": "DENIED",
                "diagnostic_summary": "UsageLimitExceeded",
                "run_id": "run-1",
            },
        )
        tool_executed["n"] += 0  # 明确未执行工具

    monkeypatch.setattr("services.agent.worker.stream_chat", fake_stream_chat)
    monkeypatch.setattr(
        "services.agent.providers.ProviderRegistry.get_model",
        lambda _config: object(),
    )
    monkeypatch.setattr("services.agent.mcp.get_mcp_manager", lambda _s: None)

    connection_id = uuid4()
    await worker._process_request_locked(
        ChatRequest(
            connection_id=connection_id,
            content="trigger budget",
            player_name="Alex",
            run_id="run-1",
        ),
        connection_id,
    )

    assert tool_executed["n"] == 0
    # 应向玩家发送错误 chunk
    assert broker.send_response.await_count >= 1
    sent = broker.send_response.await_args_list[-1].args[1]
    assert getattr(sent, "chunk_type", None) == "error"
    assert "预算" in sent.content or "错误" in sent.content or "上限" in sent.content


@pytest.mark.asyncio
async def test_error_event_persists_partial_run_history(monkeypatch):
    """mid-run 失败时应落盘已产生的 all_messages + 错误说明，供下轮 LLM 使用。"""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    from models.agent import StreamEvent

    settings = _make_settings()
    settings.compression_enabled = False
    broker = MagicMock()
    broker.get_session_lock = MagicMock(return_value=asyncio.Lock())
    broker.get_conversation_history = MagicMock(return_value=[])
    broker.set_conversation_history = MagicMock(return_value=True)
    broker.send_response = AsyncMock(return_value=True)
    broker.get_response_queue = MagicMock(return_value=object())
    broker.mark_conversation_title_generating = MagicMock(return_value=False)
    worker = AgentWorker(broker, settings)

    partial_messages = [
        ModelRequest(parts=[UserPromptPart(content="在这里放火把")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="edit_blocks",
                    args={"edits": [{"target": {"positions": [{"x": 1, "y": 64, "z": 1}]}, "block": "minecraft:torch"}]},
                    tool_call_id="tc-1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="edit_blocks",
                    content='{"ok": true}',
                    tool_call_id="tc-1",
                )
            ]
        ),
    ]

    async def fake_stream_chat(*_args, **_kwargs):
        yield StreamEvent(
            event_type="tool_call",
            content="edit_blocks",
            sequence=0,
            metadata={
                "tool_name": "edit_blocks",
                "tool_call_id": "tc-1",
                "args": {"edits": [{"target": {"positions": [{"x": 1, "y": 64, "z": 1}]}, "block": "minecraft:torch"}]},
            },
        )
        yield StreamEvent(
            event_type="tool_result",
            content='{"ok": true}',
            sequence=1,
            metadata={"tool_name": "edit_blocks", "tool_call_id": "tc-1"},
        )
        yield StreamEvent(
            event_type="error",
            content=(
                "本轮累计输入 token 已达配置上限（多步工具会把每次请求的输入相加），"
                "请缩短问题、清空或压缩上下文，或拆成更小的步骤后重试。"
            ),
            sequence=2,
            metadata={
                "error_kind": "DENIED",
                "diagnostic_summary": (
                    "Exceeded the input_tokens_limit of 126976 (input_tokens=128035)"
                ),
                "all_messages": partial_messages,
                "new_messages": partial_messages,
                "new_messages_serialized": [
                    {"kind": "request", "parts": []},
                    {"kind": "response", "parts": []},
                ],
                "salvage_partial_run": True,
                "run_id": "run-partial",
            },
        )

    monkeypatch.setattr("services.agent.worker.stream_chat", fake_stream_chat)
    monkeypatch.setattr(
        "services.agent.providers.ProviderRegistry.get_model",
        lambda _config: object(),
    )
    monkeypatch.setattr("services.agent.mcp.get_mcp_manager", lambda _s: None)

    connection_id = uuid4()
    await worker._process_request_locked(
        ChatRequest(
            connection_id=connection_id,
            content="在这里放火把",
            player_name="Alex",
            run_id="run-partial",
            use_context=True,
        ),
        connection_id,
    )

    assert broker.set_conversation_history.called
    args = broker.set_conversation_history.call_args
    saved_history = args.args[2]
    assert len(saved_history) >= len(partial_messages)
    # 末尾应有中断说明，供下轮 LLM 看见
    last = saved_history[-1]
    assert isinstance(last, ModelResponse)
    last_text = "".join(
        str(getattr(p, "content", "") or "")
        for p in last.parts
        if getattr(p, "part_kind", None) == "text"
    )
    assert "中断" in last_text or "上限" in last_text

    # 玩家仍收到 error chunk
    error_chunks = [
        c.args[1]
        for c in broker.send_response.await_args_list
        if getattr(c.args[1], "chunk_type", None) == "error"
    ]
    assert error_chunks
    assert "上限" in error_chunks[-1].content or "累计" in error_chunks[-1].content


@pytest.mark.asyncio
async def test_approval_resume_keeps_only_deferred_pending_tool_call(monkeypatch):
    """审批恢复只保留 deferred results 明确引用的无响应 pending call。"""
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
    from pydantic_ai.tools import DeferredToolResults

    from models.agent import StreamEvent

    settings = _make_settings()
    broker = MagicMock()
    broker.get_session_lock = MagicMock(return_value=asyncio.Lock())
    broker.get_response_queue = MagicMock(return_value=object())
    broker.send_response = AsyncMock(return_value=True)
    worker = AgentWorker(broker, settings)

    pending_call = ToolCallPart(
        tool_name="run_minecraft_command",
        args={"command": "time set day"},
        tool_call_id="tc-pending",
    )
    unrelated_orphan = ToolCallPart(
        tool_name="run_minecraft_command",
        args={"command": "say orphan"},
        tool_call_id="tc-orphan",
    )
    resume_history = [
        ModelRequest(parts=[UserPromptPart("set day")]),
        ModelResponse(parts=[pending_call, unrelated_orphan]),
    ]
    captured: dict[str, object] = {}

    async def fake_stream_chat(*args, **kwargs):
        captured["message_history"] = kwargs.get("message_history")
        captured["deferred_tool_results"] = kwargs.get("deferred_tool_results")
        yield StreamEvent(event_type="content", content="已继续", sequence=0)

    monkeypatch.setattr("services.agent.worker.stream_chat", fake_stream_chat)
    monkeypatch.setattr(
        "services.agent.providers.ProviderRegistry.get_model",
        lambda _config: object(),
    )
    monkeypatch.setattr("services.agent.mcp.get_mcp_manager", lambda _settings: None)

    connection_id = uuid4()
    await worker._process_request_locked(
        ChatRequest(
            connection_id=connection_id,
            content="同意",
            player_name="Alex",
            run_id="run-resume-pending",
            resume_approval_id="approval-1",
            deferred_tool_results={"approvals": {"tc-pending": True}},
            resume_message_history=resume_history,
        ),
        connection_id,
    )

    message_history = captured["message_history"]
    assert isinstance(message_history, list)
    kept_call_ids = [
        part.tool_call_id
        for message in message_history
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ]
    assert kept_call_ids == ["tc-pending"]
    assert isinstance(captured["deferred_tool_results"], DeferredToolResults)
    assert captured["deferred_tool_results"].approvals == {"tc-pending": True}


@pytest.mark.asyncio
async def test_approval_resume_uses_final_duplicate_id_or_fails_closed_if_ambiguous(
    monkeypatch,
):
    """重复 deferred ID 必须绑定框架最终可恢复 response，而非更早参数。"""
    from pydantic_ai import Agent
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        UserPromptPart,
    )
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.tools import DeferredToolRequests

    from models.agent import StreamEvent

    settings = _make_settings()
    broker = MagicMock()
    broker.get_session_lock = MagicMock(return_value=asyncio.Lock())
    broker.get_response_queue = MagicMock(return_value=object())
    broker.send_response = AsyncMock(return_value=True)
    worker = AgentWorker(broker, settings)

    executed_commands: list[str] = []
    resume_agent: Agent[None, str | DeferredToolRequests] = Agent(
        "test",
        output_type=[str, DeferredToolRequests],
    )

    @resume_agent.tool_plain(requires_approval=True)
    def run_minecraft_command(command: str) -> str:
        executed_commands.append(command)
        return f"executed:{command}"

    resume_history = [
        ModelRequest(parts=[UserPromptPart("run a command")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="run_minecraft_command",
                    args={"command": "say stale"},
                    tool_call_id="tc-duplicate",
                )
            ]
        ),
        ModelRequest(parts=[UserPromptPart("use the corrected command")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="run_minecraft_command",
                    args={"command": "time set day"},
                    tool_call_id="tc-duplicate",
                )
            ]
        ),
    ]

    def finish_after_tool(_messages, _info):
        return ModelResponse(parts=[TextPart("done")])

    async def resume_with_real_agent(*args, **kwargs):
        await resume_agent.run(
            message_history=kwargs.get("message_history"),
            deferred_tool_results=kwargs.get("deferred_tool_results"),
            model=FunctionModel(finish_after_tool),
        )
        yield StreamEvent(event_type="content", content="已继续", sequence=0)

    monkeypatch.setattr("services.agent.worker.stream_chat", resume_with_real_agent)
    monkeypatch.setattr(
        "services.agent.providers.ProviderRegistry.get_model",
        lambda _config: object(),
    )
    monkeypatch.setattr("services.agent.mcp.get_mcp_manager", lambda _settings: None)

    connection_id = uuid4()
    await worker._process_request_locked(
        ChatRequest(
            connection_id=connection_id,
            content="同意",
            player_name="Alex",
            run_id="run-resume-duplicate",
            resume_approval_id="approval-duplicate",
            deferred_tool_results={"approvals": {"tc-duplicate": True}},
            resume_message_history=resume_history,
        ),
        connection_id,
    )

    assert executed_commands == ["time set day"]

    executed_commands.clear()
    response_count = broker.send_response.await_count
    ambiguous_history = [
        ModelRequest(parts=[UserPromptPart("run a command")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="run_minecraft_command",
                    args={"command": "say stale"},
                    tool_call_id="tc-ambiguous",
                ),
                ToolCallPart(
                    tool_name="run_minecraft_command",
                    args={"command": "time set day"},
                    tool_call_id="tc-ambiguous",
                ),
            ]
        ),
    ]
    await worker._process_request_locked(
        ChatRequest(
            connection_id=connection_id,
            content="同意",
            player_name="Alex",
            run_id="run-resume-ambiguous",
            resume_approval_id="approval-ambiguous",
            deferred_tool_results={"approvals": {"tc-ambiguous": True}},
            resume_message_history=ambiguous_history,
        ),
        connection_id,
    )

    assert executed_commands == []
    new_responses = [
        call.args[1]
        for call in broker.send_response.await_args_list[response_count:]
    ]
    assert any(
        getattr(response, "chunk_type", None) == "error"
        for response in new_responses
    )


@pytest.mark.asyncio
async def test_worker_audits_validation_retry_and_later_success_without_execution_start(
    monkeypatch, tmp_path
):
    """参数校验失败只审计 validation；修正后的调用仍可产生一条成功审计。"""
    import json
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RetryPromptPart,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
    )

    from models.agent import StreamEvent
    from services.agent.harness.audit import (
        AuditWriter,
        flush_audit_writer,
        set_audit_writer,
        start_audit_writer,
        stop_audit_writer,
        wrap_tool_function,
    )
    from services.agent.tool_results import ToolResult

    settings = _make_settings()
    audit_path = tmp_path / "validation-tools.jsonl"
    settings.runtime_harness_enabled = True
    settings.runtime_harness_audit_enabled = True
    settings.runtime_harness_audit_path = str(audit_path)
    settings.runtime_harness_audit_max_records = 100
    settings.agent_trace_enabled = False
    settings.compression_enabled = False

    broker = MagicMock()
    broker.get_session_lock = MagicMock(return_value=asyncio.Lock())
    broker.get_conversation_history = MagicMock(return_value=[])
    broker.set_conversation_history = MagicMock(return_value=True)
    broker.send_response = AsyncMock(return_value=True)
    broker.get_response_queue = MagicMock(return_value=object())
    broker.mark_conversation_title_generating = MagicMock(return_value=False)
    worker = AgentWorker(broker, settings)

    bad_args = {
        "edits": [{
            "target": {"positions": [{"x": 1, "y": 64, "z": 1}]},
            "block": "minecraft:stone",
        }],
        "dimension": "minecraft:overworld",
        "api_key": "do-not-record",
    }
    good_args = {
        "edits": [{
            "target": {"positions": [{"x": 1, "y": 64, "z": 1}]},
            "block": "minecraft:stone",
        }],
        "dimension": "minecraft:overworld",
    }
    retry_at = datetime(2026, 8, 2, 15, 0, 0, tzinfo=UTC)
    retry = RetryPromptPart(
        [{
            "type": "json_invalid",
            "loc": (),
            "msg": "Invalid JSON: validation-secret",
            "input": "validation-secret",
        }],
        tool_name="edit_blocks",
        tool_call_id="tc-invalid",
        timestamp=retry_at,
    )
    messages = [
        ModelResponse(parts=[ToolCallPart(
            tool_name="edit_blocks",
            args=bad_args,
            tool_call_id="tc-invalid",
        )]),
        ModelRequest(parts=[retry]),
        ModelResponse(parts=[ToolCallPart(
            tool_name="edit_blocks",
            args=good_args,
            tool_call_id="tc-corrected",
        )]),
        ModelRequest(parts=[ToolReturnPart(
            tool_name="edit_blocks",
            content="ok",
            tool_call_id="tc-corrected",
        )]),
        ModelResponse(parts=[TextPart(content="修正成功")]),
    ]

    async def corrected_tool(ctx, edits, dimension):  # noqa: ARG001
        return ToolResult.success("ok")

    writer = AuditWriter()
    set_audit_writer(writer)
    start_audit_writer()
    try:
        corrected = wrap_tool_function("edit_blocks", corrected_tool, settings)

        async def fake_stream_chat(_prompt, deps, *_args, **_kwargs):
            await corrected(
                SimpleNamespace(deps=deps, tool_call_id="tc-corrected"),
                **good_args,
            )
            yield StreamEvent(
                event_type="tool_call",
                content="edit_blocks",
                sequence=0,
                metadata={
                    "tool_name": "edit_blocks",
                    "tool_call_id": "tc-invalid",
                    "args": bad_args,
                },
            )
            yield StreamEvent(
                event_type="content",
                content="修正成功",
                sequence=1,
                metadata=None,
            )
            yield StreamEvent(
                event_type="content",
                content="",
                sequence=2,
                metadata={
                    "is_complete": True,
                    "all_messages": messages,
                    "new_messages": messages,
                    "usage": None,
                    "tool_events": [],
                },
            )

        class _NoCompression:
            async def check_and_compress(self, *_args, **_kwargs):
                return False, "disabled"

        monkeypatch.setattr("services.agent.worker.stream_chat", fake_stream_chat)
        monkeypatch.setattr(
            "services.agent.providers.ProviderRegistry.get_model",
            lambda _config: object(),
        )
        monkeypatch.setattr("services.agent.mcp.get_mcp_manager", lambda _s: None)
        monkeypatch.setattr(
            "core.conversation.get_conversation_manager",
            lambda *_args, **_kwargs: _NoCompression(),
        )

        connection_id = uuid4()
        await worker._process_request_locked(
            ChatRequest(
                connection_id=connection_id,
                content="edit",
                player_name="Alex",
                run_id="run-validation",
                trace_id="trace-validation",
                attempt_id="attempt-validation",
                use_context=False,
            ),
            connection_id,
        )
        flush_audit_writer(timeout=2.0)

        records = [
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").splitlines()
        ]
        failures = [r for r in records if r["status"] == "failure"]
        successes = [r for r in records if r["status"] == "success"]
        assert len(failures) == 1
        assert len(successes) == 1
        assert failures[0]["tool_name"] == "edit_blocks"
        assert failures[0]["error_kind"] == "INVALID_ARGUMENT"
        assert failures[0]["result"]["failure_reason"] == "json_invalid"
        assert failures[0]["result"]["execution_stage"] == "validation"
        assert failures[0]["result"]["external_state_unknown"] == "false"
        assert failures[0]["parameters"]["dimension"] == "minecraft:overworld"
        assert failures[0]["parameters"]["api_key"] == "[REDACTED]"
        assert "validation-secret" not in json.dumps(failures[0], ensure_ascii=False)
        assert not any(
            isinstance(call.args[1], dict)
            and call.args[1].get("type") in {"run_command", "bridge_req"}
            for call in broker.send_response.await_args_list
        )
    finally:
        try:
            stop_audit_writer(timeout=2.0)
        finally:
            set_audit_writer(None)


@pytest.mark.asyncio
async def test_approval_required_records_validation_failure_once_before_suspension(
    monkeypatch, tmp_path
):
    """审批暂停前复用 validation audit/Trace 去重，且不伪造执行开始。"""
    import json
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RetryPromptPart,
        ToolCallPart,
    )
    from pydantic_ai.tools import DeferredToolRequests

    from models.agent import StreamEvent
    from services.agent.harness.approvals import PendingApprovalStore
    from services.agent.harness.audit import (
        AuditWriter,
        flush_audit_writer,
        set_audit_writer,
        start_audit_writer,
        stop_audit_writer,
    )
    from services.agent.trace import TraceRecorder, set_trace_recorder

    settings = _make_settings()
    settings.runtime_harness_enabled = True
    settings.runtime_harness_audit_enabled = True
    audit_path = tmp_path / "approval-validation.jsonl"
    settings.runtime_harness_audit_path = str(audit_path)
    settings.runtime_harness_audit_max_records = 100
    settings.approval_ttl = 120.0
    settings.tool_policy_version = "test"
    settings.compression_enabled = False

    trace_path = tmp_path / "approval-validation-trace.jsonl"
    recorder = TraceRecorder(
        path=trace_path,
        enabled=True,
        include_content=False,
        max_records=100,
    )
    await recorder.start()
    set_trace_recorder(recorder)

    broker = MagicMock()
    broker.get_session_lock = MagicMock(return_value=asyncio.Lock())
    broker.get_response_queue = MagicMock(return_value=object())
    sent_responses: list[tuple[object, object]] = []

    async def send_response(*args):
        sent_responses.append(args)
        return True

    broker.send_response = send_response
    worker = AgentWorker(broker, settings)

    retry_at = datetime(2026, 8, 2, 15, 0, 0, tzinfo=UTC)
    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="run_minecraft_command",
                    args={
                        "command": "time set day",
                        "api_key": "APPROVAL-VALIDATION-SECRET",
                    },
                    tool_call_id="tc-invalid",
                )
            ]
        ),
        ModelRequest(
            parts=[
                RetryPromptPart(
                    [
                        {
                            "type": "json_invalid",
                            "loc": ("command",),
                            "msg": "Invalid JSON: APPROVAL-VALIDATION-SECRET",
                            "input": "APPROVAL-VALIDATION-SECRET",
                        }
                    ],
                    tool_name="run_minecraft_command",
                    tool_call_id="tc-invalid",
                    timestamp=retry_at,
                )
            ]
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="run_minecraft_command",
                    args={"command": "time set day"},
                    tool_call_id="tc-approved",
                )
            ]
        ),
    ]
    approved_call = messages[-1].parts[0]
    deferred = DeferredToolRequests(
        approvals=[approved_call],
        metadata={
            "tc-approved": {
                "normalized_args": {"command": "time set day"},
                "args_summary": "command=time set day",
                "args_hash": "hash",
                "policy_version": "test",
                "reason": "needs approval",
            }
        },
    )
    store = PendingApprovalStore(default_ttl_seconds=120.0)

    async def fake_stream_chat(*_args, **_kwargs):
        yield StreamEvent(
            event_type="approval_required",
            content="工具调用需要玩家审批",
            sequence=0,
            metadata={
                "deferred_requests": deferred,
                "all_messages": messages,
                "new_messages": messages,
                "new_messages_serialized": [],
                "usage": None,
                "run_id": "run-approval-validation",
            },
        )

    runtime = SimpleNamespace(
        get_pending_approval_store=lambda _settings=None: store,
        refresh_mcp_tools=lambda _settings: None,
    )
    monkeypatch.setattr("services.agent.worker.stream_chat", fake_stream_chat)
    monkeypatch.setattr(
        "services.agent.providers.ProviderRegistry.get_model",
        lambda _config: object(),
    )
    monkeypatch.setattr("services.agent.mcp.get_mcp_manager", lambda _settings: None)
    monkeypatch.setattr("services.agent.worker.get_agent_runtime", lambda: runtime)

    writer = AuditWriter()
    set_audit_writer(writer)
    start_audit_writer()
    try:
        connection_id = uuid4()
        await worker._process_request_locked(
            ChatRequest(
                connection_id=connection_id,
                content="build",
                player_name="Alex",
                run_id="run-approval-validation",
                trace_id="trace-approval-validation",
                attempt_id="attempt-approval-validation",
            ),
            connection_id,
        )
        flush_audit_writer(timeout=2.0)
        await recorder.stop()

        records = [
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").splitlines()
        ]
        failures = [record for record in records if record["status"] == "failure"]
        assert len(failures) == 1
        assert failures[0]["error_kind"] == "INVALID_ARGUMENT"
        assert failures[0]["result"]["execution_stage"] == "validation"

        trace_events = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
        ]
        validation_events = [
            event
            for event in trace_events
            if event["event_name"] == "tool.validation.failed"
        ]
        assert len(validation_events) == 1
        assert not any(
            event["event_name"] == "tool.execution.started" for event in trace_events
        )
        assert len(store) == 1
        assert any(
            getattr(item[1], "chunk_type", None) == "approval_required"
            for item in sent_responses
        )
    finally:
        try:
            stop_audit_writer(timeout=2.0)
        finally:
            set_audit_writer(None)
            set_trace_recorder(None)


@pytest.mark.asyncio
async def test_partial_history_persistence_drops_orphan_retry_and_keeps_visible_context(monkeypatch):
    """partial history 落盘前清除孤立 retry，但保留可见文本和中断说明。"""
    from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, TextPart

    from services.agent.context import ContextBudget, ContextBuilder

    settings = _make_settings()
    broker = MagicMock()
    broker.set_conversation_history = MagicMock(return_value=True)
    broker.send_response = AsyncMock(return_value=True)
    worker = AgentWorker(broker, settings)

    partial_messages = [
        ModelResponse(parts=[TextPart(content="工具执行前的可见说明")]),
        ModelRequest(
            parts=[
                RetryPromptPart(
                    "invalid JSON",
                    tool_name="edit_blocks",
                    tool_call_id="call-bad",
                )
            ]
        ),
    ]

    class _NoCompression:
        async def check_and_compress(self, *_args, **_kwargs):
            return False, "disabled"

    monkeypatch.setattr(
        "core.conversation.get_conversation_manager",
        lambda *_args, **_kwargs: _NoCompression(),
    )

    connection_id = uuid4()
    request = ChatRequest(
        connection_id=connection_id,
        content="继续",
        player_name="Alex",
        conversation_id="partial-conversation",
        run_id="run-partial-sanitize",
    )
    await worker._persist_partial_run_history(
        connection_id=connection_id,
        request=request,
        all_messages=partial_messages,
        player_error_text="provider interrupted",
        conversation_invalidation_epoch=7,
    )

    saved_history = broker.set_conversation_history.call_args.args[2]
    assert partial_messages[1].parts[0].tool_call_id == "call-bad"
    assert not any(
        isinstance(part, RetryPromptPart)
        for message in saved_history
        for part in message.parts
    )
    assert any(
        isinstance(part, TextPart) and part.content == "工具执行前的可见说明"
        for message in saved_history
        for part in message.parts
    )
    assert any(
        isinstance(part, TextPart) and "[系统] 本轮执行中断" in part.content
        for message in saved_history
        for part in message.parts
    )

    processed = ContextBuilder().process_history(
        saved_history,
        budget=ContextBudget(
            context_window=8192,
            system_reserve=0,
            tool_schema_reserve=0,
            current_input_reserve=0,
            output_reserve=0,
            history_budget=8192,
        ),
    )
    assert not any(
        isinstance(part, RetryPromptPart)
        for message in processed
        for part in message.parts
    )


@pytest.mark.asyncio
async def test_stream_chunks_carry_trace_correlation(monkeypatch):
    """StreamChunk 构造应带上 request 的 trace_id / attempt_id。"""
    from services.agent.core import StreamEvent

    settings = _make_settings()
    broker = MagicMock()
    broker.get_session_lock = MagicMock(return_value=asyncio.Lock())
    broker.get_conversation_history = MagicMock(return_value=[])
    broker.send_response = AsyncMock(return_value=True)
    broker.get_response_queue = MagicMock(return_value=object())
    worker = AgentWorker(broker, settings)

    async def fake_stream_chat(*_args, **_kwargs):
        yield StreamEvent(
            event_type="content",
            content="hello",
            sequence=0,
            metadata=None,
        )
        yield StreamEvent(
            event_type="content",
            content="",
            sequence=1,
            metadata={
                "is_complete": True,
                "all_messages": [],
                "usage": None,
                "tool_events": [],
            },
        )

    monkeypatch.setattr("services.agent.worker.stream_chat", fake_stream_chat)
    monkeypatch.setattr(
        "services.agent.providers.ProviderRegistry.get_model",
        lambda _config: object(),
    )
    monkeypatch.setattr("services.agent.mcp.get_mcp_manager", lambda _s: None)

    connection_id = uuid4()
    await worker._process_request_locked(
        ChatRequest(
            connection_id=connection_id,
            content="hi",
            player_name="Alex",
            run_id="trace-abc",
            trace_id="trace-abc",
            attempt_id="attempt-xyz",
        ),
        connection_id,
    )

    chunks = [
        call.args[1]
        for call in broker.send_response.await_args_list
        if getattr(call.args[1], "type", None) == "stream_chunk"
        or getattr(call.args[1], "chunk_type", None)
    ]
    assert chunks, "expected at least one StreamChunk"
    for chunk in chunks:
        assert chunk.trace_id == "trace-abc"
        assert chunk.attempt_id == "attempt-xyz"
        assert chunk.conversation_id is not None


async def _flush_trace(recorder) -> list[dict]:
    """Stop writer and return parsed journal lines for the active path."""
    await recorder.stop()
    path = recorder.path
    if not path.exists():
        return []
    import json

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_single_tool_trace_contains_model_tool_model_and_final_response(
    tmp_path, monkeypatch
):
    """Worker terminal path records model pairs + final response when content enabled."""
    from services.agent.core import StreamEvent
    from services.agent.trace import TraceRecorder, set_trace_recorder

    settings = _make_settings()
    path = tmp_path / "worker_trace.jsonl"
    recorder = TraceRecorder(
        path=path, enabled=True, include_content=True, max_records=500
    )
    await recorder.start()
    set_trace_recorder(recorder)
    try:
        broker = MagicMock()
        broker.get_session_lock = MagicMock(return_value=asyncio.Lock())
        broker.get_conversation_history = MagicMock(return_value=[])
        broker.set_conversation_history = MagicMock(return_value=True)
        broker.get_response_queue = MagicMock(return_value=object())
        broker.send_response = AsyncMock(return_value=True)
        broker.mark_conversation_title_generating = MagicMock(return_value=False)
        worker = AgentWorker(broker, settings)

        final_text = "钻石剑在箱子里"
        new_messages = [
            {
                "kind": "request",
                "parts": [{"part_kind": "user-prompt", "content": "查一下钻石剑"}],
            },
            {
                "kind": "response",
                "parts": [
                    {
                        "part_kind": "tool-call",
                        "tool_name": "find_entities",
                        "tool_call_id": "tc1",
                        "args": {"entity_type": "item"},
                    }
                ],
                "finish_reason": "tool_calls",
            },
            {
                "kind": "request",
                "parts": [
                    {
                        "part_kind": "tool-return",
                        "tool_name": "find_entities",
                        "tool_call_id": "tc1",
                        "content": "found 1",
                    }
                ],
            },
            {
                "kind": "response",
                "parts": [{"part_kind": "text", "content": final_text}],
                "finish_reason": "stop",
            },
        ]

        async def fake_stream_chat(*_args, **_kwargs):
            yield StreamEvent(
                event_type="content",
                content=final_text,
                sequence=0,
                metadata=None,
            )
            yield StreamEvent(
                event_type="content",
                content="",
                sequence=1,
                metadata={
                    "is_complete": True,
                    "all_messages": [],
                    "new_messages": new_messages,
                    "new_messages_serialized": new_messages,
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "tool_events": [{"tool_name": "find_entities"}],
                },
            )

        monkeypatch.setattr("services.agent.worker.stream_chat", fake_stream_chat)
        monkeypatch.setattr(
            "services.agent.providers.ProviderRegistry.get_model",
            lambda _config: object(),
        )
        monkeypatch.setattr("services.agent.mcp.get_mcp_manager", lambda _s: None)

        connection_id = uuid4()
        await worker._process_request_locked(
            ChatRequest(
                connection_id=connection_id,
                content="查一下钻石剑",
                player_name="Alex",
                run_id="trace-tool-1",
                trace_id="trace-tool-1",
                attempt_id="attempt-1",
            ),
            connection_id,
        )

        events = await _flush_trace(recorder)
        names = [e["event_name"] for e in events]
        assert "queue.dequeued" in names
        assert "agent.attempt.started" in names
        assert names.index("model.request.completed") < names.index("trace.completed")
        assert sum(name == "model.request.completed" for name in names) == 2
        completed = [e for e in events if e["event_name"] == "trace.completed"]
        assert len(completed) == 1
        assert completed[0]["payload"]["final_response"] == final_text
    finally:
        set_trace_recorder(None)


@pytest.mark.asyncio
async def test_content_disabled_keeps_metadata_but_omits_messages_and_results(
    tmp_path, monkeypatch
):
    """include_content=False keeps lifecycle events but drops payload bodies."""
    from services.agent.core import StreamEvent
    from services.agent.trace import TraceRecorder, set_trace_recorder

    settings = _make_settings()
    path = tmp_path / "worker_trace_meta.jsonl"
    recorder = TraceRecorder(
        path=path, enabled=True, include_content=False, max_records=500
    )
    await recorder.start()
    set_trace_recorder(recorder)
    try:
        broker = MagicMock()
        broker.get_session_lock = MagicMock(return_value=asyncio.Lock())
        broker.get_conversation_history = MagicMock(return_value=[])
        broker.set_conversation_history = MagicMock(return_value=True)
        broker.get_response_queue = MagicMock(return_value=object())
        broker.send_response = AsyncMock(return_value=True)
        broker.mark_conversation_title_generating = MagicMock(return_value=False)
        worker = AgentWorker(broker, settings)

        new_messages = [
            {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "secret prompt"}]},
            {
                "kind": "response",
                "parts": [{"part_kind": "text", "content": "ok"}],
                "finish_reason": "stop",
            },
        ]

        async def fake_stream_chat(*_args, **_kwargs):
            yield StreamEvent(
                event_type="content",
                content="ok",
                sequence=0,
            )
            yield StreamEvent(
                event_type="content",
                content="",
                sequence=1,
                metadata={
                    "is_complete": True,
                    "all_messages": [],
                    "new_messages": new_messages,
                    "new_messages_serialized": new_messages,
                    "usage": None,
                    "tool_events": [{"tool_name": "list_available_providers"}],
                },
            )

        monkeypatch.setattr("services.agent.worker.stream_chat", fake_stream_chat)
        monkeypatch.setattr(
            "services.agent.providers.ProviderRegistry.get_model",
            lambda _config: object(),
        )
        monkeypatch.setattr("services.agent.mcp.get_mcp_manager", lambda _s: None)

        connection_id = uuid4()
        await worker._process_request_locked(
            ChatRequest(
                connection_id=connection_id,
                content="secret prompt",
                player_name="Alex",
                run_id="trace-secret",
                trace_id="trace-secret",
                attempt_id="attempt-s",
            ),
            connection_id,
        )

        events = await _flush_trace(recorder)
        assert events
        assert all("payload" not in e or e.get("payload") is None for e in events)
        assert any(e["event_name"] == "trace.completed" for e in events)
        raw = path.read_text(encoding="utf-8")
        assert "secret prompt" not in raw
    finally:
        set_trace_recorder(None)


@pytest.mark.asyncio
async def test_stream_error_emits_trace_failed_once(tmp_path, monkeypatch):
    from services.agent.core import StreamEvent
    from services.agent.trace import TraceRecorder, set_trace_recorder

    settings = _make_settings()
    path = tmp_path / "worker_trace_err.jsonl"
    recorder = TraceRecorder(path=path, enabled=True, include_content=False, max_records=100)
    await recorder.start()
    set_trace_recorder(recorder)
    try:
        broker = MagicMock()
        broker.get_session_lock = MagicMock(return_value=asyncio.Lock())
        broker.get_conversation_history = MagicMock(return_value=[])
        broker.send_response = AsyncMock(return_value=True)
        broker.get_response_queue = MagicMock(return_value=object())
        worker = AgentWorker(broker, settings)

        async def fake_stream_chat(*_args, **_kwargs):
            yield StreamEvent(
                event_type="error",
                content="预算超限",
                sequence=0,
                metadata={"error_kind": "DENIED", "diagnostic_summary": "UsageLimitExceeded"},
            )

        monkeypatch.setattr("services.agent.worker.stream_chat", fake_stream_chat)
        monkeypatch.setattr(
            "services.agent.providers.ProviderRegistry.get_model",
            lambda _config: object(),
        )
        monkeypatch.setattr("services.agent.mcp.get_mcp_manager", lambda _s: None)

        connection_id = uuid4()
        await worker._process_request_locked(
            ChatRequest(
                connection_id=connection_id,
                content="budget",
                player_name="Alex",
                run_id="trace-err",
                trace_id="trace-err",
                attempt_id="attempt-err",
            ),
            connection_id,
        )
        events = await _flush_trace(recorder)
        failed = [e for e in events if e["event_name"] == "trace.failed"]
        assert len(failed) == 1
        assert failed[0]["status"] == "failed"
        assert failed[0]["attributes"]["error_kind"] == "DENIED"
        # include_content=False must not leak free-text diagnostics into attributes
        assert "diagnostic_summary" not in failed[0]["attributes"]
        raw = path.read_text(encoding="utf-8")
        assert "UsageLimitExceeded" not in raw
    finally:
        set_trace_recorder(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("include_content", [False, True])
async def test_validation_failure_trace_is_gated_and_does_not_start_execution(
    tmp_path, monkeypatch, include_content
):
    """参数校验失败只产生 validation 事件，正文随 Trace content 开关控制。"""
    from datetime import UTC, datetime

    from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart

    from services.agent.core import StreamEvent
    from services.agent.trace import TraceRecorder, set_trace_recorder

    settings = _make_settings()
    settings.runtime_harness_enabled = False
    settings.runtime_harness_audit_enabled = False
    path = tmp_path / f"worker_trace_validation_{include_content}.jsonl"
    recorder = TraceRecorder(
        path=path,
        enabled=True,
        include_content=include_content,
        max_records=500,
    )
    await recorder.start()
    set_trace_recorder(recorder)
    try:
        broker = MagicMock()
        broker.get_session_lock = MagicMock(return_value=asyncio.Lock())
        broker.get_conversation_history = MagicMock(return_value=[])
        broker.set_conversation_history = MagicMock(return_value=True)
        broker.get_response_queue = MagicMock(return_value=object())
        broker.send_response = AsyncMock(return_value=True)
        broker.mark_conversation_title_generating = MagicMock(return_value=False)
        worker = AgentWorker(broker, settings)

        retry_content = [
            {
                "type": "json_invalid",
                "loc": ("edits",),
                "msg": "Invalid JSON: TRACE-VALIDATION-SECRET",
                "input": "TRACE-VALIDATION-SECRET",
            }
        ]
        messages = [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="edit_blocks",
                        tool_call_id="tc-trace-invalid",
                        args={
                            "dimension": "minecraft:overworld",
                            "api_key": "TRACE-VALIDATION-SECRET",
                        },
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        retry_content,
                        tool_name="edit_blocks",
                        tool_call_id="tc-trace-invalid",
                        timestamp=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
                    )
                ]
            ),
        ]

        async def fake_stream_chat(*_args, **_kwargs):
            yield StreamEvent(
                event_type="content",
                content="参数无效",
                sequence=0,
                metadata=None,
            )
            yield StreamEvent(
                event_type="content",
                content="",
                sequence=1,
                metadata={
                    "is_complete": True,
                    "all_messages": messages,
                    "new_messages": messages,
                    "usage": None,
                    "tool_events": [],
                },
            )

        monkeypatch.setattr("services.agent.worker.stream_chat", fake_stream_chat)
        monkeypatch.setattr(
            "services.agent.providers.ProviderRegistry.get_model",
            lambda _config: object(),
        )
        monkeypatch.setattr("services.agent.mcp.get_mcp_manager", lambda _s: None)

        connection_id = uuid4()
        await worker._process_request_locked(
            ChatRequest(
                connection_id=connection_id,
                content="edit",
                player_name="Alex",
                run_id="trace-validation-run",
                trace_id="trace-validation-run",
                attempt_id="trace-validation-attempt",
                use_context=False,
            ),
            connection_id,
        )

        events = await _flush_trace(recorder)
        validation = [
            event for event in events if event["event_name"] == "tool.validation.failed"
        ]
        assert len(validation) == 1
        event = validation[0]
        assert event["status"] == "failed"
        assert event["tool_call_id"] == "tc-trace-invalid"
        assert event["attributes"]["error_kind"] == "INVALID_ARGUMENT"
        assert event["attributes"]["execution_stage"] == "validation"
        assert event["attributes"]["validation_error_type"] == "json_invalid"
        assert event["attributes"]["validation_error_locations"] == ["edits"]
        assert event["attributes"]["parameters"]["api_key"] == "[REDACTED]"
        assert not any(
            item["event_name"] == "tool.execution.started" for item in events
        )
        if include_content:
            assert event["payload"]["error_message"][0]["type"] == "json_invalid"
            assert event["payload"]["error_message"][0]["input"] == "[REDACTED]"
            model_payloads = [
                item.get("payload")
                for item in events
                if item["event_name"] == "model.request.completed"
            ]
            assert any(
                any(
                    part.get("part_kind") == "retry-prompt"
                    for message in (payload or {}).get("messages", [])
                    for part in message.get("parts", [])
                )
                for payload in model_payloads
            )
            model_tool_calls = [
                part
                for payload in model_payloads
                for message in (payload or {}).get("messages", [])
                for part in message.get("parts", [])
                if part.get("part_kind") == "tool-call"
            ]
            assert model_tool_calls
            assert model_tool_calls[0]["args"]["api_key"] == "[REDACTED]"
        else:
            assert "payload" not in event
        raw = path.read_text(encoding="utf-8")
        assert "TRACE-VALIDATION-SECRET" not in raw
    finally:
        set_trace_recorder(None)


@pytest.mark.asyncio
async def test_approval_suspended_records_model_pairs_without_duplicate_proposed(
    tmp_path, monkeypatch
):
    """approval_required flushes model pairs before suspended; no worker tool.proposed."""
    from types import SimpleNamespace

    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import DeferredToolRequests

    from services.agent.core import StreamEvent
    from services.agent.harness.approvals import PendingApprovalStore
    from services.agent.trace import TraceRecorder, set_trace_recorder

    settings = _make_settings()
    settings.approval_ttl = 120.0
    settings.tool_policy_version = "test"
    path = tmp_path / "worker_trace_approval.jsonl"
    recorder = TraceRecorder(
        path=path, enabled=True, include_content=True, max_records=500
    )
    await recorder.start()
    set_trace_recorder(recorder)
    try:
        broker = MagicMock()
        broker.get_session_lock = MagicMock(return_value=asyncio.Lock())
        broker.get_conversation_history = MagicMock(return_value=[])
        broker.set_conversation_history = MagicMock(return_value=True)
        broker.get_response_queue = MagicMock(return_value=object())
        broker.send_response = AsyncMock(return_value=True)
        broker.mark_conversation_title_generating = MagicMock(return_value=False)
        worker = AgentWorker(broker, settings)

        new_messages = [
            {
                "kind": "request",
                "parts": [{"part_kind": "user-prompt", "content": "set day"}],
            },
            {
                "kind": "response",
                "parts": [
                    {
                        "part_kind": "tool-call",
                        "tool_name": "run_minecraft_command",
                        "tool_call_id": "tc-ap1",
                        "args": {"command": "time set day"},
                    }
                ],
                "finish_reason": "tool_calls",
            },
        ]
        deferred = DeferredToolRequests(
            approvals=[
                ToolCallPart(
                    tool_name="run_minecraft_command",
                    tool_call_id="tc-ap1",
                    args={"command": "time set day"},
                )
            ],
            metadata={
                "tc-ap1": {
                    "normalized_args": {"command": "time set day"},
                    "args_summary": "command=time set day",
                    "args_hash": "h",
                    "policy_version": "test",
                    "reason": "needs approval",
                }
            },
        )

        async def fake_stream_chat(*_args, **_kwargs):
            yield StreamEvent(
                event_type="approval_required",
                content="工具调用需要玩家审批",
                sequence=0,
                metadata={
                    "deferred_requests": deferred,
                    "all_messages": [],
                    "new_messages": new_messages,
                    "new_messages_serialized": new_messages,
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                    "run_id": "trace-ap-susp",
                },
            )

        store = PendingApprovalStore(default_ttl_seconds=120.0)
        runtime = SimpleNamespace(
            get_pending_approval_store=lambda _settings=None: store,
            refresh_mcp_tools=lambda _s: None,
            get_conversation_manager=lambda *_a, **_k: SimpleNamespace(
                check_and_compress=AsyncMock(return_value=(False, "")),
            ),
        )
        monkeypatch.setattr("services.agent.worker.stream_chat", fake_stream_chat)
        monkeypatch.setattr(
            "services.agent.providers.ProviderRegistry.get_model",
            lambda _config: object(),
        )
        monkeypatch.setattr("services.agent.mcp.get_mcp_manager", lambda _s: None)
        monkeypatch.setattr("services.agent.worker.get_agent_runtime", lambda: runtime)

        connection_id = uuid4()
        await worker._process_request_locked(
            ChatRequest(
                connection_id=connection_id,
                content="set day",
                player_name="Alex",
                run_id="trace-ap-susp",
                trace_id="trace-ap-susp",
                attempt_id="attempt-ap",
            ),
            connection_id,
        )

        events = await _flush_trace(recorder)
        names = [e["event_name"] for e in events]
        assert "model.request.completed" in names
        assert "approval.requested" in names
        assert "trace.suspended" in names
        # Worker no longer re-emits tool.proposed (harness owns that boundary).
        assert "tool.proposed" not in names
        assert names.index("model.request.completed") < names.index("trace.suspended")
        assert names.index("approval.requested") < names.index("trace.suspended")
        suspended = [e for e in events if e["event_name"] == "trace.suspended"]
        assert len(suspended) == 1
        assert suspended[0]["status"] == "suspended"
    finally:
        set_trace_recorder(None)
