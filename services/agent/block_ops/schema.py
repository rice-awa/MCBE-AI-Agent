"""Versioned block-ops response schema and stable error codes."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any


BLOCK_OPS_SCHEMA_VERSION = "1"


class BlockErrorCode(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    INVALID_COORDINATE = "INVALID_COORDINATE"
    BLOCK_UNKNOWN = "BLOCK_UNKNOWN"
    STATE_INVALID = "STATE_INVALID"
    PROTECTED_BLOCK = "PROTECTED_BLOCK"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    PRECONDITION_CHANGED = "PRECONDITION_CHANGED"
    UNLOADED_CHUNK = "UNLOADED_CHUNK"
    OUT_OF_BOUNDS = "OUT_OF_BOUNDS"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    ADDON_UNAVAILABLE = "ADDON_UNAVAILABLE"
    STATE_UNKNOWN = "STATE_UNKNOWN"
    INTERNAL_ERROR = "INTERNAL_ERROR"


def build_success_response(**fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": BLOCK_OPS_SCHEMA_VERSION,
        "ok": True,
    }
    body.update(fields)
    return body


def build_error_response(
    code: BlockErrorCode | str,
    message: str,
    **fields: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": BLOCK_OPS_SCHEMA_VERSION,
        "ok": False,
        "code": str(code),
        "message": message,
    }
    body.update(fields)
    return body


def build_internal_error_response(
    message: str = "方块工具内部参数处理失败；本次操作未发送到 Add-on",
    **fields: Any,
) -> dict[str, Any]:
    """Stable envelope for host-side projection failures (never reached Add-on)."""
    return build_error_response(
        BlockErrorCode.INTERNAL_ERROR,
        message,
        retryable=False,
        external_state_unknown=False,
        fallback_allowed=False,
        **fields,
    )


def build_state_unknown_response(
    message: str = "方块修改调用失败；外部状态未知，请勿自动重试或回退命令。",
    **fields: Any,
) -> dict[str, Any]:
    """Stable envelope for unclassified world-mutating failures after invocation starts."""
    return build_error_response(
        BlockErrorCode.STATE_UNKNOWN,
        message,
        retryable=False,
        external_state_unknown=True,
        fallback_allowed=False,
        **fields,
    )


def dumps_success(**fields: Any) -> str:
    return json.dumps(build_success_response(**fields), ensure_ascii=False)


def dumps_error(code: BlockErrorCode | str, message: str, **fields: Any) -> str:
    return json.dumps(
        build_error_response(code, message, **fields),
        ensure_ascii=False,
    )


def dumps_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)
