"""Shared addon bridge result mapping for dedicated block tools and legacy addon tools."""

from __future__ import annotations

import json
from typing import Any, Protocol

from services.agent.block_ops.schema import (
    BlockErrorCode,
    build_error_response,
    dumps_payload,
)
from services.agent.tool_results import ToolResult


class _BridgeClient(Protocol):
    async def request(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...


def map_addon_bridge_result(
    result: dict[str, Any] | Any,
    *,
    default_error_code: str = BlockErrorCode.INTERNAL_ERROR,
    failure_prefix: str | None = None,
) -> ToolResult:
    """Map an addon bridge response dict to ToolResult.

    - ok:true → ToolResult.ok with JSON text of payload (or whole result)
    - ok:false → ToolResult.failure (never success), JSON body remains informative
    """
    if not isinstance(result, dict):
        text = dumps_payload(
            build_error_response(
                BlockErrorCode.INTERNAL_ERROR,
                "invalid bridge response",
                raw=str(result),
            )
        )
        return ToolResult.failure(
            text,
            error_kind="INTERNAL",
            retryable=False,
            diagnostic_summary="invalid bridge response type",
        )

    ok = result.get("ok")
    payload = result.get("payload", result)

    if ok is False:
        body: dict[str, Any]
        if isinstance(payload, dict):
            code = payload.get("code") or default_error_code
            message = str(payload.get("message") or payload.get("error") or "addon returned ok:false")
            body = build_error_response(code, message, **{
                k: v for k, v in payload.items() if k not in {"code", "message"}
            })
        else:
            message = str(result.get("error") or payload or "addon returned ok:false")
            body = build_error_response(default_error_code, message)

        if failure_prefix and not body.get("message", "").startswith(failure_prefix):
            body["message"] = f"{failure_prefix}: {body['message']}"

        text = dumps_payload(body)
        code = str(body.get("code") or default_error_code)
        error_kind, retryable, external_unknown = _error_kind_for_code(code)
        return ToolResult.failure(
            text,
            error_kind=error_kind,
            retryable=retryable,
            external_state_unknown=external_unknown,
            diagnostic_summary=text,
        )

    # ok true / missing: treat as success for backward-compatible addon tools
    if isinstance(payload, (dict, list)):
        text = json.dumps(payload, ensure_ascii=False)
    else:
        text = json.dumps(result, ensure_ascii=False)
    return ToolResult.ok(text)


def map_bridge_exception(
    exc: BaseException,
    *,
    tool_name: str | None = None,
) -> ToolResult:
    """Map bridge transport exceptions to ToolResult.failure."""
    text = str(exc) or exc.__class__.__name__
    lower = text.lower()
    is_timeout = "timeout" in lower or "deadline" in lower
    is_conn = any(
        token in lower
        for token in ("disconnect", "connection", "unavailable", "closed", "not connected")
    )
    code = BlockErrorCode.ADDON_UNAVAILABLE
    body = build_error_response(
        code,
        f"Addon 桥接不可用: {text}" if not tool_name else f"{tool_name} 失败: Addon 桥接不可用 ({text})",
        transient=True,
    )
    return ToolResult.failure(
        dumps_payload(body),
        error_kind="TRANSIENT",
        retryable=True,
        external_state_unknown=bool(is_timeout and not is_conn),
        diagnostic_summary=text,
    )


def _error_kind_for_code(code: str) -> tuple[str, bool, bool]:
    """Return (error_kind, retryable, external_state_unknown)."""
    if code == BlockErrorCode.ADDON_UNAVAILABLE:
        return "TRANSIENT", True, False
    if code == BlockErrorCode.STATE_UNKNOWN:
        return "TRANSIENT", False, True
    if code in {
        BlockErrorCode.INVALID_ARGUMENT,
        BlockErrorCode.INVALID_COORDINATE,
        BlockErrorCode.BLOCK_UNKNOWN,
        BlockErrorCode.STATE_INVALID,
        BlockErrorCode.LIMIT_EXCEEDED,
    }:
        return "INVALID_ARGUMENT", False, False
    if code in {
        BlockErrorCode.PROTECTED_BLOCK,
        BlockErrorCode.PRECONDITION_FAILED,
        BlockErrorCode.PRECONDITION_CHANGED,
        BlockErrorCode.UNLOADED_CHUNK,
        BlockErrorCode.OUT_OF_BOUNDS,
    }:
        return "PERMANENT", False, False
    return "PERMANENT", False, False


async def call_block_capability(
    bridge: _BridgeClient | None,
    capability: str,
    payload: dict[str, Any],
) -> ToolResult:
    """Invoke a block capability and map the response to ToolResult.

    Never treats ok:false as success.
    """
    if bridge is None:
        body = build_error_response(
            BlockErrorCode.ADDON_UNAVAILABLE,
            "Addon 桥接不可用；可另行使用 run_minecraft_command 作为可选回退，本工具不会自动生成命令。",
        )
        return ToolResult.failure(
            dumps_payload(body),
            error_kind="TRANSIENT",
            retryable=True,
            diagnostic_summary="addon_bridge missing",
        )

    try:
        result = await bridge.request(capability, payload)
    except Exception as exc:
        return map_bridge_exception(exc, tool_name=capability)

    return map_addon_bridge_result(result)
