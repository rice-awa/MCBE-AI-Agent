"""Agent Trace 数据契约与异步 JSONL Journal。

TraceRecorder 使用 asyncio.Queue + 后台 writer task，写盘失败只增加
dropped/write_failed/gap 计数，绝不阻塞 Agent 主路径。
完整正文经 serialize_trace_payload 白名单序列化，受 include_content 门控。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from config.logging import get_logger

logger = get_logger(__name__)

TraceStatus = Literal[
    "started",
    "completed",
    "failed",
    "cancelled",
    "rejected",
    "accepted",
    "info",
    "suspended",
    "resumed",
    "expired",
    "abandoned",
    "succeeded",
    "denied",
    "timeout_unknown",
    "not_executed",
]

# 完整正文 payload 允许写入 journal 的键（有类型白名单，拒绝任意 kwargs）。
_PAYLOAD_ALLOWED_KEYS = frozenset(
    {
        "content",
        "messages",
        "tool_args",
        "tool_result",
        "final_response",
        "user_message",
        "assistant_message",
        "usage",
        "finish_reason",
        "error_message",
        "model_name",
        "provider",
        "parts",
        "parameters",
        "result",
        "decision",
        "reason",
    }
)

_DEFAULT_QUEUE_MAXSIZE = 10_000
_DEFAULT_PATH = "logs/agent_traces.jsonl"
_DEFAULT_MAX_RECORDS = 10_000
_STOP = object()

_recorder_lock = threading.Lock()
_default_recorder: TraceRecorder | None = None


@dataclass(frozen=True, slots=True)
class TraceContext:
    """一次玩家意图在队列/Worker 边界显式传递的相关身份。"""

    trace_id: str
    run_id: str
    attempt_id: str
    message_id: str
    connection_id: str
    player_name: str
    conversation_id: str


class TraceEvent(BaseModel):
    """版本化 Trace 事件契约（schema_version=1）。"""

    schema_version: int = 1
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    event_name: str
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))
    sequence: int = 0
    trace_id: str
    run_id: str
    attempt_id: str
    span_id: str | None = None
    parent_span_id: str | None = None
    tool_call_id: str | None = None
    status: TraceStatus = "info"
    duration_ms: int | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] | None = None


def serialize_trace_payload(
    payload: Mapping[str, Any] | dict[str, Any] | None,
    *,
    include_content: bool,
) -> dict[str, Any] | None:
    """按白名单序列化完整正文；include_content=False 时返回 None。

    从不把任意 kwargs 原样写入 journal；敏感键与未知键被丢弃。
    """
    if not include_content or payload is None:
        return None
    if not isinstance(payload, Mapping):
        return None
    out: dict[str, Any] = {}
    for key, value in payload.items():
        key_str = str(key)
        if key_str not in _PAYLOAD_ALLOWED_KEYS:
            continue
        out[key_str] = _json_safe(value)
    return out or None


def _json_safe(value: Any) -> Any:
    """尽量转为可 JSON 序列化的结构，失败时降级为 str。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


class TraceRecorder:
    """非阻塞 Trace 事件记录器（asyncio.Queue + 单 writer task）。"""

    def __init__(
        self,
        *,
        path: str | Path = _DEFAULT_PATH,
        enabled: bool = False,
        include_content: bool = False,
        max_records: int = _DEFAULT_MAX_RECORDS,
        queue_maxsize: int = _DEFAULT_QUEUE_MAXSIZE,
    ) -> None:
        self.path = Path(path)
        self.enabled = bool(enabled)
        # include_content 仅在 enabled 时生效
        self.include_content = bool(enabled and include_content)
        self.max_records = max(1, int(max_records))
        self.queue_maxsize = max(1, int(queue_maxsize))

        self._queue: asyncio.Queue[Any] | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._started = False
        self._sequences: dict[str, int] = {}
        self._seq_guard = threading.Lock()

        self.enqueued = 0
        self.written = 0
        self.dropped = 0
        self.write_failed = 0
        self.gap = 0

    def health(self) -> dict[str, Any]:
        depth = 0
        if self._queue is not None:
            try:
                depth = self._queue.qsize()
            except Exception:  # noqa: BLE001
                depth = 0
        return {
            "enabled": self.enabled,
            "include_content": self.include_content,
            "path": str(self.path),
            "max_records": self.max_records,
            "enqueued": self.enqueued,
            "written": self.written,
            "dropped": self.dropped,
            "write_failed": self.write_failed,
            "gap": self.gap,
            "queue_depth": depth,
            "running": self._started and self._writer_task is not None and not self._writer_task.done(),
        }

    def _ensure_queue(self) -> asyncio.Queue[Any]:
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=self.queue_maxsize)
        return self._queue

    def _next_sequence(self, trace_id: str) -> int:
        with self._seq_guard:
            current = self._sequences.get(trace_id, 0)
            self._sequences[trace_id] = current + 1
            return current

    async def start(self) -> None:
        if not self.enabled:
            self._started = True
            return
        if self._started and self._writer_task is not None and not self._writer_task.done():
            return
        self._ensure_queue()
        self._started = True
        self._writer_task = asyncio.create_task(self._writer_loop(), name="agent-trace-writer")
        logger.info(
            "trace_recorder_started",
            path=str(self.path),
            max_records=self.max_records,
            include_content=self.include_content,
        )

    async def stop(self) -> None:
        """关闭 writer：flush 队列后结束 task，并保留 health counters。"""
        if not self.enabled:
            self._started = False
            return

        queue = self._queue
        task = self._writer_task
        if queue is not None and task is not None and not task.done():
            try:
                await queue.put(_STOP)
            except Exception:  # noqa: BLE001
                try:
                    queue.put_nowait(_STOP)
                except Exception:  # noqa: BLE001
                    pass
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except TimeoutError:
                self.gap += 1
                logger.warning(
                    "trace_recorder_stop_timeout",
                    pending=queue.qsize() if queue is not None else None,
                )
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except Exception as exc:  # noqa: BLE001
                self.gap += 1
                logger.warning("trace_recorder_stop_failed", error=str(exc))
        elif queue is not None:
            # Writer never started: drop remaining items and count gap
            while True:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item is _STOP:
                    continue
                self.gap += 1
                self.dropped += 1

        self._writer_task = None
        self._started = False
        logger.info(
            "trace_recorder_stopped",
            enqueued=self.enqueued,
            written=self.written,
            dropped=self.dropped,
            write_failed=self.write_failed,
            gap=self.gap,
        )

    def emit(
        self,
        event_name: str,
        context: TraceContext,
        *,
        status: str = "info",
        span_id: str | None = None,
        parent_span_id: str | None = None,
        tool_call_id: str | None = None,
        duration_ms: int | None = None,
        attributes: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """非阻塞入队；任何失败只计入 counters，不向调用方抛出。"""
        if not self.enabled:
            return
        try:
            event = TraceEvent(
                event_name=event_name,
                trace_id=context.trace_id,
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
                tool_call_id=tool_call_id,
                status=status,  # type: ignore[arg-type]
                duration_ms=duration_ms,
                attributes=dict(attributes or {}),
                sequence=self._next_sequence(context.trace_id),
                payload=serialize_trace_payload(payload, include_content=self.include_content),
            )
            # 附带上下文身份到 attributes（metadata，不占 payload）
            attrs = event.attributes
            attrs.setdefault("message_id", context.message_id)
            attrs.setdefault("connection_id", context.connection_id)
            attrs.setdefault("player_name", context.player_name)
            attrs.setdefault("conversation_id", context.conversation_id)

            line = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, default=str)
            queue = self._ensure_queue()
            try:
                queue.put_nowait(line)
                self.enqueued += 1
            except asyncio.QueueFull:
                self.dropped += 1
                self.gap += 1
                logger.warning(
                    "trace_queue_full",
                    event_name=event_name,
                    trace_id=context.trace_id,
                    queue_maxsize=self.queue_maxsize,
                )
        except Exception as exc:  # noqa: BLE001 — emit 绝不能影响主路径
            self.dropped += 1
            self.gap += 1
            logger.warning(
                "trace_emit_failed",
                event_name=event_name,
                error=str(exc),
            )

    async def _writer_loop(self) -> None:
        queue = self._ensure_queue()
        while True:
            try:
                item = await queue.get()
            except asyncio.CancelledError:
                await self._drain_remaining(queue)
                raise
            except Exception as exc:  # noqa: BLE001
                self.gap += 1
                logger.warning("trace_writer_queue_get_failed", error=str(exc))
                continue
            if item is _STOP:
                await self._drain_remaining(queue)
                break
            try:
                # Disk I/O off the event loop so agent paths stay responsive
                await asyncio.to_thread(self._append_line, str(item))
                self.written += 1
            except Exception as exc:  # noqa: BLE001
                self.write_failed += 1
                self.gap += 1
                logger.warning(
                    "trace_write_failed",
                    error=str(exc),
                    path=str(self.path),
                )

    async def _drain_remaining(self, queue: asyncio.Queue[Any]) -> None:
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is _STOP:
                continue
            try:
                await asyncio.to_thread(self._append_line, str(item))
                self.written += 1
            except Exception as exc:  # noqa: BLE001
                self.write_failed += 1
                self.gap += 1
                logger.warning("trace_write_failed", error=str(exc), path=str(self.path))

    def _append_line(self, line: str) -> None:
        """追加一行 JSON 并按 max_records 轮转（同步；由 writer 经 to_thread 调用）。"""
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: list[str] = []
        if path.exists():
            existing = path.read_text(encoding="utf-8").splitlines()
        retained = [*existing, line][-self.max_records :]
        _atomic_write_text(path, "\n".join(retained) + "\n")


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def get_trace_recorder(settings: Any | None = None) -> TraceRecorder:
    """返回进程级 TraceRecorder 单例；settings 缺省时从 get_settings() 读取。"""
    global _default_recorder
    with _recorder_lock:
        if _default_recorder is not None:
            return _default_recorder
        if settings is None:
            from config.settings import get_settings

            settings = get_settings()
        enabled = bool(getattr(settings, "agent_trace_enabled", False))
        include_content = bool(getattr(settings, "agent_trace_include_content", False))
        if not enabled:
            include_content = False
        _default_recorder = TraceRecorder(
            path=getattr(settings, "agent_trace_path", _DEFAULT_PATH),
            enabled=enabled,
            include_content=include_content,
            max_records=int(getattr(settings, "agent_trace_max_records", _DEFAULT_MAX_RECORDS) or _DEFAULT_MAX_RECORDS),
        )
        return _default_recorder


def set_trace_recorder(recorder: TraceRecorder | None) -> None:
    """测试辅助：注入或清空全局 recorder。"""
    global _default_recorder
    with _recorder_lock:
        _default_recorder = recorder


__all__ = [
    "TraceContext",
    "TraceEvent",
    "TraceRecorder",
    "TraceStatus",
    "get_trace_recorder",
    "serialize_trace_payload",
    "set_trace_recorder",
]
