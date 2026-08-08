"""Limit normalization, command-line byte-budget estimation and payload injection.

Extracted from ``tools_impl`` to eliminate ``target.py``'s reverse import of
``tools_impl`` (the payload-building path needed ``apply_limits_to_payload``
but shouldn't depend on the entire tool-implementation module).
"""
from __future__ import annotations

import json
from typing import Any

from services.agent.block_ops.config import DEFAULT_COMMAND_LINE_BYTE_BUDGET
from services.agent.block_ops.schema import (
    BlockErrorCode,
    _host_limit_error,
    build_error_response,
    dumps_payload,
    fallback_allowed_for_code,
)
from services.agent.tool_results import ToolResult

# Stable request_id length matching production ``addon-{uuid4().hex}`` (38 chars).
_BRIDGE_REQUEST_ID_PLACEHOLDER = f"addon-{'0' * 32}"
_BRIDGE_REQ_PREFIX = "scriptevent mcbews:bridge_req "
_COMMAND_LINE_BUDGET_HINT = (
    "缩小 fill AABB 或减少 states；禁止拆成大量 place；勿用命令绕过审批。"
)
_COMMAND_LINE_BUDGET_MESSAGE = "出站帧超出 MCBE commandLine 字节预算，请求未发送。"


def normalize_limits_input(
    limits: dict[str, int] | None,
) -> dict[str, int] | None:
    """Accept host-side limit names and map them to Add-on top-level fields.

    Host settings use ``max_discrete_positions``; Add-on handlers read:
    - inspect: ``max_positions``
    - place/batch: ``max_discrete``
    - fill: ``max_fill_volume``, ``cells_per_tick``
    """
    if not limits:
        return None
    max_discrete = limits.get("max_discrete")
    if max_discrete is None:
        max_discrete = limits.get("max_discrete_positions")
    if max_discrete is None:
        max_discrete = limits.get("max_positions")
    out: dict[str, int] = {}
    if max_discrete is not None:
        out["max_discrete"] = int(max_discrete)
        out["max_positions"] = int(max_discrete)
    if "max_fill_volume" in limits and limits["max_fill_volume"] is not None:
        out["max_fill_volume"] = int(limits["max_fill_volume"])
    if "cells_per_tick" in limits and limits["cells_per_tick"] is not None:
        out["cells_per_tick"] = int(limits["cells_per_tick"])
    return out or None


def apply_limits_to_payload(
    payload: dict[str, Any],
    limits: dict[str, int] | None,
) -> dict[str, Any]:
    """Write Add-on-facing limit fields at the **top level** of the bridge payload.

    Nested ``payload.limits`` is intentionally not used: Add-on common/fill/inspect
    read top-level keys only.
    """
    normalized = normalize_limits_input(limits)
    if not normalized:
        return payload
    payload.update(normalized)
    return payload


def _aabb_volume(
    from_pos: dict[str, Any],
    to_pos: dict[str, Any],
) -> int | None:
    """Return inclusive AABB cell count, or None when coords are not integers."""
    try:
        if not all(k in from_pos for k in ("x", "y", "z")):
            return None
        if not all(k in to_pos for k in ("x", "y", "z")):
            return None
        dx = abs(int(to_pos["x"]) - int(from_pos["x"])) + 1
        dy = abs(int(to_pos["y"]) - int(from_pos["y"])) + 1
        dz = abs(int(to_pos["z"]) - int(from_pos["z"])) + 1
        return dx * dy * dz
    except (TypeError, ValueError):
        return None


def _request_fill_aabb(
    args: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Extract fill corner pair from request args (from/to or from_pos/to_pos)."""
    from_pos = args.get("from") if isinstance(args.get("from"), dict) else None
    to_pos = args.get("to") if isinstance(args.get("to"), dict) else None
    if from_pos is None and isinstance(args.get("from_pos"), dict):
        from_pos = args["from_pos"]
    if to_pos is None and isinstance(args.get("to_pos"), dict):
        to_pos = args["to_pos"]
    if (
        isinstance(from_pos, dict)
        and isinstance(to_pos, dict)
        and all(k in from_pos for k in ("x", "y", "z"))
        and all(k in to_pos for k in ("x", "y", "z"))
    ):
        return from_pos, to_pos
    return None, None


def estimate_bridge_command_line_bytes(
    capability: str,
    payload: dict[str, Any],
    *,
    request_id: str = _BRIDGE_REQUEST_ID_PLACEHOLDER,
) -> int:
    """Estimate MCBE ``commandLine`` UTF-8 bytes for a bridge capability request.

    Mirrors SDK ``encode_bridge_request`` wire shape:
    ``scriptevent mcbews:bridge_req`` + compact JSON
    ``{v, request_id, capability, payload}`` (separators ``(",", ":")``).
    """
    wire = {
        "v": 2,
        "request_id": request_id,
        "capability": capability,
        "payload": payload,
    }
    body = json.dumps(wire, ensure_ascii=False, separators=(",", ":"))
    return len((_BRIDGE_REQ_PREFIX + body).encode("utf-8"))


def _suggested_max_discrete_for_budget(
    capability: str,
    payload: dict[str, Any],
    *,
    budget: int,
) -> int | None:
    """Largest ``positions`` count that still fits under budget, if applicable."""
    positions = payload.get("positions")
    if not isinstance(positions, list) or not positions:
        return None
    if estimate_bridge_command_line_bytes(capability, payload) < budget:
        return None

    lo, hi = 0, len(positions)
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        trial = dict(payload)
        trial["positions"] = positions[:mid]
        if estimate_bridge_command_line_bytes(capability, trial) < budget:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best if best > 0 else 0


def command_line_budget_exceeded_result(
    *,
    estimated_bytes: int,
    budget: int = DEFAULT_COMMAND_LINE_BYTE_BUDGET,
    suggested_max_discrete: int | None = None,
    matched_count: int | None = None,
    volume: int | None = None,
) -> ToolResult:
    """Host-side LIMIT when the outbound bridge frame would exceed commandLine budget."""
    fields: dict[str, Any] = {
        "retryable": True,
        "external_state_unknown": False,
        "fallback_allowed": fallback_allowed_for_code(BlockErrorCode.LIMIT_EXCEEDED),
        "reason": "command_line_budget",
        "hint": _COMMAND_LINE_BUDGET_HINT,
        "estimated_bytes": estimated_bytes,
        "budget": budget,
    }
    if suggested_max_discrete is not None:
        fields["suggested_max_discrete"] = suggested_max_discrete
    if matched_count is not None:
        fields["matched_count"] = matched_count
    if volume is not None:
        fields["volume"] = volume
    body = build_error_response(
        BlockErrorCode.LIMIT_EXCEEDED,
        _COMMAND_LINE_BUDGET_MESSAGE,
        **fields,
    )
    return ToolResult.failure(
        dumps_payload(body),
        error_kind="INVALID_ARGUMENT",
        retryable=True,
        external_state_unknown=False,
        diagnostic_summary=(
            f"command_line_budget estimated={estimated_bytes} budget={budget}"
        ),
    )


def check_bridge_command_line_budget(
    capability: str,
    payload: dict[str, Any],
    *,
    budget: int | None = None,
    matched_count: int | None = None,
    volume: int | None = None,
) -> ToolResult | None:
    """Return LIMIT ToolResult when estimated wire size is at/over budget; else None."""
    limit = DEFAULT_COMMAND_LINE_BYTE_BUDGET if budget is None else int(budget)
    estimated = estimate_bridge_command_line_bytes(capability, payload)
    if estimated < limit:
        return None
    suggested = _suggested_max_discrete_for_budget(
        capability, payload, budget=limit
    )
    # Prefer explicit matched_count; fall back to positions / locked length.
    effective_matched = matched_count
    if effective_matched is None:
        positions = payload.get("positions")
        if isinstance(positions, list):
            effective_matched = len(positions)
        else:
            locked = payload.get("locked_targets")
            if isinstance(locked, list):
                effective_matched = len(locked)
    effective_volume = volume
    if effective_volume is None:
        from_pos = payload.get("from")
        to_pos = payload.get("to")
        if isinstance(from_pos, dict) and isinstance(to_pos, dict):
            effective_volume = _aabb_volume(from_pos, to_pos)
    return command_line_budget_exceeded_result(
        estimated_bytes=estimated,
        budget=limit,
        suggested_max_discrete=suggested,
        matched_count=effective_matched,
        volume=effective_volume,
    )


def compact_locked_targets_for_wire(
    locked_targets: list[dict[str, Any]] | None,
    *,
    dimension: str | None,
) -> list[dict[str, Any]] | None:
    """Drop per-cell dimension when it matches the top-level dimension."""
    if not locked_targets:
        return locked_targets
    out: list[dict[str, Any]] = []
    for item in locked_targets:
        if not isinstance(item, dict):
            continue
        cell: dict[str, Any] = {
            "x": item.get("x"),
            "y": item.get("y"),
            "z": item.get("z"),
        }
        cell_dim = item.get("dimension")
        if cell_dim is not None and (dimension is None or cell_dim != dimension):
            cell["dimension"] = cell_dim
        out.append(cell)
    return out


def should_omit_locked_targets_on_wire(
    *,
    mode: str,
    phase: str,
    from_pos: dict[str, Any] | None,
    to_pos: dict[str, Any] | None,
    position: dict[str, Any] | None,
    positions: list[dict[str, Any]] | None,
    locked_targets: list[dict[str, Any]] | None,
    coordinate_mode: str,
    max_locked_targets_on_wire: int | None = None,
) -> bool:
    """True when absolute execute geometry freezes targets without replaying cells.

    MCBE ``commandLine`` hard budget is ~461 bytes. Even sparse fill locked lists
    blow that budget and never leave the host (bridge timeout → STATE_UNKNOWN).
    On absolute execute, place/batch/fill geometry (position / positions /
    from+to) is authoritative; omit ``locked_targets`` on the wire. Audit and
    approval records may still hold the full list.

    ``max_locked_targets_on_wire`` default 0 means absolute execute always prefers
    omit when geometry is complete (does not force shipping locked cells). Values
    ``>0`` only cap the list on non-omit paths (see ``build_edit_payload``).
    """
    del max_locked_targets_on_wire  # policy knob reserved for non-omit cap path
    if phase != "execute" or coordinate_mode != "absolute":
        return False
    if mode == "fill" and isinstance(from_pos, dict) and isinstance(to_pos, dict):
        return True
    if mode == "place" and isinstance(position, dict):
        return True
    if mode == "batch" and isinstance(positions, list) and positions:
        return True
    # No complete absolute geometry — only omit if there is nothing to send.
    return not locked_targets


def locked_targets_wire_limit_exceeded(
    locked_targets: list[dict[str, Any]] | None,
    *,
    max_locked_targets_on_wire: int,
) -> ToolResult | None:
    """When a non-omit path must ship locked cells and the list exceeds the cap.

    ``max_locked_targets_on_wire=0`` means "prefer omit on absolute" and does not
    enforce a positive cap; only values ``>0`` trigger LIMIT on ship paths.
    """
    if max_locked_targets_on_wire <= 0 or not locked_targets:
        return None
    if len(locked_targets) <= max_locked_targets_on_wire:
        return None
    body = build_error_response(
        BlockErrorCode.LIMIT_EXCEEDED,
        (
            "锁定方块数超过线上传输限制。"
            "缩小 fill AABB 或减少 positions。"
        ),
        retryable=True,
        external_state_unknown=False,
        fallback_allowed=False,
        reason="max_locked_targets_on_wire",
        hint=_COMMAND_LINE_BUDGET_HINT,
        count=len(locked_targets),
        max_locked_targets_on_wire=max_locked_targets_on_wire,
        suggested_max_discrete=max_locked_targets_on_wire,
        matched_count=len(locked_targets),
    )
    return ToolResult.failure(
        dumps_payload(body),
        error_kind="INVALID_ARGUMENT",
        retryable=True,
        external_state_unknown=False,
        diagnostic_summary=(
            f"locked_targets_wire_limit count={len(locked_targets)} "
            f"max={max_locked_targets_on_wire}"
        ),
    )
