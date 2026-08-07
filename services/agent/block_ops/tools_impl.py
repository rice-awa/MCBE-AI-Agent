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
from services.agent.block_ops.validation import (
    _expect_to_legacy,
    _locked_targets_aabb,
    _normalize_position_array,
    _normalize_single_op_block,
    _validate_inspect_target,
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


# --- Remaining functions (tool impls, execute_block_plan) ---
# (Validation, normalization, expect mapping moved to validation.py.)
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
    from services.agent.block_ops.preflight import state_unknown_result
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
            return state_unknown_result(pid)
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
            return state_unknown_result(pid, reason="unsupported-tool")

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
