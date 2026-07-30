"""End-to-end Agent Trace fixture matrix (offline journal + TraceQuery + API).

Covers the acceptance scenarios without live providers, Minecraft, or WebSocket:
no-tool, single-tool, approval resume, deny, failure, cancel, multiplayer, privacy.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from services.agent.trace_api import TraceAPIServer
from services.agent.trace_query import TraceQuery

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TERMINAL_EVENT_NAMES = frozenset(
    {
        "trace.completed",
        "trace.failed",
        "trace.cancelled",
        "trace.expired",
        "trace.abandoned",
    }
)


def _ts(base: datetime, seconds: float = 0.0) -> str:
    return (base + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _event(
    *,
    event_name: str,
    trace_id: str,
    sequence: int,
    status: str = "info",
    attempt_id: str = "attempt-1",
    run_id: str | None = None,
    player_name: str = "alex",
    connection_id: str = "conn-shared",
    conversation_id: str = "default",
    message_id: str | None = None,
    timestamp: str | None = None,
    tool_call_id: str | None = None,
    duration_ms: int | None = None,
    attributes: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    base: datetime | None = None,
    seconds: float = 0.0,
) -> dict[str, Any]:
    when = timestamp
    if when is None:
        when = _ts(base or datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC), seconds)
    attrs: dict[str, Any] = {
        "player_name": player_name,
        "connection_id": connection_id,
        "conversation_id": conversation_id,
        "message_id": message_id or f"msg-{trace_id}",
    }
    if attributes:
        attrs.update(attributes)
    obj: dict[str, Any] = {
        "schema_version": 1,
        "event_id": f"ev-{trace_id}-{sequence}-{event_name.replace('.', '_')}",
        "event_name": event_name,
        "timestamp": when,
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
    return obj


def write_journal(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8",
    )


def write_fixture(path: Path, *events: dict[str, Any]) -> str:
    """Write events to journal; return the first event's trace_id."""
    if not events:
        raise ValueError("write_fixture requires at least one event")
    write_journal(path, list(events))
    return str(events[0]["trace_id"])


def terminal_events(detail: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        e
        for e in detail["events"]
        if e.get("event_name") in _TERMINAL_EVENT_NAMES
    ]


def assert_single_terminal(
    detail: dict[str, Any],
    *,
    expected_status: str,
    expected_name: str | None = None,
) -> dict[str, Any]:
    terminals = terminal_events(detail)
    assert len(terminals) == 1, (
        f"expected exactly one terminal event, got "
        f"{[t.get('event_name') for t in terminals]}"
    )
    term = terminals[0]
    if expected_name is not None:
        assert term["event_name"] == expected_name
    assert detail["summary"]["status"] == expected_status
    return term


def event_names(detail: dict[str, Any]) -> list[str]:
    return [e["event_name"] for e in detail["events"]]


# ---------------------------------------------------------------------------
# Scenario builders (return ordered event lists)
# ---------------------------------------------------------------------------


def build_no_tool(
    *,
    trace_id: str = "trace-no-tool",
    player_name: str = "alex",
    connection_id: str = "conn-shared",
    include_payload: bool = True,
    base: datetime | None = None,
) -> list[dict[str, Any]]:
    b = base or datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)
    user_msg = "hello there"
    final = "hi player"
    payload_user = {"content": user_msg, "user_message": user_msg} if include_payload else None
    payload_model = {
        "messages": [{"role": "assistant", "content": final}],
        "content": final,
        "model_name": "deepseek-chat",
        "provider": "deepseek",
    } if include_payload else None
    payload_final = {"content": final, "final_response": final} if include_payload else None
    return [
        _event(
            event_name="trace.started",
            trace_id=trace_id,
            sequence=0,
            status="started",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=0,
            payload=payload_user,
        ),
        _event(
            event_name="request.accepted",
            trace_id=trace_id,
            sequence=1,
            status="accepted",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=0.1,
        ),
        _event(
            event_name="agent.attempt.started",
            trace_id=trace_id,
            sequence=2,
            status="started",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=0.2,
        ),
        _event(
            event_name="model.request.completed",
            trace_id=trace_id,
            sequence=3,
            status="completed",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=1.0,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "provider": "deepseek",
                "model_name": "deepseek-chat",
                "finish_reason": "stop",
            },
            payload=payload_model,
        ),
        _event(
            event_name="delivery.completed",
            trace_id=trace_id,
            sequence=4,
            status="completed",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=1.2,
            duration_ms=15,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "target": player_name,
                "delivery_type": "tellraw",
                "chunk_count": 1,
                "byte_count": len(final),
            },
        ),
        _event(
            event_name="trace.completed",
            trace_id=trace_id,
            sequence=5,
            status="completed",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=1.5,
            duration_ms=1500,
            payload=payload_final,
        ),
    ]


def build_single_tool(
    *,
    trace_id: str = "trace-single-tool",
    player_name: str = "alex",
    connection_id: str = "conn-shared",
    include_payload: bool = True,
    base: datetime | None = None,
) -> list[dict[str, Any]]:
    b = base or datetime(2026, 7, 22, 12, 5, 0, tzinfo=UTC)
    tool_args = {"q": "creeper"}
    tool_result = {"hits": 2, "title": "Creeper"}
    final = "苦力怕会爆炸"
    p_args = {"tool_args": tool_args, "parameters": tool_args} if include_payload else None
    p_result = {"tool_result": tool_result, "result": tool_result} if include_payload else None
    p_m1 = {
        "messages": [{"role": "assistant", "content": None, "tool_calls": ["tc-1"]}],
        "provider": "deepseek",
        "model_name": "deepseek-chat",
        "finish_reason": "tool_calls",
    } if include_payload else None
    p_m2 = {
        "messages": [{"role": "assistant", "content": final}],
        "content": final,
        "provider": "deepseek",
        "model_name": "deepseek-chat",
        "finish_reason": "stop",
    } if include_payload else None
    p_final = {"content": final, "final_response": final} if include_payload else None
    return [
        _event(
            event_name="trace.started",
            trace_id=trace_id,
            sequence=0,
            status="started",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=0,
        ),
        _event(
            event_name="request.accepted",
            trace_id=trace_id,
            sequence=1,
            status="accepted",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=0.1,
        ),
        _event(
            event_name="agent.attempt.started",
            trace_id=trace_id,
            sequence=2,
            status="started",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=0.2,
        ),
        _event(
            event_name="model.request.completed",
            trace_id=trace_id,
            sequence=3,
            status="completed",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=1.0,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "provider": "deepseek",
                "model_name": "deepseek-chat",
                "finish_reason": "tool_calls",
            },
            payload=p_m1,
        ),
        _event(
            event_name="tool.proposed",
            trace_id=trace_id,
            sequence=4,
            status="info",
            player_name=player_name,
            connection_id=connection_id,
            tool_call_id="tc-1",
            base=b,
            seconds=1.1,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "tool_name": "mcwiki_search",
            },
            payload=p_args,
        ),
        _event(
            event_name="policy.decided",
            trace_id=trace_id,
            sequence=5,
            status="accepted",
            player_name=player_name,
            connection_id=connection_id,
            tool_call_id="tc-1",
            base=b,
            seconds=1.15,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "tool_name": "mcwiki_search",
                "decision": "allow",
            },
            payload={"decision": "allow", "reason": "auto-allow"} if include_payload else None,
        ),
        _event(
            event_name="tool.execution.completed",
            trace_id=trace_id,
            sequence=6,
            status="succeeded",
            player_name=player_name,
            connection_id=connection_id,
            tool_call_id="tc-1",
            base=b,
            seconds=1.5,
            duration_ms=120,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "tool_name": "mcwiki_search",
                "execution_status": "succeeded",
            },
            payload=p_result,
        ),
        _event(
            event_name="model.request.completed",
            trace_id=trace_id,
            sequence=7,
            status="completed",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=2.0,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "provider": "deepseek",
                "model_name": "deepseek-chat",
                "finish_reason": "stop",
            },
            payload=p_m2,
        ),
        _event(
            event_name="delivery.completed",
            trace_id=trace_id,
            sequence=8,
            status="completed",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=2.1,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "target": player_name,
                "delivery_type": "tellraw",
                "chunk_count": 1,
                "byte_count": 20,
            },
        ),
        _event(
            event_name="trace.completed",
            trace_id=trace_id,
            sequence=9,
            status="completed",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=2.2,
            duration_ms=2200,
            payload=p_final,
        ),
    ]


def build_approval(
    *,
    trace_id: str = "trace-approval",
    player_name: str = "alex",
    connection_id: str = "conn-shared",
    include_payload: bool = True,
    base: datetime | None = None,
) -> list[dict[str, Any]]:
    b = base or datetime(2026, 7, 22, 12, 10, 0, tzinfo=UTC)
    final = "已设置白天"
    p_final = {"content": final, "final_response": final} if include_payload else None
    return [
        _event(
            event_name="trace.started",
            trace_id=trace_id,
            sequence=0,
            status="started",
            attempt_id="attempt-1",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=0,
        ),
        _event(
            event_name="agent.attempt.started",
            trace_id=trace_id,
            sequence=1,
            status="started",
            attempt_id="attempt-1",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=0.2,
        ),
        _event(
            event_name="model.request.completed",
            trace_id=trace_id,
            sequence=2,
            status="completed",
            attempt_id="attempt-1",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=1.0,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "provider": "deepseek",
                "model_name": "deepseek-chat",
                "finish_reason": "tool_calls",
            },
            payload={
                "messages": [{"role": "assistant", "content": None}],
                "finish_reason": "tool_calls",
            }
            if include_payload
            else None,
        ),
        _event(
            event_name="approval.requested",
            trace_id=trace_id,
            sequence=3,
            status="suspended",
            attempt_id="attempt-1",
            player_name=player_name,
            connection_id=connection_id,
            tool_call_id="tc-ap",
            base=b,
            seconds=1.1,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "tool_name": "run_minecraft_command",
                "decision": None,
            },
        ),
        _event(
            event_name="trace.suspended",
            trace_id=trace_id,
            sequence=4,
            status="suspended",
            attempt_id="attempt-1",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=1.2,
        ),
        _event(
            event_name="approval.decided",
            trace_id=trace_id,
            sequence=5,
            status="accepted",
            attempt_id="attempt-1",
            player_name=player_name,
            connection_id=connection_id,
            tool_call_id="tc-ap",
            base=b,
            seconds=5.0,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "tool_name": "run_minecraft_command",
                "decision": "approve",
            },
            payload={"decision": "approve"} if include_payload else None,
        ),
        _event(
            event_name="agent.attempt.resumed",
            trace_id=trace_id,
            sequence=6,
            status="started",
            attempt_id="attempt-2",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=5.1,
        ),
        _event(
            event_name="tool.execution.completed",
            trace_id=trace_id,
            sequence=7,
            status="succeeded",
            attempt_id="attempt-2",
            player_name=player_name,
            connection_id=connection_id,
            tool_call_id="tc-ap",
            base=b,
            seconds=5.5,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "tool_name": "run_minecraft_command",
                "execution_status": "succeeded",
            },
        ),
        _event(
            event_name="model.request.completed",
            trace_id=trace_id,
            sequence=8,
            status="completed",
            attempt_id="attempt-2",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=6.0,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "provider": "deepseek",
                "model_name": "deepseek-chat",
                "finish_reason": "stop",
            },
            payload={
                "messages": [{"role": "assistant", "content": final}],
                "content": final,
            }
            if include_payload
            else None,
        ),
        _event(
            event_name="delivery.completed",
            trace_id=trace_id,
            sequence=9,
            status="completed",
            attempt_id="attempt-2",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=6.1,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "target": player_name,
                "delivery_type": "tellraw",
                "chunk_count": 1,
                "byte_count": 12,
            },
        ),
        _event(
            event_name="trace.completed",
            trace_id=trace_id,
            sequence=10,
            status="completed",
            attempt_id="attempt-2",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=6.2,
            duration_ms=6200,
            payload=p_final,
        ),
    ]


def build_deny(
    *,
    trace_id: str = "trace-deny",
    player_name: str = "alex",
    connection_id: str = "conn-shared",
    include_payload: bool = True,
    base: datetime | None = None,
) -> list[dict[str, Any]]:
    b = base or datetime(2026, 7, 22, 12, 15, 0, tzinfo=UTC)
    final = "该命令被策略拒绝"
    p_final = {"content": final, "final_response": final} if include_payload else None
    return [
        _event(
            event_name="trace.started",
            trace_id=trace_id,
            sequence=0,
            status="started",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=0,
        ),
        _event(
            event_name="agent.attempt.started",
            trace_id=trace_id,
            sequence=1,
            status="started",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=0.2,
        ),
        _event(
            event_name="model.request.completed",
            trace_id=trace_id,
            sequence=2,
            status="completed",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=1.0,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "provider": "deepseek",
                "model_name": "deepseek-chat",
                "finish_reason": "tool_calls",
            },
        ),
        _event(
            event_name="tool.proposed",
            trace_id=trace_id,
            sequence=3,
            status="info",
            player_name=player_name,
            connection_id=connection_id,
            tool_call_id="tc-deny",
            base=b,
            seconds=1.1,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "tool_name": "run_minecraft_command",
            },
            payload={"tool_args": {"command": "op @s"}, "parameters": {"command": "op @s"}}
            if include_payload
            else None,
        ),
        _event(
            event_name="policy.decided",
            trace_id=trace_id,
            sequence=4,
            status="denied",
            player_name=player_name,
            connection_id=connection_id,
            tool_call_id="tc-deny",
            base=b,
            seconds=1.15,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "tool_name": "run_minecraft_command",
                "decision": "deny",
            },
            payload={"decision": "deny", "reason": "dangerous command"}
            if include_payload
            else None,
        ),
        _event(
            event_name="tool.execution.completed",
            trace_id=trace_id,
            sequence=5,
            status="not_executed",
            player_name=player_name,
            connection_id=connection_id,
            tool_call_id="tc-deny",
            base=b,
            seconds=1.2,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "tool_name": "run_minecraft_command",
                "execution_status": "not_executed",
            },
        ),
        _event(
            event_name="model.request.completed",
            trace_id=trace_id,
            sequence=6,
            status="completed",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=1.8,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "provider": "deepseek",
                "model_name": "deepseek-chat",
                "finish_reason": "stop",
            },
            payload={"messages": [{"role": "assistant", "content": final}], "content": final}
            if include_payload
            else None,
        ),
        _event(
            event_name="trace.completed",
            trace_id=trace_id,
            sequence=7,
            status="completed",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=2.0,
            duration_ms=2000,
            payload=p_final,
        ),
    ]


def build_failure(
    *,
    trace_id: str = "trace-failure",
    player_name: str = "alex",
    connection_id: str = "conn-shared",
    include_payload: bool = True,
    base: datetime | None = None,
) -> list[dict[str, Any]]:
    b = base or datetime(2026, 7, 22, 12, 20, 0, tzinfo=UTC)
    return [
        _event(
            event_name="trace.started",
            trace_id=trace_id,
            sequence=0,
            status="started",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=0,
        ),
        _event(
            event_name="agent.attempt.started",
            trace_id=trace_id,
            sequence=1,
            status="started",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=0.2,
        ),
        _event(
            event_name="model.request.completed",
            trace_id=trace_id,
            sequence=2,
            status="failed",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=1.0,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "provider": "deepseek",
                "model_name": "deepseek-chat",
                "error_kind": "PROVIDER_ERROR",
            },
            payload={"error_message": "upstream 500"} if include_payload else None,
        ),
        _event(
            event_name="trace.failed",
            trace_id=trace_id,
            sequence=3,
            status="failed",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=1.1,
            duration_ms=1100,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "error_kind": "PROVIDER_ERROR",
            },
            payload={"error_message": "upstream 500"} if include_payload else None,
        ),
    ]


def build_cancel(
    *,
    trace_id: str = "trace-cancel",
    player_name: str = "alex",
    connection_id: str = "conn-shared",
    include_payload: bool = False,
    base: datetime | None = None,
) -> list[dict[str, Any]]:
    b = base or datetime(2026, 7, 22, 12, 25, 0, tzinfo=UTC)
    return [
        _event(
            event_name="trace.started",
            trace_id=trace_id,
            sequence=0,
            status="started",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=0,
        ),
        _event(
            event_name="agent.attempt.started",
            trace_id=trace_id,
            sequence=1,
            status="started",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=0.2,
        ),
        _event(
            event_name="model.request.completed",
            trace_id=trace_id,
            sequence=2,
            status="completed",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=0.8,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "provider": "deepseek",
                "model_name": "deepseek-chat",
            },
        ),
        _event(
            event_name="trace.cancelled",
            trace_id=trace_id,
            sequence=3,
            status="cancelled",
            player_name=player_name,
            connection_id=connection_id,
            base=b,
            seconds=0.9,
            duration_ms=900,
            attributes={
                "player_name": player_name,
                "connection_id": connection_id,
                "conversation_id": "default",
                "message_id": f"msg-{trace_id}",
                "reason": "player_disconnect",
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Matrix tests
# ---------------------------------------------------------------------------


def test_fixture_no_tool_lifecycle(tmp_path: Path) -> None:
    path = tmp_path / "agent_traces.jsonl"
    events = build_no_tool()
    tid = write_fixture(path, *events)
    detail = TraceQuery(path).get_trace(tid)
    assert detail is not None
    names = event_names(detail)
    assert names == [
        "trace.started",
        "request.accepted",
        "agent.attempt.started",
        "model.request.completed",
        "delivery.completed",
        "trace.completed",
    ]
    assert_single_terminal(detail, expected_status="completed", expected_name="trace.completed")
    assert detail["summary"]["player_name"] == "alex"
    assert detail["summary"]["connection_id"] == "conn-shared"
    assert detail["summary"]["attempt_count"] == 1
    assert len(detail["models"]) == 1
    assert len(detail["delivery"]) == 1
    assert detail["delivery"][0]["delivery_type"] == "tellraw"
    assert detail["events"][-1]["payload"]["final_response"] == "hi player"


def test_fixture_single_tool_chain(tmp_path: Path) -> None:
    path = tmp_path / "agent_traces.jsonl"
    events = build_single_tool()
    tid = write_fixture(path, *events)
    detail = TraceQuery(path).get_trace(tid)
    assert detail is not None
    names = event_names(detail)
    assert "model.request.completed" in names
    assert "tool.proposed" in names
    assert "policy.decided" in names
    assert "tool.execution.completed" in names
    # model -> proposed -> policy -> execution -> model
    i_m1 = names.index("model.request.completed")
    i_prop = names.index("tool.proposed")
    i_pol = names.index("policy.decided")
    i_exec = names.index("tool.execution.completed")
    # second model.request.completed
    model_idxs = [i for i, n in enumerate(names) if n == "model.request.completed"]
    assert len(model_idxs) == 2
    assert i_m1 < i_prop < i_pol < i_exec < model_idxs[1]
    assert_single_terminal(detail, expected_status="completed")
    assert len(detail["tools"]) == 1
    tool = detail["tools"][0]
    assert tool["tool_name"] == "mcwiki_search"
    assert tool["execution_status"] == "succeeded"
    assert tool["tool_args"]["q"] == "creeper"
    assert tool["tool_result"]["hits"] == 2
    assert detail["events"][-1]["payload"]["content"] == "苦力怕会爆炸"


def test_fixture_approval_resume_two_attempts(tmp_path: Path) -> None:
    path = tmp_path / "agent_traces.jsonl"
    events = build_approval()
    tid = write_fixture(path, *events)
    detail = TraceQuery(path).get_trace(tid)
    assert detail is not None
    names = event_names(detail)
    assert "agent.attempt.started" in names
    assert "approval.requested" in names
    assert "trace.suspended" in names
    assert "approval.decided" in names
    assert "agent.attempt.resumed" in names
    assert names.index("approval.requested") < names.index("trace.suspended")
    assert names.index("trace.suspended") < names.index("approval.decided")
    assert names.index("approval.decided") < names.index("agent.attempt.resumed")
    assert_single_terminal(detail, expected_status="completed", expected_name="trace.completed")
    assert detail["summary"]["attempt_count"] == 2
    assert detail["summary"]["attempt_ids"] == ["attempt-1", "attempt-2"]
    assert len(detail["attempts"]) == 2
    assert detail["attempts"][0]["attempt_id"] == "attempt-1"
    assert detail["attempts"][1]["attempt_id"] == "attempt-2"
    # Terminal event is on attempt-2
    term = terminal_events(detail)[0]
    assert term["attempt_id"] == "attempt-2"
    assert len(detail["approvals"]) >= 2
    assert detail["approvals"][-1]["decision"] == "approve"


def test_fixture_deny_not_executed(tmp_path: Path) -> None:
    path = tmp_path / "agent_traces.jsonl"
    events = build_deny()
    tid = write_fixture(path, *events)
    detail = TraceQuery(path).get_trace(tid)
    assert detail is not None
    names = event_names(detail)
    assert "policy.decided" in names
    assert "tool.execution.completed" in names
    assert names.index("policy.decided") < names.index("tool.execution.completed")
    assert_single_terminal(detail, expected_status="completed")
    tools = detail["tools"]
    assert len(tools) == 1
    tool = tools[0]
    assert tool["execution_status"] == "not_executed"
    # policy.decided status denied
    policy = next(e for e in detail["events"] if e["event_name"] == "policy.decided")
    assert policy["status"] == "denied"
    assert policy["attributes"]["decision"] == "deny"
    exec_ev = next(
        e for e in detail["events"] if e["event_name"] == "tool.execution.completed"
    )
    assert exec_ev["status"] == "not_executed"


def test_fixture_failure_terminal(tmp_path: Path) -> None:
    path = tmp_path / "agent_traces.jsonl"
    events = build_failure()
    tid = write_fixture(path, *events)
    detail = TraceQuery(path).get_trace(tid)
    assert detail is not None
    assert_single_terminal(detail, expected_status="failed", expected_name="trace.failed")
    assert detail["summary"]["player_name"] == "alex"
    failed = terminal_events(detail)[0]
    assert failed["attributes"]["error_kind"] == "PROVIDER_ERROR"


def test_fixture_cancel_terminal(tmp_path: Path) -> None:
    path = tmp_path / "agent_traces.jsonl"
    events = build_cancel()
    tid = write_fixture(path, *events)
    detail = TraceQuery(path).get_trace(tid)
    assert detail is not None
    assert_single_terminal(detail, expected_status="cancelled", expected_name="trace.cancelled")
    assert detail["summary"]["last_event_name"] == "trace.cancelled"


def test_fixture_multiplayer_isolation(tmp_path: Path) -> None:
    """Same connection_id, two player names → separate trace IDs and payloads."""
    path = tmp_path / "agent_traces.jsonl"
    conn = "conn-world-1"
    alex_events = build_no_tool(
        trace_id="trace-mp-alex",
        player_name="alex",
        connection_id=conn,
        base=datetime(2026, 7, 22, 13, 0, 0, tzinfo=UTC),
    )
    # Override final payload to prove isolation
    for e in alex_events:
        if e["event_name"] == "trace.completed":
            e["payload"] = {"content": "reply-for-alex", "final_response": "reply-for-alex"}
        if e["event_name"] == "model.request.completed":
            e["payload"] = {
                "messages": [{"role": "assistant", "content": "reply-for-alex"}],
                "content": "reply-for-alex",
            }

    steve_events = build_single_tool(
        trace_id="trace-mp-steve",
        player_name="steve",
        connection_id=conn,
        base=datetime(2026, 7, 22, 13, 1, 0, tzinfo=UTC),
    )
    for e in steve_events:
        if e["event_name"] == "trace.completed":
            e["payload"] = {"content": "reply-for-steve", "final_response": "reply-for-steve"}

    write_journal(path, alex_events + steve_events)
    q = TraceQuery(path)

    alex = q.get_trace("trace-mp-alex")
    steve = q.get_trace("trace-mp-steve")
    assert alex is not None and steve is not None
    assert alex["summary"]["trace_id"] != steve["summary"]["trace_id"]
    assert alex["summary"]["connection_id"] == steve["summary"]["connection_id"] == conn
    assert alex["summary"]["player_name"] == "alex"
    assert steve["summary"]["player_name"] == "steve"
    assert alex["events"][-1]["payload"]["content"] == "reply-for-alex"
    assert steve["events"][-1]["payload"]["content"] == "reply-for-steve"
    # No cross-contamination of player identity in events
    assert all(
        e["attributes"]["player_name"] == "alex" for e in alex["events"]
    )
    assert all(
        e["attributes"]["player_name"] == "steve" for e in steve["events"]
    )
    # List filters isolate players
    only_alex = q.list_traces(player="alex", limit=50)
    only_steve = q.list_traces(player="steve", limit=50)
    assert {s["trace_id"] for s in only_alex} == {"trace-mp-alex"}
    assert {s["trace_id"] for s in only_steve} == {"trace-mp-steve"}
    assert_single_terminal(alex, expected_status="completed")
    assert_single_terminal(steve, expected_status="completed")


def test_fixture_privacy_content_gating(tmp_path: Path) -> None:
    """disabled omits payload; enabled preserves user/LLM/tool/final content verbatim."""
    path_on = tmp_path / "with_content.jsonl"
    path_off = tmp_path / "without_content.jsonl"

    user_text = "VERBATIM-USER-PROMPT-xyz"
    tool_arg_q = "VERBATIM-TOOL-ARG-q"
    final_text = "VERBATIM-FINAL-RESPONSE-abc"
    tool_result_body = {"hits": 3, "snippet": "VERBATIM-TOOL-RESULT"}

    # Enabled: full payloads
    events_on = build_single_tool(trace_id="trace-priv-on", include_payload=True)
    for e in events_on:
        if e["event_name"] == "tool.proposed":
            e["payload"] = {
                "tool_args": {"q": tool_arg_q},
                "parameters": {"q": tool_arg_q},
            }
        if e["event_name"] == "tool.execution.completed":
            e["payload"] = {
                "tool_result": tool_result_body,
                "result": tool_result_body,
            }
        if e["event_name"] == "trace.completed":
            e["payload"] = {"content": final_text, "final_response": final_text}
        if e["event_name"] == "model.request.completed" and e["sequence"] == 7:
            e["payload"] = {
                "messages": [{"role": "assistant", "content": final_text}],
                "content": final_text,
            }
        if e["event_name"] == "trace.started":
            e["payload"] = {"content": user_text, "user_message": user_text}
    write_journal(path_on, events_on)

    # Disabled: same structure, no payload keys
    events_off = build_single_tool(trace_id="trace-priv-off", include_payload=False)
    write_journal(path_off, events_off)

    on = TraceQuery(path_on).get_trace("trace-priv-on")
    off = TraceQuery(path_off).get_trace("trace-priv-off")
    assert on is not None and off is not None

    # Enabled preserves verbatim
    started = next(e for e in on["events"] if e["event_name"] == "trace.started")
    assert started["payload"]["user_message"] == user_text
    proposed = next(e for e in on["events"] if e["event_name"] == "tool.proposed")
    assert proposed["payload"]["tool_args"]["q"] == tool_arg_q
    exec_ev = next(
        e for e in on["events"] if e["event_name"] == "tool.execution.completed"
    )
    assert exec_ev["payload"]["tool_result"] == tool_result_body
    completed = next(e for e in on["events"] if e["event_name"] == "trace.completed")
    assert completed["payload"]["content"] == final_text
    raw_on = path_on.read_text(encoding="utf-8")
    assert user_text in raw_on
    assert final_text in raw_on
    assert tool_arg_q in raw_on

    # Disabled: no payloads and secret strings absent from journal
    assert all(e.get("payload") is None for e in off["events"])
    raw_off = path_off.read_text(encoding="utf-8")
    assert "苦力怕" not in raw_off  # default single-tool final text when include_payload
    assert "creeper" not in raw_off
    assert "payload" not in raw_off or '"payload": null' not in raw_off
    # Lifecycle still intact
    assert_single_terminal(off, expected_status="completed")
    assert_single_terminal(on, expected_status="completed")
    names_off = event_names(off)
    assert "tool.proposed" in names_off
    assert "trace.completed" in names_off


def test_matrix_combined_journal_unique_terminals(tmp_path: Path) -> None:
    """All matrix scenarios in one journal: each has exactly one terminal."""
    path = tmp_path / "matrix.jsonl"
    builders = [
        build_no_tool(trace_id="m-no-tool"),
        build_single_tool(trace_id="m-single"),
        build_approval(trace_id="m-approval"),
        build_deny(trace_id="m-deny"),
        build_failure(trace_id="m-fail"),
        build_cancel(trace_id="m-cancel"),
    ]
    all_events: list[dict[str, Any]] = []
    for batch in builders:
        all_events.extend(batch)
    write_journal(path, all_events)

    q = TraceQuery(path)
    expected = {
        "m-no-tool": "completed",
        "m-single": "completed",
        "m-approval": "completed",
        "m-deny": "completed",
        "m-fail": "failed",
        "m-cancel": "cancelled",
    }
    for tid, status in expected.items():
        detail = q.get_trace(tid)
        assert detail is not None, tid
        assert_single_terminal(detail, expected_status=status)
    listed = q.list_traces(limit=50)
    assert {s["trace_id"] for s in listed} == set(expected)
    health = q.health()
    assert health["trace_count"] == 6
    assert health["malformed_lines"] == 0
    assert health["parsed_lines"] == len(all_events)


# ---------------------------------------------------------------------------
# Browser / API path against fixture journal + real web/trace static
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.status = status
        self.body = body
        self.headers = headers
        self._json: Any | None = None

    @property
    def json(self) -> Any:
        if self._json is None:
            self._json = json.loads(self.body.decode("utf-8"))
        return self._json

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")


class _ApiClient:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def get(self, path: str) -> _Response:
        url = self.base + path
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read()
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return _Response(resp.status, body, headers)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            return _Response(exc.code, body, headers)


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_browser_path_api_and_static(tmp_path: Path, repo_root: Path) -> None:
    """Start TraceAPIServer against fixture journal + web/trace; exercise HTTP surface."""
    journal = tmp_path / "agent_traces.jsonl"
    events = build_no_tool(trace_id="trace-1", include_payload=True)
    # Add a second failed trace for list variety
    events.extend(build_failure(trace_id="trace-2", player_name="steve"))
    write_journal(journal, events)

    static_dir = repo_root / "web" / "trace"
    assert (static_dir / "index.html").is_file(), "expected committed web/trace UI"

    # Place a secret outside static root for traversal checks
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-TOKEN-xyz", encoding="utf-8")

    query = TraceQuery(journal)
    server = TraceAPIServer(
        host="127.0.0.1",
        port=0,
        query=query,
        static_dir=static_dir,
    )
    server.start_background()
    try:
        client = _ApiClient(f"http://127.0.0.1:{server.port}")

        health = client.get("/api/health")
        assert health.status == 200
        assert "application/json" in health.headers.get("content-type", "")
        assert health.json["parsed_lines"] >= 2
        assert health.json["trace_count"] == 2
        assert health.headers.get("cache-control") == "no-store"

        traces = client.get("/api/traces")
        assert traces.status == 200
        assert "application/json" in traces.headers.get("content-type", "")
        ids = {t["trace_id"] for t in traces.json["traces"]}
        assert ids == {"trace-1", "trace-2"}

        detail = client.get("/api/traces/trace-1")
        assert detail.status == 200
        assert "application/json" in detail.headers.get("content-type", "")
        assert detail.json["summary"]["status"] == "completed"
        assert detail.json["summary"]["trace_id"] == "trace-1"
        assert "events" in detail.json
        assert "attempts" in detail.json
        assert "models" in detail.json
        assert "tools" in detail.json
        assert "approvals" in detail.json
        assert "delivery" in detail.json

        index = client.get("/")
        assert index.status == 200
        assert "text/html" in index.headers.get("content-type", "") or index.text.strip().startswith(
            "<!DOCTYPE"
        ) or "<html" in index.text.lower()
        assert len(index.text) > 50  # real workbench HTML, no build step

        # Path traversal must not leak secret
        trav = client.get("/assets/../../secret.txt")
        assert trav.status in {403, 404}
        assert b"TOP-SECRET-TOKEN-xyz" not in trav.body
        trav2 = client.get("/assets/%2e%2e/%2e%2e/secret.txt")
        assert trav2.status in {403, 404}
        assert b"TOP-SECRET-TOKEN-xyz" not in trav2.body
    finally:
        server.shutdown()


def test_integration_helper_returns_trace_id(tmp_path: Path) -> None:
    """Fixture helper returns trace_id and TraceQuery reads the same JSONL shape as API."""
    path = tmp_path / "agent_traces.jsonl"
    tid = write_fixture(path, *build_no_tool(trace_id="helper-tid"))
    assert tid == "helper-tid"
    detail = TraceQuery(path).get_trace(tid)
    assert detail is not None
    assert detail["summary"]["trace_id"] == tid
    assert isinstance(detail["events"], list)
    assert detail["events"][0]["schema_version"] == 1
