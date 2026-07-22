"""Offline tests for TraceQuery journal aggregation."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from services.agent.trace import (
    TraceContext,
    TraceRecorder,
    set_trace_recorder,
)
from services.agent.trace_query import ABANDONED_AFTER_SECONDS, TraceQuery


def _event(
    *,
    event_name: str,
    trace_id: str = "trace-1",
    sequence: int = 0,
    status: str = "info",
    attempt_id: str = "attempt-1",
    run_id: str | None = None,
    event_id: str | None = None,
    timestamp: str | None = None,
    tool_call_id: str | None = None,
    duration_ms: int | None = None,
    attributes: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
) -> dict[str, Any]:
    attrs = {
        "player_name": "alex",
        "connection_id": "conn-1",
        "conversation_id": "default",
        "message_id": "msg-1",
    }
    if attributes:
        attrs.update(attributes)
    obj: dict[str, Any] = {
        "schema_version": 1,
        "event_id": event_id or f"ev-{trace_id}-{sequence}-{event_name}",
        "event_name": event_name,
        "timestamp": timestamp
        or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "sequence": sequence,
        "trace_id": trace_id,
        "run_id": run_id or trace_id,
        "attempt_id": attempt_id,
        "status": status,
        "attributes": attrs,
    }
    if tool_call_id is not None:
        obj["tool_call_id"] = tool_call_id
    if duration_ms is not None:
        obj["duration_ms"] = duration_ms
    if payload is not None:
        obj["payload"] = payload
    if span_id is not None:
        obj["span_id"] = span_id
    if parent_span_id is not None:
        obj["parent_span_id"] = parent_span_id
    return obj


def write_fixture_events(path: Path) -> None:
    """Write out-of-order events for a completed tool-using trace."""
    base = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)
    events = [
        # Intentionally out of file order (sequence 2 then 0 then 1 then ...)
        _event(
            event_name="tool.execution.completed",
            sequence=4,
            status="succeeded",
            tool_call_id="tc-1",
            timestamp=(base + timedelta(seconds=4)).isoformat().replace("+00:00", "Z"),
            attributes={
                "player_name": "alex",
                "tool_name": "mcwiki_search",
                "execution_status": "succeeded",
            },
            payload={"tool_result": {"hits": 1}, "result": {"hits": 1}},
        ),
        _event(
            event_name="trace.started",
            sequence=0,
            status="started",
            timestamp=base.isoformat().replace("+00:00", "Z"),
        ),
        _event(
            event_name="agent.attempt.started",
            sequence=1,
            status="started",
            timestamp=(base + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
        ),
        _event(
            event_name="model.request.completed",
            sequence=2,
            status="completed",
            timestamp=(base + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
            attributes={
                "player_name": "alex",
                "provider": "deepseek",
                "model_name": "deepseek-chat",
            },
            payload={
                "messages": [{"role": "user", "content": "wiki?"}],
                "model_name": "deepseek-chat",
                "provider": "deepseek",
            },
        ),
        _event(
            event_name="tool.proposed",
            sequence=3,
            status="info",
            tool_call_id="tc-1",
            timestamp=(base + timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
            attributes={"player_name": "alex", "tool_name": "mcwiki_search"},
            payload={"tool_args": {"q": "creeper"}, "parameters": {"q": "creeper"}},
        ),
        _event(
            event_name="trace.completed",
            sequence=5,
            status="completed",
            duration_ms=5000,
            timestamp=(base + timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
            payload={"content": "done", "final_response": "done"},
        ),
    ]
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8",
    )


def test_query_groups_out_of_order_events_by_trace(tmp_path: Path) -> None:
    write_fixture_events(tmp_path / "agent_traces.jsonl")
    result = TraceQuery(tmp_path / "agent_traces.jsonl").get_trace("trace-1")
    assert result is not None
    assert result["summary"]["status"] == "completed"
    assert result["events"][0]["sequence"] == 0
    assert result["events"][-1]["event_name"] == "trace.completed"
    assert result["tools"][0]["tool_name"] == "mcwiki_search"
    assert result["models"][0]["model_name"] == "deepseek-chat"
    assert result["models"][0]["payload"]["messages"][0]["content"] == "wiki?"
    assert result["summary"]["player_name"] == "alex"
    assert result["summary"]["event_count"] == 6


def test_query_ignores_truncated_last_line_and_reports_gap(tmp_path: Path) -> None:
    path = tmp_path / "agent_traces.jsonl"
    path.write_text('{"event_name":"trace.started","trace_id":"t1","sequence":0}\n{"broken"', encoding="utf-8")
    health = TraceQuery(path).health()
    assert health["malformed_lines"] == 1
    assert health["parsed_lines"] == 1


def test_query_empty_journal(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    q = TraceQuery(path)
    assert q.list_traces() == []
    assert q.get_trace("missing") is None
    health = q.health()
    assert health["parsed_lines"] == 0
    assert health["malformed_lines"] == 0
    assert health["exists"] is True


def test_query_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "nope.jsonl"
    q = TraceQuery(path)
    assert q.list_traces() == []
    assert q.get_trace("x") is None
    health = q.health()
    assert health["exists"] is False
    assert health["parsed_lines"] == 0


def test_list_filter_by_status_and_player(tmp_path: Path) -> None:
    path = tmp_path / "agent_traces.jsonl"
    base = datetime(2026, 7, 22, 10, 0, 0, tzinfo=UTC)
    lines = [
        _event(
            event_name="trace.started",
            trace_id="t-ok",
            sequence=0,
            status="started",
            timestamp=base.isoformat().replace("+00:00", "Z"),
            attributes={"player_name": "alex"},
        ),
        _event(
            event_name="trace.completed",
            trace_id="t-ok",
            sequence=1,
            status="completed",
            timestamp=(base + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            attributes={"player_name": "alex"},
        ),
        _event(
            event_name="trace.started",
            trace_id="t-fail",
            sequence=0,
            status="started",
            timestamp=(base + timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
            attributes={"player_name": "steve"},
        ),
        _event(
            event_name="trace.failed",
            trace_id="t-fail",
            sequence=1,
            status="failed",
            timestamp=(base + timedelta(seconds=11)).isoformat().replace("+00:00", "Z"),
            attributes={"player_name": "steve"},
        ),
        _event(
            event_name="trace.started",
            trace_id="t-run",
            sequence=0,
            status="started",
            # Fresh timestamp so it stays "running"
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            attributes={"player_name": "alex"},
        ),
    ]
    path.write_text("\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")
    q = TraceQuery(path)

    failed = q.list_traces(status="failed", limit=10)
    assert len(failed) == 1
    assert failed[0]["trace_id"] == "t-fail"
    assert failed[0]["status"] == "failed"

    alex = q.list_traces(player="alex", limit=50)
    assert {s["trace_id"] for s in alex} == {"t-ok", "t-run"}

    limited = q.list_traces(limit=1)
    assert len(limited) == 1


def test_get_trace_missing_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "agent_traces.jsonl"
    path.write_text(
        json.dumps(_event(event_name="trace.started", sequence=0, status="started")) + "\n",
        encoding="utf-8",
    )
    assert TraceQuery(path).get_trace("does-not-exist") is None


def test_non_terminal_running_vs_abandoned(tmp_path: Path) -> None:
    path = tmp_path / "agent_traces.jsonl"
    recent = datetime.now(UTC)
    old = recent - timedelta(seconds=ABANDONED_AFTER_SECONDS + 60)
    lines = [
        _event(
            event_name="trace.started",
            trace_id="t-recent",
            sequence=0,
            status="started",
            timestamp=recent.isoformat().replace("+00:00", "Z"),
        ),
        _event(
            event_name="trace.started",
            trace_id="t-old",
            sequence=0,
            status="started",
            timestamp=old.isoformat().replace("+00:00", "Z"),
        ),
    ]
    path.write_text("\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")
    q = TraceQuery(path)
    r = q.get_trace("t-recent")
    o = q.get_trace("t-old")
    assert r is not None and r["summary"]["status"] == "running"
    assert o is not None and o["summary"]["status"] == "abandoned"
    # Must not fabricate completed
    assert r["summary"]["status"] != "completed"
    assert o["summary"]["status"] != "completed"


def test_payload_exposed_exactly_as_recorded(tmp_path: Path) -> None:
    path = tmp_path / "agent_traces.jsonl"
    payload = {"content": "exact body", "messages": [{"role": "user", "content": "hi"}]}
    event = _event(
        event_name="model.request.completed",
        sequence=0,
        status="completed",
        payload=payload,
    )
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    detail = TraceQuery(path).get_trace("trace-1")
    assert detail is not None
    assert detail["events"][0]["payload"] == payload
    assert detail["models"][0]["payload"] == payload


def test_health_includes_recorder_when_available(tmp_path: Path) -> None:
    path = tmp_path / "agent_traces.jsonl"
    path.write_text("", encoding="utf-8")
    recorder = TraceRecorder(path=path, enabled=False)
    set_trace_recorder(recorder)
    try:
        health = TraceQuery(path).health()
        assert health["path"] == str(path)
        assert health["abandoned_after_seconds"] == ABANDONED_AFTER_SECONDS
        assert health["recorder"] is not None
        assert "enqueued" in health["recorder"]
        assert health["recorder"]["enabled"] is False
    finally:
        set_trace_recorder(None)


def test_skips_blank_lines_and_non_object_json(tmp_path: Path) -> None:
    path = tmp_path / "agent_traces.jsonl"
    good = _event(event_name="trace.completed", sequence=0, status="completed")
    path.write_text(
        "\n"
        + json.dumps(good)
        + "\n\n"
        + "null\n"
        + "[1,2]\n"
        + "not-json\n",
        encoding="utf-8",
    )
    q = TraceQuery(path)
    health = q.health()
    assert health["parsed_lines"] == 1
    assert health["malformed_lines"] == 3
    detail = q.get_trace("trace-1")
    assert detail is not None
    assert detail["summary"]["status"] == "completed"


def test_list_newest_first(tmp_path: Path) -> None:
    path = tmp_path / "agent_traces.jsonl"
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 6, 1, tzinfo=UTC)
    lines = [
        _event(
            event_name="trace.completed",
            trace_id="older",
            sequence=0,
            status="completed",
            timestamp=t1.isoformat().replace("+00:00", "Z"),
        ),
        _event(
            event_name="trace.completed",
            trace_id="newer",
            sequence=0,
            status="completed",
            timestamp=t2.isoformat().replace("+00:00", "Z"),
        ),
    ]
    path.write_text("\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")
    listed = TraceQuery(path).list_traces(limit=10)
    assert [s["trace_id"] for s in listed] == ["newer", "older"]


def test_attempts_models_approvals_delivery_groups(tmp_path: Path) -> None:
    path = tmp_path / "agent_traces.jsonl"
    base = datetime(2026, 7, 22, 8, 0, 0, tzinfo=UTC)
    lines = [
        _event(
            event_name="trace.started",
            sequence=0,
            status="started",
            attempt_id="a1",
            timestamp=base.isoformat().replace("+00:00", "Z"),
        ),
        _event(
            event_name="agent.attempt.started",
            sequence=1,
            status="started",
            attempt_id="a1",
            timestamp=(base + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
        ),
        _event(
            event_name="approval.requested",
            sequence=2,
            status="suspended",
            attempt_id="a1",
            tool_call_id="tc-x",
            timestamp=(base + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
            attributes={"player_name": "alex", "decision": None},
        ),
        _event(
            event_name="trace.suspended",
            sequence=3,
            status="suspended",
            attempt_id="a1",
            timestamp=(base + timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
        ),
        _event(
            event_name="approval.decided",
            sequence=4,
            status="accepted",
            attempt_id="a1",
            tool_call_id="tc-x",
            timestamp=(base + timedelta(seconds=4)).isoformat().replace("+00:00", "Z"),
            attributes={"player_name": "alex", "decision": "approve"},
        ),
        _event(
            event_name="agent.attempt.resumed",
            sequence=5,
            status="started",
            attempt_id="a2",
            timestamp=(base + timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
        ),
        _event(
            event_name="delivery.completed",
            sequence=6,
            status="completed",
            attempt_id="a2",
            duration_ms=12,
            timestamp=(base + timedelta(seconds=6)).isoformat().replace("+00:00", "Z"),
            attributes={
                "player_name": "alex",
                "target": "alex",
                "delivery_type": "tellraw",
                "chunk_count": 2,
                "byte_count": 40,
            },
        ),
        _event(
            event_name="trace.completed",
            sequence=7,
            status="completed",
            attempt_id="a2",
            timestamp=(base + timedelta(seconds=7)).isoformat().replace("+00:00", "Z"),
        ),
    ]
    path.write_text("\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")
    detail = TraceQuery(path).get_trace("trace-1")
    assert detail is not None
    assert len(detail["attempts"]) == 2
    assert detail["attempts"][0]["attempt_id"] == "a1"
    assert detail["attempts"][1]["attempt_id"] == "a2"
    assert len(detail["approvals"]) == 2
    assert detail["approvals"][1]["decision"] == "approve"
    assert len(detail["delivery"]) == 1
    assert detail["delivery"][0]["chunk_count"] == 2
    assert detail["summary"]["status"] == "completed"
