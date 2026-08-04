"""Model-facing inspect_block / edit_blocks tool implementations."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic_ai import RunContext

from config.logging import get_logger
from config.redaction import redact_exception
from models.agent import AgentDependencies
from services.agent.block_ops.bridge import (
    _precondition_hint_for_counts,
    _safe_actual_type_counts,
    call_block_capability,
    map_bridge_exception,
)
from services.agent.block_ops.capability import (
    BlockCapabilityStatus,
    ensure_block_capability,
)
from services.agent.block_ops.config import (
    DEFAULT_COMMAND_LINE_BYTE_BUDGET,
    get_block_tools_limits,
    get_command_line_byte_budget,
)
from services.agent.block_ops.project import (
    _per_edit_changed,
    _per_edit_skipped,
    _per_edit_skipped_type_counts,
    project_group_edit_result_for_model,
)
from services.agent.block_ops.schema import (
    BlockErrorCode,
    build_error_response,
    dumps_payload,
)
from services.agent.tool_results import ToolResult

logger = get_logger(__name__)

BLOCK_TOOL_NAMES = frozenset({"inspect_block", "edit_blocks"})
CoordinateMode = Literal["absolute", "player_relative"]
EditMode = Literal["place", "batch", "fill"]

# Stable request_id length matching production ``addon-{uuid4().hex}`` (38 chars).
_BRIDGE_REQUEST_ID_PLACEHOLDER = f"addon-{'0' * 32}"
_BRIDGE_REQ_PREFIX = "scriptevent mcbews:bridge_req "
_COMMAND_LINE_BUDGET_HINT = (
    "缩小 fill AABB 或减少 states；禁止拆成大量 place；勿用命令绕过审批。"
)
_COMMAND_LINE_BUDGET_MESSAGE = "出站帧超出 MCBE commandLine 字节预算，请求未发送。"
_AUDIT_EDIT_EVIDENCE_FIELDS = frozenset({
    "before", "after", "before_samples", "after_samples", "verification",
    "verification_summary", "rollback", "failed_index", "written_count",
    "repairs_applied", "candidates", "valid_state_keys", "protected",
    "multiblock", "type_id", "component", "target",
})


@dataclass(frozen=True)
class BlockPreflightPlan:
    """Separated preflight artifacts for approval, invocation, and evidence."""

    authorized_args: dict[str, Any]
    execute_args: dict[str, Any]
    approval_metadata: dict[str, Any]


# Execute-projection fields. The new ``edits`` contract carries the full edit
# description (target/block/expect); legacy flat fields remain for harness
# recovery of previously-approved operations.
_EDIT_EXECUTE_FIELDS = frozenset({
    "edits", "dimension", "block", "expect", "status",
    "type_id", "mode", "coordinate_mode", "position", "positions",
    "from_pos", "to_pos", "states", "replace_any", "expected_previous",
    "locked_targets", "locked_targets_by_edit", "noop_edit_indices",
    "repairs_applied", "phase",
})
_INSPECT_EXECUTE_FIELDS = frozenset({
    "coordinate_mode", "dimension", "position", "positions", "target",
    "locked_targets", "phase",
})


def project_block_execute_args(tool_name: str, authorized_args: dict[str, Any]) -> dict[str, Any]:
    """Strictly project bridge-facing authorization into public Python arguments."""
    fields = _EDIT_EXECUTE_FIELDS if tool_name == "edit_blocks" else _INSPECT_EXECUTE_FIELDS
    if tool_name not in BLOCK_TOOL_NAMES:
        raise ValueError(f"unsupported block tool: {tool_name}")
    projected = {key: value for key, value in authorized_args.items() if key in fields}
    if tool_name == "edit_blocks":
        # New ``edits`` contract (issue 03): the edit list is authoritative and
        # carries the resolved absolute target, so the execute projection only
        # needs edits/dimension/locked_targets/phase (+optional noop status).
        if "edits" in projected:
            projected = {
                key: value for key, value in projected.items()
                if key in {
                    "edits", "dimension", "locked_targets",
                    "locked_targets_by_edit", "noop_edit_indices",
                    "repairs_applied", "phase", "status",
                }
                and value is not None
            }
            return _project_new_edit_execute_args(projected)
        # Legacy flat contract (harness recovery of previously-approved ops).
        if isinstance(authorized_args.get("from"), dict):
            projected["from_pos"] = authorized_args["from"]
        if isinstance(authorized_args.get("to"), dict):
            projected["to_pos"] = authorized_args["to"]
        required = {"type_id", "mode", "coordinate_mode", "dimension", "phase", "locked_targets"}
    else:
        # inspect: target path (issue 02) or legacy position/positions path.
        has_target = "target" in projected and isinstance(projected["target"], dict)
        if has_target:
            # target path: only model-visible fields (target, dimension, phase).
            # coordinate_mode is derived from target inside inspect_block_impl.
            projected = {
                key: value for key, value in projected.items()
                if key in {"target", "dimension", "phase", "locked_targets"}
            }
            required = {"dimension", "phase", "target"}
        else:
            required = {"coordinate_mode", "dimension", "phase", "locked_targets"}
    missing = [key for key in required if key not in projected]
    # noop fill: all targets already at desired state; no locked_targets, no write.
    is_noop = projected.get("status") == "noop"
    if is_noop and tool_name == "edit_blocks" and projected.get("mode") == "fill":
        if missing or projected.get("phase") != "execute":
            raise ValueError("block preflight execution contract is incomplete")
        if not isinstance(projected.get("from_pos"), dict) or not isinstance(
            projected.get("to_pos"), dict
        ):
            raise ValueError("block preflight execution contract requires fill bounds")
        return projected
    # inspect target path: locked_targets not required (no mutation, no lock).
    if tool_name == "inspect_block" and has_target:
        if missing or projected.get("phase") != "execute":
            raise ValueError("block preflight execution contract is incomplete")
        return projected
    if missing or projected.get("phase") != "execute" or not projected.get("locked_targets"):
        raise ValueError("block preflight execution contract is incomplete")
    if tool_name == "edit_blocks":
        mode = projected["mode"]
        if mode == "place" and not isinstance(projected.get("position"), dict):
            raise ValueError("block preflight execution contract requires place position")
        if mode == "batch" and not isinstance(projected.get("positions"), list):
            raise ValueError("block preflight execution contract requires batch positions")
        if mode == "batch" and not projected["positions"]:
            raise ValueError("block preflight execution contract requires non-empty batch positions")
        if mode == "fill" and (
            not isinstance(projected.get("from_pos"), dict)
            or not isinstance(projected.get("to_pos"), dict)
        ):
            raise ValueError("block preflight execution contract requires fill bounds")
        if mode not in {"place", "batch", "fill"}:
            raise ValueError("block preflight execution contract has invalid edit mode")
    return projected


def _project_new_edit_execute_args(projected: dict[str, Any]) -> dict[str, Any]:
    """Validate and return the execute projection for the new ``edits`` contract."""
    edits = projected.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ValueError("block preflight execution contract requires edits")
    is_noop = projected.get("status") == "noop"
    if is_noop:
        if projected.get("phase") != "execute":
            raise ValueError("block preflight execution contract is incomplete")
        return projected
    required = {"edits", "dimension", "phase", "locked_targets"}
    missing = [key for key in required if key not in projected]
    if missing or projected.get("phase") != "execute" or not projected.get("locked_targets"):
        raise ValueError("block preflight execution contract is incomplete")
    return projected


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


def _host_limit_error(
    code: BlockErrorCode,
    message: str,
    **fields: Any,
) -> ToolResult:
    return ToolResult.failure(
        dumps_payload(build_error_response(code, message, **fields)),
        error_kind="INVALID_ARGUMENT",
        retryable=False,
    )


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


def _normalize_aabb_corners(
    from_pos: dict[str, Any],
    to_pos: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return min/max ordered corners for stable fill authorization."""
    try:
        xs = sorted((int(from_pos["x"]), int(to_pos["x"])))
        ys = sorted((int(from_pos["y"]), int(to_pos["y"])))
        zs = sorted((int(from_pos["z"]), int(to_pos["z"])))
        return (
            {"x": xs[0], "y": ys[0], "z": zs[0]},
            {"x": xs[1], "y": ys[1], "z": zs[1]},
        )
    except (KeyError, TypeError, ValueError):
        return from_pos, to_pos


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
    if expect_info["kind"] == "any":
        return True, None, None
    if expect_info["kind"] in {"type", "permutation"}:
        type_id = expect_info.get("type_id") or ""
        if not type_id:
            return False, None, _host_limit_error(
                BlockErrorCode.INVALID_ARGUMENT,
                "expect.type_id 必填",
            )
        if expect_info["kind"] == "permutation":
            return False, {"type_id": type_id, "states": expect_info.get("states")}, None
        return False, {"type_id": type_id}, None
    return False, None, None


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
    _resolve_edits_target_for_execute(canonical)
    return canonical


def _resolve_edits_target_for_execute(canonical: dict[str, Any]) -> None:
    """Rewrite ``edits[*].target`` to the resolved absolute execute target.

    The execute projection (issue 03) is re-fed to ``edit_blocks_impl`` on
    approval resume, where preflight is skipped and the legacy payload is
    re-derived from ``edits``. A relative target (``{forward,right,up}``) would
    otherwise be reinterpreted against the player's *current* position instead
    of the approved anchor. Rewriting to absolute coordinates (frozen from the
    preflight's ``locked_targets`` / authorized AABB) keeps the resumed payload
    identical to the approved one.
    """
    edits = canonical.get("edits")
    if not isinstance(edits, list) or not edits or not isinstance(edits[0], dict):
        return
    edit = edits[0]
    if not isinstance(edit.get("target"), dict):
        return
    locked = canonical.get("locked_targets")
    mode = canonical.get("mode")
    if mode == "fill":
        from_pos = canonical.get("from")
        to_pos = canonical.get("to")
        if isinstance(from_pos, dict) and isinstance(to_pos, dict):
            edit["target"] = {"box": {"from": from_pos, "to": to_pos}}
        return
    if isinstance(locked, list) and locked:
        positions = [
            {"x": item.get("x"), "y": item.get("y"), "z": item.get("z")}
            for item in locked
            if isinstance(item, dict)
        ]
        if positions:
            edit["target"] = {"positions": positions}
    elif isinstance(canonical.get("position"), dict):
        edit["target"] = {"positions": [canonical["position"]]}


def build_block_preflight_plan(
    tool_name: str,
    original_args: dict[str, Any],
    preflight_payload: dict[str, Any],
) -> BlockPreflightPlan:
    authorized_args = merge_canonical_from_preflight(original_args, preflight_payload)
    execute_args = project_block_execute_args(tool_name, authorized_args)
    approval_metadata = {
        key: value
        for key, value in preflight_payload.items()
        if key not in authorized_args and key not in {"schema_version", "ok"}
    }
    if (
        tool_name == "edit_blocks"
        and isinstance(original_args.get("edits"), list)
        and isinstance(
        approval_metadata.get("repairs_applied"), list
        )
    ):
        execute_args["repairs_applied"] = approval_metadata["repairs_applied"][:8]
    return BlockPreflightPlan(authorized_args, execute_args, approval_metadata)


def _expect_hint_for_args(
    args: dict[str, Any],
    actual_type_counts: Any = None,
    *,
    failure_cause: str | None = None,
) -> str:
    """Build a model-facing recovery hint from observed block types.

    The original expect policy is only used to avoid recommending ``any`` for
    a protected-data failure. It must never be echoed as a hidden legacy
    parameter or used instead of the observed type counts.
    """
    edits = args.get("edits")
    expect_kind: Any = None
    if isinstance(edits, list) and edits and isinstance(edits[0], dict):
        expect_kind = _normalize_expect(edits[0].get("expect")).get("kind")
    elif args.get("replace_any") is True:
        expect_kind = "any"

    avoid_any = failure_cause == "protected" or expect_kind == "any"
    return _precondition_hint_for_counts(actual_type_counts, avoid_any=avoid_any)


def _classify_zero_match_preflight(
    tool_name: str,
    tool_args: dict[str, Any],
    preflight_fields: dict[str, Any],
) -> ToolResult | None:
    """Classify a successful preflight with zero matched targets.

    Returns:
      - None when the preflight has matched targets or is a genuine noop that
        should proceed as a no-write plan.
      - A failure ToolResult (PRECONDITION_FAILED) when zero targets matched and
        the world is NOT already at the target state.

    This prevents the historical INTERNAL_ERROR mapping when ``locked_targets``
    is empty (spec §2.3 glass-replaces-planks scenario).
    """
    if tool_name != "edit_blocks":
        return None
    locked = preflight_fields.get("locked_targets")
    matched_count = preflight_fields.get("matched_count")
    if isinstance(matched_count, int) and matched_count > 0:
        return None
    if isinstance(locked, list) and locked:
        return None

    # Determine whether every target was already at the desired state (true noop).
    already_target = preflight_fields.get("already_target")
    volume = preflight_fields.get("volume")
    if isinstance(already_target, int) and isinstance(volume, int) and volume > 0:
        if already_target == volume:
            return None  # genuine noop; plan carries status=noop
    elif preflight_fields.get("status") == "noop":
        return None

    # Zero match, not a noop -> PRECONDITION_FAILED with actionable type counts.
    actual_counts = preflight_fields.get("previous_type_counts")
    if not isinstance(actual_counts, dict) or not actual_counts:
        actual_counts = preflight_fields.get("actual_type_counts")
    actual_counts = _safe_actual_type_counts(actual_counts)
    failure_cause = (
        "protected"
        if preflight_fields.get("protected") is True
        or preflight_fields.get("protected_samples")
        else None
    )
    hint = _expect_hint_for_args(
        tool_args,
        actual_counts,
        failure_cause=failure_cause,
    )
    body = build_error_response(
        BlockErrorCode.PRECONDITION_FAILED,
        "目标方块不满足 expect 前置条件。",
        status="failed",
        matched_count=0,
        actual_type_counts=actual_counts,
        hint=hint,
        retryable=False,
        external_state_unknown=False,
        fallback_allowed=False,
    )
    return ToolResult.failure(
        dumps_payload(body),
        error_kind="PERMANENT",
        retryable=False,
        diagnostic_summary="fill_zero_match_precondition_failed",
    )


def _normalize_edits_target(edit: dict[str, Any]) -> Any:
    """Normalize the ``target`` of a single edit item to a ``NormalizedTarget``."""
    from services.agent.block_ops.target import normalize_inspect_target

    target = edit.get("target")
    normalized, error = normalize_inspect_target(target)
    if error is not None:
        raise _EditTargetError(error)
    assert normalized is not None
    return normalized


def _target_box_volume(normalized: Any) -> int | None:
    """Return the inclusive volume for an absolute or relative normalized box."""
    if normalized.shape != "box":
        return None
    from_pos = normalized.box_from
    to_pos = normalized.box_to
    keys = (
        ("x", "y", "z")
        if normalized.coordinate_mode == "absolute"
        else ("forward", "right", "up")
    )
    try:
        if not isinstance(from_pos, dict) or not isinstance(to_pos, dict):
            return None
        if not all(key in from_pos and key in to_pos for key in keys):
            return None
        lengths = [abs(int(to_pos[key]) - int(from_pos[key])) + 1 for key in keys]
    except (TypeError, ValueError):
        return None
    return lengths[0] * lengths[1] * lengths[2]


@dataclass(frozen=True)
class _EditNormalization:
    """Per-edit normalized input ready for preflight payload construction."""

    legacy: dict[str, Any]
    normalized: Any  # NormalizedTarget
    block_info: dict[str, Any]
    expect_info: dict[str, Any]
    repairs: list[str] = field(default_factory=list)


class _EditTargetError(Exception):
    """Carrier for a validation ToolResult raised out of target normalization."""

    def __init__(self, result: ToolResult) -> None:
        super().__init__()
        self.result = result


def _normalize_one_edit(
    edit: dict[str, Any],
    dimension: str | None,
    max_positions: int,
    max_fill_volume: int,
) -> tuple[_EditNormalization | None, ToolResult | None]:
    """Validate and normalize a single edit item into legacy preflight fields.

    Returns ``(normalization, error)``. Enforces per-edit position/volume limits
    and absolute-dimension requirements.
    """
    if not isinstance(edit, dict):
        return None, _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "edits 的每项必须是对象",
        )
    try:
        normalized = _normalize_edits_target(edit)
    except _EditTargetError as exc:
        return None, exc.result

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
        volume = _target_box_volume(normalized)
        if volume is not None and volume > max_fill_volume:
            return None, _host_limit_error(
                BlockErrorCode.LIMIT_EXCEEDED,
                f"box 体积 {volume} 超过上限 {max_fill_volume}",
                limit=max_fill_volume,
                volume=volume,
            )

    block_info, repairs = _normalize_block_input(edit.get("block"))
    if not block_info.get("type_id"):
        return None, _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "block 必填（type_id 缺失）",
        )
    expect_info = _normalize_expect(edit.get("expect"), repairs)
    legacy = _unified_target_to_legacy(normalized, block_info, expect_info, dimension)
    return _EditNormalization(legacy, normalized, block_info, expect_info, repairs), None


def _normalize_edits_for_preflight(
    tool_args: dict[str, Any],
    max_positions: int,
    max_fill_volume: int,
    max_edits_per_group: int,
    max_total_targets_per_group: int,
) -> tuple[list[_EditNormalization] | None, ToolResult | None]:
    """Validate and map the new ``edits`` contract to per-edit legacy fields.

    Accepts one or more edits (issue 04). Enforces the per-edit limits, the
    group edit-count limit, and the total-target limit. Returns the list of
    normalizations in input order, or an error.
    """
    edits = tool_args.get("edits")
    if not isinstance(edits, list) or not edits:
        return None, _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "edits 必须是非空列表",
        )
    if len(edits) > max_edits_per_group:
        return None, _host_limit_error(
            BlockErrorCode.LIMIT_EXCEEDED,
            f"edits 数量 {len(edits)} 超过单组上限 {max_edits_per_group}",
            limit=max_edits_per_group,
            count=len(edits),
        )

    dimension = tool_args.get("dimension")
    results: list[_EditNormalization] = []
    total_targets = 0
    total_discrete_positions = 0
    for edit in edits:
        normalization, error = _normalize_one_edit(
            edit, dimension, max_positions, max_fill_volume
        )
        if error is not None:
            return None, error
        assert normalization is not None
        results.append(normalization)
        # Tally total target cells for the group limit. For positions this is the
        # discrete count; for box it is the (absolute, when known) volume.
        norm = normalization.normalized
        if norm.shape == "positions":
            positions_count = len(norm.positions or [])
            total_targets += positions_count
            total_discrete_positions += positions_count
        else:
            total_targets += _target_box_volume(norm) or 0

    if total_discrete_positions > max_positions:
        return None, _host_limit_error(
            BlockErrorCode.LIMIT_EXCEEDED,
            (
                f"编辑组离散位置数 {total_discrete_positions} 超过上限 "
                f"{max_positions}"
            ),
            limit=max_positions,
            count=total_discrete_positions,
        )
    if total_targets > max_total_targets_per_group:
        return None, _host_limit_error(
            BlockErrorCode.LIMIT_EXCEEDED,
            f"编辑组总目标数 {total_targets} 超过上限 {max_total_targets_per_group}",
            limit=max_total_targets_per_group,
            count=total_targets,
        )

    return results, None


def _cell_key(x: Any, y: Any, z: Any) -> tuple[int, int, int]:
    """Stable integer cell key for conflict/dedup bookkeeping."""
    return (int(x), int(y), int(z))


def _resolved_positions_from_locked(
    locked_targets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Synthesize absolute ``positions`` cells from resolved locked targets."""
    out: list[dict[str, Any]] = []
    for item in locked_targets:
        if isinstance(item, dict) and all(k in item for k in ("x", "y", "z")):
            out.append({"x": item["x"], "y": item["y"], "z": item["z"]})
    return out


def _expand_aabb_cells(
    from_pos: dict[str, Any],
    to_pos: dict[str, Any],
) -> list[tuple[int, int, int]]:
    """Enumerate the inclusive integer cell set of a resolved AABB."""
    x0, x1 = sorted((int(from_pos["x"]), int(to_pos["x"])))
    y0, y1 = sorted((int(from_pos["y"]), int(to_pos["y"])))
    z0, z1 = sorted((int(from_pos["z"]), int(to_pos["z"])))
    cells: list[tuple[int, int, int]] = []
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            for z in range(z0, z1 + 1):
                cells.append((x, y, z))
    return cells


@dataclass(frozen=True)
class _GroupEdit:
    """Per-edit state after grouped preflight, dedup and target freezing."""

    index: int
    normalization: _EditNormalization
    legacy: dict[str, Any]
    block_info: dict[str, Any]
    expect_info: dict[str, Any]
    # Frozen absolute target actually executed. A box with a partial match or
    # overlap is lowered to owned positions so it cannot rewrite skipped or
    # deduped cells at execute time.
    frozen_target: dict[str, Any]
    frozen_positions: list[dict[str, Any]] | None
    frozen_from: dict[str, Any] | None
    frozen_to: dict[str, Any] | None
    locked_targets: list[dict[str, Any]]  # owned, matched absolute cells only
    signature: tuple[Any, ...]
    matched: int
    skipped: int
    previous_type_counts: dict[str, Any]
    volume: int
    status: str  # preflight status for this edit (applied/noop/partial/failed/unknown)
    is_noop: bool
    # Resolved absolute cells targeted before dedup (for conflict detection).
    resolved_cells: frozenset[tuple[int, int, int]]
    repairs: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class _EditPreflight:
    """Raw per-edit bridge preflight result, before dedup/freezing."""

    index: int
    normalization: _EditNormalization
    legacy: dict[str, Any]
    block_info: dict[str, Any]
    expect_info: dict[str, Any]
    signature: tuple[Any, Any, Any, Any, Any]
    preflight_fields: dict[str, Any]
    locked_targets: list[dict[str, Any]]
    resolved_positions: list[dict[str, Any]] | None
    resolved_from: dict[str, Any] | None
    resolved_to: dict[str, Any] | None
    resolved_cells: frozenset[tuple[int, int, int]]
    matched: int
    skipped: int
    previous_type_counts: dict[str, Any]
    volume: int
    status: str
    is_noop: bool
    repairs: list[Any] = field(default_factory=list)


def _block_states_key(states: Any) -> tuple[tuple[str, Any], ...] | None:
    """Hashable, order-independent key for a block-states dict (None when empty)."""
    if not isinstance(states, dict) or not states:
        return None
    return tuple(sorted((str(k), v) for k, v in states.items()))


def _edit_signature(block_info: dict[str, Any], expect_info: dict[str, Any]) -> tuple[Any, ...]:
    """Signature used for overlap dedup/conflict detection.

    Two edits touching the same cell conflict unless their final block and
    ``expect`` precondition are *identical``. The signature captures exactly
    that: ``(block_type_id, block_states, expect_kind, expect_type_id,
    expect_states)``.
    """
    expect_kind = expect_info.get("kind")
    if expect_kind == "permutation":
        expect_tail: tuple[Any, ...] = (
            expect_kind,
            expect_info.get("type_id"),
            _block_states_key(expect_info.get("states")),
        )
    elif expect_kind in ("type", "any"):
        expect_tail = (expect_kind, expect_info.get("type_id"))
    else:
        expect_tail = (expect_kind,)
    return (
        block_info.get("type_id"),
        _block_states_key(block_info.get("states")),
    ) + expect_tail


@dataclass
class _CellOwnership:
    """Result of conflict detection + dedup across a group of edits."""

    # cell_key -> edit index that owns the cell (first edit claiming it)
    owner: dict[tuple[int, int, int], int] = field(default_factory=dict)
    # edit_index -> set of cell keys owned by that edit
    owned: dict[int, set[tuple[int, int, int]]] = field(default_factory=dict)


def _compute_cell_ownership(
    edits: list[_EditPreflight],
) -> _CellOwnership:
    """Assign each resolved cell to the first edit claiming it (dedup).

    Raises :class:`_ConflictingEditsError` if a cell is claimed by two edits
    with *different* signatures (spec §5.2). Identical signatures are deduped
    silently — the later edit simply does not own the shared cell.
    """
    ownership = _CellOwnership()
    for edit in edits:
        sig = edit.signature
        for cell in edit.resolved_cells:
            prior = ownership.owner.get(cell)
            if prior is None:
                ownership.owner[cell] = edit.index
                ownership.owned.setdefault(edit.index, set()).add(cell)
            else:
                prior_sig = edits[prior].signature
                if prior_sig != sig:
                    raise _ConflictingEditsError(cell, prior, edit.index)
                # Identical signature: keep the prior owner (dedup). The later
                # edit does not own this cell.
    return ownership


class _ConflictingEditsError(Exception):
    """Raised when two edits target the same cell with different signatures."""

    def __init__(
        self,
        cell: tuple[int, int, int],
        first_index: int,
        second_index: int,
    ) -> None:
        super().__init__(f"cell {cell} conflict between edits {first_index} and {second_index}")
        self.cell = cell
        self.first_index = first_index
        self.second_index = second_index


def _freeze_owned_positions(
    owned: set[tuple[int, int, int]],
) -> list[dict[str, Any]]:
    """Stable, canonical ordering of owned cells as absolute positions."""
    return [
        {"x": c[0], "y": c[1], "z": c[2]}
        for c in sorted(owned)
    ]


def _build_group_edits(
    preflights: list[_EditPreflight],
    ownership: _CellOwnership,
) -> list[_GroupEdit]:
    """Freeze each edit's executable target to its owned cells (spec §8.3).

    Position edits drop cells owned by earlier edits. A box keeps its AABB only
    when every cell is both owned and preflight-matched; otherwise it is lowered
    to owned matched positions. This is necessary for ``expect=any``: replaying
    a partially-overlapping fill would otherwise write shared cells twice.
    """
    group: list[_GroupEdit] = []
    for pre in preflights:
        owned = ownership.owned.get(pre.index) or set()
        owned_locked_targets = [
            cell
            for cell in pre.locked_targets
            if isinstance(cell, dict)
            and all(key in cell for key in ("x", "y", "z"))
            and _cell_key(cell["x"], cell["y"], cell["z"]) in owned
        ]
        owned_matched_cells = {
            _cell_key(cell["x"], cell["y"], cell["z"])
            for cell in owned_locked_targets
        }
        # A discrete target already has an exact owned set. A box with either
        # an overlap or preflight filtering needs exact position execution.
        lower_box_to_positions = (
            pre.resolved_positions is None
            and (
                owned != pre.resolved_cells
                or owned_matched_cells != owned
            )
        )
        if pre.resolved_positions is not None or lower_box_to_positions:
            frozen_positions = _freeze_owned_positions(
                owned if pre.resolved_positions is not None else owned_matched_cells
            )
            no_owned_positions = not frozen_positions
            is_noop = pre.is_noop or no_owned_positions
            if no_owned_positions:
                # Approval-resume still traverses Pydantic's public schema.
                # Keep a valid, frozen representative target for a deduped/noop
                # edit; ``noop_edit_indices`` guarantees it is never executed.
                frozen_positions = _freeze_owned_positions(pre.resolved_cells)[:1]
                if not frozen_positions:
                    original_positions = pre.normalization.normalized.positions
                    if isinstance(original_positions, list):
                        frozen_positions = list(original_positions[:1])
            frozen_target: dict[str, Any] = {"positions": frozen_positions}
            group.append(_GroupEdit(
                index=pre.index,
                normalization=pre.normalization,
                legacy=pre.legacy,
                block_info=pre.block_info,
                expect_info=pre.expect_info,
                frozen_target=frozen_target,
                frozen_positions=frozen_positions,
                frozen_from=None,
                frozen_to=None,
                locked_targets=owned_locked_targets,
                signature=pre.signature,
                matched=pre.matched if not is_noop else 0,
                skipped=pre.skipped,
                previous_type_counts=pre.previous_type_counts,
                volume=pre.volume,
                status="noop" if is_noop else pre.status,
                is_noop=is_noop,
                resolved_cells=pre.resolved_cells,
                repairs=pre.repairs,
            ))
        else:
            # Full box: every resolved cell remains owned and matched.
            owns_any = bool(pre.resolved_cells and owned)
            is_noop = pre.is_noop or (bool(pre.resolved_cells) and not owns_any)
            frozen_target = {
                "box": {"from": pre.resolved_from, "to": pre.resolved_to}
            }
            group.append(_GroupEdit(
                index=pre.index,
                normalization=pre.normalization,
                legacy=pre.legacy,
                block_info=pre.block_info,
                expect_info=pre.expect_info,
                frozen_target=frozen_target,
                frozen_positions=None,
                frozen_from=pre.resolved_from,
                frozen_to=pre.resolved_to,
                locked_targets=owned_locked_targets,
                signature=pre.signature,
                matched=0 if is_noop else pre.matched,
                skipped=pre.skipped,
                previous_type_counts=pre.previous_type_counts,
                volume=pre.volume,
                status="noop" if is_noop else pre.status,
                is_noop=is_noop,
                resolved_cells=pre.resolved_cells,
                repairs=pre.repairs,
            ))
    return group


def _status_from_preflight(preflight_fields: dict[str, Any]) -> str:
    """Derive a per-edit preflight status (spec §9.1) from addon fields."""
    status = preflight_fields.get("status")
    if isinstance(status, str) and status:
        return status
    matched = preflight_fields.get("matched_count")
    skipped = preflight_fields.get("skipped")
    if isinstance(matched, int) and matched == 0:
        return "failed"
    if isinstance(skipped, int) and skipped > 0:
        return "partial"
    return "applied"


def _split_preflight_body(
    body: Any,
) -> tuple[dict[str, Any] | None, ToolResult | None]:
    """Parse a bridge preflight response body, returning (fields, error)."""
    if not isinstance(body, dict):
        return None, ToolResult.failure(
            dumps_payload(
                build_error_response(
                    BlockErrorCode.INTERNAL_ERROR,
                    "preflight 响应无法解析",
                )
            ),
            error_kind="INTERNAL",
        )
    if body.get("ok") is True:
        return {k: v for k, v in body.items() if k not in {"schema_version", "ok"}}, None
    if isinstance(body, dict) and body.get("ok") is False:
        return body, None
    return body if isinstance(body, dict) else {}, None


async def _preflight_one_edit(
    *,
    index: int,
    normalization: _EditNormalization,
    budget: int,
    limits_payload: dict[str, Any],
    max_locked_on_wire: int,
    deps: Any,
    edit_args: dict[str, Any],
) -> tuple[_EditPreflight | None, ToolResult | None]:
    """Run bridge preflight for a single edit and classify its result.

    Mirrors the single-edit preflight path but returns structured data instead
    of mutating shared locals. Position edits that fail the strict zero-match
    check return a PRECONDITION_FAILED error (the whole group is rejected).
    """
    legacy = normalization.legacy
    mode = legacy["mode"]
    coord_mode = legacy["coordinate_mode"]
    dimension = legacy.get("dimension")
    position = legacy.get("position")
    positions = legacy.get("positions")
    from_pos = legacy.get("from")
    to_pos = legacy.get("to")
    type_id_val = legacy["type_id"]
    states = legacy.get("states")
    replace_any = legacy["replace_any"]
    expected_previous = legacy.get("expected_previous")

    # Limits are already enforced per-edit in _normalize_one_edit; pass the
    # real limits here so the structural _validate_edit_args check does not
    # re-reject a normalized batch/fill as LIMIT_EXCEEDED.
    preflight_max_positions = limits_payload.get("max_discrete_positions", 0) or 0
    preflight_max_fill_volume = limits_payload.get("max_fill_volume", 0) or 0
    validation = _validate_edit_args(
        mode=mode,
        coordinate_mode=coord_mode,
        dimension=dimension,
        position=position,
        positions=positions,
        from_pos=from_pos if isinstance(from_pos, dict) else None,
        to_pos=to_pos if isinstance(to_pos, dict) else None,
        type_id=type_id_val,
        replace_any=replace_any,
        expected_previous=expected_previous,
        max_positions=preflight_max_positions,
        max_fill_volume=preflight_max_fill_volume,
    )
    if validation is not None:
        return None, validation

    payload = build_edit_payload(
        mode=mode,
        coordinate_mode=coord_mode,
        dimension=dimension,
        position=position,
        positions=positions,
        from_pos=from_pos if isinstance(from_pos, dict) else None,
        to_pos=to_pos if isinstance(to_pos, dict) else None,
        type_id=type_id_val,
        states=states,
        replace_any=replace_any,
        expected_previous=expected_previous,
        player_name=deps.player_name,
        phase="preflight",
        limits=limits_payload,
        max_locked_targets_on_wire=max_locked_on_wire,
    )
    preflight_volume = (
        _aabb_volume(from_pos, to_pos)
        if isinstance(from_pos, dict) and isinstance(to_pos, dict)
        else None
    )
    budget_fail = check_bridge_command_line_budget(
        "edit_blocks",
        payload,
        budget=budget,
        volume=preflight_volume,
    )
    if budget_fail is not None:
        return None, budget_fail

    result = await call_block_capability(deps.addon_bridge, "edit_blocks", payload)
    if not result.is_success:
        return None, result
    try:
        body = json.loads(result.output)
    except Exception:
        return None, ToolResult.failure(
            dumps_payload(
                build_error_response(
                    BlockErrorCode.INTERNAL_ERROR,
                    "preflight 响应无法解析",
                )
            ),
            error_kind="INTERNAL",
        )
    preflight_fields, parse_error = _split_preflight_body(body)
    if parse_error is not None:
        return None, parse_error
    assert preflight_fields is not None

    # Strict zero-match classification (spec §4.3 / §9.2). For position edits a
    # non-noop zero match rejects the whole group before approval. ``edit_args``
    # carries only this edit so the repair hint names this edit's ``expect``
    # (not ``edits[0]``) when a later edit fails.
    zero_match_failure = _classify_zero_match_preflight("edit_blocks", edit_args, preflight_fields)
    if zero_match_failure is not None:
        return None, zero_match_failure

    locked = preflight_fields.get("locked_targets")
    locked_targets = list(locked) if isinstance(locked, list) else []
    resolved_positions = (
        _resolved_positions_from_locked(locked_targets) if mode != "fill" else None
    )
    resolved_from = preflight_fields.get("from") or (from_pos if mode == "fill" else None)
    resolved_to = preflight_fields.get("to") or (to_pos if mode == "fill" else None)
    if mode == "fill" and isinstance(resolved_from, dict) and isinstance(resolved_to, dict):
        try:
            resolved_cells = frozenset(_expand_aabb_cells(resolved_from, resolved_to))
        except (KeyError, TypeError, ValueError):
            resolved_cells = frozenset(
                _cell_key(c["x"], c["y"], c["z"]) for c in locked_targets
                if isinstance(c, dict) and all(k in c for k in ("x", "y", "z"))
            )
    else:
        resolved_cells = frozenset(
            _cell_key(c["x"], c["y"], c["z"]) for c in resolved_positions or []
        )

    matched = preflight_fields.get("matched_count")
    matched = int(matched) if isinstance(matched, int) else len(locked_targets)
    skipped = preflight_fields.get("skipped")
    skipped = int(skipped) if isinstance(skipped, int) else 0
    previous_counts = preflight_fields.get("previous_type_counts")
    previous_type_counts = dict(previous_counts) if isinstance(previous_counts, dict) else {}
    volume = preflight_fields.get("volume")
    if not isinstance(volume, int):
        volume = len(resolved_cells)
    status = _status_from_preflight(preflight_fields)
    already_target = preflight_fields.get("already_target")
    is_noop = bool(
        preflight_fields.get("status") == "noop"
        or (isinstance(already_target, int) and isinstance(volume, int)
            and volume > 0 and already_target == volume)
    )
    repairs: list[Any] = list(normalization.repairs)
    addon_repairs = preflight_fields.get("repairs_applied")
    if isinstance(addon_repairs, list):
        for repair in addon_repairs:
            if repair not in repairs:
                repairs.append(repair)

    return _EditPreflight(
        index=index,
        normalization=normalization,
        legacy=legacy,
        block_info=normalization.block_info,
        expect_info=normalization.expect_info,
        signature=_edit_signature(normalization.block_info, normalization.expect_info),
        preflight_fields=preflight_fields,
        locked_targets=locked_targets,
        resolved_positions=resolved_positions,
        resolved_from=resolved_from,
        resolved_to=resolved_to,
        resolved_cells=resolved_cells,
        matched=matched,
        skipped=skipped,
        previous_type_counts=previous_type_counts,
        volume=volume,
        status=status,
        is_noop=is_noop,
        repairs=repairs,
    ), None


async def _run_grouped_edit_preflight(
    *,
    ctx: RunContext[AgentDependencies],
    deps: Any,
    tool_args: dict[str, Any],
    limits: Any,
    limits_payload: dict[str, Any],
    budget: int,
) -> tuple[BlockPreflightPlan | None, ToolResult | None]:
    """Grouped independent-edit preflight (issue 04, spec §8.1/§8.2).

    Validates all edits, runs conflict detection + dedup, calls the bridge once
    per edit, aggregates counts, and produces a single
    :class:`BlockPreflightPlan` whose ``execute_args`` freezes every edit's
    resolved absolute target. One preflight, one approval, one plan.
    """
    normalizations, validation = _normalize_edits_for_preflight(
        tool_args,
        limits.max_discrete_positions,
        limits.max_fill_volume,
        limits.max_edits_per_group,
        limits.max_total_targets_per_group,
    )
    if validation is not None:
        return None, validation
    assert normalizations is not None

    # Per-edit bridge preflight (spec §8.1 steps 7-8). Each edit is independent
    # and preflighted against the same world snapshot. The zero-match hint must
    # reference the failing edit's own ``expect``, so each preflight receives a
    # single-edit args view.
    raw_edits = tool_args.get("edits")
    preflights: list[_EditPreflight] = []
    for index, normalization in enumerate(normalizations):
        raw_edit = (
            raw_edits[index]
            if isinstance(raw_edits, list) and index < len(raw_edits)
            else {}
        )
        preflight, failure = await _preflight_one_edit(
            index=index,
            normalization=normalization,
            budget=budget,
            limits_payload=limits_payload,
            max_locked_on_wire=limits.max_locked_targets_on_wire,
            deps=deps,
            edit_args={"edits": [raw_edit]} if isinstance(raw_edit, dict) else {},
        )
        if failure is not None:
            return None, failure
        assert preflight is not None
        preflights.append(preflight)

    # Single-edit fast path: delegate to the legacy plan builder so the
    # execute_args (and thus the idempotency hash) are byte-identical to the
    # pre-issue-04 single-edit contract. This keeps approval-resume stable
    # across the grouped/non-grouped boundary.
    if len(preflights) == 1:
        plan = build_block_preflight_plan(
            "edit_blocks", tool_args, preflights[0].preflight_fields,
        )
        return plan, None

    # Conflict detection + dedup (spec §5.2 / §8.1 steps 5-6).
    try:
        ownership = _compute_cell_ownership(preflights)
    except _ConflictingEditsError as exc:
        body = build_error_response(
            BlockErrorCode.CONFLICTING_EDITS,
            (
                f"编辑组内存在冲突目标：位置 "
                f"({exc.cell[0]}, {exc.cell[1]}, {exc.cell[2]}) 同时被"
                f"编辑 {exc.first_index} 与编辑 {exc.second_index} 以不同方块"
                f"或不同前置条件声明；请拆分为不同调用。"
            ),
            cell={"x": exc.cell[0], "y": exc.cell[1], "z": exc.cell[2]},
            first_index=exc.first_index,
            second_index=exc.second_index,
            retryable=False,
            external_state_unknown=False,
            fallback_allowed=False,
        )
        return None, ToolResult.failure(
            dumps_payload(body),
            error_kind="INVALID_ARGUMENT",
            retryable=False,
            diagnostic_summary="conflicting_edits",
        )

    group = _build_group_edits(preflights, ownership)

    # Build the single frozen execute_args carrying every edit (spec §8.3).
    # Resolve the absolute dimension from locked targets when the original used
    # relative coordinates (no explicit dimension). All edits in a group share
    # the current event player's dimension.
    dimension = tool_args.get("dimension")
    if not dimension:
        for g in group:
            for cell in g.locked_targets:
                if isinstance(cell, dict) and cell.get("dimension"):
                    dimension = cell["dimension"]
                    break
            if dimension:
                break
    frozen_edits = [_edit_to_frozen_args(g) for g in group]
    # Flat merged locked_targets keeps backward compatibility with harness
    # recovery that reads execute_args["locked_targets"] as a single list.
    locked_targets_by_edit = [list(g.locked_targets) for g in group]
    flat_locked = [cell for locks in locked_targets_by_edit for cell in locks]
    authorized_args: dict[str, Any] = {
        "edits": frozen_edits,
        "dimension": dimension,
        "locked_targets": flat_locked,
        "locked_targets_by_edit": locked_targets_by_edit,
        "noop_edit_indices": [g.index for g in group if g.is_noop],
        "phase": "execute",
    }
    if group and all(g.is_noop for g in group):
        # A no-op plan is still canonical executable state, but it deliberately
        # has no locks and must pass the strict approval-resume projection.
        authorized_args["status"] = "noop"
    execute_args = dict(authorized_args)

    # Approval summary (spec §8.2) — bounded, never includes full locked_targets.
    # Counts reflect the deduped plan (owned cells), not raw preflight overlap.
    approval_metadata = _build_group_approval_metadata(
        group, preflights, ownership, dimension
    )
    if approval_metadata["repairs_applied"]:
        execute_args["repairs_applied"] = approval_metadata["repairs_applied"][:8]

    # Reject before approval if any edit's post-omit execute frame overflows.
    for g in group:
        if g.is_noop:
            continue
        exec_failure = _check_group_edit_execute_budget(
            g, dimension, budget, limits.max_locked_targets_on_wire, deps.player_name,
        )
        if exec_failure is not None:
            return None, exec_failure

    return BlockPreflightPlan(authorized_args, execute_args, approval_metadata), None


def _edit_to_frozen_args(group_edit: _GroupEdit) -> dict[str, Any]:
    """Public, harness-consumable description of one frozen edit."""
    block = {"type_id": group_edit.block_info.get("type_id")}
    if group_edit.block_info.get("states") is not None:
        block["states"] = group_edit.block_info["states"]
    out: dict[str, Any] = {
        "target": group_edit.frozen_target,
        "block": block,
    }
    if group_edit.expect_info.get("kind") != "air":
        out["expect"] = _expect_info_to_args(group_edit.expect_info)
    return out


def _expect_info_to_args(expect_info: dict[str, Any]) -> Any:
    """Convert a normalized expect descriptor back to a model-facing value."""
    kind = expect_info.get("kind")
    if kind == "any":
        return "any"
    if kind == "type":
        return expect_info.get("type_id")
    if kind == "permutation":
        out: dict[str, Any] = {"type_id": expect_info.get("type_id")}
        states = expect_info.get("states")
        if isinstance(states, dict) and states:
            out["states"] = states
        return out
    return "air"


def _build_group_meta(
    group: list[_GroupEdit],
    preflights: list[_EditPreflight],
    ownership: _CellOwnership,
) -> dict[str, Any]:
    """Internal-only group metadata (carried in authorized_args, not approval).

    Holds per-edit locked targets and ownership so resume can re-derive each
    edit's execute payload without re-preflight. Bounded: locked targets are the
    resolved absolute cells, not full before/after snapshots.
    """
    locked_by_index: list[list[dict[str, Any]]] = []
    for g in group:
        locked_by_index.append(list(g.locked_targets))
    deduped_cells: set[tuple[int, int, int]] = set()
    for g in group:
        if g.frozen_positions is None and not g.is_noop:
            # Box edit overlapping earlier identical edits: report shared cells.
            owned = ownership.owned.get(g.index) or set()
            deduped_cells |= g.resolved_cells - owned
    return {
        "edit_count": len(group),
        "locked_targets_by_index": locked_by_index,
        "deduped_cells": sorted(deduped_cells),
        "noop_indices": [g.index for g in group if g.is_noop],
    }


def _aggregate_type_counts(
    counts_list: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge per-edit previous_type_counts into a single bounded map."""
    merged: dict[str, Any] = {}
    for counts in counts_list:
        if not isinstance(counts, dict):
            continue
        for key, value in counts.items():
            try:
                merged[key] = int(merged.get(key, 0)) + int(value)
            except (TypeError, ValueError):
                merged[key] = value
    return merged


def _build_group_approval_metadata(
    group: list[_GroupEdit],
    preflights: list[_EditPreflight],
    ownership: _CellOwnership,
    dimension: str | None,
) -> dict[str, Any]:
    """Bounded approval summary for the whole group (spec §8.2).

    Counts reflect the *deduped* plan (spec §5.2): each edit contributes only
    the cells it owns after identical-overlap dedup, so the summary matches
    what execution can actually change. ``target_block_counts`` counts target
    cells per block type (not edits), and auto-repair evidence recorded during
    normalization is surfaced here (spec §4.2 / §8.2).
    """
    total_targets = 0
    total_matched = 0
    total_skipped = 0
    any_expect_any = False
    any_non_rollbackable_fill = False
    target_block_summary: dict[str, Any] = {}
    replaced_counts: list[dict[str, Any]] = []
    repairs: list[str] = []
    per_edit: list[dict[str, Any]] = []
    for g in group:
        owned = ownership.owned.get(g.index) or set()
        effective = len(owned)
        total_targets += effective
        matched = 0
        if not g.is_noop:
            if g.locked_targets:
                # Exact post-dedup match count: preflight matched cells
                # (locked_targets) that this edit still owns.
                matched = sum(
                    1
                    for cell in g.locked_targets
                    if isinstance(cell, dict)
                    and all(k in cell for k in ("x", "y", "z"))
                    and _cell_key(cell["x"], cell["y"], cell["z"]) in owned
                )
            else:
                matched = g.matched
            total_matched += matched
            total_skipped += g.skipped
        block_id = g.block_info.get("type_id")
        if isinstance(block_id, str) and block_id and effective:
            target_block_summary[block_id] = (
                int(target_block_summary.get(block_id, 0)) + effective
            )
        if g.expect_info.get("kind") == "any":
            any_expect_any = True
        if g.frozen_positions is None and not g.is_noop:
            any_non_rollbackable_fill = True
        per_edit.append({
            "index": g.index,
            "mode": "fill" if g.frozen_positions is None else ("place" if len(g.frozen_positions or []) <= 1 else "batch"),
            "type_id": block_id,
            "matched": matched,
            "skipped": g.skipped,
            "status": g.status,
        })
        for repair in g.repairs:
            if repair not in repairs:
                repairs.append(repair)
    for pr in preflights:
        replaced_counts.append(pr.previous_type_counts)
    return {
        "dimension": dimension,
        "edit_count": len(group),
        "total_targets": total_targets,
        "matched": total_matched,
        "skipped": total_skipped,
        "target_block_counts": target_block_summary,
        "replaced_non_air_counts": _aggregate_type_counts(replaced_counts),
        "expect_any": any_expect_any,
        "non_rollbackable_fill": any_non_rollbackable_fill,
        "repairs_applied": repairs[:8],
        "edits": per_edit,
    }


def _check_group_edit_execute_budget(
    group_edit: _GroupEdit,
    dimension: str | None,
    budget: int,
    max_locked_on_wire: int,
    player_name: str,
) -> ToolResult | None:
    """Budget + wire-cap check for one edit's *execute* payload (post-omit)."""
    if group_edit.frozen_positions is not None:
        mode = "place" if len(group_edit.frozen_positions) <= 1 else "batch"
        position = group_edit.frozen_positions[0] if mode == "place" else None
        positions = group_edit.frozen_positions if mode == "batch" else None
        from_pos = None
        to_pos = None
    else:
        mode = "fill"
        position = None
        positions = None
        from_pos = group_edit.frozen_from
        to_pos = group_edit.frozen_to
    payload = build_edit_payload(
        mode=mode,
        coordinate_mode="absolute",
        dimension=dimension,
        position=position,
        positions=positions,
        from_pos=from_pos if isinstance(from_pos, dict) else None,
        to_pos=to_pos if isinstance(to_pos, dict) else None,
        type_id=str(group_edit.block_info.get("type_id") or ""),
        states=group_edit.block_info.get("states"),
        replace_any=group_edit.expect_info.get("kind") == "any",
        expected_previous=None,
        player_name=player_name,
        phase="execute",
        locked_targets=list(group_edit.locked_targets),
        max_locked_targets_on_wire=max_locked_on_wire,
    )
    if "locked_targets" in payload:
        wire_fail = locked_targets_wire_limit_exceeded(
            payload.get("locked_targets")
            if isinstance(payload.get("locked_targets"), list)
            else list(group_edit.locked_targets),
            max_locked_targets_on_wire=max_locked_on_wire,
        )
        if wire_fail is not None:
            return wire_fail
    locked = payload.get("locked_targets")
    matched_count = len(locked) if isinstance(locked, list) else len(group_edit.locked_targets)
    volume = (
        _aabb_volume(from_pos, to_pos)
        if isinstance(from_pos, dict) and isinstance(to_pos, dict)
        else len(group_edit.resolved_cells)
    )
    return check_bridge_command_line_budget(
        "edit_blocks",
        payload,
        budget=budget,
        matched_count=matched_count,
        volume=volume,
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
    if tool_args.get("phase") == "execute" and (
        tool_args.get("locked_targets")
        or (
            tool_name == "edit_blocks"
            and tool_args.get("status") == "noop"
            and isinstance(tool_args.get("edits"), list)
        )
    ):
        return dict(tool_args), None

    budget = get_command_line_byte_budget(deps.settings)

    if tool_name == "edit_blocks" and "edits" in tool_args:
        plan, failure = await _run_grouped_edit_preflight(
            ctx=ctx,
            deps=deps,
            tool_args=tool_args,
            limits=limits,
            limits_payload=limits_payload,
            budget=budget,
        )
        return plan, failure

    if tool_name == "inspect_block":
        # inspect is low-risk; preflight only needed for relative resolution.
        # Absolute inspect can execute directly without a separate preflight phase.
        target = tool_args.get("target")
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

    if tool_name == "edit_blocks":
        # Legacy flat path only: every ``edit_blocks`` call carrying ``edits``
        # is intercepted by the grouped preflight branch above.
        mode = str(tool_args.get("mode") or "place")
        coord_mode = str(tool_args.get("coordinate_mode") or "absolute")
        dimension = tool_args.get("dimension")
        position = tool_args.get("position")
        positions = tool_args.get("positions")
        from_pos = tool_args.get("from") or tool_args.get("from_pos")
        to_pos = tool_args.get("to") or tool_args.get("to_pos")
        type_id_val = str(tool_args.get("type_id") or "")
        states = tool_args.get("states")
        replace_any = bool(tool_args.get("replace_any", False))
        expected_previous = tool_args.get("expected_previous")
        validation = _validate_edit_args(
            mode=mode,
            coordinate_mode=coord_mode,
            dimension=dimension,
            position=position,
            positions=positions,
            from_pos=from_pos if isinstance(from_pos, dict) else None,
            to_pos=to_pos if isinstance(to_pos, dict) else None,
            type_id=type_id_val,
            replace_any=replace_any,
            expected_previous=expected_previous,
            max_positions=limits.max_discrete_positions,
            max_fill_volume=limits.max_fill_volume,
        )
        if validation is not None:
            return None, validation

        payload = build_edit_payload(
            mode=mode,
            coordinate_mode=coord_mode,
            dimension=dimension,
            position=position,
            positions=positions,
            from_pos=from_pos if isinstance(from_pos, dict) else None,
            to_pos=to_pos if isinstance(to_pos, dict) else None,
            type_id=type_id_val,
            states=states,
            replace_any=replace_any,
            expected_previous=expected_previous,
            player_name=deps.player_name,
            phase="preflight",
            limits=limits_payload,
            max_locked_targets_on_wire=limits.max_locked_targets_on_wire,
        )
        budget = get_command_line_byte_budget(deps.settings)
        preflight_volume = (
            _aabb_volume(from_pos, to_pos)
            if isinstance(from_pos, dict) and isinstance(to_pos, dict)
            else None
        )
        budget_fail = check_bridge_command_line_budget(
            "edit_blocks",
            payload,
            budget=budget,
            volume=preflight_volume,
        )
        if budget_fail is not None:
            return None, budget_fail

        result = await call_block_capability(deps.addon_bridge, "edit_blocks", payload)
        if not result.is_success:
            return None, result
        try:
            body = json.loads(result.output)
        except Exception:
            return None, ToolResult.failure(
                dumps_payload(
                    build_error_response(
                        BlockErrorCode.INTERNAL_ERROR,
                        "preflight 响应无法解析",
                    )
                ),
                error_kind="INTERNAL",
            )
        if isinstance(body, dict) and body.get("ok") is True:
            preflight_fields = {k: v for k, v in body.items() if k not in {"schema_version", "ok"}}
        elif isinstance(body, dict):
            preflight_fields = body
        else:
            preflight_fields = {}
        zero_match_failure = _classify_zero_match_preflight(tool_name, tool_args, preflight_fields)
        if zero_match_failure is not None:
            return None, zero_match_failure
        plan = build_block_preflight_plan(tool_name, tool_args, preflight_fields)

        # Reject before approval when the post-omit execute frame would still overflow.
        exec_args = plan.execute_args
        if "edits" in exec_args:
            # New contract: rebuild the execute payload from the resolved edits.
            exec_edits = exec_args.get("edits")
            if isinstance(exec_edits, list) and exec_edits and isinstance(exec_edits[0], dict):
                exec_legacy = _unified_target_to_legacy(
                    _normalize_edits_target(exec_edits[0]),
                    _normalize_block_input(exec_edits[0].get("block"))[0],
                    _normalize_expect(exec_edits[0].get("expect")),
                    exec_args.get("dimension"),
                )
            else:
                exec_legacy = None
            if exec_legacy is None:
                return None, _host_limit_error(
                    BlockErrorCode.INVALID_ARGUMENT,
                    "edits 执行契约不完整",
                )
            exec_mode = exec_legacy["mode"]
            exec_coord_mode = exec_legacy["coordinate_mode"]
            exec_dimension = exec_legacy.get("dimension")
            exec_position = exec_legacy.get("position")
            exec_positions = exec_legacy.get("positions")
            exec_from = exec_legacy.get("from")
            exec_to = exec_legacy.get("to")
            exec_type_id = exec_legacy["type_id"]
            exec_states = exec_legacy.get("states")
            exec_replace_any = exec_legacy["replace_any"]
            exec_expected_previous = exec_legacy.get("expected_previous")
            locked_for_exec = (
                exec_args.get("locked_targets")
                if isinstance(exec_args.get("locked_targets"), list)
                else None
            )
        else:
            exec_mode = str(exec_args.get("mode") or mode)
            exec_coord_mode = str(exec_args.get("coordinate_mode") or coord_mode)
            exec_dimension = exec_args.get("dimension")
            exec_position = exec_args.get("position") if isinstance(exec_args.get("position"), dict) else None
            exec_positions = exec_args.get("positions") if isinstance(exec_args.get("positions"), list) else None
            exec_from = exec_args.get("from_pos") or exec_args.get("from")
            exec_to = exec_args.get("to_pos") or exec_args.get("to")
            exec_type_id = str(exec_args.get("type_id") or type_id_val)
            exec_states = exec_args.get("states")
            exec_replace_any = bool(exec_args.get("replace_any", False))
            exec_expected_previous = exec_args.get("expected_previous")
            locked_for_exec = (
                exec_args.get("locked_targets")
                if isinstance(exec_args.get("locked_targets"), list)
                else None
            )
        exec_payload = build_edit_payload(
            mode=exec_mode,
            coordinate_mode=exec_coord_mode,
            dimension=exec_dimension,
            position=exec_position,
            positions=exec_positions,
            from_pos=exec_from if isinstance(exec_from, dict) else None,
            to_pos=exec_to if isinstance(exec_to, dict) else None,
            type_id=str(exec_type_id or ""),
            states=exec_states,
            replace_any=bool(exec_replace_any),
            expected_previous=exec_expected_previous,
            player_name=deps.player_name,
            phase="execute",
            locked_targets=locked_for_exec,
            max_locked_targets_on_wire=limits.max_locked_targets_on_wire,
        )
        # Cap only when wire still ships locked_targets (non-omit path).
        if "locked_targets" in exec_payload:
            wire_cap_fail = locked_targets_wire_limit_exceeded(
                exec_payload.get("locked_targets")
                if isinstance(exec_payload.get("locked_targets"), list)
                else locked_for_exec,
                max_locked_targets_on_wire=limits.max_locked_targets_on_wire,
            )
            if wire_cap_fail is not None:
                return None, wire_cap_fail
        locked = exec_args.get("locked_targets")
        matched_count = len(locked) if isinstance(locked, list) else None
        exec_volume = (
            _aabb_volume(exec_from, exec_to)
            if isinstance(exec_from, dict) and isinstance(exec_to, dict)
            else preflight_volume
        )
        execute_budget_fail = check_bridge_command_line_budget(
            "edit_blocks",
            exec_payload,
            budget=budget,
            matched_count=matched_count,
            volume=exec_volume,
        )
        if execute_budget_fail is not None:
            return None, execute_budget_fail
        return plan, None

    return dict(tool_args), None


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
    return await call_block_capability(deps.addon_bridge, "inspect_block", payload)


def _legacy_kwargs_from_edit(edit: dict[str, Any], dimension: str | None) -> dict[str, Any]:
    """Map one frozen/normalized edit dict back to legacy flat kwargs."""
    normalized = _normalize_edits_target(edit)
    block_info, _repairs = _normalize_block_input(edit.get("block"))
    expect_info = _normalize_expect(edit.get("expect"))
    legacy = _unified_target_to_legacy(normalized, block_info, expect_info, dimension)
    return {
        "mode": legacy.get("mode", "place"),
        "coordinate_mode": legacy.get("coordinate_mode", "absolute"),
        "type_id": legacy.get("type_id", ""),
        "states": legacy.get("states"),
        "replace_any": legacy.get("replace_any", False),
        "expected_previous": legacy.get("expected_previous"),
        "position": legacy.get("position"),
        "positions": legacy.get("positions"),
        "from_pos": legacy.get("from"),
        "to_pos": legacy.get("to"),
    }


async def _execute_one_group_edit(
    ctx: RunContext[AgentDependencies],
    *,
    index: int,
    edit: dict[str, Any],
    dimension: str | None,
    phase: str,
    locked_targets: list[dict[str, Any]] | None,
    is_noop: bool,
) -> dict[str, Any]:
    """Execute a single (frozen) edit and return a per-edit outcome dict.

    On a definite failure, returns an outcome with ``status`` ``failed`` or
    ``unknown`` plus a ``failure`` ToolResult the caller uses to stop the rest
    of the group (spec §8.3).
    """
    deps = ctx.deps
    if is_noop:
        return {
            "index": index,
            "status": "noop",
            "changed": 0,
            "skipped": 0,
            "mode": _mode_of_edit(edit),
        }
    # Deduped position/batch edits freeze to an empty positions list (spec
    # §5.2). There is nothing to send: report a truthful noop outcome instead
    # of failing validation (or raising) on an empty batch payload.
    target = edit.get("target")
    if (
        isinstance(target, dict)
        and isinstance(target.get("positions"), list)
        and not target["positions"]
    ):
        return {
            "index": index,
            "status": "noop",
            "changed": 0,
            "skipped": 0,
            "mode": "place",
        }
    legacy = _legacy_kwargs_from_edit(edit, dimension)
    mode = legacy["mode"]
    unsupported = await _require_supported(ctx)
    if unsupported is not None:
        code, fallback_allowed = _failure_metadata(unsupported)
        return {
            "index": index,
            "status": "unknown",
            "changed": 0,
            "skipped": 0,
            "mode": mode,
            "warning": "Addon 桥接不可用",
            "failure": unsupported,
            "code": code,
            "fallback_allowed": fallback_allowed,
        }

    limits = get_block_tools_limits(deps.settings)
    validation = _validate_edit_args(
        mode=mode,
        coordinate_mode=legacy["coordinate_mode"],
        dimension=dimension,
        position=legacy.get("position"),
        positions=legacy.get("positions"),
        from_pos=legacy.get("from_pos"),
        to_pos=legacy.get("to_pos"),
        type_id=legacy["type_id"],
        replace_any=legacy["replace_any"],
        expected_previous=legacy.get("expected_previous"),
        max_positions=limits.max_discrete_positions,
        max_fill_volume=limits.max_fill_volume,
    )
    if validation is not None:
        return {
            "index": index,
            "status": "failed",
            "changed": 0,
            "skipped": 0,
            "mode": mode,
            "failure": validation,
        }

    payload = build_edit_payload(
        mode=mode,
        coordinate_mode=legacy["coordinate_mode"],
        dimension=dimension,
        position=legacy.get("position"),
        positions=legacy.get("positions"),
        from_pos=legacy.get("from_pos"),
        to_pos=legacy.get("to_pos"),
        type_id=legacy["type_id"],
        states=legacy.get("states"),
        replace_any=legacy["replace_any"],
        expected_previous=legacy.get("expected_previous"),
        player_name=deps.player_name,
        phase=phase,
        locked_targets=locked_targets,
        limits={
            "max_discrete_positions": limits.max_discrete_positions,
            "max_fill_volume": limits.max_fill_volume,
            "cells_per_tick": limits.cells_per_tick,
            "max_locked_targets_on_wire": limits.max_locked_targets_on_wire,
        },
        max_locked_targets_on_wire=limits.max_locked_targets_on_wire,
    )
    if "locked_targets" in payload:
        wire_fail = locked_targets_wire_limit_exceeded(
            payload.get("locked_targets")
            if isinstance(payload.get("locked_targets"), list)
            else None,
            max_locked_targets_on_wire=limits.max_locked_targets_on_wire,
        )
        if wire_fail is not None:
            return {
                "index": index,
                "status": "failed",
                "changed": 0,
                "skipped": 0,
                "mode": mode,
                "failure": wire_fail,
            }
    budget = get_command_line_byte_budget(deps.settings)
    volume: int | None = None
    from_pos = legacy.get("from_pos")
    to_pos = legacy.get("to_pos")
    if isinstance(from_pos, dict) and isinstance(to_pos, dict):
        volume = _aabb_volume(from_pos, to_pos)
    budget_fail = check_bridge_command_line_budget(
        "edit_blocks",
        payload,
        budget=budget,
        volume=volume,
    )
    if budget_fail is not None:
        return {
            "index": index,
            "status": "failed",
            "changed": 0,
            "skipped": 0,
            "mode": mode,
            "failure": budget_fail,
        }

    authorized_bounds: dict[str, Any] | None = None
    if mode == "fill" and isinstance(legacy.get("from_pos"), dict) and isinstance(
        legacy.get("to_pos"), dict
    ):
        authorized_bounds = {"from": legacy["from_pos"], "to": legacy["to_pos"]}
    try:
        result = await call_block_capability(
            deps.addon_bridge,
            "edit_blocks",
            payload,
            mode=mode,
            authorized_bounds=authorized_bounds,
            project_for_model=False,
        )
    except Exception as exc:
        mapped = map_bridge_exception(exc, tool_name="edit_blocks")
        unknown = bool(getattr(mapped, "external_state_unknown", False))
        code, fallback_allowed = _failure_metadata(mapped)
        return {
            "index": index,
            "status": "unknown" if unknown else "failed",
            "changed": 0,
            "skipped": 0,
            "mode": mode,
            "failure": mapped,
            "code": code,
            "fallback_allowed": fallback_allowed,
        }

    if not result.is_success:
        code, fallback_allowed = _failure_metadata(result)
        unknown = code == BlockErrorCode.STATE_UNKNOWN
        try:
            failure_body = json.loads(result.output)
        except Exception:
            failure_body = {}
        audit_evidence = _bounded_edit_audit_evidence(
            index, failure_body if isinstance(failure_body, dict) else {}
        )
        audit_evidence["code"] = code
        return {
            "index": index,
            "status": "unknown" if unknown else "failed",
            "changed": 0,
            "skipped": 0,
            "mode": mode,
            "failure": result,
            "code": code,
            "fallback_allowed": fallback_allowed,
            "audit_evidence": audit_evidence,
        }

    try:
        body = json.loads(result.output)
    except Exception:
        body = {}
    changed = _per_edit_changed(body)
    skipped = _per_edit_skipped(body)
    skipped_counts = _per_edit_skipped_type_counts(body)
    status = body.get("status") or ("partial" if skipped > 0 else "applied")
    if changed == 0 and skipped == 0 and body.get("status") != "noop":
        status = body.get("status") or "applied"
    warning = None
    if skipped > 0:
        warning = "部分位置因前置条件不满足而跳过"
    return {
        "index": index,
        "status": status,
        "changed": changed,
        "skipped": skipped,
        "skipped_type_counts": skipped_counts,
        "mode": mode,
        "warning": warning,
        "audit_evidence": _bounded_edit_audit_evidence(index, body),
    }


def _failure_metadata(result: ToolResult) -> tuple[str, bool]:
    """Extract the stable code and fail-closed fallback decision from a result."""
    try:
        body = json.loads(result.output)
    except Exception:
        return BlockErrorCode.INTERNAL_ERROR, False
    if isinstance(body, dict):
        code = body.get("code")
        if isinstance(code, str) and code:
            return code, bool(body.get("fallback_allowed", False))
    return BlockErrorCode.INTERNAL_ERROR, False


def _bounded_edit_audit_evidence(index: int, body: dict[str, Any]) -> dict[str, Any]:
    """Retain bounded Add-on execution evidence for tool audit only."""
    evidence: dict[str, Any] = {"index": index}
    for key in _AUDIT_EDIT_EVIDENCE_FIELDS:
        value = body.get(key)
        if isinstance(value, list):
            evidence[key] = value[:8]
        elif value is not None:
            evidence[key] = value
    return evidence


async def _execute_edits_group(
    ctx: RunContext[AgentDependencies],
    *,
    edits: list[dict[str, Any]],
    dimension: str | None,
    phase: str | None,
    locked_targets_by_edit: list[list[dict[str, Any]]] | None = None,
    noop_edit_indices: list[int] | None = None,
    repairs_applied: list[Any] | None = None,
) -> ToolResult:
    """Execute a group of independent edits in canonical order (spec §8.3).

    Each edit is executed via its own bridge call. A definite failure stops the
    remaining edits; the aggregated result (spec §9.3) is returned even for a
    failed/unknown group and never claims full atomicity. Only the projected
    group result is returned to the model — full before/after/locked_targets/
    verification stay in the audit log.
    """
    exec_phase = phase or "execute"
    noop_indices = {
        index for index in (noop_edit_indices or []) if isinstance(index, int)
    }
    per_edit: list[dict[str, Any]] = []
    for index, edit in enumerate(edits):
        edit_locks = None
        if (
            isinstance(locked_targets_by_edit, list)
            and index < len(locked_targets_by_edit)
            and isinstance(locked_targets_by_edit[index], list)
        ):
            edit_locks = locked_targets_by_edit[index]
        outcome = await _execute_one_group_edit(
            ctx,
            index=index,
            edit=edit,
            dimension=dimension,
            phase=exec_phase,
            locked_targets=edit_locks,
            is_noop=index in noop_indices,
        )
        per_edit.append(outcome)
        # Stop remaining edits on a definite failure (spec §8.3). Unknown also
        # halts because the world state can no longer be trusted for later edits.
        failure = outcome.get("failure")
        if outcome.get("status") in {"failed", "unknown"} and isinstance(failure, ToolResult):
            for later_index in range(index + 1, len(edits)):
                per_edit.append({
                    "index": later_index,
                    "status": "failed",
                    "changed": 0,
                    "skipped": 0,
                    "mode": _mode_of_edit(edits[later_index]),
                    "stopped_by_index": index,
                })
            break

    # Always return the aggregated group result (spec §9.3) — even when an edit
    # failed/unknown — so the model sees which edits landed and never describes
    # a partial/unknown group as fully complete. The body's ``ok``/``status``
    # fields carry the group-level outcome; the success ToolResult keeps the
    # call idempotent (no re-execution of already-applied edits).
    group = project_group_edit_result_for_model(
        per_edit, repairs_applied=repairs_applied
    )
    unknown_seen = any(o.get("status") == "unknown" for o in per_edit)
    audit_evidence = {
        "edits": [
            outcome["audit_evidence"]
            for outcome in per_edit
            if isinstance(outcome.get("audit_evidence"), dict)
        ]
    }
    return ToolResult(
        output=json.dumps(group, ensure_ascii=False),
        external_state_unknown=unknown_seen,
        audit_evidence=audit_evidence,
    )


def _mode_of_edit(edit: dict[str, Any]) -> str:
    target = edit.get("target")
    if isinstance(target, dict):
        if isinstance(target.get("positions"), list):
            return "place" if len(target["positions"]) <= 1 else "batch"
        if isinstance(target.get("box"), dict):
            return "fill"
    return "place"


async def edit_blocks_impl(
    ctx: RunContext[AgentDependencies],
    *,
    mode: EditMode = "place",
    coordinate_mode: CoordinateMode = "absolute",
    dimension: str | None = None,
    position: dict[str, Any] | None = None,
    positions: list[dict[str, Any]] | None = None,
    from_pos: dict[str, Any] | None = None,
    to_pos: dict[str, Any] | None = None,
    type_id: str = "",
    states: dict[str, Any] | None = None,
    replace_any: bool = False,
    expected_previous: dict[str, Any] | None = None,
    locked_targets: list[dict[str, Any]] | None = None,
    locked_targets_by_edit: list[list[dict[str, Any]]] | None = None,
    noop_edit_indices: list[int] | None = None,
    repairs_applied: list[Any] | None = None,
    phase: str | None = None,
    edits: list[dict[str, Any]] | None = None,
    status: str | None = None,
) -> ToolResult:
    """Mutate blocks via place | batch | fill after approval.

    The model-facing interface now uses the unified ``edits`` contract (issue
    03); the legacy flat kwargs remain for harness recovery of previously
    approved operations. When ``edits`` is supplied it is mapped to the legacy
    shape internally.

    ``status`` is a harness-only bookkeeping marker (e.g. ``noop`` for an
    all-noop group); it is stripped from the model schema and never sent to the
    Add-on.
    """
    deps = ctx.deps

    # New grouped ``edits`` contract (issue 04): execute each frozen edit
    # independently in canonical order and aggregate the result (spec §8.3/§9.3).
    if edits is not None:
        return await _execute_edits_group(
            ctx,
            edits=edits,
            dimension=dimension,
            phase=phase,
            locked_targets_by_edit=locked_targets_by_edit,
            noop_edit_indices=noop_edit_indices,
            repairs_applied=repairs_applied,
        )

    # Legacy flat contract (harness recovery of previously-approved operations).
    logger.info(
        "agent_tool_call",
        tool="edit_blocks",
        connection_id=_connection_id(deps),
        run_id=deps.run_id,
        player_name=deps.player_name,
        mode=mode,
        type_id=type_id,
        has_edits=False,
    )

    unsupported = await _require_supported(ctx)
    if unsupported is not None:
        return unsupported

    limits = get_block_tools_limits(deps.settings)
    validation = _validate_edit_args(
        mode=mode,
        coordinate_mode=coordinate_mode,
        dimension=dimension,
        position=position,
        positions=positions,
        from_pos=from_pos,
        to_pos=to_pos,
        type_id=type_id,
        replace_any=replace_any,
        expected_previous=expected_previous,
        max_positions=limits.max_discrete_positions,
        max_fill_volume=limits.max_fill_volume,
    )
    if validation is not None:
        return validation

    exec_phase = phase or "execute"
    payload = build_edit_payload(
        mode=mode,
        coordinate_mode=coordinate_mode,
        dimension=dimension,
        position=position,
        positions=positions,
        from_pos=from_pos,
        to_pos=to_pos,
        type_id=type_id,
        states=states,
        replace_any=replace_any,
        expected_previous=expected_previous,
        player_name=deps.player_name,
        phase=exec_phase,
        locked_targets=locked_targets,
        limits={
            "max_discrete_positions": limits.max_discrete_positions,
            "max_fill_volume": limits.max_fill_volume,
            "cells_per_tick": limits.cells_per_tick,
            "max_locked_targets_on_wire": limits.max_locked_targets_on_wire,
        },
        max_locked_targets_on_wire=limits.max_locked_targets_on_wire,
    )
    if "locked_targets" in payload:
        wire_cap_fail = locked_targets_wire_limit_exceeded(
            payload.get("locked_targets")
            if isinstance(payload.get("locked_targets"), list)
            else locked_targets,
            max_locked_targets_on_wire=limits.max_locked_targets_on_wire,
        )
        if wire_cap_fail is not None:
            return wire_cap_fail
    locked_on_wire = payload.get("locked_targets")
    estimated_wire = estimate_bridge_command_line_bytes("edit_blocks", payload)
    logger.info(
        "edit_blocks_bridge_payload",
        connection_id=_connection_id(deps),
        run_id=deps.run_id,
        player_name=deps.player_name,
        mode=mode,
        phase=exec_phase,
        type_id=type_id,
        coordinate_mode=coordinate_mode,
        dimension=dimension,
        locked_targets_input=len(locked_targets) if isinstance(locked_targets, list) else 0,
        locked_targets_on_wire=len(locked_on_wire) if isinstance(locked_on_wire, list) else 0,
        has_from=from_pos is not None,
        has_to=to_pos is not None,
        has_position=position is not None,
        positions_count=len(positions) if isinstance(positions, list) else 0,
        payload_bytes=len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ),
        estimated_command_line_bytes=estimated_wire,
        omitted_locked_targets="locked_targets" not in payload and bool(locked_targets),
    )
    budget = get_command_line_byte_budget(deps.settings)
    volume = (
        _aabb_volume(from_pos, to_pos)
        if isinstance(from_pos, dict) and isinstance(to_pos, dict)
        else None
    )
    matched_count = (
        len(locked_targets)
        if isinstance(locked_targets, list)
        else (len(positions) if isinstance(positions, list) else None)
    )
    budget_fail = check_bridge_command_line_budget(
        "edit_blocks",
        payload,
        budget=budget,
        matched_count=matched_count,
        volume=volume,
    )
    if budget_fail is not None:
        return budget_fail
    authorized_bounds: dict[str, Any] | None = None
    if mode == "fill" and isinstance(from_pos, dict) and isinstance(to_pos, dict):
        authorized_bounds = {"from": from_pos, "to": to_pos}
        vol = _aabb_volume(from_pos, to_pos)
        if vol is not None:
            authorized_bounds["volume"] = vol
    return await call_block_capability(
        deps.addon_bridge,
        "edit_blocks",
        payload,
        mode=mode,
        authorized_bounds=authorized_bounds,
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
    )
