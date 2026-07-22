"""BrokerResponseBridge unit tests."""

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcbe_ws_sdk import FlowControlSettings
from mcbe_ws_sdk.gateway.connection import ConnectionState

from core.queue import MessageBroker
from models.messages import StreamChunk
from services.gateway.broker_bridge import BrokerResponseBridge
from services.gateway.ws_command_runner import WsCommandRunner


@pytest.mark.asyncio
async def test_stream_chunk_becomes_outbound_payload():
    broker = MessageBroker(max_size=10)
    cid = uuid4()
    broker.register_connection(cid)
    sent: list[str] = []

    async def send_payload(p: str) -> None:
        sent.append(p)

    state = ConnectionState(id=cid, send_payload=send_payload)
    flow = FlowControlSettings()
    runner = WsCommandRunner(flow)
    bridge = BrokerResponseBridge(broker, flow, runner, profile=None)
    await bridge.start(state)
    await broker.send_response(
        cid,
        StreamChunk(
            connection_id=cid,
            chunk_type="content",
            content="你好",
            sequence=1,
            player_name="Steve",
        ),
    )
    for _ in range(50):
        if sent:
            break
        await asyncio.sleep(0.02)
    await bridge.stop(cid)
    assert sent, "expected tellraw payloads"
    payloads = [json.loads(payload) for payload in sent]
    assert any("你好" in payload["body"]["commandLine"] for payload in payloads)
    assert all(payload["body"]["origin"]["type"] == "player" for payload in payloads)
    assert all(
        payload["body"]["commandLine"].startswith("tellraw Steve ")
        for payload in payloads
    )
