"""Model-facing inspect_block / edit_blocks tool implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic_ai import RunContext

from config.logging import get_logger
from models.agent import AgentDependencies
from services.agent.block_ops.bridge import call_block_capability
from services.agent.block_ops.capability import (
    BlockCapabilityStatus,
    ensure_block_capability,
)
from services.agent.block_ops.config import get_block_tools_limits
from services.agent.block_ops.preflight_cache import get_preflight_cache
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


@dataclass(frozen=True)
class BlockPreflightPlan:
    """Separated preflight artifacts for approval, invocation, and evidence."""

    authorized_args: dict[str, Any]
    execute_args: dict[str, Any]
    approval_metadata: dict[str, Any]


_EDIT_EXECUTE_FIELDS = frozenset({
    "type_id", "mode", "coordinate_mode", "dimension", "position", "positions",
    "from_pos", "to_pos", "states", "replace_any", "expected_previous",
    "locked_targets", "phase",
})
_INSPECT_EXECUTE_FIELDS = frozenset({
    "coordinate_mode", "dimension", "position", "positions", "locked_targets", "phase",
})


def project_block_execute_args(tool_name: str, authorized_args: dict[str, Any]) -> dict[str, Any]:
    """Strictly project bridge-facing authorization into public Python arguments."""
    fields = _EDIT_EXECUTE_FIELDS if tool_name == "edit_blocks" else _INSPECT_EXECUTE_FIELDS
    if tool_name not in BLOCK_TOOL_NAMES:
        raise ValueError(f"unsupported block tool: {tool_name}")
    projected = {key: value for key, value in authorized_args.items() if key in fields}
    if tool_name == "edit_blocks":
        if isinstance(authorized_args.get("from"), dict):
            projected["from_pos"] = authorized_args["from"]
        if isinstance(authorized_args.get("to"), dict):
            projected["to_pos"] = authorized_args["to"]
        required = {"type_id", "mode", "coordinate_mode", "dimension", "phase", "locked_targets"}
    else:
        required = {"coordinate_mode", "dimension", "phase", "locked_targets"}
    missing = [key for key in required if key not in projected]
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


def _connection_id(deps: AgentDependencies) -> str:
    return str(deps.connection_id)


def _unavailable_result(reason: str) -> ToolResult:
    body = build_error_response(
        BlockErrorCode.ADDON_UNAVAILABLE,
        (
            f"{reason}。"
            "可另行调用 run_minecraft_command 作为可选回退；"
            "本工具不会自动生成或执行 setblock/fill 命令。"
        ),
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
        return _unavailable_result("当前 Add-on 不支持专用方块工具（能力握手缺失 block_ops）")
    if record.status == BlockCapabilityStatus.UNAVAILABLE:
        return _unavailable_result("Addon 桥接不可用或超时")
    return _unavailable_result(f"方块能力探测失败: {record.detail or 'unknown'}")


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
    if coordinate_mode == "absolute" and not dimension:
        return _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "absolute 模式必须提供 dimension",
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
    if coordinate_mode == "absolute" and not dimension:
        return _host_limit_error(
            BlockErrorCode.INVALID_ARGUMENT,
            "absolute 模式必须提供 dimension",
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
    if locked_targets is not None:
        payload["locked_targets"] = locked_targets
    apply_limits_to_payload(payload, limits)
    return payload


def merge_canonical_from_preflight(
    original_args: dict[str, Any],
    preflight_payload: dict[str, Any],
) -> dict[str, Any]:
    """Build approval/execution canonical args from preflight response payload."""
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
    ):
        if key in preflight_payload and preflight_payload[key] is not None:
            canonical[key] = preflight_payload[key]

    # Prefer absolute locked targets for subsequent execution.
    locked = preflight_payload.get("locked_targets")
    if isinstance(locked, list) and locked:
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
        elif (original_args.get("mode") or "") == "fill" and len(locked) >= 2:
            first, last = locked[0], locked[-1]
            if isinstance(first, dict) and isinstance(last, dict):
                xs = [int(t["x"]) for t in locked if isinstance(t, dict) and "x" in t]
                ys = [int(t["y"]) for t in locked if isinstance(t, dict) and "y" in t]
                zs = [int(t["z"]) for t in locked if isinstance(t, dict) and "z" in t]
                if xs and ys and zs:
                    canonical["from"] = {"x": min(xs), "y": min(ys), "z": min(zs)}
                    canonical["to"] = {"x": max(xs), "y": max(ys), "z": max(zs)}
                    canonical["coordinate_mode"] = "absolute"
                    if "dimension" in first:
                        canonical["dimension"] = first["dimension"]
        elif "bounds" in preflight_payload and isinstance(preflight_payload["bounds"], dict):
            bounds = preflight_payload["bounds"]
            if "min" in bounds and "max" in bounds:
                canonical["from"] = bounds["min"]
                canonical["to"] = bounds["max"]
                canonical["coordinate_mode"] = "absolute"

    if "bounds" in preflight_payload and isinstance(preflight_payload["bounds"], dict):
        bounds = preflight_payload["bounds"]
        if "min" in bounds and "max" in bounds:
            canonical["from"] = bounds["min"]
            canonical["to"] = bounds["max"]
            if "dimension" in bounds:
                canonical["dimension"] = bounds["dimension"]
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

    # Mark as ready for execute phase.
    canonical["phase"] = "execute"
    return canonical


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
    return BlockPreflightPlan(authorized_args, execute_args, approval_metadata)


async def run_block_preflight(
    ctx: RunContext[AgentDependencies],
    tool_name: str,
    tool_args: dict[str, Any],
) -> tuple[BlockPreflightPlan | dict[str, Any] | None, ToolResult | None]:
    """Run bridge preflight before harness policy.

    Returns (preflight_plan, failure). Absolute inspect may return direct args.
    """
    deps = ctx.deps
    unsupported = await _require_supported(ctx)
    if unsupported is not None:
        return None, unsupported

    limits = get_block_tools_limits(deps.settings)
    limits_payload = {
        "max_discrete_positions": limits.max_discrete_positions,
        "max_fill_volume": limits.max_fill_volume,
        "cells_per_tick": limits.cells_per_tick,
    }

    # If already canonical with locked_targets from a prior approval recovery, skip re-preflight.
    if tool_args.get("phase") == "execute" and tool_args.get("locked_targets"):
        return dict(tool_args), None

    if tool_name == "inspect_block":
        # inspect is low-risk; preflight only needed for relative resolution.
        # Absolute inspect can execute directly without a separate preflight phase.
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
            import json

            body = json.loads(result.output)
        except Exception:
            return dict(tool_args), None
        payload_body = body if body.get("ok") is not False else body
        # If response is success envelope with nested fields
        if isinstance(body, dict) and body.get("ok") is True:
            # payload may be flattened into body for versioned responses
            preflight_fields = {k: v for k, v in body.items() if k not in {"schema_version", "ok"}}
        else:
            preflight_fields = body if isinstance(body, dict) else {}
        return build_block_preflight_plan(tool_name, tool_args, preflight_fields), None

    if tool_name == "edit_blocks":
        mode = str(tool_args.get("mode") or "place")
        coord_mode = str(tool_args.get("coordinate_mode") or "absolute")
        from_pos = tool_args.get("from") or tool_args.get("from_pos")
        to_pos = tool_args.get("to") or tool_args.get("to_pos")
        validation = _validate_edit_args(
            mode=mode,
            coordinate_mode=coord_mode,
            dimension=tool_args.get("dimension"),
            position=tool_args.get("position"),
            positions=tool_args.get("positions"),
            from_pos=from_pos if isinstance(from_pos, dict) else None,
            to_pos=to_pos if isinstance(to_pos, dict) else None,
            type_id=str(tool_args.get("type_id") or ""),
            replace_any=bool(tool_args.get("replace_any", False)),
            expected_previous=tool_args.get("expected_previous"),
            max_positions=limits.max_discrete_positions,
            max_fill_volume=limits.max_fill_volume,
        )
        if validation is not None:
            return None, validation

        payload = build_edit_payload(
            mode=mode,
            coordinate_mode=coord_mode,
            dimension=tool_args.get("dimension"),
            position=tool_args.get("position"),
            positions=tool_args.get("positions"),
            from_pos=from_pos if isinstance(from_pos, dict) else None,
            to_pos=to_pos if isinstance(to_pos, dict) else None,
            type_id=str(tool_args.get("type_id") or ""),
            states=tool_args.get("states"),
            replace_any=bool(tool_args.get("replace_any", False)),
            expected_previous=tool_args.get("expected_previous"),
            player_name=deps.player_name,
            phase="preflight",
            limits=limits_payload,
        )
        result = await call_block_capability(deps.addon_bridge, "edit_blocks", payload)
        if not result.is_success:
            return None, result
        try:
            import json

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
        return build_block_preflight_plan(tool_name, tool_args, preflight_fields), None

    return dict(tool_args), None


async def inspect_block_impl(
    ctx: RunContext[AgentDependencies],
    *,
    coordinate_mode: CoordinateMode = "absolute",
    dimension: str | None = None,
    position: dict[str, Any] | None = None,
    positions: list[dict[str, Any]] | None = None,
    locked_targets: list[dict[str, Any]] | None = None,
    phase: str | None = None,
) -> ToolResult:
    """Query one or more block snapshots via addon bridge."""
    deps = ctx.deps
    logger.info(
        "agent_tool_call",
        tool="inspect_block",
        connection_id=_connection_id(deps),
        run_id=deps.run_id,
        player_name=deps.player_name,
        coordinate_mode=coordinate_mode,
    )

    unsupported = await _require_supported(ctx)
    if unsupported is not None:
        return unsupported

    limits = get_block_tools_limits(deps.settings)
    validation = _validate_inspect_args(
        coordinate_mode=coordinate_mode,
        dimension=dimension,
        position=position,
        positions=positions,
        max_positions=limits.max_discrete_positions,
    )
    if validation is not None:
        return validation

    payload = build_inspect_payload(
        coordinate_mode=coordinate_mode,
        dimension=dimension,
        position=position,
        positions=positions,
        player_name=deps.player_name,
        phase=phase or "execute",
        locked_targets=locked_targets,
        limits={
            "max_discrete_positions": limits.max_discrete_positions,
            "max_fill_volume": limits.max_fill_volume,
            "cells_per_tick": limits.cells_per_tick,
        },
    )
    return await call_block_capability(deps.addon_bridge, "inspect_block", payload)


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
    type_id: str,
    states: dict[str, Any] | None = None,
    replace_any: bool = False,
    expected_previous: dict[str, Any] | None = None,
    locked_targets: list[dict[str, Any]] | None = None,
    phase: str | None = None,
) -> ToolResult:
    """Mutate blocks via place | batch | fill after approval."""
    deps = ctx.deps
    logger.info(
        "agent_tool_call",
        tool="edit_blocks",
        connection_id=_connection_id(deps),
        run_id=deps.run_id,
        player_name=deps.player_name,
        mode=mode,
        type_id=type_id,
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

    # Prefer locked targets from preflight cache if available and not passed.
    if locked_targets is None:
        cache = get_preflight_cache()
        # Best-effort: callers via harness pass locked_targets in args.
        pass

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
        phase=phase or "execute",
        locked_targets=locked_targets,
        limits={
            "max_discrete_positions": limits.max_discrete_positions,
            "max_fill_volume": limits.max_fill_volume,
            "cells_per_tick": limits.cells_per_tick,
        },
    )
    return await call_block_capability(deps.addon_bridge, "edit_blocks", payload)
