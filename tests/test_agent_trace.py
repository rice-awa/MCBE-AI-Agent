"""Agent Trace 数据契约与异步 Journal 测试（离线）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from config.settings import Settings
from services.agent.trace import (
    TraceContext,
    TraceEvent,
    TraceRecorder,
    get_trace_recorder,
    serialize_trace_payload,
    set_trace_recorder,
)


@pytest.fixture
def context() -> TraceContext:
    return TraceContext(
        trace_id="trace-1",
        run_id="trace-1",
        attempt_id="attempt-1",
        message_id="msg-1",
        connection_id="conn-1",
        player_name="alex",
        conversation_id="default",
    )


def test_trace_event_has_stable_fields_and_rejects_unknown_status():
    event = TraceEvent(
        event_name="trace.started",
        trace_id="trace-1",
        run_id="trace-1",
        attempt_id="attempt-1",
        status="started",
        attributes={"include_content": True},
    )
    assert event.schema_version == 1
    assert event.event_id
    assert event.sequence == 0

    with pytest.raises(ValidationError):
        TraceEvent(
            event_name="trace.completed",
            trace_id="trace-1",
            run_id="trace-1",
            attempt_id="attempt-1",
            status="maybe",
        )


def test_trace_context_is_frozen():
    ctx = TraceContext(
        trace_id="t",
        run_id="t",
        attempt_id="a",
        message_id="m",
        connection_id="c",
        player_name="p",
        conversation_id="d",
    )
    with pytest.raises(Exception):
        ctx.trace_id = "other"  # type: ignore[misc]


def test_serialize_trace_payload_whitelist_and_content_gate():
    raw = {
        "content": "hello player",
        "messages": [{"role": "user", "content": "hi"}],
        "api_key": "sk-secret",
        "headers": {"Authorization": "Bearer x"},
        "unknown_blob": {"nested": True},
        "usage": {"input_tokens": 10, "output_tokens": 3},
    }
    assert serialize_trace_payload(raw, include_content=False) is None
    assert serialize_trace_payload(None, include_content=True) is None

    serialized = serialize_trace_payload(raw, include_content=True)
    assert serialized is not None
    assert serialized["content"] == "hello player"
    assert serialized["messages"] == [{"role": "user", "content": "hi"}]
    assert serialized["usage"] == {"input_tokens": 10, "output_tokens": 3}
    assert "api_key" not in serialized
    assert "headers" not in serialized
    assert "unknown_blob" not in serialized


def test_serialize_trace_payload_compacts_long_system_prompt():
    import hashlib

    long_prompt = "S" * 400
    short_prompt = "keep-me-full"
    raw = {
        "messages": [
            {
                "kind": "request",
                "parts": [
                    {"part_kind": "system-prompt", "content": long_prompt},
                    {"part_kind": "system-prompt", "content": short_prompt},
                    {"part_kind": "user-prompt", "content": "hi player"},
                ],
            },
            {
                "kind": "response",
                "parts": [{"part_kind": "text", "content": "hello"}],
                "finish_reason": "stop",
            },
        ]
    }
    serialized = serialize_trace_payload(raw, include_content=True)
    assert serialized is not None
    parts = serialized["messages"][0]["parts"]
    compacted = parts[0]
    assert compacted["part_kind"] == "system-prompt"
    assert "content" not in compacted
    assert compacted["content_omitted"] is True
    assert compacted["content_chars"] == 400
    assert compacted["content_sha256"] == hashlib.sha256(long_prompt.encode()).hexdigest()
    assert compacted["content_preview"] == "S" * 120
    assert parts[1]["content"] == short_prompt
    assert parts[2]["content"] == "hi player"
    assert serialized["messages"][1]["parts"][0]["content"] == "hello"


@pytest.mark.asyncio
async def test_disabled_recorder_does_not_write_content(tmp_path, context):
    recorder = TraceRecorder(path=tmp_path / "trace.jsonl", enabled=False, include_content=True)
    await recorder.start()
    recorder.emit("trace.started", context, payload={"content": "secret"})
    await recorder.stop()
    assert not (tmp_path / "trace.jsonl").exists()
    assert recorder.enqueued == 0
    assert recorder.written == 0


@pytest.mark.asyncio
async def test_enabled_recorder_writes_metadata_without_payload_by_default(tmp_path, context):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path=path, enabled=True, include_content=False, max_records=100)
    await recorder.start()
    recorder.emit(
        "trace.started",
        context,
        status="started",
        attributes={"include_content": False, "channel": "tellraw"},
        payload={"content": "secret-should-not-persist"},
    )
    recorder.emit(
        "request.accepted",
        context,
        status="accepted",
        attributes={"player_name": "alex"},
    )
    await recorder.stop()

    assert path.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    events = [json.loads(line) for line in lines]
    assert events[0]["schema_version"] == 1
    assert events[0]["event_name"] == "trace.started"
    assert events[0]["trace_id"] == "trace-1"
    assert events[0]["run_id"] == "trace-1"
    assert events[0]["attempt_id"] == "attempt-1"
    assert events[0]["sequence"] == 0
    assert events[1]["sequence"] == 1
    assert events[0]["attributes"]["channel"] == "tellraw"
    assert events[0].get("payload") is None
    assert "secret" not in path.read_text(encoding="utf-8")
    assert recorder.enqueued == 2
    assert recorder.written == 2
    assert recorder.dropped == 0
    assert recorder.write_failed == 0


@pytest.mark.asyncio
async def test_enabled_recorder_persists_whitelisted_payload_when_include_content(
    tmp_path, context
):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path=path, enabled=True, include_content=True, max_records=100)
    await recorder.start()
    recorder.emit(
        "model.request.completed",
        context,
        status="completed",
        payload={
            "content": "final answer",
            "messages": [{"role": "assistant", "content": "final answer"}],
            "api_key": "sk-leak",
        },
    )
    await recorder.stop()

    event = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert event["payload"]["content"] == "final answer"
    assert event["payload"]["messages"][0]["content"] == "final answer"
    assert "api_key" not in event["payload"]
    assert "sk-leak" not in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_record_model_messages_puts_usage_in_attributes_not_payload(tmp_path, context):
    """usage/finish_reason/provider live in attributes; payload keeps only messages."""
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path=path, enabled=True, include_content=True, max_records=100)
    await recorder.start()
    recorder.record_model_messages(
        context,
        messages=[{"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "hi"}]}],
        usage={"input_tokens": 10, "output_tokens": 3, "requests": 1, "tool_calls": 0},
        provider="deepseek",
        model_name="deepseek-v4-flash",
        finish_reason="stop",
        attributes={"pair_index": 0},
    )
    await recorder.stop()

    event = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert event["event_name"] == "model.request.completed"
    # Metadata fields go to attributes (visible even when content is off).
    attrs = event["attributes"]
    assert attrs["usage"] == {"input_tokens": 10, "output_tokens": 3, "requests": 1, "tool_calls": 0}
    assert attrs["provider"] == "deepseek"
    assert attrs["model_name"] == "deepseek-v4-flash"
    assert attrs["finish_reason"] == "stop"
    assert attrs["pair_index"] == 0
    # Payload keeps only the messages body — no duplicated usage/finish_reason/provider.
    payload = event["payload"]
    assert set(payload.keys()) == {"messages"}
    assert "usage" not in payload
    assert "finish_reason" not in payload
    assert "provider" not in payload
    assert "model_name" not in payload


@pytest.mark.asyncio
async def test_record_model_messages_usage_visible_when_content_disabled(tmp_path, context):
    """With include_content=False, usage still recorded in attributes."""
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path=path, enabled=True, include_content=False, max_records=100)
    await recorder.start()
    recorder.record_model_messages(
        context,
        messages=[{"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "hi"}]}],
        usage={"input_tokens": 10, "output_tokens": 3},
        provider="deepseek",
        model_name="deepseek-v4-flash",
        finish_reason="stop",
    )
    await recorder.stop()

    event = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert "payload" not in event or event.get("payload") is None
    assert event["attributes"]["usage"] == {"input_tokens": 10, "output_tokens": 3}
    assert event["attributes"]["provider"] == "deepseek"
    assert "hi" not in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_queue_overflow_increments_dropped_and_gap(tmp_path, context):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(
        path=path,
        enabled=True,
        include_content=False,
        max_records=1000,
        queue_maxsize=1,
    )
    await recorder.start()
    # Pause writer by filling queue before it can drain, then emit more.
    # Hold the queue full by not letting the writer progress: put a blocker via
    # a full queue after the first event is enqueued while writer is busy.
    # Use a path that fails open so write is slow is hard; instead directly
    # exercise put_nowait Full by stopping the writer task and enqueueing.
    await recorder.stop()
    # Re-open with tiny queue and blocked writer: emit without start so queue
    # is not consumed, or start then stop writer mid-flight.
    recorder = TraceRecorder(
        path=path,
        enabled=True,
        include_content=False,
        max_records=1000,
        queue_maxsize=1,
    )
    # Manually prepare queue without starting writer so put_nowait fills up.
    recorder._ensure_queue()
    recorder.emit("trace.started", context, status="started")
    recorder.emit("request.accepted", context, status="accepted")
    recorder.emit("queue.enqueued", context, status="info")

    assert recorder.enqueued == 1
    assert recorder.dropped >= 2
    assert recorder.gap >= 2
    # No writer started → file may not exist
    await recorder.stop()


@pytest.mark.asyncio
async def test_rotation_keeps_only_max_records(tmp_path, context):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path=path, enabled=True, include_content=False, max_records=3)
    await recorder.start()
    for i in range(5):
        recorder.emit(
            "queue.enqueued",
            context,
            status="info",
            attributes={"i": i},
        )
    await recorder.stop()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    events = [json.loads(line) for line in lines]
    assert [e["attributes"]["i"] for e in events] == [2, 3, 4]
    assert recorder.written == 5


@pytest.mark.asyncio
async def test_shutdown_flushes_queued_events(tmp_path, context):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path=path, enabled=True, include_content=False, max_records=100)
    await recorder.start()
    for i in range(10):
        recorder.emit("delivery.completed", context, status="completed", attributes={"n": i})
    await recorder.stop()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 10
    assert recorder.written == 10
    assert recorder.enqueued == 10


@pytest.mark.asyncio
async def test_write_failure_does_not_raise_and_counts_failures(tmp_path, context, monkeypatch):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path=path, enabled=True, include_content=False, max_records=100)

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(recorder, "_append_line", boom)
    await recorder.start()
    recorder.emit("trace.started", context, status="started")
    await recorder.stop()

    assert recorder.write_failed >= 1
    assert recorder.gap >= 1
    # emit itself must not raise (already returned)


def test_settings_agent_trace_defaults_and_validation():
    settings = Settings()
    assert settings.agent_trace_enabled is False
    assert settings.agent_trace_include_content is False
    assert settings.agent_trace_path == "logs/agent_traces.jsonl"
    assert settings.agent_trace_max_records == 10000
    assert settings.agent_trace_api_host == "127.0.0.1"
    assert settings.agent_trace_api_port == 8787

    with pytest.raises(ValidationError):
        Settings(agent_trace_max_records=0)
    with pytest.raises(ValidationError):
        Settings(agent_trace_api_port=0)
    with pytest.raises(ValidationError):
        Settings(agent_trace_api_port=70000)

    # include_content is forced off when enabled is false
    forced = Settings(agent_trace_enabled=False, agent_trace_include_content=True)
    assert forced.agent_trace_enabled is False
    assert forced.agent_trace_include_content is False

    both = Settings(agent_trace_enabled=True, agent_trace_include_content=True)
    assert both.agent_trace_enabled is True
    assert both.agent_trace_include_content is True


def test_settings_agent_trace_loaded_from_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "agent": {
            "agent_trace_enabled": True,
            "agent_trace_include_content": True,
            "agent_trace_path": "tmp/agent_traces.jsonl",
            "agent_trace_max_records": 42,
            "agent_trace_api_host": "0.0.0.0",
            "agent_trace_api_port": 9001,
        }
    }
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")

    settings = Settings()
    assert settings.agent_trace_enabled is True
    assert settings.agent_trace_include_content is True
    assert settings.agent_trace_path == "tmp/agent_traces.jsonl"
    assert settings.agent_trace_max_records == 42
    assert settings.agent_trace_api_host == "0.0.0.0"
    assert settings.agent_trace_api_port == 9001


def test_get_trace_recorder_reads_settings(tmp_path, monkeypatch):
    set_trace_recorder(None)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "agent": {
                    "agent_trace_enabled": False,
                    "agent_trace_include_content": True,
                    "agent_trace_path": "logs/from_settings.jsonl",
                }
            }
        ),
        encoding="utf-8",
    )
    try:
        # Settings() loads partial JSON via customise_sources (no full REQUIRED_CONFIG_PATHS).
        settings = Settings()
        recorder = get_trace_recorder(settings)
        assert recorder.enabled is False
        # include_content ineffective when disabled
        assert recorder.include_content is False
        assert Path(recorder.path).name == "from_settings.jsonl"

        # Singleton reuses the same instance
        assert get_trace_recorder() is recorder
    finally:
        set_trace_recorder(None)


@pytest.mark.asyncio
async def test_emit_never_raises_on_bad_status(tmp_path, context):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path=path, enabled=True, include_content=False)
    await recorder.start()
    # Invalid status must not bubble to caller; counted as dropped (contract fail)
    recorder.emit("trace.started", context, status="not-a-real-status")
    assert recorder.dropped == 1
    assert recorder.gap == 1
    assert recorder.enqueued == 0
    assert recorder.written == 0
    assert not path.exists() or path.read_text(encoding="utf-8").strip() == ""

    # Subsequent valid emit still works
    recorder.emit("trace.started", context, status="started")
    await recorder.stop()
    assert recorder.enqueued == 1
    assert recorder.written == 1
    assert recorder.dropped == 1
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["status"] == "started"
    assert row["event_name"] == "trace.started"
