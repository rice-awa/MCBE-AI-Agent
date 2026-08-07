"""Unified target normalization for block tools (issue 02).

The model-facing ``inspect_block`` accepts a single ``target`` argument that is
either ``{positions: [...]}`` or ``{box: {from, to}}``. This module normalizes
that unified target into the legacy shape the Add-on still expects
(``coordinate_mode`` / ``position`` / ``positions`` / ``from`` / ``to``),
while enforcing:

- ``positions`` and ``box`` are mutually exclusive;
- a single point uses a length-1 ``positions`` list (no separate ``position``);
- world coordinates (``{x,y,z}``) and player-relative coordinates
  (``{forward,right,up}``) cannot be mixed within the same target;
- box corner order is normalized (min/max) before reaching the Add-on.

Coordinate resolution (player anchor, floor, dimension repair) remains the
Add-on's responsibility; this module only validates shape and classifies the
coordinate mode so the host can reject mixed targets before the bridge call.
"""

from __future__ import annotations

from typing import Any

from services.agent.block_ops.schema import BlockErrorCode, _host_limit_error

_ABSOLUTE_KEYS = frozenset({"x", "y", "z"})
_RELATIVE_KEYS = frozenset({"forward", "right", "up"})


def normalize_aabb_corners(
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


def _is_absolute_coord(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and _ABSOLUTE_KEYS.issubset(value.keys())
        and all(isinstance(value[k], (int, float)) for k in _ABSOLUTE_KEYS)
    )


def _is_relative_coord(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and _RELATIVE_KEYS.issubset(value.keys())
        and all(isinstance(value[k], (int, float)) for k in _RELATIVE_KEYS)
    )


def _coord_kind(value: Any) -> str | None:
    """Return 'absolute', 'player_relative', or None if neither."""
    if _is_absolute_coord(value):
        return "absolute"
    if _is_relative_coord(value):
        return "player_relative"
    return None


class NormalizedTarget:
    """Result of normalizing a unified target into Add-on-compatible fields."""

    __slots__ = ("shape", "coordinate_mode", "positions", "box_from", "box_to")

    def __init__(
        self,
        *,
        shape: str,
        coordinate_mode: str,
        positions: list[dict[str, Any]] | None = None,
        box_from: dict[str, Any] | None = None,
        box_to: dict[str, Any] | None = None,
    ) -> None:
        self.shape = shape  # "positions" | "box"
        self.coordinate_mode = coordinate_mode  # "absolute" | "player_relative"
        self.positions = positions
        self.box_from = box_from
        self.box_to = box_to


def normalize_inspect_target(
    target: Any,
) -> tuple[NormalizedTarget | None, Any]:
    """Normalize a model-facing ``target`` into Add-on-compatible fields.

    Returns ``(normalized, error_result)``. Exactly one is non-None.
    The ``error_result`` is a ``ToolResult`` (or None on success).
    """
    if not isinstance(target, dict):
        return None, _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "target 必须是对象，提供 positions 或 box",
        )

    has_positions = "positions" in target and target["positions"] is not None
    has_box = "box" in target and target["box"] is not None
    if not has_positions and not has_box:
        return None, _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "target 必须提供 positions 或 box",
        )
    if has_positions and has_box:
        return None, _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "target.positions 与 target.box 互斥，只能提供一个",
        )

    if has_positions:
        positions = target["positions"]
        if not isinstance(positions, list) or not positions:
            return None, _host_limit_error(
                BlockErrorCode.INVALID_ARGUMENT,
                "target.positions 必须是非空列表",
            )
        # Determine coordinate mode from the first element; all must match.
        first_kind = _coord_kind(positions[0])
        if first_kind is None:
            return None, _host_limit_error(
                BlockErrorCode.INVALID_COORDINATE,
                "target.positions[0] 必须是 {x,y,z} 或 {forward,right,up}",
            )
        for i, p in enumerate(positions):
            kind = _coord_kind(p)
            if kind is None:
                return None, _host_limit_error(
                    BlockErrorCode.INVALID_COORDINATE,
                    f"target.positions[{i}] 必须是 {first_kind} 坐标",
                )
            if kind != first_kind:
                return None, _host_limit_error(
                    BlockErrorCode.INVALID_COORDINATE,
                    "同一 target 内不能混用绝对坐标和玩家相对坐标",
                )
        return (
            NormalizedTarget(
                shape="positions",
                coordinate_mode=first_kind,
                positions=positions,
            ),
            None,
        )

    # Box shape.
    box = target["box"]
    if not isinstance(box, dict) or "from" not in box or "to" not in box:
        return None, _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "target.box 必须提供 from 和 to",
        )
    from_pos = box["from"]
    to_pos = box["to"]
    from_kind = _coord_kind(from_pos)
    to_kind = _coord_kind(to_pos)
    if from_kind is None:
        return None, _host_limit_error(
            BlockErrorCode.INVALID_COORDINATE,
            "target.box.from 必须是 {x,y,z} 或 {forward,right,up}",
        )
    if to_kind is None:
        return None, _host_limit_error(
            BlockErrorCode.INVALID_COORDINATE,
            "target.box.to 必须是 {x,y,z} 或 {forward,right,up}",
        )
    if from_kind != to_kind:
        return None, _host_limit_error(
            BlockErrorCode.INVALID_COORDINATE,
            "同一 target 内不能混用绝对坐标和玩家相对坐标",
        )
    return (
        NormalizedTarget(
            shape="box",
            coordinate_mode=from_kind,
            box_from=from_pos,
            box_to=to_pos,
        ),
        None,
    )


def normalize_array_target(
    target: Any,
) -> tuple[NormalizedTarget | None, Any]:
    """Normalize an array-shaped target into a unified NormalizedTarget.

    Accepts the model-facing shorthand:
      - single point ``[x, y, z]`` → positions shape (one absolute cell);
      - two corners ``[[x1, y1, z1], [x2, y2, z2]]`` → box shape with
        min/max-normalized corners.

    Non-int / wrong-length coordinates yield a structured INVALID_COORDINATE
    result (never a Python exception). Array targets are always absolute
    world coordinates.
    """
    if not isinstance(target, list):
        return None, _host_limit_error(
            BlockErrorCode.INVALID_COORDINATE,
            "target 必须是 [x, y, z] 或 [[x1, y1, z1], [x2, y2, z2]]",
        )

    def _cell(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, list) or len(value) != 3:
            return None
        for item in value:
            if isinstance(item, bool) or not isinstance(item, int):
                return None
        return {"x": value[0], "y": value[1], "z": value[2]}

    # Point form: [x, y, z].
    if len(target) == 3 and not any(isinstance(v, list) for v in target):
        cell = _cell(target)
        if cell is None:
            return None, _host_limit_error(
                BlockErrorCode.INVALID_COORDINATE,
                "target 必须是 3 个整数坐标 [x, y, z]",
            )
        return (
            NormalizedTarget(
                shape="positions",
                coordinate_mode="absolute",
                positions=[cell],
            ),
            None,
        )

    # Box form: [[x1, y1, z1], [x2, y2, z2]].
    if len(target) == 2 and all(isinstance(v, list) for v in target):
        from_cell = _cell(target[0])
        to_cell = _cell(target[1])
        if from_cell is None or to_cell is None:
            return None, _host_limit_error(
                BlockErrorCode.INVALID_COORDINATE,
                "target 两角点必须是恰好 3 个整数的坐标数组",
            )
        from_pos, to_pos = normalize_aabb_corners(from_cell, to_cell)
        return (
            NormalizedTarget(
                shape="box",
                coordinate_mode="absolute",
                box_from=from_pos,
                box_to=to_pos,
            ),
            None,
        )

    return None, _host_limit_error(
        BlockErrorCode.INVALID_COORDINATE,
        "target 必须是 [x, y, z] 或 [[x1, y1, z1], [x2, y2, z2]]",
    )


# build_inspect_payload_from_target has been moved to message.py
# to eliminate the reverse import of tools_impl.apply_limits_to_payload.
