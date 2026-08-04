"""Model-facing inspect_block / place_block / fill_block tool implementations."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext

from config.logging import get_logger
from config.redaction import redact_exception
from models.agent import AgentDependencies
from services.agent.block_ops.bridge import call_block_capability
from services.agent.block_ops.capability import (
    BlockCapabilityStatus,
    ensure_block_capability,
)
from services.agent.block_ops.config import (
    DEFAULT_COMMAND_LINE_BYTE_BUDGET,
    get_block_tools_limits,
    get_command_line_byte_budget,
)
from services.agent.block_ops.schema import (
    BlockErrorCode,
    _host_limit_error,
    build_error_response,
    build_state_unknown_response,
    dumps_payload,
)
from services.agent.block_ops.target import (
    normalize_aabb_corners as _normalize_aabb_corners,
)
from services.agent.tool_results import ToolResult

logger = get_logger(__name__)

BLOCK_TOOL_NAMES = frozenset({"inspect_block", "place_block", "fill_block"})

# Stable request_id length matching production ``addon-{uuid4().hex}`` (38 chars).
_BRIDGE_REQUEST_ID_PLACEHOLDER = f"addon-{'0' * 32}"
_BRIDGE_REQ_PREFIX = "scriptevent mcbews:bridge_req "
_COMMAND_LINE_BUDGET_HINT = (
    "缩小 fill AABB 或减少 states；禁止拆成大量 place；勿用命令绕过审批。"
)
_COMMAND_LINE_BUDGET_MESSAGE = "出站帧超出 MCBE commandLine 字节预算，请求未发送。"


@dataclass(frozen=True)
class BlockPreflightPlan:
    """Separated preflight artifacts for approval, invocation, and evidence."""

    authorized_args: dict[str, Any]
    execute_args: dict[str, Any]
    approval_metadata: dict[str, Any]
    plan_id: str = ""


def _connection_id(deps: AgentDependencies) -> str:
    return str(deps.connection_id)


def _plain_tool_data(value: Any) -> Any:
    """Convert validated Pydantic arguments to plain JSON-like data once."""
    if isinstance(value, dict):
        return {key: _plain_tool_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_tool_data(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _plain_tool_data(model_dump(by_alias=True, exclude_none=True))
    return value


def _capability_failure_result(
    reason: str,
    *,
    code: BlockErrorCode = BlockErrorCode.ADDON_UNAVAILABLE,
) -> ToolResult:
    body = build_error_response(
        code,
        (
            f"{reason}。"
            "可另行使用命令工具作为回退，但该命令仍需独立审批。"
        ),
        retryable=True,
        external_state_unknown=False,
        fallback_allowed=True,
    )
    return ToolResult.failure(
        dumps_payload(body),
        error_kind="TRANSIENT",
        retryable=True,
        diagnostic_summary=reason,
    )


async def _require_supported(ctx: RunContext[AgentDependencies]) -> ToolResult | None:
    """Return failure ToolResult if capability is not SUPPORTED; else None."""
    deps = ctx.deps
    record = await ensure_block_capability(_connection_id(deps), deps.addon_bridge)
    if record.status == BlockCapabilityStatus.SUPPORTED:
        return None
    if record.status == BlockCapabilityStatus.UNSUPPORTED:
        return _capability_failure_result(
            "当前 Add-on 不支持专用方块工具（能力握手缺失 block_ops）",
            code=BlockErrorCode.UNSUPPORTED_CAPABILITY,
        )
    if record.status == BlockCapabilityStatus.UNAVAILABLE:
        return _capability_failure_result("Addon 桥接不可用或超时")
    logger.warning(
        "block_capability_probe_failed",
        connection_id=_connection_id(deps),
        run_id=deps.run_id,
        player_name=deps.player_name,
        diagnostic_summary=redact_exception(record.detail),
    )
    return _capability_failure_result("方块能力探测失败")


def _validate_inspect_args(
    *,
    coordinate_mode: str,
    dimension: str | None,
    position: dict[str, Any] | None,
    positions: list[dict[str, Any]] | None,
    max_positions: int,
) -> ToolResult | None:
    if coordinate_mode not in {"absolute", "player_relative"}:
        return _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "coordinate_mode 必须是 absolute 或 player_relative",
        )
    has_single = position is not None
    has_multi = positions is not None
    if not has_single and not has_multi:
        return _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "必须提供 position 或 positions",
        )
    if has_single and has_multi:
        return _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "position 与 positions 互斥，只能提供其中一个",
        )
    if positions is not None:
        if not isinstance(positions, list) or not positions:
            return _host_limit_error(
                BlockErrorCode.INVALID_ARGUMENT,
                "positions 必须是非空列表",
            )
        if len(positions) > max_positions:
            return _host_limit_error(
                BlockErrorCode.LIMIT_EXCEEDED,
                f"positions 数量 {len(positions)} 超过上限 {max_positions}",
                limit=max_positions,
                count=len(positions),
            )
    return None


def _validate_inspect_target(
    target: Any,
    *,
    dimension: str | None,
    max_positions: int,
    max_fill_volume: int,
) -> tuple[Any | None, ToolResult | None]:
    """Validate a unified ``target`` and return (normalized_target, error).

    The normalized target is a :class:`NormalizedTarget` ready for payload
    construction. Volume / count limits are enforced here so the host rejects
    oversized targets before the bridge call.
    """
    from services.agent.block_ops.target import normalize_inspect_target

    normalized, error = normalize_inspect_target(target)
    if error is not None:
        return None, error
    assert normalized is not None
    if normalized.shape == "positions":
        count = len(normalized.positions or [])
        if count > max_positions:
            return None, _host_limit_error(
                BlockErrorCode.LIMIT_EXCEEDED,
                f"positions 数量 {count} 超过上限 {max_positions}",
                limit=max_positions,
                count=count,
            )
    else:
        # Box: rough host-side volume check when absolute coords present.
        from_pos = normalized.box_from
        to_pos = normalized.box_to
        try:
            if (
                isinstance(from_pos, dict)
                and isinstance(to_pos, dict)
                and all(k in from_pos for k in ("x", "y", "z"))
                and all(k in to_pos for k in ("x", "y", "z"))
            ):
                dx = abs(int(to_pos["x"]) - int(from_pos["x"])) + 1
                dy = abs(int(to_pos["y"]) - int(from_pos["y"])) + 1
                dz = abs(int(to_pos["z"]) - int(from_pos["z"])) + 1
                volume = dx * dy * dz
                if volume > max_fill_volume:
                    return None, _host_limit_error(
                        BlockErrorCode.LIMIT_EXCEEDED,
                        f"box 体积 {volume} 超过上限 {max_fill_volume}",
                        limit=max_fill_volume,
                        volume=volume,
                    )
        except (TypeError, ValueError):
            pass
    return normalized, None


def _validate_edit_args(
    *,
    mode: str,
    coordinate_mode: str,
    dimension: str | None,
    position: dict[str, Any] | None,
    positions: list[dict[str, Any]] | None,
    from_pos: dict[str, Any] | None,
    to_pos: dict[str, Any] | None,
    type_id: str | None,
    replace_any: bool,
    expected_previous: dict[str, Any] | None,
    max_positions: int,
    max_fill_volume: int,
) -> ToolResult | None:
    if mode not in {"place", "batch", "fill"}:
        return _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "mode 必须是 place、batch 或 fill",
        )
    if coordinate_mode not in {"absolute", "player_relative"}:
        return _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "coordinate_mode 必须是 absolute 或 player_relative",
        )
    if not type_id or not str(type_id).strip():
        return _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "type_id 必填",
        )
    if replace_any and expected_previous is not None:
        return _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "replace_any 与 expected_previous 互斥",
        )

    if mode == "place":
        if position is None:
            return _host_limit_error(BlockErrorCode.INVALID_ARGUMENT, "place 模式必须提供 position")
        if positions is not None or from_pos is not None or to_pos is not None:
            return _host_limit_error(
                BlockErrorCode.INVALID_ARGUMENT,
                "place 模式只接受 position",
            )
    elif mode == "batch":
        if not positions or not isinstance(positions, list):
            return _host_limit_error(
                BlockErrorCode.INVALID_ARGUMENT,
                "batch 模式必须提供非空 positions",
            )
        if len(positions) > max_positions:
            return _host_limit_error(
                BlockErrorCode.LIMIT_EXCEEDED,
                f"positions 数量 {len(positions)} 超过上限 {max_positions}",
                limit=max_positions,
                count=len(positions),
            )
        if position is not None or from_pos is not None or to_pos is not None:
            return _host_limit_error(
                BlockErrorCode.INVALID_ARGUMENT,
                "batch 模式只接受 positions",
            )
    else:  # fill
        if from_pos is None or to_pos is None:
            return _host_limit_error(
                BlockErrorCode.INVALID_ARGUMENT,
                "fill 模式必须提供 from 与 to",
            )
        if position is not None or positions is not None:
            return _host_limit_error(
                BlockErrorCode.INVALID_ARGUMENT,
                "fill 模式只接受 from/to",
            )
        # Host-side rough volume check when absolute coords present (addon also enforces).
        try:
            if (
                coordinate_mode == "absolute"
                and all(k in from_pos for k in ("x", "y", "z"))
                and all(k in to_pos for k in ("x", "y", "z"))
            ):
                dx = abs(int(to_pos["x"]) - int(from_pos["x"])) + 1
                dy = abs(int(to_pos["y"]) - int(from_pos["y"])) + 1
                dz = abs(int(to_pos["z"]) - int(from_pos["z"])) + 1
                volume = dx * dy * dz
                if volume > max_fill_volume:
                    return _host_limit_error(
                        BlockErrorCode.LIMIT_EXCEEDED,
                        f"fill 体积 {volume} 超过上限 {max_fill_volume}",
                        limit=max_fill_volume,
                        volume=volume,
                    )
        except (TypeError, ValueError):
            pass
    return None


def _normalize_limits_input(limits: dict[str, int] | None) -> dict[str, int] | None:
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


def apply_limits_to_payload(payload: dict[str, Any], limits: dict[str, int] | None) -> dict[str, Any]:
    """Write Add-on-facing limit fields at the **top level** of the bridge payload.

    Nested ``payload.limits`` is intentionally not used: Add-on common/fill/inspect
    read top-level keys only.
    """
    normalized = _normalize_limits_input(limits)
    if not normalized:
        return payload
    payload.update(normalized)
    return payload


def _aabb_volume(from_pos: dict[str, Any], to_pos: dict[str, Any]) -> int | None:
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


def _normalize_position_array(
    value: Any,
    *,
    field_name: str,
) -> tuple[dict[str, Any] | None, ToolResult | None]:
    """Normalize an ``[x, y, z]`` int array into an ``{x, y, z}`` dict.

    Wrong-length / non-list / float / string / bool inputs yield a structured
    INVALID_COORDINATE result (never a Python exception), matching the
    int-only absolute-coordinate contract (spec §3.4).
    """
    if not isinstance(value, list) or len(value) != 3:
        return None, _host_limit_error(
            BlockErrorCode.INVALID_COORDINATE,
            f"{field_name} 必须是恰好 3 个整数的坐标数组 [x, y, z]",
        )
    for i, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, int):
            return None, _host_limit_error(
                BlockErrorCode.INVALID_COORDINATE,
                f"{field_name}[{i}] 必须是整数坐标",
            )
    return {"x": value[0], "y": value[1], "z": value[2]}, None


def _normalize_single_op_block(
    block: Any,
    states: dict[str, Any] | None,
) -> tuple[str, dict[str, Any] | None, ToolResult | None]:
    """Normalize ``block`` (+ optional separate ``states``) for single-op tools.

    Returns ``(type_id, effective_states, error)``; ``error`` is a structured
    INVALID_ARGUMENT result when no usable type_id is present.
    """
    block_info, _repairs = _normalize_block_input(block)
    type_id = str(block_info.get("type_id") or "").strip()
    if not type_id:
        return "", None, _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "block.type_id 必填",
        )
    effective_states = states
    if effective_states is None:
        effective_states = block_info.get("states")
    if effective_states is not None and not isinstance(effective_states, dict):
        return "", None, _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "states 必须是对象",
        )
    return type_id, effective_states, None


def _expect_to_legacy(
    expect: Any,
) -> tuple[bool, dict[str, Any] | None, ToolResult | None]:
    """Map an ``expect`` value to ``(replace_any, expected_previous)``.

    ``air`` (default) -> replace air only; ``any`` -> replace any block;
    a type_id (or ``{type_id, states}``) -> expected_previous. An empty
    type_id in the descriptor is rejected as INVALID_ARGUMENT.
    """
    expect_info = _normalize_expect(expect)
    if expect_info["kind"] in {"type", "permutation"} and not expect_info.get("type_id"):
        return False, None, _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "expect.type_id 必填",
        )
    replace_any, expected_previous = _expect_info_to_legacy(expect_info)
    return replace_any, expected_previous, None


def _normalize_block_id(
    raw: Any,
    *,
    field_name: str = "block.type_id",
) -> tuple[str, list[str]]:
    """Lowercase a block type_id and ensure the ``minecraft:`` prefix.

    Returns ``(normalized, repairs)`` where ``repair`` records mutations so the
    host can surface them to the model as evidence. Invalid input yields an
    empty type_id (caller is responsible for rejecting that).
    """
    if not isinstance(raw, str) or not raw.strip():
        return "", []
    value = raw.strip().lower()
    repairs: list[Any] = []
    if value != raw.strip():
        repairs.append(f"{field_name}: lowercased {raw.strip()!r} -> {value!r}")
    if ":" not in value:
        value = f"minecraft:{value}"
        repairs.append(f"{field_name}: added namespace -> {value!r}")
    return value, repairs


def _normalize_block_input(block: Any) -> tuple[dict[str, Any], list[str]]:
    """Accept a ``block`` spec as a string type_id or ``{type_id, states}``.

    Returns ``({"type_id": ..., "states": ...}, repairs)``. String input yields
    ``states=None``; object input keeps ``states`` only when it is a dict.
    """
    if block is None:
        return {"type_id": "", "states": None}, []
    if isinstance(block, str):
        type_id, repairs = _normalize_block_id(block)
        return {"type_id": type_id, "states": None}, repairs
    if isinstance(block, dict):
        type_id, repairs = _normalize_block_id(block.get("type_id"))
        states = block.get("states")
        if not isinstance(states, dict):
            states = None
        return {"type_id": type_id, "states": states}, repairs
    return {"type_id": "", "states": None}, []


def _normalize_expect(
    expect: Any,
    repairs: list[str] | None = None,
) -> dict[str, Any]:
    """Normalize an ``expect`` spec to a tagged descriptor.

    Shapes:
      - omitted / "air"  -> {"kind": "air"}     (default: replace air only)
      - "any"            -> {"kind": "any"}     (replace any existing block)
      - "minecraft:..."  -> {"kind": "type", "type_id": ...}
      - {type_id, states?} -> {"kind": "permutation"|"type", ...}
    """
    if expect is None or expect == "air":
        return {"kind": "air"}
    if expect == "any":
        return {"kind": "any"}
    if isinstance(expect, str):
        type_id, expect_repairs = _normalize_block_id(
            expect, field_name="expect.type_id"
        )
        if repairs is not None:
            repairs.extend(expect_repairs)
        return {"kind": "type", "type_id": type_id}
    if isinstance(expect, dict):
        type_id, expect_repairs = _normalize_block_id(
            expect.get("type_id"), field_name="expect.type_id"
        )
        if repairs is not None:
            repairs.extend(expect_repairs)
        states = expect.get("states")
        if isinstance(states, dict) and states:
            return {"kind": "permutation", "type_id": type_id, "states": states}
        return {"kind": "type", "type_id": type_id}
    return {"kind": "air"}


def _expect_info_to_legacy(expect_info: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
    """Map a normalized expect descriptor to the legacy preflight fields.

    Returns ``(replace_any, expected_previous)`` for ``build_edit_payload``.
    """
    kind = expect_info.get("kind")
    if kind == "any":
        return True, None
    if kind == "type":
        return False, {"type_id": expect_info.get("type_id")}
    if kind == "permutation":
        return False, {
            "type_id": expect_info.get("type_id"),
            "states": expect_info.get("states"),
        }
    # air (default): replace air only.
    return False, None


def _unified_target_to_legacy(
    normalized_target: Any,
    block_info: dict[str, Any],
    expect_info: dict[str, Any],
    dimension: str | None,
) -> dict[str, Any]:
    """Map a unified ``target`` + ``block`` + ``expect`` into legacy edit fields.

    The normalized target is a :class:`NormalizedTarget` (shared shape with
    ``inspect_block``). Single position -> place, multi positions -> batch, box
    -> fill. ``coordinate_mode`` is derived from the target. Corner order for
    box targets is normalized (min/max) before reaching the bridge.
    """
    shape = normalized_target.shape
    coord_mode = normalized_target.coordinate_mode
    position: dict[str, Any] | None = None
    positions: list[dict[str, Any]] | None = None
    from_pos: dict[str, Any] | None = None
    to_pos: dict[str, Any] | None = None
    if shape == "positions":
        cells = list(normalized_target.positions or [])
        if len(cells) == 1:
            position = cells[0]
        else:
            positions = cells
    else:
        from_pos, to_pos = _normalize_aabb_corners(
            normalized_target.box_from, normalized_target.box_to
        )
    replace_any, expected_previous = _expect_info_to_legacy(expect_info)
    legacy: dict[str, Any] = {
        "mode": "place" if position is not None else ("batch" if positions is not None else "fill"),
        "coordinate_mode": coord_mode,
        "type_id": block_info.get("type_id"),
        "replace_any": replace_any,
        "expected_previous": expected_previous,
    }
    if dimension is not None:
        legacy["dimension"] = dimension
    if position is not None:
        legacy["position"] = position
    if positions is not None:
        legacy["positions"] = positions
    if from_pos is not None:
        legacy["from"] = from_pos
    if to_pos is not None:
        legacy["to"] = to_pos
    if block_info.get("states") is not None:
        legacy["states"] = block_info["states"]
    return legacy


def _locked_targets_aabb(
    locked: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Fallback AABB from sparse locked cell list (min/max only)."""
    xs = [int(t["x"]) for t in locked if isinstance(t, dict) and "x" in t]
    ys = [int(t["y"]) for t in locked if isinstance(t, dict) and "y" in t]
    zs = [int(t["z"]) for t in locked if isinstance(t, dict) and "z" in t]
    if not xs or not ys or not zs:
        return None, None
    return (
        {"x": min(xs), "y": min(ys), "z": min(zs)},
        {"x": max(xs), "y": max(ys), "z": max(zs)},
    )


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
        "fallback_allowed": False,
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
    """Drop per-cell dimension when it matches the top-level dimension (saves commandLine budget)."""
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

    ``max_locked_targets_on_wire=0`` means “prefer omit on absolute” and does not
    enforce a positive cap; only values ``>0`` trigger LIMIT on ship paths.
    """
    if max_locked_targets_on_wire <= 0 or not locked_targets:
        return None
    if len(locked_targets) <= max_locked_targets_on_wire:
        return None
    body = build_error_response(
        BlockErrorCode.LIMIT_EXCEEDED,
        (
            f"locked_targets 数量 {len(locked_targets)} 超过 "
            f"max_locked_targets_on_wire={max_locked_targets_on_wire}，请求未发送。"
        ),
        retryable=True,
        external_state_unknown=False,
        fallback_allowed=False,
        reason="max_locked_targets_on_wire",
        hint=_COMMAND_LINE_BUDGET_HINT,
        suggested_max_discrete=max_locked_targets_on_wire,
        matched_count=len(locked_targets),
    )
    return ToolResult.failure(
        dumps_payload(body),
        error_kind="INVALID_ARGUMENT",
        retryable=True,
        external_state_unknown=False,
        diagnostic_summary=(
            f"max_locked_targets_on_wire matched={len(locked_targets)} "
            f"cap={max_locked_targets_on_wire}"
        ),
    )


def build_inspect_payload(
    *,
    coordinate_mode: str,
    dimension: str | None,
    position: dict[str, Any] | None,
    positions: list[dict[str, Any]] | None,
    player_name: str,
    phase: str | None = None,
    locked_targets: list[dict[str, Any]] | None = None,
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "coordinate_mode": coordinate_mode,
        "player_name": player_name,
    }
    if dimension is not None:
        payload["dimension"] = dimension
    if position is not None:
        payload["position"] = position
    if positions is not None:
        payload["positions"] = positions
    if phase is not None:
        payload["phase"] = phase
    if locked_targets is not None:
        payload["locked_targets"] = locked_targets
    apply_limits_to_payload(payload, limits)
    return payload


def build_edit_payload(
    *,
    mode: str,
    coordinate_mode: str,
    dimension: str | None,
    position: dict[str, Any] | None,
    positions: list[dict[str, Any]] | None,
    from_pos: dict[str, Any] | None,
    to_pos: dict[str, Any] | None,
    type_id: str,
    states: dict[str, Any] | None,
    replace_any: bool,
    expected_previous: dict[str, Any] | None,
    player_name: str,
    phase: str,
    locked_targets: list[dict[str, Any]] | None = None,
    limits: dict[str, int] | None = None,
    max_locked_targets_on_wire: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": mode,
        "coordinate_mode": coordinate_mode,
        "type_id": type_id,
        "replace_any": bool(replace_any),
        "player_name": player_name,
        "phase": phase,
    }
    if dimension is not None:
        payload["dimension"] = dimension
    if position is not None:
        payload["position"] = position
    if positions is not None:
        payload["positions"] = positions
    if from_pos is not None:
        payload["from"] = from_pos
    if to_pos is not None:
        payload["to"] = to_pos
    if states is not None:
        payload["states"] = states
    if expected_previous is not None:
        payload["expected_previous"] = expected_previous

    wire_cap = 0 if max_locked_targets_on_wire is None else int(max_locked_targets_on_wire)
    if limits is not None and "max_locked_targets_on_wire" in limits and max_locked_targets_on_wire is None:
        try:
            wire_cap = int(limits["max_locked_targets_on_wire"])
        except (TypeError, ValueError, KeyError):
            wire_cap = 0

    omit_locked = should_omit_locked_targets_on_wire(
        mode=mode,
        phase=phase,
        from_pos=from_pos if isinstance(from_pos, dict) else None,
        to_pos=to_pos if isinstance(to_pos, dict) else None,
        position=position if isinstance(position, dict) else None,
        positions=positions if isinstance(positions, list) else None,
        locked_targets=locked_targets if isinstance(locked_targets, list) else None,
        coordinate_mode=coordinate_mode,
        max_locked_targets_on_wire=wire_cap,
    )
    if locked_targets is not None and not omit_locked:
        # Cap is enforced by locked_targets_wire_limit_exceeded at send/preflight;
        # default wire_cap=0 never forces shipping on absolute (omit above).
        payload["locked_targets"] = compact_locked_targets_for_wire(
            locked_targets,
            dimension=dimension,
        )
    # Limits only affect preflight enumeration / host checks. Omitting them on
    # execute saves critical commandLine bytes under the 461 B MCBE budget.
    # A single-cell ``place`` preflight also omits them: the host has already
    # validated the one position against every limit, the Add-on needs no
    # enumeration caps for a single cell, and the extra keys push expect-type
    # frames (``expected_previous``) over the MCBE commandLine budget.
    if phase != "execute" and mode != "place":
        apply_limits_to_payload(payload, limits)
    return payload


def merge_canonical_from_preflight(
    original_args: dict[str, Any],
    preflight_payload: dict[str, Any],
) -> dict[str, Any]:
    """Build approval/execution canonical args from preflight response payload.

    For fill, authorized ``from``/``to`` keep the **request AABB** (or full
    preflight bounds) and are never shrunk to sparse ``locked_targets`` min/max.
    ``locked_targets`` is still stored in full for audit and idempotency.
    """
    canonical = dict(original_args)
    if not isinstance(preflight_payload, dict):
        return canonical

    for key in (
        "coordinate_mode",
        "dimension",
        "mode",
        "type_id",
        "states",
        "replace_any",
        "expected_previous",
        "position",
        "positions",
        "from",
        "to",
        "locked_targets",
        "status",
    ):
        if key in preflight_payload and preflight_payload[key] is not None:
            canonical[key] = preflight_payload[key]

    # Prefer absolute locked targets for subsequent execution.
    # Inspect target path (issue 02) keeps ``target`` authoritative; do not
    # synthesize legacy ``position``/``positions`` from locked_targets.
    locked = preflight_payload.get("locked_targets")
    has_target = isinstance(original_args.get("target"), dict)
    if isinstance(locked, list) and locked and not has_target:
        canonical["locked_targets"] = locked
        if len(locked) == 1 and isinstance(locked[0], dict):
            t = locked[0]
            if "dimension" in t:
                canonical["dimension"] = t["dimension"]
            if all(k in t for k in ("x", "y", "z")):
                mode = canonical.get("mode") or original_args.get("mode")
                if mode in (None, "place") or original_args.get("position") is not None:
                    canonical["position"] = {"x": t["x"], "y": t["y"], "z": t["z"]}
                    canonical["coordinate_mode"] = "absolute"
                elif original_args.get("positions") is not None:
                    canonical["positions"] = [
                        {"x": item.get("x"), "y": item.get("y"), "z": item.get("z")}
                        for item in locked
                        if isinstance(item, dict)
                    ]
                    canonical["coordinate_mode"] = "absolute"
        elif original_args.get("positions") is not None or (
            (original_args.get("mode") or "") == "batch"
        ):
            canonical["positions"] = [
                {"x": item.get("x"), "y": item.get("y"), "z": item.get("z")}
                for item in locked
                if isinstance(item, dict)
            ]
            canonical["coordinate_mode"] = "absolute"
            if locked and isinstance(locked[0], dict) and "dimension" in locked[0]:
                canonical["dimension"] = locked[0]["dimension"]
        elif (original_args.get("mode") or "") == "fill":
            # Do not shrink fill AABB from sparse locked min/max here.
            # Dimension / absolute mode still freeze from locked evidence.
            first = locked[0] if isinstance(locked[0], dict) else None
            if isinstance(first, dict) and "dimension" in first:
                canonical["dimension"] = first["dimension"]
            canonical["coordinate_mode"] = "absolute"

    # Preflight evidence can include both a representative position and a
    # positions list. Public edit_blocks accepts exactly the target shape for
    # its mode, so keep only the frozen shape that will be executed.
    mode = canonical.get("mode") or original_args.get("mode") or "place"
    if mode == "place":
        canonical.pop("positions", None)
        canonical.pop("from", None)
        canonical.pop("to", None)
    elif mode == "batch":
        canonical.pop("position", None)
        canonical.pop("from", None)
        canonical.pop("to", None)
    elif mode == "fill":
        canonical.pop("position", None)
        canonical.pop("positions", None)
        # Authorized AABB priority (never shrink request volume to sparse locked):
        # 1) original request corners (from/to or from_pos/to_pos), normalized
        # 2) preflight bounds (full query volume when present)
        # 3) preflight from/to if present
        # 4) locked min/max only as last resort
        request_from, request_to = _request_fill_aabb(original_args)
        if request_from is not None and request_to is not None:
            nfrom, nto = _normalize_aabb_corners(request_from, request_to)
            canonical["from"] = nfrom
            canonical["to"] = nto
        else:
            filled = False
            bounds = preflight_payload.get("bounds")
            if isinstance(bounds, dict) and "min" in bounds and "max" in bounds:
                bmin, bmax = bounds["min"], bounds["max"]
                if isinstance(bmin, dict) and isinstance(bmax, dict):
                    canonical["from"] = bmin
                    canonical["to"] = bmax
                    if "dimension" in bounds:
                        canonical["dimension"] = bounds["dimension"]
                    filled = True
            if not filled:
                p_from = preflight_payload.get("from")
                p_to = preflight_payload.get("to")
                if isinstance(p_from, dict) and isinstance(p_to, dict):
                    nfrom, nto = _normalize_aabb_corners(p_from, p_to)
                    canonical["from"] = nfrom
                    canonical["to"] = nto
                    filled = True
            if not filled and isinstance(locked, list) and locked:
                lfrom, lto = _locked_targets_aabb(locked)
                if lfrom is not None and lto is not None:
                    canonical["from"] = lfrom
                    canonical["to"] = lto
        if isinstance(locked, list) and locked:
            canonical["coordinate_mode"] = "absolute"
        elif "coordinate_mode" not in canonical:
            canonical["coordinate_mode"] = (
                original_args.get("coordinate_mode") or "absolute"
            )
        # 授权和审计使用桥协议字段名；Python 签名别名只存在于严格执行投影。
        canonical.pop("from_pos", None)
        canonical.pop("to_pos", None)
    else:
        # Non-fill may still surface preflight bounds for inspect-like payloads.
        if "bounds" in preflight_payload and isinstance(preflight_payload["bounds"], dict):
            bounds = preflight_payload["bounds"]
            if "min" in bounds and "max" in bounds:
                if "from" not in canonical:
                    canonical["from"] = bounds["min"]
                if "to" not in canonical:
                    canonical["to"] = bounds["max"]
                if "dimension" in bounds:
                    canonical["dimension"] = bounds["dimension"]
                canonical["coordinate_mode"] = "absolute"

    # Mark as ready for execute phase.
    canonical["phase"] = "execute"
    return canonical


def build_block_preflight_plan(
    tool_name: str,
    original_args: dict[str, Any],
    preflight_payload: dict[str, Any],
) -> BlockPreflightPlan:
    """Build the separated plan (authorization / execute args / approval metadata).

    Only ``inspect_block`` reaches here today: place/fill skip preflight and
    execute directly (spec §3.1/§3.2). Execute args are the model-visible
    subset of the canonical args, marked ready for the execute phase.
    """
    if tool_name != "inspect_block":
        raise ValueError(f"unsupported block tool: {tool_name}")
    authorized_args = merge_canonical_from_preflight(original_args, preflight_payload)
    has_target = isinstance(authorized_args.get("target"), dict)
    if has_target:
        projected = {
            key: value
            for key, value in authorized_args.items()
            if key in {"target", "dimension", "phase", "locked_targets"}
        }
        required = {"dimension", "phase", "target"}
    else:
        projected = {
            key: value
            for key, value in authorized_args.items()
            if key in {
                "coordinate_mode", "dimension", "position", "positions",
                "target", "locked_targets", "phase",
            }
        }
        required = {"coordinate_mode", "dimension", "phase", "locked_targets"}
    missing = [key for key in required if key not in projected]
    # inspect target path: locked_targets not required (no mutation, no lock).
    if has_target:
        if missing or projected.get("phase") != "execute":
            raise ValueError("block preflight execution contract is incomplete")
    elif missing or projected.get("phase") != "execute" or not projected.get("locked_targets"):
        raise ValueError("block preflight execution contract is incomplete")
    approval_metadata = {
        key: value
        for key, value in preflight_payload.items()
        if key not in authorized_args and key not in {"schema_version", "ok"}
    }
    return BlockPreflightPlan(
        authorized_args,
        projected,
        approval_metadata,
        plan_id=secrets.token_hex(16),
    )


async def run_block_preflight(
    ctx: RunContext[AgentDependencies],
    tool_name: str,
    tool_args: dict[str, Any],
) -> tuple[BlockPreflightPlan | dict[str, Any] | None, ToolResult | None]:
    """Run bridge preflight before harness policy.

    Returns (preflight_plan, failure). Absolute inspect may return direct args.
    """
    plain_args = _plain_tool_data(tool_args)
    assert isinstance(plain_args, dict)
    tool_args = plain_args
    deps = ctx.deps
    unsupported = await _require_supported(ctx)
    if unsupported is not None:
        return None, unsupported

    limits = get_block_tools_limits(deps.settings)
    limits_payload = {
        "max_discrete_positions": limits.max_discrete_positions,
        "max_fill_volume": limits.max_fill_volume,
        "cells_per_tick": limits.cells_per_tick,
        "max_locked_targets_on_wire": limits.max_locked_targets_on_wire,
    }

    # If already canonical with locked_targets from a prior approval recovery, skip re-preflight.
    if tool_args.get("phase") == "execute" and tool_args.get("locked_targets"):
        return dict(tool_args), None

    if tool_name == "inspect_block":
        # inspect is low-risk; preflight only needed for relative resolution.
        # Absolute inspect can execute directly without a separate preflight phase.
        target = tool_args.get("target")
        if isinstance(target, list):
            # Array target path (spec §3.3): [x,y,z] point or [[a],[b]] box.
            # Both are absolute and need no bridge preflight; impl normalizes.
            from services.agent.block_ops.target import normalize_array_target

            _normalized, validation = normalize_array_target(target)
            if validation is not None:
                return None, validation
            return dict(tool_args), None
        if target is not None:
            # Unified target path (issue 02): validate shape + limits.
            normalized, validation = _validate_inspect_target(
                target,
                dimension=tool_args.get("dimension"),
                max_positions=limits.max_discrete_positions,
                max_fill_volume=limits.max_fill_volume,
            )
            if validation is not None:
                return None, validation
            assert normalized is not None
            # Absolute target with no locked_targets can execute directly.
            if normalized.coordinate_mode == "absolute" and not tool_args.get("locked_targets"):
                return dict(tool_args), None
            # Relative target needs Add-on preflight for player anchor resolution.
            from services.agent.block_ops.target import build_inspect_payload_from_target

            payload = build_inspect_payload_from_target(
                normalized,
                dimension=tool_args.get("dimension"),
                player_name=deps.player_name,
                phase="preflight",
                limits=limits_payload,
            )
            result = await call_block_capability(deps.addon_bridge, "inspect_block", payload)
            if not result.is_success:
                return None, result
            try:
                body = json.loads(result.output)
            except Exception:
                return dict(tool_args), None
            if isinstance(body, dict) and body.get("ok") is True:
                preflight_fields = {
                    k: v for k, v in body.items() if k not in {"schema_version", "ok"}
                }
            else:
                preflight_fields = body if isinstance(body, dict) else {}
            return build_block_preflight_plan(tool_name, tool_args, preflight_fields), None

        # Legacy path (harness recovery / internal callers).
        coord_mode = str(tool_args.get("coordinate_mode") or "absolute")
        validation = _validate_inspect_args(
            coordinate_mode=coord_mode,
            dimension=tool_args.get("dimension"),
            position=tool_args.get("position"),
            positions=tool_args.get("positions"),
            max_positions=limits.max_discrete_positions,
        )
        if validation is not None:
            return None, validation
        if coord_mode == "absolute" and not tool_args.get("locked_targets"):
            return dict(tool_args), None

        payload = build_inspect_payload(
            coordinate_mode=coord_mode,
            dimension=tool_args.get("dimension"),
            position=tool_args.get("position"),
            positions=tool_args.get("positions"),
            player_name=deps.player_name,
            phase="preflight",
            limits=limits_payload,
        )
        result = await call_block_capability(deps.addon_bridge, "inspect_block", payload)
        if not result.is_success:
            return None, result
        try:
            body = json.loads(result.output)
        except Exception:
            return dict(tool_args), None
        # If response is success envelope with nested fields
        if isinstance(body, dict) and body.get("ok") is True:
            # payload may be flattened into body for versioned responses
            preflight_fields = {k: v for k, v in body.items() if k not in {"schema_version", "ok"}}
        else:
            preflight_fields = body if isinstance(body, dict) else {}
        return build_block_preflight_plan(tool_name, tool_args, preflight_fields), None


    # place/fill 直通：canonical args 必须保持模型可见契约。pydantic-ai 经
    # alias="from" 校验后以字段名（from_）传入 harness，但 wrap_tool_validate
    # 与 execute_block_plan 都按模型可见键（from）校验/取参，这里统一转回
    # 别名键，避免审批恢复时 canonical args 校验失败。
    if tool_name == "fill_block" and "from_" in tool_args and "from" not in tool_args:
        from_pos = tool_args.pop("from_")
        tool_args["from"] = from_pos
    return dict(tool_args), None


def _state_unknown_result(plan_id: str, *, reason: str = "missing") -> ToolResult:
    """审批恢复失败：缓存缺失/过期/工具不匹配 → STATE_UNKNOWN（不抛 TypeError）。"""
    return ToolResult.failure(
        dumps_payload(
            build_state_unknown_response(
                f"方块工具审批计划缺失或已过期（plan_id={plan_id or '空'}，{reason}）；"
                "请重新发起操作",
            )
        ),
        error_kind="PERMANENT",
        retryable=False,
        external_state_unknown=True,
        diagnostic_summary="block preflight plan not found or expired",
        error_type=BlockErrorCode.STATE_UNKNOWN,
    )


async def execute_block_plan(plan_id: str, ctx: RunContext[AgentDependencies]) -> ToolResult:
    """按 plan_id 执行已批准的单点方块操作（place/fill）。

    从预检缓存取 frozen canonical args，直接调用对应 impl；同一 plan 只执行
    一次（成功写入已执行标记 + 结果缓存）。缓存缺失/过期/工具不支持返回
    ``STATE_UNKNOWN``。

    幂等性由两层组成：harness 的 (run_id, tool_call_id) 幂等 store 处理同轮
    恢复；这里的 ``executed`` 标记是第二道防线。``executed`` /
    ``execution_result`` 的读-查-写必须在缓存锁下进行，且并发恢复必须按
    plan_id 串行化（per-plan asyncio.Lock）——缓存锁是 threading.RLock，不能
    跨 ``await`` 持有（会阻塞同一事件循环里的其他协程）。
    """
    from services.agent.block_ops.preflight_cache import (
        get_plan_execution_lock,
        get_preflight_cache,
    )

    pid = str(plan_id or "").strip()
    cache = get_preflight_cache()
    # 快路径：锁外先确认 plan 存在且未执行，避免为伪造/过期 plan_id 创建锁。
    with cache._lock:
        entry = cache.get_by_plan_id(pid)
        if entry is None:
            return _state_unknown_result(pid)
        if entry.executed and isinstance(entry.execution_result, ToolResult):
            return entry.execution_result

    # 并发恢复串行化：等待期间另一个协程可能已完成本 plan。
    async with get_plan_execution_lock(pid):
        with cache._lock:
            if entry.executed and isinstance(entry.execution_result, ToolResult):
                return entry.execution_result

        tool_name = entry.tool_name
        canonical = entry.canonical_args
        if tool_name == "place_block":
            result = await place_block_impl(
                ctx,
                pos=canonical.get("pos"),
                block=canonical.get("block"),
                expect=str(canonical.get("expect") or "air"),
                states=canonical.get("states"),
            )
        elif tool_name == "fill_block":
            result = await fill_block_impl(
                ctx,
                from_=canonical.get("from_", canonical.get("from")),
                to=canonical.get("to"),
                block=canonical.get("block"),
                expect=str(canonical.get("expect") or "air"),
                states=canonical.get("states"),
            )
        else:
            return _state_unknown_result(pid, reason="unsupported-tool")

        # 幂等：成功才落 executed 标记 + 结果缓存；失败允许重新恢复重试。
        with cache._lock:
            if result.is_success:
                entry.executed = True
                entry.execution_result = result
        return result


async def inspect_block_impl(
    ctx: RunContext[AgentDependencies],
    *,
    target: Any,
    phase: str = "execute",
    locked_targets: list[dict[str, Any]] | None = None,
) -> ToolResult:
    """Query one or more block snapshots via the addon bridge (spec §3.3).

    The model-facing ``target`` is a single point ``[x, y, z]``, two box
    corners ``[[x1, y1, z1], [x2, y2, z2]]``, or the unified dict form
    ``{positions: [...]}`` / ``{box: {from, to}}``. ``dimension`` is never
    sent: the Add-on defaults to the current player dimension (spec §3.3).
    """
    deps = ctx.deps
    logger.info(
        "agent_tool_call",
        tool="inspect_block",
        connection_id=_connection_id(deps),
        run_id=deps.run_id,
        player_name=deps.player_name,
        has_target=target is not None,
    )

    unsupported = await _require_supported(ctx)
    if unsupported is not None:
        return unsupported

    if target is None:
        return _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "target 必填",
        )

    limits = get_block_tools_limits(deps.settings)
    limits_payload = {
        "max_discrete_positions": limits.max_discrete_positions,
        "max_fill_volume": limits.max_fill_volume,
        "cells_per_tick": limits.cells_per_tick,
        "max_locked_targets_on_wire": limits.max_locked_targets_on_wire,
        "inspect_summary_threshold": limits.inspect_summary_threshold,
        "inspect_sample_limit": limits.inspect_sample_limit,
    }

    if isinstance(target, dict):
        normalized, validation = _validate_inspect_target(
            target,
            dimension=None,
            max_positions=limits.max_discrete_positions,
            max_fill_volume=limits.max_fill_volume,
        )
    else:
        from services.agent.block_ops.target import normalize_array_target

        normalized, validation = normalize_array_target(target)
    if validation is not None:
        return validation
    assert normalized is not None

    from services.agent.block_ops.target import build_inspect_payload_from_target

    payload = build_inspect_payload_from_target(
        normalized,
        dimension=None,
        player_name=deps.player_name,
        phase=phase or "execute",
        locked_targets=locked_targets,
        limits=limits_payload,
    )
    return await call_block_capability(
        deps.addon_bridge,
        "inspect_block",
        payload,
        tool_name="inspect_block",
    )


# ---------------------------------------------------------------------------
# Task 2: single-op implementations (place_block / fill_block)
# ---------------------------------------------------------------------------


async def place_block_impl(
    ctx: RunContext[AgentDependencies],
    *,
    pos: Any,
    block: Any,
    expect: Any = "air",
    states: dict[str, Any] | None = None,
    phase: str = "execute",
    locked_targets: list[dict[str, Any]] | None = None,
) -> ToolResult:
    """Write a block at a single absolute cell (spec §3.1).

    Maps to a ``mode=place`` Add-on edit frame. ``dimension`` is intentionally
    not sent: the Add-on defaults to the current player dimension (spec §3.3).
    """
    deps = ctx.deps
    logger.info(
        "agent_tool_call",
        tool="place_block",
        connection_id=_connection_id(deps),
        run_id=deps.run_id,
        player_name=deps.player_name,
        pos=pos,
    )

    unsupported = await _require_supported(ctx)
    if unsupported is not None:
        return unsupported

    position, error = _normalize_position_array(pos, field_name="pos")
    if error is not None:
        return error
    assert position is not None

    type_id, effective_states, error = _normalize_single_op_block(block, states)
    if error is not None:
        return error

    replace_any, expected_previous, error = _expect_to_legacy(expect)
    if error is not None:
        return error

    payload = build_edit_payload(
        mode="place",
        coordinate_mode="absolute",
        dimension=None,
        position=position,
        positions=None,
        from_pos=None,
        to_pos=None,
        type_id=type_id,
        states=effective_states,
        replace_any=replace_any,
        expected_previous=expected_previous,
        player_name=deps.player_name,
        phase=phase or "execute",
        locked_targets=locked_targets,
    )

    budget = get_command_line_byte_budget(deps.settings)
    budget_fail = check_bridge_command_line_budget(
        "edit_blocks",
        payload,
        budget=budget,
        matched_count=1,
    )
    if budget_fail is not None:
        return budget_fail

    return await call_block_capability(
        deps.addon_bridge,
        "edit_blocks",
        payload,
        mode="place",
        tool_name="place_block",
    )


async def fill_block_impl(
    ctx: RunContext[AgentDependencies],
    *,
    from_: Any,
    to: Any,
    block: Any,
    expect: Any = "air",
    states: dict[str, Any] | None = None,
    phase: str = "execute",
    locked_targets: list[dict[str, Any]] | None = None,
) -> ToolResult:
    """Fill the AABB between two absolute corners (spec §3.2).

    Corners are min/max-normalized before reaching the Add-on. The host
    rejects oversized volumes (LIMIT_EXCEEDED with a shrink-direction hint)
    before any bridge call. ``dimension`` is intentionally not sent: the
    Add-on defaults to the current player dimension (spec §3.3).
    """
    deps = ctx.deps
    logger.info(
        "agent_tool_call",
        tool="fill_block",
        connection_id=_connection_id(deps),
        run_id=deps.run_id,
        player_name=deps.player_name,
        from_pos=from_,
        to_pos=to,
    )

    unsupported = await _require_supported(ctx)
    if unsupported is not None:
        return unsupported

    from_raw, error = _normalize_position_array(from_, field_name="from_")
    if error is not None:
        return error
    to_raw, error = _normalize_position_array(to, field_name="to")
    if error is not None:
        return error
    assert from_raw is not None and to_raw is not None

    from_pos, to_pos = _normalize_aabb_corners(from_raw, to_raw)

    limits = get_block_tools_limits(deps.settings)
    volume = _aabb_volume(from_pos, to_pos)
    if volume is None:
        return _host_limit_error(
            BlockErrorCode.INVALID_COORDINATE,
            "fill 角落坐标无法计算体积",
        )
    if volume > limits.max_fill_volume:
        return _host_limit_error(
            BlockErrorCode.LIMIT_EXCEEDED,
            f"fill 体积 {volume} 超过上限 {limits.max_fill_volume}，请求未发送",
            limit=limits.max_fill_volume,
            volume=volume,
            hint=_COMMAND_LINE_BUDGET_HINT,
        )

    type_id, effective_states, error = _normalize_single_op_block(block, states)
    if error is not None:
        return error

    replace_any, expected_previous, error = _expect_to_legacy(expect)
    if error is not None:
        return error

    payload = build_edit_payload(
        mode="fill",
        coordinate_mode="absolute",
        dimension=None,
        position=None,
        positions=None,
        from_pos=from_pos,
        to_pos=to_pos,
        type_id=type_id,
        states=effective_states,
        replace_any=replace_any,
        expected_previous=expected_previous,
        player_name=deps.player_name,
        phase=phase or "execute",
        locked_targets=locked_targets,
    )

    budget = get_command_line_byte_budget(deps.settings)
    budget_fail = check_bridge_command_line_budget(
        "edit_blocks",
        payload,
        budget=budget,
        volume=volume,
    )
    if budget_fail is not None:
        return budget_fail

    return await call_block_capability(
        deps.addon_bridge,
        "edit_blocks",
        payload,
        mode="fill",
        authorized_bounds={"from": from_pos, "to": to_pos, "volume": volume},
        tool_name="fill_block",
    )
