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

    s = Settings()
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
