"""Parameter validation and normalization helpers for block_ops tools.

Extracted from ``tools_impl`` to provide a focused module for all
``_normalize_*``, ``_validate_*`` helpers used across tool implementations
and preflight orchestration.
"""
from __future__ import annotations

from typing import Any

from services.agent.block_ops.schema import (
    BlockErrorCode,
    _host_limit_error,
)
from services.agent.block_ops.target import (
    normalize_aabb_corners as _normalize_aabb_corners,
)
from services.agent.tool_results import ToolResult


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
