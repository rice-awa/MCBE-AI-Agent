"""Model-facing inspect_block / edit_blocks tool implementations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

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

# Stable request_id length matching production ``addon-{uuid4().hex}`` (38 chars).
_BRIDGE_REQUEST_ID_PLACEHOLDER = f"addon-{'0' * 32}"
_BRIDGE_REQ_PREFIX = "scriptevent mcbews:bridge_req "
_COMMAND_LINE_BUDGET_HINT = (
    "减小 batch.positions 数量或 fill AABB；继续使用 batch/fill，"
    "禁止拆成大量 place（禁止 place 风暴）；勿用命令绕过审批。"
)
_COMMAND_LINE_BUDGET_MESSAGE = "出站帧超出 MCBE commandLine 字节预算，请求未发送。"


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
        return _unavailable_result("当前 Add-on 不支持专用方块工具（能力握手缺失 block_ops）")
    if record.status == BlockCapabilityStatus.UNAVAILABLE:
        return _unavailable_result("Addon 桥接不可用或超时")
    logger.warning(
        "block_capability_probe_failed",
        connection_id=_connection_id(deps),
        run_id=deps.run_id,
        player_name=deps.player_name,
        diagnostic_summary=redact_exception(record.detail),
    )
    return _unavailable_result("方块能力探测失败")


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
) -> bool:
    """True when absolute execute geometry freezes targets without replaying cells.

    MCBE ``commandLine`` hard budget is ~461 bytes. Even sparse fill locked lists
    blow that budget and never leave the host (bridge timeout → STATE_UNKNOWN).
    On absolute execute, place/batch/fill geometry (position / positions /
    from+to) is authoritative; omit ``locked_targets`` on the wire. Audit and
    approval records may still hold the full list.
    """
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

    omit_locked = should_omit_locked_targets_on_wire(
        mode=mode,
        phase=phase,
        from_pos=from_pos if isinstance(from_pos, dict) else None,
        to_pos=to_pos if isinstance(to_pos, dict) else None,
        position=position if isinstance(position, dict) else None,
        positions=positions if isinstance(positions, list) else None,
        locked_targets=locked_targets if isinstance(locked_targets, list) else None,
        coordinate_mode=coordinate_mode,
    )
    if locked_targets is not None and not omit_locked:
        payload["locked_targets"] = compact_locked_targets_for_wire(
            locked_targets,
            dimension=dimension,
        )
    # Limits only affect preflight enumeration / host checks. Omitting them on
    # execute saves critical commandLine bytes under the 461 B MCBE budget.
    if phase != "execute":
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
        plan = build_block_preflight_plan(tool_name, tool_args, preflight_fields)

        # Reject before approval when the post-omit execute frame would still overflow.
        exec_args = plan.execute_args
        exec_from = exec_args.get("from_pos") or exec_args.get("from")
        exec_to = exec_args.get("to_pos") or exec_args.get("to")
        exec_payload = build_edit_payload(
            mode=str(exec_args.get("mode") or mode),
            coordinate_mode=str(exec_args.get("coordinate_mode") or coord_mode),
            dimension=exec_args.get("dimension"),
            position=exec_args.get("position") if isinstance(exec_args.get("position"), dict) else None,
            positions=exec_args.get("positions") if isinstance(exec_args.get("positions"), list) else None,
            from_pos=exec_from if isinstance(exec_from, dict) else None,
            to_pos=exec_to if isinstance(exec_to, dict) else None,
            type_id=str(exec_args.get("type_id") or tool_args.get("type_id") or ""),
            states=exec_args.get("states"),
            replace_any=bool(exec_args.get("replace_any", False)),
            expected_previous=exec_args.get("expected_previous"),
            player_name=deps.player_name,
            phase="execute",
            locked_targets=(
                exec_args.get("locked_targets")
                if isinstance(exec_args.get("locked_targets"), list)
                else None
            ),
        )
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
        },
    )
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
