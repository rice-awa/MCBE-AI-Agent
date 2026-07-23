"""Shared addon bridge result mapping for dedicated block tools and legacy addon tools."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from config.logging import get_logger
from config.redaction import redact_exception
from services.agent.block_ops.project import project_block_result_for_model
from services.agent.block_ops.schema import (
    BlockErrorCode,
    build_error_response,
    dumps_payload,
)
from services.agent.tool_results import ToolResult

logger = get_logger(__name__)

_LIMIT_HINT = (
    "减小 batch.positions 数量或 fill AABB；继续使用 batch/fill，"
    "禁止拆成大量 place（禁止 place 风暴）；勿用命令绕过审批。"
)
_LIMIT_MESSAGE = "出站帧超出 MCBE commandLine 字节预算，请求未发送。"
_PRECONDITION_HINT = "跳过该格，或设置 replace_any=true 后重新调用（需再审批）。"
_PRECONDITION_NOT_AIR_MESSAGE = "目标非空气（默认仅替换空气）。"
_SENSITIVE_MESSAGE_RE = re.compile(
    r"(password|token|secret|api[_-]?key|authorization|bearer\s+\S+)",
    re.IGNORECASE,
)
_BLOCK_CAPABILITIES = frozenset({"edit_blocks", "inspect_block"})


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
    project_for_model: bool = False,
    mode: str | None = None,
    authorized_bounds: dict[str, Any] | None = None,
) -> ToolResult:
    """Map an addon bridge response dict to ToolResult.

    - ok:true → ToolResult.ok with JSON text of payload (or whole result)
    - ok:false → ToolResult.failure (never success), JSON body remains informative
    - When ``project_for_model`` is True (block tools), success bodies are slimmed.
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
            body = _safe_addon_error_body(payload.get("code") or default_error_code, payload)
        else:
            body = _safe_addon_error_body(default_error_code)

        if failure_prefix:
            body["message"] = f"{failure_prefix}: {body['message']}"

        text = dumps_payload(body)
        code = str(body.get("code") or default_error_code)
        error_kind, retryable, external_unknown = _error_kind_for_code(code)
        # Prefer explicit body retryable when present (LIMIT host/addon alignment).
        if isinstance(body.get("retryable"), bool):
            retryable = body["retryable"]
        return ToolResult.failure(
            text,
            error_kind=error_kind,
            retryable=retryable,
            external_state_unknown=external_unknown,
            diagnostic_summary=text,
        )

    # ok true / missing: treat as success for backward-compatible addon tools
    if isinstance(payload, (dict, list)):
        model_payload: Any = payload
        if project_for_model and isinstance(payload, dict):
            # Audit/debug: keep a compact note that full payload existed.
            logger.debug(
                "block_bridge_success_raw",
                mode=mode or payload.get("mode"),
                keys=sorted(str(k) for k in payload.keys()),
            )
            model_payload = project_block_result_for_model(
                payload,
                mode=mode,
                authorized_bounds=authorized_bounds,
            )
        text = json.dumps(model_payload, ensure_ascii=False)
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
            _LIMIT_MESSAGE,
            retryable=True,
            external_state_unknown=False,
            fallback_allowed=False,
            reason="command_line_budget",
            hint=_LIMIT_HINT,
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


def _looks_sensitive(text: str) -> bool:
    return bool(_SENSITIVE_MESSAGE_RE.search(text))


def _xyz_target(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("x", "y", "z"):
        if key in value and value[key] is not None:
            out[key] = value[key]
    return out if out else None


def _extract_actual_type_id(payload: dict[str, Any]) -> str | None:
    actual = payload.get("actual")
    if isinstance(actual, dict):
        type_id = actual.get("type_id")
        if isinstance(type_id, str) and type_id and not _looks_sensitive(type_id):
            return type_id
    type_id = payload.get("actual_type_id")
    if isinstance(type_id, str) and type_id and not _looks_sensitive(type_id):
        return type_id
    return None


def _precondition_message(payload: dict[str, Any]) -> str:
    raw = payload.get("message")
    if isinstance(raw, str) and raw.strip() and not _looks_sensitive(raw):
        lower = raw.lower()
        if (
            "not air" in lower
            or "非空气" in raw
            or "only air" in lower
            or "replace air" in lower
        ):
            return _PRECONDITION_NOT_AIR_MESSAGE
        # Short operational phrases from addon are useful; reject long dumps.
        if len(raw) <= 120 and "\n" not in raw and "traceback" not in lower:
            return raw.strip()
    return _PRECONDITION_NOT_AIR_MESSAGE if payload.get("actual") else "前置条件失败。"


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_addon_error_body(
    code: Any,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an external error envelope with whitelist decision fields only.

    Non-whitelist codes never mirror untrusted message / stack / path content.
    """
    try:
        stable_code = BlockErrorCode(str(code))
    except ValueError:
        stable_code = BlockErrorCode.INTERNAL_ERROR

    error_kind, retryable, external_unknown = _error_kind_for_code(stable_code)
    fallback_allowed = stable_code == BlockErrorCode.ADDON_UNAVAILABLE
    src = payload if isinstance(payload, dict) else {}

    if stable_code == BlockErrorCode.LIMIT_EXCEEDED:
        message = _LIMIT_MESSAGE
        raw_msg = src.get("message")
        if (
            isinstance(raw_msg, str)
            and raw_msg.strip()
            and not _looks_sensitive(raw_msg)
            and len(raw_msg) <= 160
        ):
            message = raw_msg.strip()
        fields: dict[str, Any] = {
            "retryable": True,
            "external_state_unknown": False,
            "fallback_allowed": False,
            "hint": _LIMIT_HINT,
        }
        reason = src.get("reason")
        if isinstance(reason, str) and reason and not _looks_sensitive(reason):
            fields["reason"] = reason
        else:
            fields["reason"] = "command_line_budget"
        for key in ("suggested_max_discrete", "matched_count", "volume"):
            parsed = _optional_int(src.get(key))
            if parsed is not None:
                fields[key] = parsed
        return build_error_response(stable_code, message, **fields)

    if stable_code in {
        BlockErrorCode.PRECONDITION_FAILED,
        BlockErrorCode.PRECONDITION_CHANGED,
    }:
        fields = {
            "retryable": False,
            "external_state_unknown": False,
            "fallback_allowed": False,
            "hint": _PRECONDITION_HINT,
        }
        actual_type_id = _extract_actual_type_id(src)
        if actual_type_id is not None:
            fields["actual_type_id"] = actual_type_id
        target = _xyz_target(src.get("target"))
        if target is None:
            target = _xyz_target(src.get("position"))
        if target is not None:
            fields["target"] = target
        message = _precondition_message(src)
        return build_error_response(stable_code, message, **fields)

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
    if code == BlockErrorCode.LIMIT_EXCEEDED:
        # Safe to retry after shrinking the plan; host never mutated on budget miss.
        return "INVALID_ARGUMENT", True, False
    if code in {
        BlockErrorCode.INVALID_ARGUMENT,
        BlockErrorCode.INVALID_COORDINATE,
        BlockErrorCode.BLOCK_UNKNOWN,
        BlockErrorCode.STATE_INVALID,
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
    *,
    mode: str | None = None,
    authorized_bounds: dict[str, Any] | None = None,
) -> ToolResult:
    """Invoke a block capability and map the response to ToolResult.

    Never treats ok:false as success. Block tools project success bodies for the model.
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

    # Preflight responses feed merge_canonical / approval; keep full payload.
    # Only project execute (model-visible) success bodies for block tools.
    phase = payload.get("phase") if isinstance(payload, dict) else None
    project = capability in _BLOCK_CAPABILITIES and phase != "preflight"
    effective_mode = mode
    if effective_mode is None and isinstance(payload, dict):
        raw_mode = payload.get("mode")
        if isinstance(raw_mode, str):
            effective_mode = raw_mode
    return map_addon_bridge_result(
        result,
        project_for_model=project,
        mode=effective_mode,
        authorized_bounds=authorized_bounds,
    )
