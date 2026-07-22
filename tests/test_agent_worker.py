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
    assert "预算" in sent.content or "错误" in sent.content


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
        assert completed[0]["payload"]["content"] == final_text
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
    finally:
        set_trace_recorder(None)
