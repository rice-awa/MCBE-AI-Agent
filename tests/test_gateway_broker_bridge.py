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
    """Successful delivery emits no delivery.* events; chunk body never traced."""
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
                conversation_id="conv-delivery",
            ),
        )
        for _ in range(50):
            if sent:
                break
            await asyncio.sleep(0.02)
        await bridge.stop(cid)
        await recorder.stop()

        assert sent, "expected outbound payload"
        # Success path records no delivery.* events (failures only).
        events: list[dict] = []
        if path.exists():
            events = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        names = [e["event_name"] for e in events]
        assert not any(name.startswith("delivery.") for name in names)
        # Even if a trace file exists, the chunk body must never appear in it.
        if path.exists():
            assert secret not in path.read_text(encoding="utf-8")
    finally:
        set_trace_recorder(None)


@pytest.mark.asyncio
async def test_delivery_failed_recorded_without_body(tmp_path):
    """Failed delivery emits delivery.failed with counts/reason; never chunk body."""
    from services.agent.trace import TraceRecorder, set_trace_recorder

    path = tmp_path / "delivery_failed_trace.jsonl"
    recorder = TraceRecorder(path=path, enabled=True, include_content=True, max_records=100)
    await recorder.start()
    set_trace_recorder(recorder)
    try:
        broker = MessageBroker(max_size=10)
        cid = uuid4()
        broker.register_connection(cid)

        # send_payload=None makes _delivery() return None -> no_delivery failure path.
        state = ConnectionState(id=cid, send_payload=None)
        flow = FlowControlSettings()
        runner = WsCommandRunner(flow)
        bridge = BrokerResponseBridge(broker, flow, runner, profile=None)
        await bridge.start(state)
        secret = "SECRET_FAIL_BODY"
        await broker.send_response(
            cid,
            StreamChunk(
                connection_id=cid,
                chunk_type="content",
                content=secret,
                sequence=2,
                player_name="Steve",
                trace_id="trace-fail",
                attempt_id="attempt-fail",
                conversation_id="conv-fail",
            ),
        )
        await asyncio.sleep(0.05)
        await bridge.stop(cid)
        await recorder.stop()

        assert path.exists(), "expected a delivery.failed trace event"
        events = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        failed = [e for e in events if e["event_name"] == "delivery.failed"]
        assert len(failed) == 1
        attrs = failed[0]["attributes"]
        assert attrs["reason"] == "no_delivery"
        assert attrs["delivery_type"] == "tellraw"
        assert attrs["target"] == "Steve"
        assert attrs["chunk_count"] == 1
        assert "payload" not in failed[0] or failed[0].get("payload") is None
        # Chunk body must never be recorded in trace events.
        assert secret not in path.read_text(encoding="utf-8")
    finally:
        set_trace_recorder(None)


@pytest.mark.asyncio
async def test_thinking_end_skips_delivery_events(tmp_path):
    """thinking_end early-return must not emit any delivery.* events."""
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
        assert not any(name.startswith("delivery.") for name in names)
        assert sent == []
    finally:
        set_trace_recorder(None)
