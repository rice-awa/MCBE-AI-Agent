"""Shared addon bridge result mapping for dedicated block tools and legacy addon tools."""

from __future__ import annotations

import json
from typing import Any, Protocol

from config.logging import get_logger
from config.redaction import redact_exception
from services.agent.block_ops.schema import (
    BlockErrorCode,
    build_error_response,
    dumps_payload,
)
from services.agent.tool_results import ToolResult

logger = get_logger(__name__)


class _BridgeClient(Protocol):
    async def request(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...


def _is_pre_mutation_transport_error(exc: BaseException) -> bool:
    """True when the failure happened before any world mutation could start.

    These must NOT be mapped to STATE_UNKNOWN: the host never successfully
    delivered (or never successfully started waiting for) a bridge request that
    could have written blocks.
    """
    name = type(exc).__name__
    text = str(exc)
    # FrameTooLargeError from SDK flow control — command never left the host.
    if name in {"FrameTooLargeError", "BridgeLimitError", "ProtocolError"}:
        return True
    if "FrameTooLarge" in name or "raw command too long" in text:
        return True
    if "commandLine" in text and ("too long" in text or "budget" in text or ">" in text):
        return True
    # Explicit host-side send failures that never reached the game.
    markers = (
        "命令执行失败: FrameTooLarge",
        "命令执行失败: raw command too long",
        "BridgeOutboundError",
        "bridge outbound failed before send",
        "bridge request never left host",
    )
    return any(marker in text for marker in markers)


def _is_bridge_timeout(exc: BaseException) -> bool:
    name = type(exc).__name__
    text = str(exc)
    if name in {"BridgeTimeoutError", "TimeoutError", "asyncio.TimeoutError"}:
        return True
    return "Bridge request timed out" in text or "bridge_request_timeout" in text


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
                "Addon 返回了无效响应；本次操作未发送成功结果。",
                retryable=False,
                external_state_unknown=False,
                fallback_allowed=False,
            )
        )
        return ToolResult.failure(
            text,
            error_kind="INTERNAL",
            retryable=False,
            diagnostic_summary="invalid bridge response type",
        )

    ok = result.get("ok")
    if not isinstance(ok, bool) or (ok is True and "payload" not in result):
        text = dumps_payload(
            build_error_response(
                BlockErrorCode.INTERNAL_ERROR,
                "Addon 返回了无效响应；本次操作未发送成功结果。",
                retryable=False,
                external_state_unknown=False,
                fallback_allowed=False,
            )
        )
        return ToolResult.failure(
            text,
            error_kind="INTERNAL",
            retryable=False,
            diagnostic_summary="invalid bridge response contract",
        )
    payload = result.get("payload", result)

    if ok is False:
        body: dict[str, Any]
        if isinstance(payload, dict):
            body = _safe_addon_error_body(payload.get("code") or default_error_code)
        else:
            body = _safe_addon_error_body(default_error_code)

        if failure_prefix:
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
    diagnostic_summary = redact_exception(exc) or exc.__class__.__name__
    error_type = exc.__class__.__name__
    pre_mutation = _is_pre_mutation_transport_error(exc)
    is_timeout = _is_bridge_timeout(exc)

    logger.warning(
        "block_bridge_exception",
        tool_name=tool_name,
        error_type=error_type,
        diagnostic_summary=diagnostic_summary,
        pre_mutation=pre_mutation,
        is_timeout=is_timeout,
    )

    # Payload never left the host (e.g. commandLine over 461 B). Safe to retry
    # after shrinking the plan; world state is known-unchanged by this call.
    if pre_mutation:
        body = build_error_response(
            BlockErrorCode.LIMIT_EXCEEDED,
            "出站帧超出 MCBE commandLine 字节预算，请求未发送。",
            retryable=True,
            external_state_unknown=False,
            fallback_allowed=False,
            reason="command_line_budget",
            hint=(
                "减小 batch.positions 数量或 fill AABB；继续使用 batch/fill，"
                "禁止拆成大量 place（禁止 place 风暴）；勿用命令绕过审批。"
            ),
        )
        return ToolResult.failure(
            dumps_payload(body),
            error_kind="INVALID_ARGUMENT",
            retryable=True,
            external_state_unknown=False,
            diagnostic_summary=diagnostic_summary,
            error_type=error_type,
        )

    if tool_name == "edit_blocks":
        # Timeout after a successful outbound means the addon may have written —
        # keep STATE_UNKNOWN + no-auto-retry contract.
        if is_timeout:
            message = (
                "方块修改桥接超时；外部状态未知，请勿自动重试或回退命令。"
                f" 诊断: {diagnostic_summary}"
            )
        else:
            message = (
                "方块修改调用失败；外部状态未知，请勿自动重试或回退命令。"
                f" 诊断: {diagnostic_summary}"
            )
        body = build_error_response(
            BlockErrorCode.STATE_UNKNOWN,
            message,
            retryable=False,
            external_state_unknown=True,
            fallback_allowed=False,
        )
        return ToolResult.failure(
            dumps_payload(body),
            error_kind="PERMANENT",
            retryable=False,
            external_state_unknown=True,
            diagnostic_summary=diagnostic_summary,
            error_type=error_type,
        )

    code = BlockErrorCode.ADDON_UNAVAILABLE
    body = build_error_response(
        code,
        "Addon 桥接不可用；可另行使用命令工具作为回退，但该命令仍需独立审批。",
        retryable=True,
        external_state_unknown=False,
        fallback_allowed=True,
    )
    return ToolResult.failure(
        dumps_payload(body),
        error_kind="TRANSIENT",
        retryable=True,
        external_state_unknown=False,
        diagnostic_summary=diagnostic_summary,
        error_type=error_type,
    )


def _safe_addon_error_body(code: Any) -> dict[str, Any]:
    """Return an external error envelope without mirroring bridge payload content."""
    try:
        stable_code = BlockErrorCode(str(code))
    except ValueError:
        stable_code = BlockErrorCode.INTERNAL_ERROR
    error_kind, retryable, external_unknown = _error_kind_for_code(stable_code)
    fallback_allowed = stable_code == BlockErrorCode.ADDON_UNAVAILABLE
    message = f"Addon 返回错误：{stable_code}。"
    if fallback_allowed:
        message += "可另行使用命令工具作为回退，但该命令仍需独立审批。"
    return build_error_response(
        stable_code,
        message,
        retryable=retryable,
        external_state_unknown=external_unknown,
        fallback_allowed=fallback_allowed,
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
            "Addon 桥接不可用；可另行使用命令工具作为回退，但该命令仍需独立审批。",
            retryable=True,
            external_state_unknown=False,
            fallback_allowed=True,
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
