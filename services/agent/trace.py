"""Agent Trace 数据契约与异步 JSONL Journal。

TraceRecorder 使用 asyncio.Queue + 后台 writer task，写盘失败只增加
dropped/write_failed/gap 计数，绝不阻塞 Agent 主路径。
完整正文经 serialize_trace_payload 白名单序列化，受 include_content 门控。
"""

from __future__ import annotations

import asyncio
import hashlib
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


# system-prompt 超过该长度时只保留预览 + hash，避免 journal 被静态提示词撑爆。
_SYSTEM_PROMPT_FULL_MAX_CHARS = 256
_SYSTEM_PROMPT_PREVIEW_CHARS = 120


def serialize_trace_payload(
    payload: Mapping[str, Any] | dict[str, Any] | None,
    *,
    include_content: bool,
) -> dict[str, Any] | None:
    """按白名单序列化完整正文；include_content=False 时返回 None。

    从不把任意 kwargs 原样写入 journal；敏感键与未知键被丢弃。
    messages 内超长 system-prompt 会被压缩为 preview/hash 元数据。
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
        if key_str == "messages":
            out[key_str] = _compact_trace_messages(value)
        else:
            out[key_str] = _json_safe(value)
    return out or None


def _compact_trace_messages(messages: Any) -> Any:
    """JSON-safe 化 messages，并把超长 system-prompt 压成元数据。"""
    if not isinstance(messages, list):
        return _json_safe(messages)
    compacted: list[Any] = []
    for msg in messages:
        if not isinstance(msg, Mapping):
            compacted.append(_json_safe(msg))
            continue
        msg_out: dict[str, Any] = {
            str(k): _json_safe(v) for k, v in msg.items() if k != "parts"
        }
        parts = msg.get("parts")
        if isinstance(parts, list):
            msg_out["parts"] = [_compact_message_part(part) for part in parts]
        elif parts is not None:
            msg_out["parts"] = _json_safe(parts)
        compacted.append(msg_out)
    return compacted


def _compact_message_part(part: Any) -> Any:
    """压缩单个 message part；仅处理超长 system-prompt 正文。"""
    if not isinstance(part, Mapping):
        return _json_safe(part)
    part_out = {str(k): _json_safe(v) for k, v in part.items()}
    if part_out.get("part_kind") != "system-prompt":
        return part_out
    content = part_out.get("content")
    if not isinstance(content, str) or len(content) <= _SYSTEM_PROMPT_FULL_MAX_CHARS:
        return part_out
    part_out.pop("content", None)
    part_out["content_chars"] = len(content)
    part_out["content_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    part_out["content_preview"] = content[:_SYSTEM_PROMPT_PREVIEW_CHARS]
    part_out["content_omitted"] = True
    return part_out


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

            dumped = event.model_dump(mode="json")
            # content 关闭时不保留 null payload 键，便于审计侧区分「无正文」。
            if dumped.get("payload") is None:
                dumped.pop("payload", None)
            line = json.dumps(dumped, ensure_ascii=False, default=str)
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

    def record_model_messages(
        self,
        context: TraceContext,
        *,
        messages: list[Any],
        usage: dict[str, Any] | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        finish_reason: str | None = None,
        duration_ms: int | None = None,
        attributes: dict[str, Any] | None = None,
        status: str = "completed",
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> None:
        """记录一轮 model request/response。

        payload 只保留完整 messages 正文（受 include_content 门控）；
        usage/finish_reason/model_name/provider 全部归入 attributes（元数据层，
        关闭 content 时仍可见，且不与 messages 内重复字段冗余）。
        """
        attrs = dict(attributes or {})
        if provider is not None:
            attrs.setdefault("provider", provider)
        if model_name is not None:
            attrs.setdefault("model_name", model_name)
        if finish_reason is not None:
            attrs.setdefault("finish_reason", finish_reason)
        if usage is not None:
            attrs.setdefault("usage", usage)
        payload: dict[str, Any] = {"messages": messages}
        self.emit(
            "model.request.completed",
            context,
            status=status,
            span_id=span_id,
            parent_span_id=parent_span_id,
            duration_ms=duration_ms,
            attributes=attrs,
            payload=payload,
        )

    def record_tool_call(
        self,
        context: TraceContext,
        *,
        tool_name: str,
        tool_call_id: str | None = None,
        tool_args: dict[str, Any] | None = None,
        parameters: dict[str, Any] | None = None,
        event_name: str = "tool.proposed",
        status: str = "info",
        attributes: dict[str, Any] | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> None:
        """记录工具提议/调用参数（args 仅在 include_content 时进入 payload）。"""
        attrs = dict(attributes or {})
        attrs.setdefault("tool_name", tool_name)
        if tool_call_id is not None:
            attrs.setdefault("tool_call_id", tool_call_id)
        args = parameters if parameters is not None else tool_args
        payload: dict[str, Any] | None = None
        if args is not None:
            payload = {"tool_args": args, "parameters": args}
        self.emit(
            event_name,
            context,
            status=status,
            span_id=span_id,
            parent_span_id=parent_span_id,
            tool_call_id=tool_call_id,
            attributes=attrs,
            payload=payload,
        )

    def record_tool_result(
        self,
        context: TraceContext,
        *,
        tool_name: str,
        tool_call_id: str | None = None,
        result: Any = None,
        status: str = "succeeded",
        event_name: str | None = None,
        duration_ms: int | None = None,
        attributes: dict[str, Any] | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> None:
        """记录工具执行结果；status 使用归一化执行态。"""
        normalized = str(status or "succeeded")
        if event_name is None:
            if normalized in {"failed", "denied", "timeout_unknown"}:
                event_name = "tool.execution.failed"
            elif normalized == "cancelled":
                event_name = "tool.execution.cancelled"
            elif normalized == "not_executed":
                event_name = "tool.execution.completed"
            else:
                event_name = "tool.execution.completed"
        attrs = dict(attributes or {})
        attrs.setdefault("tool_name", tool_name)
        attrs.setdefault("execution_status", normalized)
        if tool_call_id is not None:
            attrs.setdefault("tool_call_id", tool_call_id)
        payload: dict[str, Any] | None = None
        if result is not None:
            payload = {"tool_result": result, "result": result}
        self.emit(
            event_name,
            context,
            status=normalized if normalized in {
                "succeeded",
                "failed",
                "denied",
                "cancelled",
                "timeout_unknown",
                "not_executed",
                "info",
                "completed",
            } else "info",
            span_id=span_id,
            parent_span_id=parent_span_id,
            tool_call_id=tool_call_id,
            duration_ms=duration_ms,
            attributes=attrs,
            payload=payload,
        )

    def record_final_response(
        self,
        context: TraceContext,
        *,
        content: str,
        attributes: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        status: str = "completed",
        event_name: str = "trace.completed",
    ) -> None:
        """记录终态与用户可见最终答复（正文仅 content 门控）。"""
        attrs = dict(attributes or {})
        payload = {
            "content": content,
            "final_response": content,
        }
        self.emit(
            event_name,
            context,
            status=status,
            duration_ms=duration_ms,
            attributes=attrs,
            payload=payload,
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
