"""AgentWorker 超时配置单元测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from services.agent.worker import AgentWorker


def _make_settings(
    worker_http_timeout: int = 30,
    worker_poll_timeout: float = 2.0,
    run_command_timeout: float = 5.0,
) -> MagicMock:
    s = MagicMock()
    s.worker_http_timeout = worker_http_timeout
    s.worker_poll_timeout = worker_poll_timeout
    s.run_command_timeout = run_command_timeout
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

    assert result == "ok"
    assert captured["value"] == 7.5


def test_settings_defaults_and_constraints():
    """Settings 字段应有正确默认值与约束。"""
    from config.settings import Settings

    s = Settings()
    assert s.worker_http_timeout == 60
    assert s.worker_poll_timeout == 1.0
    assert s.run_command_timeout == 10.0

    with pytest.raises(Exception):
        Settings(worker_http_timeout=0)
    with pytest.raises(Exception):
        Settings(worker_poll_timeout=0)
    with pytest.raises(Exception):
        Settings(run_command_timeout=0)
