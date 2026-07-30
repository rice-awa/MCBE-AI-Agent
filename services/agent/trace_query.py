"""Tolerant read-only aggregation over the agent_traces JSONL journal.

Reads line-by-line, skips malformed lines (counted in health), sorts events by
``(sequence, event_id)``, and derives summary status from the last terminal
trace event. Traces without a terminal event are reported as ``running`` when
their last event is younger than ``ABANDONED_AFTER_SECONDS``, otherwise
``abandoned``. Payloads are exposed exactly as recorded — never fabricated.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Traces without a terminal event older than this are shown as abandoned.
# Documented threshold for query-layer inference only (does not rewrite journal).
ABANDONED_AFTER_SECONDS = 30 * 60  # 30 minutes

_TERMINAL_EVENT_NAMES = frozenset(
    {
        "trace.completed",
        "trace.failed",
        "trace.cancelled",
        "trace.expired",
        "trace.abandoned",
    }
)

_TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "expired",
        "abandoned",
    }
)

_MODEL_EVENT_PREFIX = "model."
_TOOL_EVENT_PREFIXES = ("tool.", "policy.")
_APPROVAL_EVENT_PREFIX = "approval."
_DELIVERY_EVENT_PREFIX = "delivery."
_ATTEMPT_EVENT_PREFIX = "agent.attempt."


def _parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _ts_key(value: Any) -> str:
    """Sortable timestamp string; empty sorts earliest."""
    return str(value or "")


def _safe_sequence(value: Any) -> int:
    """Coerce sequence for sort; bad values sort as 0 (event still kept)."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _attr(event: dict[str, Any], key: str, default: Any = None) -> Any:
    attrs = event.get("attributes")
    if isinstance(attrs, dict) and key in attrs:
        return attrs.get(key)
    return default


class TraceQuery:
    """Synchronous, read-only journal query over a JSONL path."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._parsed_lines = 0
        self._malformed_lines = 0
        self._last_event_timestamp: str | None = None
        self._events_by_trace: dict[str, list[dict[str, Any]]] = {}
        self._loaded = False
        self._file_size = 0
        self._lock = threading.RLock()

    def _reset_stats(self) -> None:
        self._parsed_lines = 0
        self._malformed_lines = 0
        self._last_event_timestamp = None
        self._events_by_trace = {}
        self._file_size = 0
        self._loaded = False

    def _load(self) -> None:
        """Reload journal from disk (always fresh for read-only API).

        Caller must hold ``self._lock``.
        """
        self._reset_stats()
        path = self.path
        if not path.exists() or not path.is_file():
            self._loaded = True
            return

        try:
            self._file_size = path.stat().st_size
        except OSError:
            self._file_size = 0

        try:
            # errors="replace" so non-UTF-8 bytes never raise UnicodeDecodeError
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        self._malformed_lines += 1
                        continue
                    if not isinstance(obj, dict):
                        self._malformed_lines += 1
                        continue
                    self._parsed_lines += 1
                    ts = obj.get("timestamp")
                    if isinstance(ts, str) and (
                        self._last_event_timestamp is None
                        or _ts_key(ts) > _ts_key(self._last_event_timestamp)
                    ):
                        self._last_event_timestamp = ts
                    trace_id = obj.get("trace_id")
                    if not isinstance(trace_id, str) or not trace_id:
                        # Valid JSON but missing identity — count as parsed gap-ish;
                        # keep under malformed for health clarity (query cannot group).
                        self._malformed_lines += 1
                        self._parsed_lines -= 1
                        continue
                    self._events_by_trace.setdefault(trace_id, []).append(obj)
        except OSError:
            # Unreadable path: treat as empty with no parse progress
            self._events_by_trace = {}

        for trace_id, events in self._events_by_trace.items():
            events.sort(
                key=lambda e: (
                    _safe_sequence(e.get("sequence")),
                    str(e.get("event_id") or ""),
                )
            )
            self._events_by_trace[trace_id] = events

        self._loaded = True

    def _ensure_loaded(self) -> None:
        # Always re-read so concurrent writers are reflected; cheap for offline tests.
        # Caller must hold ``self._lock``.
        self._load()

    def _derive_status(
        self,
        events: list[dict[str, Any]],
        *,
        now: datetime | None = None,
    ) -> str:
        terminal: dict[str, Any] | None = None
        for event in events:
            name = str(event.get("event_name") or "")
            status = str(event.get("status") or "")
            if name in _TERMINAL_EVENT_NAMES or (
                name.startswith("trace.") and status in _TERMINAL_STATUSES
            ):
                terminal = event
        if terminal is not None:
            status = str(terminal.get("status") or "")
            if status in _TERMINAL_STATUSES:
                return status
            name = str(terminal.get("event_name") or "")
            if name.startswith("trace.") and len(name) > 6:
                suffix = name.split(".", 1)[1]
                if suffix in _TERMINAL_STATUSES:
                    return suffix
            return status or "completed"

        # Non-terminal: running vs abandoned by last event age
        last_ts = None
        for event in reversed(events):
            last_ts = _parse_ts(event.get("timestamp"))
            if last_ts is not None:
                break
        if last_ts is None:
            return "abandoned"
        ref = now or datetime.now(UTC)
        age = (ref - last_ts).total_seconds()
        if age >= ABANDONED_AFTER_SECONDS:
            return "abandoned"
        return "running"

    def _build_summary(
        self,
        trace_id: str,
        events: list[dict[str, Any]],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        first = events[0] if events else {}
        last = events[-1] if events else {}
        status = self._derive_status(events, now=now)

        player_name = None
        connection_id = None
        conversation_id = None
        message_id = None
        for event in events:
            player_name = player_name or _attr(event, "player_name") or event.get("player_name")
            connection_id = (
                connection_id or _attr(event, "connection_id") or event.get("connection_id")
            )
            conversation_id = (
                conversation_id
                or _attr(event, "conversation_id")
                or event.get("conversation_id")
            )
            message_id = message_id or _attr(event, "message_id") or event.get("message_id")

        started_at = first.get("timestamp")
        ended_at = None
        duration_ms = None
        for event in events:
            name = str(event.get("event_name") or "")
            if name in _TERMINAL_EVENT_NAMES or (
                name.startswith("trace.")
                and str(event.get("status") or "") in _TERMINAL_STATUSES
            ):
                ended_at = event.get("timestamp")
                if event.get("duration_ms") is not None:
                    duration_ms = event.get("duration_ms")

        if ended_at is None and status not in {"running"}:
            ended_at = last.get("timestamp")

        attempt_ids: list[str] = []
        for event in events:
            aid = event.get("attempt_id")
            if isinstance(aid, str) and aid and aid not in attempt_ids:
                attempt_ids.append(aid)

        return {
            "trace_id": trace_id,
            "run_id": first.get("run_id") or last.get("run_id") or trace_id,
            "status": status,
            "player_name": player_name,
            "connection_id": connection_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_ms": duration_ms,
            "event_count": len(events),
            "attempt_count": len(attempt_ids),
            "attempt_ids": attempt_ids,
            "last_event_name": last.get("event_name"),
            "last_event_timestamp": last.get("timestamp"),
        }

    def _group_attempts(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for event in events:
            aid = event.get("attempt_id")
            if not isinstance(aid, str) or not aid:
                continue
            if aid not in by_id:
                by_id[aid] = []
                order.append(aid)
            by_id[aid].append(event)

        attempts: list[dict[str, Any]] = []
        for aid in order:
            evs = by_id[aid]
            status = "info"
            for event in evs:
                name = str(event.get("event_name") or "")
                if name.startswith(_ATTEMPT_EVENT_PREFIX) or name.startswith("trace."):
                    status = str(event.get("status") or status)
            attempts.append(
                {
                    "attempt_id": aid,
                    "run_id": evs[0].get("run_id"),
                    "status": status,
                    "event_count": len(evs),
                    "started_at": evs[0].get("timestamp"),
                    "ended_at": evs[-1].get("timestamp"),
                    "events": evs,
                }
            )
        return attempts

    def _group_models(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        for event in events:
            name = str(event.get("event_name") or "")
            if not name.startswith(_MODEL_EVENT_PREFIX):
                continue
            attrs = event.get("attributes") if isinstance(event.get("attributes"), dict) else {}
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else None
            models.append(
                {
                    "event_id": event.get("event_id"),
                    "event_name": name,
                    "sequence": event.get("sequence"),
                    "timestamp": event.get("timestamp"),
                    "status": event.get("status"),
                    "duration_ms": event.get("duration_ms"),
                    "provider": attrs.get("provider") or (payload or {}).get("provider"),
                    "model_name": attrs.get("model_name") or (payload or {}).get("model_name"),
                    "finish_reason": attrs.get("finish_reason")
                    or (payload or {}).get("finish_reason"),
                    "usage": attrs.get("usage") or (payload or {}).get("usage"),
                    "span_id": event.get("span_id"),
                    "parent_span_id": event.get("parent_span_id"),
                    "attempt_id": event.get("attempt_id"),
                    "payload": payload,
                    "attributes": attrs,
                }
            )
        return models

    def _group_tools(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Aggregate tool lifecycle events; order by first appearance."""
        by_key: dict[str, dict[str, Any]] = {}
        order: list[str] = []

        def key_for(event: dict[str, Any]) -> str:
            tcid = event.get("tool_call_id") or _attr(event, "tool_call_id")
            if isinstance(tcid, str) and tcid:
                return f"id:{tcid}"
            name = _attr(event, "tool_name") or "unknown"
            seq = event.get("sequence")
            return f"anon:{name}:{seq}:{event.get('event_id')}"

        for event in events:
            name = str(event.get("event_name") or "")
            if not any(name.startswith(p) for p in _TOOL_EVENT_PREFIXES):
                continue
            key = key_for(event)
            if key not in by_key:
                by_key[key] = {
                    "tool_call_id": event.get("tool_call_id") or _attr(event, "tool_call_id"),
                    "tool_name": _attr(event, "tool_name"),
                    "status": event.get("status"),
                    "execution_status": _attr(event, "execution_status"),
                    "span_id": event.get("span_id"),
                    "parent_span_id": event.get("parent_span_id"),
                    "attempt_id": event.get("attempt_id"),
                    "events": [],
                    "payloads": [],
                }
                order.append(key)
            entry = by_key[key]
            entry["events"].append(event)
            tool_name = _attr(event, "tool_name")
            if tool_name:
                entry["tool_name"] = tool_name
            if event.get("tool_call_id"):
                entry["tool_call_id"] = event.get("tool_call_id")
            if _attr(event, "execution_status") is not None:
                entry["execution_status"] = _attr(event, "execution_status")
            if event.get("status") is not None:
                entry["status"] = event.get("status")
            if isinstance(event.get("payload"), dict):
                entry["payloads"].append(event["payload"])
                # Surface latest args/result for convenience
                payload = event["payload"]
                if "tool_args" in payload:
                    entry["tool_args"] = payload["tool_args"]
                if "parameters" in payload:
                    entry["parameters"] = payload["parameters"]
                if "tool_result" in payload:
                    entry["tool_result"] = payload["tool_result"]
                if "result" in payload:
                    entry["result"] = payload["result"]
            if event.get("duration_ms") is not None:
                entry["duration_ms"] = event.get("duration_ms")

        return [by_key[k] for k in order]

    def _group_approvals(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        approvals: list[dict[str, Any]] = []
        for event in events:
            name = str(event.get("event_name") or "")
            if not name.startswith(_APPROVAL_EVENT_PREFIX):
                continue
            attrs = event.get("attributes") if isinstance(event.get("attributes"), dict) else {}
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else None
            approvals.append(
                {
                    "event_id": event.get("event_id"),
                    "event_name": name,
                    "sequence": event.get("sequence"),
                    "timestamp": event.get("timestamp"),
                    "status": event.get("status"),
                    "tool_call_id": event.get("tool_call_id") or attrs.get("tool_call_id"),
                    "decision": attrs.get("decision") or (payload or {}).get("decision"),
                    "reason": attrs.get("reason") or (payload or {}).get("reason"),
                    "attempt_id": event.get("attempt_id"),
                    "payload": payload,
                    "attributes": attrs,
                }
            )
        return approvals

    def _group_delivery(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        delivery: list[dict[str, Any]] = []
        for event in events:
            name = str(event.get("event_name") or "")
            if not name.startswith(_DELIVERY_EVENT_PREFIX):
                continue
            attrs = event.get("attributes") if isinstance(event.get("attributes"), dict) else {}
            delivery.append(
                {
                    "event_id": event.get("event_id"),
                    "event_name": name,
                    "sequence": event.get("sequence"),
                    "timestamp": event.get("timestamp"),
                    "status": event.get("status"),
                    "duration_ms": event.get("duration_ms"),
                    "target": attrs.get("target"),
                    "delivery_type": attrs.get("delivery_type"),
                    "chunk_type": attrs.get("chunk_type"),
                    "chunk_count": attrs.get("chunk_count"),
                    "byte_count": attrs.get("byte_count"),
                    "attempt_id": event.get("attempt_id"),
                    "attributes": attrs,
                    "payload": event.get("payload"),
                }
            )
        return delivery

    def list_traces(
        self,
        *,
        status: str | None = None,
        player: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return newest-first trace summaries; apply limit after filters."""
        with self._lock:
            self._ensure_loaded()
            now = datetime.now(UTC)
            summaries: list[dict[str, Any]] = []
            for trace_id, events in self._events_by_trace.items():
                if not events:
                    continue
                summary = self._build_summary(trace_id, events, now=now)
                if status is not None and summary.get("status") != status:
                    continue
                if player is not None and summary.get("player_name") != player:
                    continue
                summaries.append(summary)

            # Newest first: prefer ended_at, else last_event_timestamp, else started_at
            def sort_key(s: dict[str, Any]) -> str:
                return _ts_key(
                    s.get("ended_at") or s.get("last_event_timestamp") or s.get("started_at")
                )

            summaries.sort(key=sort_key, reverse=True)
            lim = max(0, int(limit))
            return summaries[:lim]

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        """Return full aggregation for one trace, or None if missing."""
        with self._lock:
            self._ensure_loaded()
            events = self._events_by_trace.get(trace_id)
            if not events:
                return None
            now = datetime.now(UTC)
            # Return event dicts as recorded (shallow copy of each line object)
            events_out = [dict(e) for e in events]
            return {
                "summary": self._build_summary(trace_id, events, now=now),
                "events": events_out,
                "attempts": self._group_attempts(events_out),
                "models": self._group_models(events_out),
                "tools": self._group_tools(events_out),
                "approvals": self._group_approvals(events_out),
                "delivery": self._group_delivery(events_out),
            }

    def health(self) -> dict[str, Any]:
        """Journal + optional recorder health snapshot."""
        with self._lock:
            self._ensure_loaded()
            parsed_lines = self._parsed_lines
            malformed_lines = self._malformed_lines
            last_event_timestamp = self._last_event_timestamp
            file_size = self._file_size
            trace_count = len(self._events_by_trace)

        recorder_health: dict[str, Any] | None = None
        try:
            from services.agent.trace import get_trace_recorder

            recorder = get_trace_recorder()
            if recorder is not None:
                recorder_health = recorder.health()
        except Exception:  # noqa: BLE001 — health must not raise
            recorder_health = None

        return {
            "path": str(self.path),
            "exists": self.path.exists() and self.path.is_file(),
            "file_size": file_size,
            "parsed_lines": parsed_lines,
            "malformed_lines": malformed_lines,
            "last_event_timestamp": last_event_timestamp,
            "trace_count": trace_count,
            "abandoned_after_seconds": ABANDONED_AFTER_SECONDS,
            "recorder": recorder_health,
        }


__all__ = [
    "ABANDONED_AFTER_SECONDS",
    "TraceQuery",
]
