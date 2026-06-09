"""运行时 Harness 工具调用审计。"""

from __future__ import annotations

import inspect
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

from config.settings import Settings
from services.agent.harness.catalog import ParameterPreviewPolicy, get_tool_entry

AuditRecord = dict[str, Any]
ToolFunction = Callable[..., Any]


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


def audit_enabled(settings: Settings | None) -> bool:
    if settings is None:
        return False
    return settings.runtime_harness_enabled and settings.runtime_harness_audit_enabled


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
) -> AuditRecord:
    entry = get_tool_entry(tool_name)
    deps = getattr(ctx, "deps", None)
    settings = getattr(deps, "settings", None)
    record: AuditRecord = {
        "timestamp": datetime.now(UTC).isoformat(),
        "tool_name": tool_name,
        "intent": str(entry.intent) if entry is not None else "unknown",
        "risk": str(entry.risk) if entry is not None else "unknown",
        "status": status,
        "duration_ms": duration_ms,
        "player_name": getattr(deps, "player_name", None),
        "provider": getattr(deps, "provider", None) or getattr(settings, "default_provider", None),
        "conversation_id": getattr(deps, "conversation_id", None) or getattr(ctx, "conversation_id", None),
        "connection_id_short": _short_connection_id(getattr(deps, "connection_id", None)),
        "parameters": preview_parameters(tool_name, parameters),
        "result": summarize_result(result=result, exception=exception),
    }
    return record


def summarize_result(
    *,
    result: Any = None,
    exception: BaseException | None = None,
) -> dict[str, str | None]:
    if exception is not None:
        return {
            "success": "failure",
            "result_preview": None,
            "failure_reason": str(exception),
        }

    text = str(result) if result is not None else ""
    failure_reason = _failure_reason_from_text(text)
    return {
        "success": "failure" if failure_reason else "success",
        "result_preview": None,
        "failure_reason": failure_reason,
    }


def write_audit_record(record: AuditRecord, path: str | Path, max_records: int) -> None:
    audit_path = Path(path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    existing = audit_path.read_text(encoding="utf-8").splitlines() if audit_path.exists() else []
    lines = [*existing, json.dumps(record, ensure_ascii=False, default=str)]
    retained = lines[-max(1, max_records):]
    audit_path.write_text("\n".join(retained) + "\n", encoding="utf-8")


def wrap_tool_function(tool_name: str, function: ToolFunction, settings: Settings | None) -> ToolFunction:
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
            _write_record(settings, tool_name, parameters, ctx, "failure", duration_ms, exception=exc)
            raise
        duration_ms = _duration_ms(start)
        _write_record(settings, tool_name, parameters, ctx, "success", duration_ms, result=result)
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
            _write_record(settings, tool_name, parameters, ctx, "failure", duration_ms, exception=exc)
            raise
        duration_ms = _duration_ms(start)
        _write_record(settings, tool_name, parameters, ctx, "success", duration_ms, result=result)
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


def wrap_registered_tools(toolset: Any, settings: Settings | None) -> None:
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


def _write_record(
    settings: Settings | None,
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
    audit_path = getattr(effective_settings, "runtime_harness_audit_path", "logs/runtime_harness_tools.jsonl")
    max_records = getattr(effective_settings, "runtime_harness_audit_max_records", 5000)
    record = build_audit_record(
        tool_name=tool_name,
        parameters=parameters,
        ctx=ctx,
        status=status,
        duration_ms=duration_ms,
        result=result,
        exception=exception,
    )
    write_audit_record(record, audit_path, max_records)


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
        return _truncate_text(value, max_length)
    if isinstance(value, list):
        return [_truncate_value(item, max_length) for item in value]
    if isinstance(value, tuple):
        return [_truncate_value(item, max_length) for item in value]
    if isinstance(value, dict):
        return {str(key): _truncate_value(child, max_length) for key, child in value.items()}
    return value


def _truncate_text(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


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
            return _truncate_text(stripped, 200)
    return None
