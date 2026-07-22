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


@pytest.mark.asyncio
async def test_stream_chunk_emits_delivery_events_without_body(tmp_path):
    """delivery.* events carry target/type/counts; never chunk body."""
    from services.agent.trace import TraceRecorder, set_trace_recorder

    path = tmp_path / "delivery_trace.jsonl"
    recorder = TraceRecorder(path=path, enabled=True, include_content=True, max_records=100)
    await recorder.start()
    set_trace_recorder(recorder)
    try:
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
        secret = "SECRET_BODY_SHOULD_NOT_APPEAR"
        await broker.send_response(
            cid,
            StreamChunk(
                connection_id=cid,
                chunk_type="content",
                content=secret,
                sequence=1,
                player_name="Steve",
                trace_id="trace-del",
                attempt_id="attempt-del",
            ),
        )
        for _ in range(50):
            if sent:
                break
            await asyncio.sleep(0.02)
        await bridge.stop(cid)
        await recorder.stop()

        assert sent, "expected outbound payload"
        events = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        names = [e["event_name"] for e in events]
        assert "delivery.enqueued" in names
        assert "delivery.started" in names
        assert "delivery.completed" in names
        raw = path.read_text(encoding="utf-8")
        assert secret not in raw
        completed = next(e for e in events if e["event_name"] == "delivery.completed")
        assert completed["attributes"]["delivery_type"] == "tellraw"
        assert completed["attributes"]["target"] == "Steve"
        assert completed["attributes"]["chunk_count"] == 1
        assert "payload" not in completed or completed.get("payload") is None
    finally:
        set_trace_recorder(None)


@pytest.mark.asyncio
async def test_thinking_end_skips_delivery_enqueued(tmp_path):
    """thinking_end early-return must not orphan delivery.enqueued."""
    from services.agent.trace import TraceRecorder, set_trace_recorder

    path = tmp_path / "thinking_end_trace.jsonl"
    recorder = TraceRecorder(path=path, enabled=True, include_content=True, max_records=50)
    await recorder.start()
    set_trace_recorder(recorder)
    try:
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
                chunk_type="thinking_end",
                content="",
                sequence=1,
                player_name="Steve",
                trace_id="trace-think",
                attempt_id="attempt-think",
            ),
        )
        await asyncio.sleep(0.05)
        await bridge.stop(cid)
        await recorder.stop()

        events: list[dict] = []
        if path.exists():
            events = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        names = [e["event_name"] for e in events]
        assert "delivery.enqueued" not in names
        assert sent == []
    finally:
        set_trace_recorder(None)
