"""CLI tests for agent trace audit commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

import cli as cli_module
from cli import cli


def _event(
    *,
    event_name: str,
    trace_id: str = "trace-1",
    sequence: int = 0,
    status: str = "info",
    attempt_id: str = "attempt-1",
    timestamp: str | None = None,
    duration_ms: int | None = None,
    attributes: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
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
        "event_id": f"ev-{trace_id}-{sequence}-{event_name}",
        "event_name": event_name,
        "timestamp": timestamp
        or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "sequence": sequence,
        "trace_id": trace_id,
        "run_id": trace_id,
        "attempt_id": attempt_id,
        "status": status,
        "attributes": attrs,
    }
    if duration_ms is not None:
        obj["duration_ms"] = duration_ms
    if payload is not None:
        obj["payload"] = payload
    return obj


def write_trace_fixture(path: Path) -> None:
    """Write a completed alex trace plus one malformed journal line."""
    base = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)
    events = [
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
            payload={"messages": [{"role": "user", "content": "hello"}]},
        ),
        _event(
            event_name="trace.completed",
            sequence=3,
            status="completed",
            duration_ms=3000,
            timestamp=(base + timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
            payload={"final_response": "ok"},
        ),
    ]
    lines = [json.dumps(e, ensure_ascii=False) for e in events]
    lines.append("{not-json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def trace_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    journal = tmp_path / "agent_traces.jsonl"
    write_trace_fixture(journal)
    settings = SimpleNamespace(
        agent_trace_path=str(journal),
        agent_trace_enabled=True,
        agent_trace_include_content=False,
        agent_trace_api_host="127.0.0.1",
        agent_trace_api_port=8787,
    )
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    return journal


def test_trace_list_outputs_summary_rows(cli_runner: CliRunner, trace_fixture: Path) -> None:
    result = cli_runner.invoke(cli, ["trace", "list", "--recent", "5"])
    assert result.exit_code == 0, result.output
    assert "TRACE" in result.output
    assert "completed" in result.output
    assert "trace-1" in result.output


def test_trace_list_filters_by_status_and_player(
    cli_runner: CliRunner, trace_fixture: Path
) -> None:
    result = cli_runner.invoke(
        cli,
        ["trace", "list", "--recent", "20", "--status", "completed", "--player", "alex"],
    )
    assert result.exit_code == 0, result.output
    assert "trace-1" in result.output

    empty = cli_runner.invoke(
        cli,
        ["trace", "list", "--status", "failed", "--player", "alex"],
    )
    assert empty.exit_code == 0, empty.output
    assert "trace-1" not in empty.output


def test_trace_list_rejects_non_positive_recent(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(cli, ["trace", "list", "--recent", "0"])
    assert result.exit_code != 0
    assert "Invalid value for '--recent'" in result.output


def test_trace_show_json_returns_complete_event_tree(
    cli_runner: CliRunner, trace_fixture: Path
) -> None:
    result = cli_runner.invoke(cli, ["trace", "show", "trace-1", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["summary"]["trace_id"] == "trace-1"
    assert payload["summary"]["status"] == "completed"
    assert isinstance(payload["events"], list)
    assert len(payload["events"]) >= 2
    assert "attempts" in payload
    assert "models" in payload
    assert "tools" in payload


def test_trace_show_human_includes_timeline(
    cli_runner: CliRunner, trace_fixture: Path
) -> None:
    result = cli_runner.invoke(cli, ["trace", "show", "trace-1"])
    assert result.exit_code == 0, result.output
    assert "trace-1" in result.output
    assert "completed" in result.output
    assert "trace.started" in result.output or "timeline" in result.output.lower()


def test_trace_show_missing_id_raises(cli_runner: CliRunner, trace_fixture: Path) -> None:
    result = cli_runner.invoke(cli, ["trace", "show", "missing-trace-id"])
    assert result.exit_code != 0
    assert "missing-trace-id" in result.output or "not found" in result.output.lower()


def test_trace_health_reports_malformed_lines(
    cli_runner: CliRunner, trace_fixture: Path
) -> None:
    result = cli_runner.invoke(cli, ["trace", "health"])
    assert result.exit_code == 0, result.output
    assert "malformed_lines" in result.output


def test_trace_serve_uses_settings_defaults(
    cli_runner: CliRunner, trace_fixture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serve should construct TraceAPIServer with settings defaults and static_dir."""
    import services.agent.trace_api as trace_api_module

    captured: dict[str, Any] = {}

    class FakeServer:
        def __init__(self, host: str, port: int, query: Any, static_dir: Any) -> None:
            captured["host"] = host
            captured["port"] = port
            captured["query_path"] = str(query.path)
            captured["static_dir"] = Path(static_dir)

        def serve_forever(self) -> None:
            return None

        def shutdown(self) -> None:
            return None

    monkeypatch.setattr(trace_api_module, "TraceAPIServer", FakeServer)

    result = cli_runner.invoke(cli, ["trace", "serve"])
    assert result.exit_code == 0, result.output
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8787
    assert captured["query_path"] == str(trace_fixture)
    assert captured["static_dir"].name == "trace"
    assert captured["static_dir"].parent.name == "web"
