"""HostGatewayServer lifecycle tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcbe_ws_sdk import McbeServerFacade

from core.queue import MessageBroker
from services.gateway.server import HostGatewayServer


def _settings() -> MagicMock:
    s = MagicMock()
    s.host = "127.0.0.1"
    s.port = 18080
    s.dev_mode = True
    s.enable_ws_raw_log = False
    s.run_command_timeout = 5.0
    s.default_provider = "deepseek"
    s.max_chunk_content_length = 400
    s.chunk_sentence_mode = True
    s.flow_control = SimpleNamespace(
        command_line_byte_budget=461,
        max_chunk_content_length=400,
        chunk_sentence_mode=True,
        chunk_delays=SimpleNamespace(
            tellraw=0.05,
            scriptevent=0.05,
            ai_resp=0.15,
            ai_resp_prelude=0.5,
            text_resp=None,
            text_resp_prelude=None,
        ),
    )
    s.websocket = SimpleNamespace(
        ping_interval=20,
        ping_timeout=20,
        close_timeout=10,
        max_size=2**20,
        max_queue=32,
    )
    s.minecraft = SimpleNamespace(
        welcome_message_template="hi {version} {connection_id} {provider} {model} {context_status} {help_command}",
        context_enabled_text="on",
        context_disabled_text="off",
        error_prefix="E",
        error_color="red",
        info_prefix="I",
        info_color="white",
        success_prefix="S",
        success_color="green",
        commands={},
        get_command_description=lambda _t: ("", ""),
    )
    s.get_provider_config.return_value = SimpleNamespace(model="m", context_window=None)
    return s


@pytest.mark.asyncio
async def test_host_gateway_start_stop(monkeypatch):
    started = asyncio.Event()

    async def fake_lifetime(self):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(McbeServerFacade, "run_lifetime", fake_lifetime)

    broker = MessageBroker(max_size=10)
    jwt = MagicMock()
    server = HostGatewayServer(broker, _settings(), jwt)

    await server.start()
    await asyncio.wait_for(started.wait(), timeout=1)
    assert server.addon is not None
    await server.stop()
    # stop should leave no running facade task
    assert server._task is None or server._task.done()
