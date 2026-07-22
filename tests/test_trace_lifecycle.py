"""Application TraceRecorder lifecycle helpers (no MCP / network)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.agent.trace import (
    TraceContext,
    get_trace_recorder,
    set_trace_recorder,
)


@pytest.fixture(autouse=True)
def _clear_recorder():
    set_trace_recorder(None)
    yield
    set_trace_recorder(None)


@pytest.mark.asyncio
async def test_start_stop_helpers_flush_journal(tmp_path: Path) -> None:
    """Production helpers start the writer so emit persists, and stop flushes."""
    from cli import _start_trace_recorder, _stop_trace_recorder

    path = tmp_path / "lifecycle.jsonl"
    settings = SimpleNamespace(
        agent_trace_enabled=True,
        agent_trace_include_content=False,
        agent_trace_path=str(path),
        agent_trace_max_records=100,
    )

    await _start_trace_recorder(settings)
    recorder = get_trace_recorder(settings)
    assert recorder.enabled is True
    health = recorder.health()
    assert health["running"] is True

    ctx = TraceContext(
        trace_id="trace-life",
        run_id="trace-life",
        attempt_id="attempt-life",
        message_id="msg-life",
        connection_id="conn-life",
        player_name="Alex",
        conversation_id="default",
    )
    recorder.emit("trace.started", ctx, status="started")

    await _stop_trace_recorder(settings)
    assert path.exists()
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["event_name"] == "trace.started"
    assert lines[0]["trace_id"] == "trace-life"
    assert recorder.health()["running"] is False


@pytest.mark.asyncio
async def test_application_start_stop_call_trace_helpers() -> None:
    """Application.start/stop must wire the same helpers used by production."""
    from cli import Application

    settings = SimpleNamespace(
        queue_max_size=10,
        host="127.0.0.1",
        port=8080,
        default_provider="deepseek",
        llm_worker_count=0,
        dev_mode=True,
        agent_trace_enabled=True,
        agent_trace_include_content=False,
        agent_trace_path="logs/agent_traces.jsonl",
        agent_trace_max_records=100,
    )

    start_mock = AsyncMock()
    stop_mock = AsyncMock()

    with (
        patch("cli.get_settings", return_value=settings),
        patch("cli.MessageBroker"),
        patch("cli.JWTHandler"),
        patch("cli.HostGatewayServer") as gateway_cls,
        patch("cli.get_agent_runtime") as runtime_factory,
        patch("cli._start_trace_recorder", start_mock),
        patch("cli._stop_trace_recorder", stop_mock),
    ):
        gateway = gateway_cls.return_value
        gateway.start = AsyncMock()
        gateway.stop = AsyncMock()
        gateway.addon = None

        runtime = MagicMock()
        runtime.initialize = AsyncMock(return_value=False)
        runtime.shutdown = AsyncMock()
        mcp = MagicMock()
        mcp.get_status_summary.return_value = {
            "enabled": False,
            "active_servers": 0,
            "total_servers": 0,
        }
        runtime.get_mcp_manager.return_value = mcp
        runtime.get_agent_manager.return_value = MagicMock(mcp_toolsets=[])
        runtime_factory.return_value = runtime

        app = Application()
        # Unblock start after hooks run: set shutdown immediately
        app._shutdown_event.set()
        await app.start()
        await app.stop()

    start_mock.assert_awaited_once_with(settings)
    stop_mock.assert_awaited_once_with(settings)
