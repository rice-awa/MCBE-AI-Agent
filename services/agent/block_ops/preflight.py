"""Preflight orchestration, canonical-args merging, and approval-plan building.

Extracted from ``tools_impl`` to separate preflight orchestration from
tool implementation and payload building.

Handles capability probing, target validation, bridge preflight calls,
canonical-args merging from preflight results, and approval-plan creation
for the harness approval lifecycle.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext

from models.agent import AgentDependencies
from services.agent.block_ops.bridge import call_block_capability
from services.agent.block_ops.capability import (
    BlockCapabilityStatus,
    ensure_block_capability,
)
from services.agent.block_ops.config import get_block_tools_limits
from services.agent.block_ops.message import (
    build_inspect_payload,
    build_inspect_payload_from_target,
)
from services.agent.block_ops.schema import BlockErrorCode, build_state_unknown_response, dumps_payload
from services.agent.block_ops.target import (
    normalize_aabb_corners as _normalize_aabb_corners,
)
from services.agent.block_ops.tools_impl import (
    _connection_id,
    _plain_tool_data,
    _require_supported,
)
from services.agent.block_ops.limits import _request_fill_aabb
from services.agent.block_ops.validation import (
    _locked_targets_aabb,
    _validate_inspect_args,
    _validate_inspect_target,
)
from services.agent.tool_results import ToolResult

logger = __import__("config.logging", fromlist=["get_logger"]).get_logger(__name__)


@dataclass(frozen=True)
class BlockPreflightPlan:
    """Separated preflight artifacts for approval, invocation, and evidence."""

    authorized_args: dict[str, Any]
    execute_args: dict[str, Any]
    approval_metadata: dict[str, Any]
    plan_id: str = ""


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
        # Authorized audit uses bridge protocol field names; Python signature
        # aliases exist only in strict execution projections.
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


def state_unknown_result(
    plan_id: str,
    *,
    reason: str = "missing",
) -> ToolResult:
    """Approval recovery failure: cache missing/expired/tool mismatch → STATE_UNKNOWN."""
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


async def run_block_preflight(
    ctx: RunContext[AgentDependencies],
    tool_name: str,
    tool_args: dict[str, Any],
) -> tuple[BlockPreflightPlan | dict[str, Any] | None, ToolResult | None]:
    """Run bridge preflight before harness policy.

    Returns ``(preflight_plan, failure)``. Absolute inspect may return direct args.
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
        if isinstance(body, dict) and body.get("ok") is True:
            preflight_fields = {
                k: v for k, v in body.items() if k not in {"schema_version", "ok"}
            }
        else:
            preflight_fields = body if isinstance(body, dict) else {}
        return build_block_preflight_plan(tool_name, tool_args, preflight_fields), None

    # place/fill passthrough: canonical args must preserve the model-visible
    # contract. pydantic-ai validates with ``alias="from"`` then passes the
    # field name (``from_``) to the harness. ``wrap_tool_validate`` and
    # ``execute_block_plan`` both check/access by model-visible key (``from``),
    # so we convert back to the alias key here to avoid approval recovery
    # canonical-args validation failures.
    if tool_name == "fill_block" and "from_" in tool_args and "from" not in tool_args:
        from_pos = tool_args.pop("from_")
        tool_args["from"] = from_pos
    return dict(tool_args), None
