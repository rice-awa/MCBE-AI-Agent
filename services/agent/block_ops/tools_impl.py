"""Model-facing inspect_block / place_block / fill_block tool implementations."""
from __future__ import annotations

import json
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
    get_block_tools_limits,
    get_command_line_byte_budget,
)
from services.agent.block_ops.limits import (
    _aabb_volume,
    _request_fill_aabb,
    _BRIDGE_REQUEST_ID_PLACEHOLDER,
    _COMMAND_LINE_BUDGET_HINT,
    _COMMAND_LINE_BUDGET_MESSAGE,
    apply_limits_to_payload,
    check_bridge_command_line_budget,
    command_line_budget_exceeded_result,
    compact_locked_targets_for_wire,
    estimate_bridge_command_line_bytes,
    locked_targets_wire_limit_exceeded,
    normalize_limits_input,
    should_omit_locked_targets_on_wire,
)
from services.agent.block_ops.message import (
    build_edit_payload,
    build_inspect_payload,
    build_inspect_payload_from_target,
)
from services.agent.block_ops.schema import (
    BlockErrorCode,
    _host_limit_error,
    build_error_response,
    dumps_payload,
)
from services.agent.block_ops.target import (
    normalize_aabb_corners as _normalize_aabb_corners,
)
from services.agent.tool_results import ToolResult

logger = get_logger(__name__)

BLOCK_TOOL_NAMES = frozenset({"inspect_block", "place_block", "fill_block"})

# Bridge constants shared with preflight and limits modules.
_BRIDGE_REQ_PREFIX = "scriptevent mcbews:bridge_req "


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


# --- Remaining functions (validation helpers, tool impls, execute_block_plan) ---
# (Limits, payload-building, and preflight orchestration moved to
#  limits.py / message.py / preflight.py respectively.)


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
    from services.agent.block_ops.limits import _aabb_volume as ___aabb_volume
    volume = ___aabb_volume(from_pos, to_pos)
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
