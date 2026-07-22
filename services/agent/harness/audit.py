"""运行时 Harness 工具调用审计。

正常路径只将记录 enqueue 到内存队列；由 AgentRuntime 管理的后台 writer
负责 append / 轮转。写入、轮转或 flush 失败仅告警，不改变工具结果。
"""

from __future__ import annotations

import inspect
import json
import os
import queue
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Protocol

from config.logging import get_logger
from config.redaction import (
    DEFAULT_EXCEPTION_MAX,
    DEFAULT_PARAM_MAX,
    redact_exception,
    redact_mapping,
    truncate_for_log,
)
from services.agent.harness.catalog import ParameterPreviewPolicy, get_tool_entry
from services.agent.tool_results import ToolResult

AuditRecord = dict[str, Any]
ToolFunction = Callable[..., Any]

logger = get_logger(__name__)


class AuditSettings(Protocol):
    runtime_harness_enabled: bool
    runtime_harness_audit_enabled: bool
    runtime_harness_audit_path: str
    runtime_harness_audit_max_records: int


_FAILURE_PREFIXES = (
    "命令执行失败",
    "消息发送失败",
    "彩色消息发送失败",
    "标题发送失败",
    "Actionbar 发送失败",
    "脚本事件发送失败",
    "请求失败",
    "搜索请求失败",
    "搜索失败",
    "页面请求失败",
    "页面获取失败",
    "获取玩家快照失败",
    "获取背包快照失败",
    "查询实体失败",
    "执行世界命令失败",
)

_AUDIT_WRITE_LOCKS: dict[Path, threading.Lock] = {}
_AUDIT_WRITE_LOCKS_GUARD = threading.Lock()

_DEFAULT_AUDIT_PATH = "logs/runtime_harness_tools.jsonl"
_DEFAULT_MAX_RECORDS = 5000
_QUEUE_MAXSIZE = 10_000
_STOP = object()


class _FlushRequest:
    """Writer 内部 flush 栅栏。"""

    __slots__ = ("event",)

    def __init__(self) -> None:
        self.event = threading.Event()


def audit_enabled(settings: AuditSettings | None) -> bool:
    if settings is None:
        return False
    return bool(
        getattr(settings, "runtime_harness_enabled", False)
        and getattr(settings, "runtime_harness_audit_enabled", False)
    )


def preview_parameters(
    tool_name: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    entry = get_tool_entry(tool_name)
    policy = entry.preview if entry is not None else ParameterPreviewPolicy()
    preview: dict[str, Any] = {}
    for key in policy.include:
        if key not in parameters:
            continue
        if key in policy.sensitive:
            preview[key] = "[REDACTED]"
            continue
        preview[key] = _truncate_value(parameters[key], policy.max_length)
    for key in policy.sensitive:
        if key in parameters and key not in preview:
            preview[key] = "[REDACTED]"
    # 对 include 之外但明显敏感的字段也打码（防御性）
    for key, value in parameters.items():
        if key in preview:
            continue
        # 仅对敏感 key 额外暴露为 REDACTED，避免记录完整玩家内容
        from config.redaction import is_sensitive_key

        if is_sensitive_key(key):
            preview[str(key)] = "[REDACTED]"
    return preview


def build_audit_record(
    *,
    tool_name: str,
    parameters: dict[str, Any],
    ctx: Any | None,
    status: str,
    duration_ms: int,
    result: Any = None,
    exception: BaseException | None = None,
    tool_call_id: str | None = None,
    policy_version: str | None = None,
    run_id: str | None = None,
    authorized_args: dict[str, Any] | None = None,
    approval_evidence: dict[str, Any] | None = None,
) -> AuditRecord:
    entry = get_tool_entry(tool_name)
    deps = getattr(ctx, "deps", None)
    settings = getattr(deps, "settings", None)

    resolved_run_id = (
        run_id
        if run_id is not None
        else getattr(deps, "run_id", None) or getattr(ctx, "run_id", None)
    )
    resolved_tool_call_id = (
        tool_call_id
        if tool_call_id is not None
        else getattr(ctx, "tool_call_id", None)
    )
    resolved_policy = (
        policy_version
        if policy_version is not None
        else (
            str(entry.policy_version)
            if entry is not None
            else getattr(settings, "tool_policy_version", None)
        )
    )

    result_summary = summarize_result(result=result, exception=exception)
    record: AuditRecord = {
        "timestamp": datetime.now(UTC).isoformat(),
        "tool_name": tool_name,
        "intent": str(entry.intent) if entry is not None else "unknown",
        "risk": str(entry.risk) if entry is not None else "unknown",
        "status": status,
        "duration_ms": duration_ms,
        "run_id": resolved_run_id,
        "tool_call_id": resolved_tool_call_id,
        "policy_version": resolved_policy,
        "player_name": getattr(deps, "player_name", None),
        "provider": getattr(deps, "provider", None) or getattr(settings, "default_provider", None),
        "conversation_id": getattr(deps, "conversation_id", None) or getattr(ctx, "conversation_id", None),
        "connection_id_short": _short_connection_id(getattr(deps, "connection_id", None)),
        "parameters": preview_parameters(tool_name, parameters),
        "result": result_summary,
        "error_kind": result_summary.get("error_kind"),
    }
    if authorized_args is not None:
        record["authorized_parameters"] = preview_parameters(tool_name, authorized_args)
    if approval_evidence is not None:
        record["approval_evidence"] = _summarize_approval_evidence(approval_evidence)
    return record


def _summarize_approval_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Keep bounded approval evidence without recording payloads or locked targets."""
    excluded = {"locked_targets", "payload", "raw_payload", "bridge_payload"}
    return redact_mapping(
        {key: value for key, value in evidence.items() if str(key).lower() not in excluded},
        max_length=DEFAULT_PARAM_MAX,
    )


def summarize_result(
    *,
    result: Any = None,
    exception: BaseException | None = None,
) -> dict[str, str | None]:
    if exception is not None:
        return {
            "success": "failure",
            "result_preview": None,
            "failure_reason": redact_exception(exception, DEFAULT_EXCEPTION_MAX),
            "error_kind": "INTERNAL",
            "external_state_unknown": "false",
        }

    if isinstance(result, ToolResult):
        return {
            "success": result.status,
            "result_preview": None,
            "failure_reason": (
                truncate_for_log(result.failure_reason, DEFAULT_EXCEPTION_MAX)
                if not result.is_success and result.failure_reason
                else None
            ),
            "error_kind": result.error_kind,
            "external_state_unknown": "true" if result.external_state_unknown else "false",
            "diagnostic_summary": (
                truncate_for_log(result.diagnostic_summary, DEFAULT_EXCEPTION_MAX)
                if not result.is_success and result.diagnostic_summary
                else None
            ),
        }

    text = str(result) if result is not None else ""
    failure_reason = _failure_reason_from_text(text)
    return {
        "success": "failure" if failure_reason else "success",
        "result_preview": None,
        "failure_reason": failure_reason,
        "error_kind": "PERMANENT" if failure_reason else None,
        "external_state_unknown": "false",
    }


def write_audit_record(record: AuditRecord, path: str | Path, max_records: int) -> None:
    """同步写入（writer 线程与测试旋转用例使用）。失败由调用方处理。"""
    audit_path = Path(path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with _audit_write_lock(audit_path):
        existing = audit_path.read_text(encoding="utf-8").splitlines() if audit_path.exists() else []
        lines = [*existing, json.dumps(record, ensure_ascii=False, default=str)]
        retained = lines[-max(1, int(max_records)):]
        _atomic_write_text(audit_path, "\n".join(retained) + "\n")


def enqueue_audit_record(
    record: AuditRecord,
    path: str | Path,
    max_records: int,
) -> None:
    """非阻塞入队。队列满或 writer 异常时只告警，不抛给调用方。"""
    try:
        get_audit_writer().enqueue(record, str(path), int(max_records))
    except Exception as exc:  # noqa: BLE001 — 审计绝不能影响工具结果
        logger.warning(
            "audit_enqueue_failed",
            error=redact_exception(exc),
            tool_name=record.get("tool_name"),
            run_id=record.get("run_id"),
        )


class AuditWriter:
    """内存队列 + 单后台线程的异步审计 writer。"""

    def __init__(self) -> None:
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._thread: threading.Thread | None = None
        self._started = False
        self._stopping = False
        self._guard = threading.Lock()
        self._default_path = _DEFAULT_AUDIT_PATH
        self._default_max_records = _DEFAULT_MAX_RECORDS

    @property
    def is_running(self) -> bool:
        return self._started and self._thread is not None and self._thread.is_alive()

    def start(self, settings: AuditSettings | None = None) -> None:
        with self._guard:
            if settings is not None:
                self._default_path = str(
                    getattr(settings, "runtime_harness_audit_path", self._default_path)
                    or self._default_path
                )
                self._default_max_records = int(
                    getattr(settings, "runtime_harness_audit_max_records", self._default_max_records)
                    or self._default_max_records
                )
            if self._started and self._thread is not None and self._thread.is_alive():
                return
            self._stopping = False
            self._thread = threading.Thread(
                target=self._run,
                name="runtime-harness-audit-writer",
                daemon=True,
            )
            self._started = True
            self._thread.start()
            logger.info(
                "audit_writer_started",
                audit_path=self._default_path,
                max_records=self._default_max_records,
            )

    def enqueue(self, record: AuditRecord, path: str | None = None, max_records: int | None = None) -> None:
        # 惰性启动，保证测试与未显式 initialize 的路径也能 flush
        if not self.is_running:
            self.start()
        item = (
            record,
            path or self._default_path,
            int(max_records if max_records is not None else self._default_max_records),
        )
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            logger.warning(
                "audit_queue_full",
                tool_name=record.get("tool_name"),
                run_id=record.get("run_id"),
                queue_maxsize=_QUEUE_MAXSIZE,
            )

    def flush(self, timeout: float = 5.0) -> None:
        """等待队列排空；超时或失败只告警。"""
        if not self.is_running:
            self._drain_sync()
            return
        request = _FlushRequest()
        try:
            self._queue.put(request, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            logger.warning("audit_flush_enqueue_failed", error=redact_exception(exc))
            return
        if not request.event.wait(timeout=timeout):
            logger.warning("audit_flush_timeout", timeout=timeout, pending=self._queue.qsize())

    def stop(self, timeout: float = 5.0) -> None:
        """flush 后停止后台线程。"""
        with self._guard:
            if not self._started:
                return
            self._stopping = True
        try:
            self.flush(timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            logger.warning("audit_stop_flush_failed", error=redact_exception(exc))
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(_STOP)
            except Exception:  # noqa: BLE001
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning("audit_writer_stop_timeout", timeout=timeout)
        with self._guard:
            self._started = False
            self._thread = None
        logger.info("audit_writer_stopped")

    def _drain_sync(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            self._handle_item(item)

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get()
            except Exception as exc:  # noqa: BLE001
                logger.warning("audit_writer_queue_get_failed", error=redact_exception(exc))
                continue
            if item is _STOP:
                self._drain_sync()
                break
            self._handle_item(item)

    def _handle_item(self, item: Any) -> None:
        try:
            if isinstance(item, _FlushRequest):
                item.event.set()
                return
            record, path, max_records = item
            try:
                write_audit_record(record, path, max_records)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "audit_write_failed",
                    error=redact_exception(exc),
                    path=str(path),
                    tool_name=record.get("tool_name") if isinstance(record, dict) else None,
                    run_id=record.get("run_id") if isinstance(record, dict) else None,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("audit_writer_item_failed", error=redact_exception(exc))


_default_writer: AuditWriter | None = None
_default_writer_guard = threading.Lock()


def get_audit_writer() -> AuditWriter:
    global _default_writer
    with _default_writer_guard:
        if _default_writer is None:
            _default_writer = AuditWriter()
        return _default_writer


def set_audit_writer(writer: AuditWriter | None) -> None:
    """测试钩子：替换/清空进程级 writer。"""
    global _default_writer
    with _default_writer_guard:
        _default_writer = writer


def start_audit_writer(settings: AuditSettings | None = None) -> AuditWriter:
    writer = get_audit_writer()
    writer.start(settings)
    return writer


def flush_audit_writer(timeout: float = 5.0) -> None:
    get_audit_writer().flush(timeout=timeout)


def stop_audit_writer(timeout: float = 5.0) -> None:
    writer = get_audit_writer()
    writer.stop(timeout=timeout)


def _audit_write_lock(audit_path: Path) -> threading.Lock:
    resolved = audit_path.resolve(strict=False)
    with _AUDIT_WRITE_LOCKS_GUARD:
        lock = _AUDIT_WRITE_LOCKS.get(resolved)
        if lock is None:
            lock = threading.Lock()
            _AUDIT_WRITE_LOCKS[resolved] = lock
        return lock


def _atomic_write_text(path: Path, content: str) -> None:
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_name = temp_file.name
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def wrap_tool_function(tool_name: str, function: ToolFunction, settings: AuditSettings | None) -> ToolFunction:
    signature = inspect.signature(function)
    is_async = inspect.iscoroutinefunction(function)

    async def _audit_async(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        if not audit_enabled(settings):
            return await function(*args, **kwargs)
        ctx, parameters = _extract_context_and_parameters(signature, args, kwargs)
        start = time.perf_counter()
        try:
            result = await function(*args, **kwargs)
        except Exception as exc:
            duration_ms = _duration_ms(start)
            _enqueue_record(settings, tool_name, parameters, ctx, "failure", duration_ms, exception=exc)
            raise
        duration_ms = _duration_ms(start)
        audit_status = (
            "failure"
            if isinstance(result, ToolResult) and not result.is_success
            else "success"
        )
        _enqueue_record(settings, tool_name, parameters, ctx, audit_status, duration_ms, result=result)
        return result

    def _audit_sync(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        if not audit_enabled(settings):
            return function(*args, **kwargs)
        ctx, parameters = _extract_context_and_parameters(signature, args, kwargs)
        start = time.perf_counter()
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            duration_ms = _duration_ms(start)
            _enqueue_record(settings, tool_name, parameters, ctx, "failure", duration_ms, exception=exc)
            raise
        duration_ms = _duration_ms(start)
        audit_status = (
            "failure"
            if isinstance(result, ToolResult) and not result.is_success
            else "success"
        )
        _enqueue_record(settings, tool_name, parameters, ctx, audit_status, duration_ms, result=result)
        return result

    if is_async:
        @wraps(function)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return await _audit_async(args, kwargs)

        return async_wrapper

    @wraps(function)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        return _audit_sync(args, kwargs)

    return sync_wrapper


def wrap_registered_tools(toolset: Any, settings: AuditSettings | None) -> None:
    for tool_name, tool in getattr(toolset, "tools", {}).items():
        function = getattr(tool, "function", None)
        if function is None or getattr(function, "_runtime_harness_audited", False):
            continue
        wrapped = wrap_tool_function(tool_name, function, settings)
        setattr(wrapped, "_runtime_harness_audited", True)
        tool.function = wrapped
        function_schema = getattr(tool, "function_schema", None)
        if function_schema is not None and hasattr(function_schema, "function"):
            function_schema.function = wrapped


def _enqueue_record(
    settings: AuditSettings | None,
    tool_name: str,
    parameters: dict[str, Any],
    ctx: Any | None,
    status: str,
    duration_ms: int,
    *,
    result: Any = None,
    exception: BaseException | None = None,
) -> None:
    effective_settings = settings or getattr(getattr(ctx, "deps", None), "settings", None)
    if not audit_enabled(effective_settings):
        return
    audit_path = getattr(effective_settings, "runtime_harness_audit_path", _DEFAULT_AUDIT_PATH)
    max_records = getattr(effective_settings, "runtime_harness_audit_max_records", _DEFAULT_MAX_RECORDS)
    record = build_audit_record(
        tool_name=tool_name,
        parameters=parameters,
        ctx=ctx,
        status=status,
        duration_ms=duration_ms,
        result=result,
        exception=exception,
    )
    enqueue_audit_record(record, audit_path, max_records)


def _extract_context_and_parameters(
    signature: inspect.Signature,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[Any | None, dict[str, Any]]:
    try:
        bound = signature.bind_partial(*args, **kwargs)
    except TypeError:
        return (args[0] if args else None), dict(kwargs)
    ctx = bound.arguments.get("ctx")
    parameters = {
        name: value
        for name, value in bound.arguments.items()
        if name != "ctx"
    }
    return ctx, parameters


def _truncate_value(value: Any, max_length: int) -> Any:
    if isinstance(value, str):
        return truncate_for_log(value, max_length)
    if isinstance(value, list):
        return [_truncate_value(item, max_length) for item in value]
    if isinstance(value, tuple):
        return [_truncate_value(item, max_length) for item in value]
    if isinstance(value, dict):
        return redact_mapping(value, max_length=max_length or DEFAULT_PARAM_MAX)
    return value


def _short_connection_id(connection_id: Any) -> str | None:
    if connection_id is None:
        return None
    return str(connection_id)[:8]


def _duration_ms(start: float) -> int:
    return max(0, round((time.perf_counter() - start) * 1000))


def _failure_reason_from_text(text: str) -> str | None:
    stripped = text.strip()
    for prefix in _FAILURE_PREFIXES:
        if stripped.startswith(prefix):
            return truncate_for_log(stripped, 200)
    return None
