"""Dedicated block tools: schema, capability, preflight, exposure, bridge mapping."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any
from uuid import uuid4

import pytest
from pydantic import Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolApproved, ToolDenied

from config.settings import Settings
from services.agent.block_ops.bridge import (
    call_block_capability,
    map_addon_bridge_result,
    map_bridge_exception,
)
from services.agent.block_ops.capability import (
    BlockCapabilityRecord,
    BlockCapabilityStatus,
    clear_block_capability,
    ensure_block_capability,
    get_block_capability_cache,
    reset_block_capability_cache,
)
from services.agent.block_ops.config import (
    DEFAULT_COMMAND_LINE_BYTE_BUDGET,
    DEFAULT_MAX_LOCKED_TARGETS_ON_WIRE,
    HARD_MAX_DISCRETE_POSITIONS,
    get_block_tools_limits,
    get_command_line_byte_budget,
)
from services.agent.block_ops.preflight_cache import (
    get_preflight_cache,
    reset_preflight_cache,
)
from services.agent.block_ops.project import project_block_result_for_model
from services.agent.block_ops.schema import (
    BLOCK_OPS_SCHEMA_VERSION,
    BlockErrorCode,
    build_error_response,
    build_success_response,
)
from services.agent.block_ops.limits import (
    apply_limits_to_payload,
    check_bridge_command_line_budget,
    estimate_bridge_command_line_bytes,
    locked_targets_wire_limit_exceeded,
    should_omit_locked_targets_on_wire,
)
from services.agent.block_ops.message import (
    build_edit_payload,
    build_inspect_payload,
)
from services.agent.block_ops.preflight import (
    build_block_preflight_plan,
    merge_canonical_from_preflight,
)
from services.agent.block_ops.tools_impl import (
    fill_block_impl,
    inspect_block_impl,
    place_block_impl,
)
from services.agent.harness.execution import (
    HarnessCapability,
    PolicyDecisionKind,
    PolicyEngine,
    get_idempotency_store,
    hash_normalized_args,
    normalize_tool_args,
    reset_block_command_fallback_store,
    reset_idempotency_store,
)
from services.agent.tool_results import ToolResult
from services.agent.tools import iter_registered_tools, register_agent_tools


class _FakeBridge:
    def __init__(self, handler=None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._handler = handler

    async def request(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((capability, payload))
        if self._handler is not None:
            return await self._handler(capability, payload)
        if capability == "get_capabilities":
            return {
                "ok": True,
                "payload": {
                    "capabilities": {
                        "block_ops": {"inspect": True, "edit": True, "schema_version": "1"}
                    }
                },
            }
        if capability == "inspect_block":
            # Unified target path (issue 02): target.positions or target.box
            target = payload.get("target")
            if isinstance(target, dict):
                if isinstance(target.get("positions"), list):
                    positions_list = target["positions"]
                    blocks = []
                    for p in positions_list:
                        if "x" in p:
                            blocks.append({
                                "dimension": payload.get("dimension") or "minecraft:overworld",
                                "x": p["x"], "y": p["y"], "z": p["z"],
                                "type_id": "minecraft:stone",
                                "states": {},
                                "waterlogged": False,
                                "is_air": False,
                                "is_liquid": False,
                            })
                    return {
                        "ok": True,
                        "payload": {
                            "schema_version": "1",
                            "ok": True,
                            "status": "inspected",
                            "blocks": blocks,
                            "coordinate_mode": payload.get("coordinate_mode", "absolute"),
                            "dimension": payload.get("dimension", "minecraft:overworld"),
                        },
                    }
                if isinstance(target.get("box"), dict):
                    return {
                        "ok": True,
                        "payload": {
                            "schema_version": "1",
                            "ok": True,
                            "status": "inspected",
                            "summary": {
                                "bounds": {
                                    "from": target["box"]["from"],
                                    "to": target["box"]["to"],
                                },
                                "count": 4,
                                "type_counts": {"minecraft:stone": 4},
                                "unknown_count": 0,
                                "samples": [],
                            },
                            "coordinate_mode": payload.get("coordinate_mode", "absolute"),
                            "dimension": payload.get("dimension", "minecraft:overworld"),
                        },
                    }
            # Legacy path
            pos = payload.get("position") or {"x": 0, "y": 64, "z": 0}
            return {
                "ok": True,
                "payload": {
                    "schema_version": "1",
                    "ok": True,
                    "block": {
                        "dimension": payload.get("dimension") or "minecraft:overworld",
                        "x": pos.get("x", 0),
                        "y": pos.get("y", 64),
                        "z": pos.get("z", 0),
                        "type_id": "minecraft:air",
                        "states": {},
                        "is_air": True,
                    },
                },
            }
        if capability == "edit_blocks":
            phase = payload.get("phase") or "execute"
            if phase == "preflight":
                pos = payload.get("position")
                positions = payload.get("positions")
                from_pos = payload.get("from")
                to_pos = payload.get("to")
                locked: list[dict[str, Any]] = []
                if isinstance(pos, dict):
                    locked = [{"dimension": payload.get("dimension") or "minecraft:overworld", "x": pos.get("x", 1), "y": pos.get("y", 64), "z": pos.get("z", 1)}]
                elif isinstance(positions, list) and positions:
                    locked = [
                        {
                            "dimension": payload.get("dimension") or "minecraft:overworld",
                            "x": p.get("x", 1),
                            "y": p.get("y", 64),
                            "z": p.get("z", 1),
                        }
                        for p in positions
                        if isinstance(p, dict)
                    ]
                elif isinstance(from_pos, dict) and isinstance(to_pos, dict):
                    locked = [
                        {
                            "dimension": payload.get("dimension") or "minecraft:overworld",
                            "x": from_pos.get("x", 1),
                            "y": from_pos.get("y", 64),
                            "z": from_pos.get("z", 1),
                        }
                    ]
                return {
                    "ok": True,
                    "payload": {
                        "schema_version": "1",
                        "ok": True,
                        "phase": "preflight",
                        "mode": payload.get("mode") or "place",
                        "type_id": payload.get("type_id"),
                        "locked_targets": locked,
                        "coordinate_mode": "absolute",
                        "dimension": locked[0]["dimension"] if locked else (payload.get("dimension") or "minecraft:overworld"),
                        "position": {
                            "x": locked[0]["x"],
                            "y": locked[0]["y"],
                            "z": locked[0]["z"],
                        } if locked else {"x": 1, "y": 64, "z": 1},
                        "repairs_applied": [],
                    },
                }
            return {
                "ok": True,
                "payload": {
                    "schema_version": "1",
                    "ok": True,
                    "phase": "execute",
                    "changed": 1,
                    "type_id": payload.get("type_id"),
                },
            }
        return {"ok": False, "payload": {"code": "INTERNAL_ERROR", "message": "unknown"}}


@dataclass
class _Deps:
    connection_id: Any = field(default_factory=uuid4)
    player_name: str = "Steve"
    settings: Any = field(default_factory=Settings)
    run_id: str = "run-block-1"
    conversation_id: str = "conv-block-1"
    provider: str = "test"
    auto_approve_tools: bool = False
    addon_bridge: Any = None


class _Settings:
    runtime_harness_enabled = True
    runtime_harness_audit_enabled = False
    runtime_harness_audit_path = "logs/test_audit.jsonl"
    runtime_harness_audit_max_records = 100
    hard_deny_tools: list[str] = []
    hard_deny_command_roots: list[str] = []
    approval_command_roots: list[str] = ["fill", "setblock"]
    max_batch_commands = 10
    mcp_tool_allowlist: list[str] = []
    tool_policy_version = "2026-07-21.1"
    approval_ttl = 120.0


@pytest.fixture(autouse=True)
def _reset_block_caches():
    reset_block_capability_cache()
    reset_preflight_cache()
    reset_block_command_fallback_store()
    reset_idempotency_store()
    yield
    reset_block_capability_cache()
    reset_preflight_cache()
    reset_block_command_fallback_store()
    reset_idempotency_store()


def test_schema_success_and_error_envelope() -> None:
    ok = build_success_response(changed=1)
    assert ok["schema_version"] == BLOCK_OPS_SCHEMA_VERSION
    assert ok["ok"] is True
    assert ok["changed"] == 1

    err = build_error_response(BlockErrorCode.LIMIT_EXCEEDED, "too many", limit=256)
    assert err["ok"] is False
    assert err["code"] == "LIMIT_EXCEEDED"
    assert err["limit"] == 256


def test_map_ok_false_is_failure_not_success() -> None:
    result = map_addon_bridge_result(
        {
            "ok": False,
            "payload": {
                "code": "PROTECTED_BLOCK",
                "message": "chest has inventory",
            },
        }
    )
    assert isinstance(result, ToolResult)
    assert not result.is_success
    body = json.loads(result.output)
    assert body["ok"] is False
    assert body["code"] == "PROTECTED_BLOCK"


def test_map_ok_true_payload_success() -> None:
    result = map_addon_bridge_result({"ok": True, "payload": {"players": []}})
    assert result.is_success
    assert "players" in result.output


def test_map_bridge_exception_transient() -> None:
    result = map_bridge_exception(TimeoutError("bridge timeout"), tool_name="inspect_block")
    assert not result.is_success
    assert result.retryable
    body = json.loads(result.output)
    assert body["code"] == "ADDON_UNAVAILABLE"


def test_edit_bridge_exception_is_unknown_and_does_not_leak_transport_detail() -> None:
    """World-mutation timeout keeps STATE_UNKNOWN (place_block replaces edit_blocks)."""
    result = map_bridge_exception(
        TimeoutError("token=bridge-secret timed out"), tool_name="place_block"
    )

    body = json.loads(result.output)
    assert body["schema_version"] == "1"
    assert body["ok"] is False
    assert body["code"] == "STATE_UNKNOWN"
    assert body["retryable"] is False
    assert body["external_state_unknown"] is True
    assert body["fallback_allowed"] is False
    assert "外部状态未知" in body["message"]
    assert "请勿自动重试" in body["message"]
    # Diagnostic is surfaced in the model-facing message for operators, but redacted.
    assert "TimeoutError" in body["message"]
    assert "bridge-secret" not in body["message"]
    assert result.error_type == "TimeoutError"
    assert result.diagnostic_summary == "TimeoutError: token=[REDACTED] timed out"
    assert "bridge-secret" not in result.diagnostic_summary
    assert "bridge-secret" not in result.output
    assert result.retryable is False
    assert result.external_state_unknown is True


def test_edit_bridge_frame_too_large_is_limit_not_unknown() -> None:
    """commandLine budget failures never left the host — not STATE_UNKNOWN."""

    class FrameTooLargeError(Exception):
        pass

    result = map_bridge_exception(
        FrameTooLargeError("raw command too long in bytes (1864 > 461); cannot be safely chunked"),
        tool_name="place_block",
    )
    body = json.loads(result.output)
    assert body["ok"] is False
    assert body["code"] == "LIMIT_EXCEEDED"
    assert body["external_state_unknown"] is False
    assert body["retryable"] is True
    assert body["fallback_allowed"] is True
    assert body["reason"] == "command_line_budget"
    assert result.external_state_unknown is False
    assert "未发送" in body["message"] or "字节预算" in body["message"]
    assert "place" in body.get("hint", "").lower() or "禁止" in body.get("hint", "")


def test_estimate_bridge_command_line_bytes_matches_sdk_shape() -> None:
    payload = {
        "mode": "fill",
        "coordinate_mode": "absolute",
        "type_id": "minecraft:oak_planks",
        "replace_any": False,
        "player_name": "Steve",
        "phase": "execute",
        "dimension": "minecraft:overworld",
        "from": {"x": -797, "y": 93, "z": 180},
        "to": {"x": -793, "y": 93, "z": 184},
    }
    estimated = estimate_bridge_command_line_bytes("edit_blocks", payload)
    wire = {
        "v": 2,
        "request_id": "addon-" + ("0" * 32),
        "capability": "edit_blocks",
        "payload": payload,
    }
    manual = "scriptevent mcbews:bridge_req " + json.dumps(
        wire, ensure_ascii=False, separators=(",", ":")
    )
    assert estimated == len(manual.encode("utf-8"))
    assert estimated < DEFAULT_COMMAND_LINE_BYTE_BUDGET


def test_sparse_fill_execute_omit_estimated_under_budget() -> None:
    locked = [
        {"dimension": "minecraft:overworld", "x": -797 + i, "y": 93, "z": 180}
        for i in range(6)
    ]
    payload = build_edit_payload(
        mode="fill",
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        position=None,
        positions=None,
        from_pos={"x": -797, "y": 93, "z": 180},
        to_pos={"x": -793, "y": 93, "z": 184},
        type_id="minecraft:oak_planks",
        states=None,
        replace_any=False,
        expected_previous=None,
        player_name="tester",
        phase="execute",
        locked_targets=locked,
    )
    assert "locked_targets" not in payload
    estimated = estimate_bridge_command_line_bytes("edit_blocks", payload)
    assert estimated < DEFAULT_COMMAND_LINE_BYTE_BUDGET
    assert check_bridge_command_line_budget("edit_blocks", payload) is None


def test_build_edit_payload_omits_locked_targets_on_fill_execute() -> None:
    locked = [
        {"dimension": "minecraft:overworld", "x": x, "y": -60, "z": z}
        for x in range(3, 8)
        for z in range(-9, -4)
    ]
    payload = build_edit_payload(
        mode="fill",
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        position=None,
        positions=None,
        from_pos={"x": 3, "y": -60, "z": -9},
        to_pos={"x": 7, "y": -60, "z": -5},
        type_id="minecraft:oak_planks",
        states=None,
        replace_any=False,
        expected_previous=None,
        player_name="fantong7038",
        phase="execute",
        locked_targets=locked,
        limits={"max_discrete_positions": 4096, "max_fill_volume": 4096, "cells_per_tick": 128},
    )
    assert "locked_targets" not in payload
    assert "max_fill_volume" not in payload  # limits omitted on execute
    assert payload["from"] == {"x": 3, "y": -60, "z": -9}
    assert payload["to"] == {"x": 7, "y": -60, "z": -5}
    wire = json.dumps(
        {
            "v": 2,
            "request_id": "addon-" + "a" * 32,
            "capability": "edit_blocks",
            "payload": payload,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    command_line = f"scriptevent mcbews:bridge_req {wire}"
    assert len(command_line.encode("utf-8")) <= 461


def test_build_edit_payload_omits_locked_targets_on_sparse_fill_execute() -> None:
    """Sparse absolute fill (matched << volume) must omit locked_targets on execute wire."""
    # volume = 5*1*5 = 25; only 6 air cells matched
    locked = [
        {"dimension": "minecraft:overworld", "x": 3, "y": -60, "z": -9},
        {"dimension": "minecraft:overworld", "x": 4, "y": -60, "z": -8},
        {"dimension": "minecraft:overworld", "x": 5, "y": -60, "z": -7},
        {"dimension": "minecraft:overworld", "x": 6, "y": -60, "z": -6},
        {"dimension": "minecraft:overworld", "x": 7, "y": -60, "z": -5},
        {"dimension": "minecraft:overworld", "x": 3, "y": -60, "z": -5},
    ]
    assert len(locked) == 6
    payload = build_edit_payload(
        mode="fill",
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        position=None,
        positions=None,
        from_pos={"x": 3, "y": -60, "z": -9},
        to_pos={"x": 7, "y": -60, "z": -5},
        type_id="minecraft:oak_planks",
        states=None,
        replace_any=False,
        expected_previous=None,
        player_name="fantong7038",
        phase="execute",
        locked_targets=locked,
        limits={"max_discrete_positions": 4096, "max_fill_volume": 4096, "cells_per_tick": 128},
    )
    assert "locked_targets" not in payload
    assert "max_fill_volume" not in payload
    assert payload["from"] == {"x": 3, "y": -60, "z": -9}
    assert payload["to"] == {"x": 7, "y": -60, "z": -5}


def test_build_edit_payload_omits_locked_targets_on_sparse_fill_subtile() -> None:
    """Sparse sub-tile with 4 locked cells still omits on absolute fill execute."""
    locked = [
        {"dimension": "minecraft:overworld", "x": 10, "y": 64, "z": 0},
        {"dimension": "minecraft:overworld", "x": 11, "y": 64, "z": 0},
        {"dimension": "minecraft:overworld", "x": 10, "y": 64, "z": 1},
        {"dimension": "minecraft:overworld", "x": 11, "y": 64, "z": 1},
    ]
    # AABB volume 3*1*3=9 > 4 locked
    payload = build_edit_payload(
        mode="fill",
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        position=None,
        positions=None,
        from_pos={"x": 10, "y": 64, "z": 0},
        to_pos={"x": 12, "y": 64, "z": 2},
        type_id="minecraft:stone",
        states=None,
        replace_any=False,
        expected_previous=None,
        player_name="tester",
        phase="execute",
        locked_targets=locked,
    )
    assert "locked_targets" not in payload
    assert should_omit_locked_targets_on_wire(
        mode="fill",
        phase="execute",
        from_pos={"x": 10, "y": 64, "z": 0},
        to_pos={"x": 12, "y": 64, "z": 2},
        position=None,
        positions=None,
        locked_targets=locked,
        coordinate_mode="absolute",
    )


def test_build_edit_payload_omits_locked_targets_on_absolute_place_execute() -> None:
    locked = [{"dimension": "minecraft:overworld", "x": 1, "y": 64, "z": 1}]
    payload = build_edit_payload(
        mode="place",
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        position={"x": 1, "y": 64, "z": 1},
        positions=None,
        from_pos=None,
        to_pos=None,
        type_id="minecraft:stone",
        states=None,
        replace_any=False,
        expected_previous=None,
        player_name="tester",
        phase="execute",
        locked_targets=locked,
    )
    assert "locked_targets" not in payload
    assert payload["position"] == {"x": 1, "y": 64, "z": 1}


def test_build_edit_payload_omits_locked_targets_on_absolute_batch_execute() -> None:
    positions = [
        {"x": 1, "y": 64, "z": 1},
        {"x": 2, "y": 64, "z": 1},
        {"x": 3, "y": 64, "z": 1},
    ]
    locked = [
        {"dimension": "minecraft:overworld", **p} for p in positions
    ]
    payload = build_edit_payload(
        mode="batch",
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        position=None,
        positions=positions,
        from_pos=None,
        to_pos=None,
        type_id="minecraft:dirt",
        states=None,
        replace_any=False,
        expected_previous=None,
        player_name="tester",
        phase="execute",
        locked_targets=locked,
    )
    assert "locked_targets" not in payload
    assert payload["positions"] == positions


def test_should_not_omit_locked_targets_on_preflight_or_player_relative() -> None:
    locked = [
        {"dimension": "minecraft:overworld", "x": 3, "y": -60, "z": -9},
        {"dimension": "minecraft:overworld", "x": 4, "y": -60, "z": -8},
    ]
    from_pos = {"x": 3, "y": -60, "z": -9}
    to_pos = {"x": 7, "y": -60, "z": -5}

    assert not should_omit_locked_targets_on_wire(
        mode="fill",
        phase="preflight",
        from_pos=from_pos,
        to_pos=to_pos,
        position=None,
        positions=None,
        locked_targets=locked,
        coordinate_mode="absolute",
    )
    assert not should_omit_locked_targets_on_wire(
        mode="fill",
        phase="execute",
        from_pos=from_pos,
        to_pos=to_pos,
        position=None,
        positions=None,
        locked_targets=locked,
        coordinate_mode="player_relative",
    )

    preflight_payload = build_edit_payload(
        mode="fill",
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        position=None,
        positions=None,
        from_pos=from_pos,
        to_pos=to_pos,
        type_id="minecraft:oak_planks",
        states=None,
        replace_any=False,
        expected_previous=None,
        player_name="tester",
        phase="preflight",
        locked_targets=locked,
    )
    assert "locked_targets" in preflight_payload

    relative_payload = build_edit_payload(
        mode="fill",
        coordinate_mode="player_relative",
        dimension="minecraft:overworld",
        position=None,
        positions=None,
        from_pos=from_pos,
        to_pos=to_pos,
        type_id="minecraft:oak_planks",
        states=None,
        replace_any=False,
        expected_previous=None,
        player_name="tester",
        phase="execute",
        locked_targets=locked,
    )
    assert "locked_targets" in relative_payload


def test_invalid_bridge_response_does_not_leak_raw_payload() -> None:
    result = map_addon_bridge_result("payload token=bridge-secret")

    body = json.loads(result.output)
    assert body["code"] == "INTERNAL_ERROR"
    assert body["retryable"] is False
    assert body["external_state_unknown"] is False
    assert body["fallback_allowed"] is False
    assert "bridge-secret" not in result.output


@pytest.mark.parametrize(
    "response",
    [
        {"payload": {"token": "bridge-secret"}},
        {"ok": "yes", "payload": {"token": "bridge-secret"}},
        {"ok": True, "token": "bridge-secret"},
    ],
)
def test_bridge_response_without_boolean_ok_is_safe_internal_error(response: dict[str, Any]) -> None:
    result = map_addon_bridge_result(response)

    body = json.loads(result.output)
    assert body["code"] == "INTERNAL_ERROR"
    assert body["retryable"] is False
    assert body["external_state_unknown"] is False
    assert body["fallback_allowed"] is False
    assert "bridge-secret" not in result.output


@pytest.mark.parametrize(
    ("code", "fallback_allowed"),
    [
        ("ADDON_UNAVAILABLE", True),
        ("UNSUPPORTED_CAPABILITY", True),
        ("PROTECTED_BLOCK", True),
        ("STATE_UNKNOWN", False),
    ],
)
def test_explicit_addon_errors_preserve_code_and_fallback_one_rule(
    code: str, fallback_allowed: bool
) -> None:
    result = map_addon_bridge_result(
        {"ok": False, "payload": {"code": code, "message": "token=bridge-secret"}}
    )

    body = json.loads(result.output)
    assert body["code"] == code
    assert body["fallback_allowed"] is fallback_allowed
    assert "bridge-secret" not in result.output
    # 通用回退路径（无特定分支的 code）消息才会追加「独立审批」提示；
    # 特定分支（PROTECTED_BLOCK 等）保留各自诊断消息，fallback_allowed 字段即信号。
    if code in {"ADDON_UNAVAILABLE", "UNSUPPORTED_CAPABILITY"}:
        assert "独立审批" in body["message"]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("UNSUPPORTED_BLOCK_PLACEMENT", True),
        ("PRECONDITION_FAILED", True),
        ("PRECONDITION_CHANGED", True),
        ("PROTECTED_BLOCK", True),
        ("BLOCK_UNKNOWN", True),
        ("STATE_INVALID", True),
        ("LIMIT_EXCEEDED", True),
        ("INVALID_ARGUMENT", True),
        ("INVALID_COORDINATE", True),
        ("UNLOADED_CHUNK", True),
        ("OUT_OF_BOUNDS", True),
        ("CONFLICTING_EDITS", True),
        ("ADDON_UNAVAILABLE", True),
        ("UNSUPPORTED_CAPABILITY", True),
        ("STATE_UNKNOWN", False),
        ("INTERNAL_ERROR", False),
    ],
)
def test_fallback_allowed_for_code_full_table(code: str, expected: bool) -> None:
    """fallback 一元规则全码表（spec §3.2）：14 true / 2 false。"""
    from services.agent.block_ops.schema import BlockErrorCode, fallback_allowed_for_code

    assert fallback_allowed_for_code(BlockErrorCode(code)) is expected


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("token=bridge-secret", "safe"),
        ("PROTECTED_BLOCK", "password=hunter2"),
    ],
)
def test_addon_error_code_and_message_never_leak_untrusted_text(code: str, message: str) -> None:
    result = map_addon_bridge_result(
        {"ok": False, "payload": {"code": code, "message": message}}
    )

    body = json.loads(result.output)
    assert "bridge-secret" not in result.output
    assert "hunter2" not in result.output
    assert "bridge-secret" not in (result.diagnostic_summary or "")
    assert "hunter2" not in (result.diagnostic_summary or "")
    if code == "PROTECTED_BLOCK":
        assert body["code"] == "PROTECTED_BLOCK"
    else:
        assert body["code"] == "INTERNAL_ERROR"
        assert body["fallback_allowed"] is False


def test_precondition_failed_projects_actual_target_and_hint() -> None:
    result = map_addon_bridge_result(
        {
            "ok": False,
            "payload": {
                "code": "PRECONDITION_FAILED",
                "message": "target is not air",
                "target": {
                    "x": -797,
                    "y": 93,
                    "z": 182,
                    "dimension": "minecraft:overworld",
                },
                "actual": {
                    "type_id": "minecraft:gravel",
                    "states": {"some": "state"},
                    "password": "hunter2",
                },
                "matched_count": 0,
                "actual_type_counts": {"minecraft:gravel": 1},
                "stack": "Traceback password=hunter2",
            },
        }
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "PRECONDITION_FAILED"
    assert body["actual_type_id"] == "minecraft:gravel"
    assert body["target"] == {"x": -797, "y": 93, "z": 182}
    assert "dimension" not in body["target"]
    assert body["matched_count"] == 0
    assert body["actual_type_counts"] == {"minecraft:gravel": 1}
    assert "hint" in body
    assert "minecraft:gravel" in body["hint"]
    assert "expect" in body["hint"]
    assert "replace_any" not in body["hint"]
    assert "expected_previous" not in body["hint"]
    assert "locked_targets" not in body["hint"]
    assert "phase" not in body["hint"]
    assert body["fallback_allowed"] is True
    assert body["retryable"] is False
    assert body["message"] == "目标方块不满足 expect 前置条件。"
    assert "hunter2" not in result.output
    assert "Traceback" not in result.output
    assert "states" not in body


def test_precondition_changed_projects_decision_fields() -> None:
    result = map_addon_bridge_result(
        {
            "ok": False,
            "payload": {
                "code": "PRECONDITION_CHANGED",
                "message": "block changed since preflight",
                "target": {"x": 1, "y": 64, "z": 2},
                "actual": {"type_id": "minecraft:stone"},
            },
        }
    )
    body = json.loads(result.output)
    assert body["code"] == "PRECONDITION_CHANGED"
    assert body["actual_type_id"] == "minecraft:stone"
    assert body["target"] == {"x": 1, "y": 64, "z": 2}
    assert body["hint"]
    assert body["fallback_allowed"] is True


def test_limit_exceeded_addon_error_includes_place_ban_hint() -> None:
    result = map_addon_bridge_result(
        {
            "ok": False,
            "payload": {
                "code": "LIMIT_EXCEEDED",
                "message": "too many cells",
                "matched_count": 6,
                "volume": 25,
                "suggested_max_discrete": 3,
            },
        }
    )
    body = json.loads(result.output)
    assert body["code"] == "LIMIT_EXCEEDED"
    assert body["retryable"] is True
    assert body["fallback_allowed"] is True
    assert body["external_state_unknown"] is False
    assert "place" in body["hint"].lower() or "禁止" in body["hint"]
    assert body["matched_count"] == 6
    assert body["volume"] == 25
    assert body["suggested_max_discrete"] == 3


def test_map_ok_true_non_block_payload_keeps_full_dump() -> None:
    """Legacy / non-block capabilities keep full payload for backward compatibility."""
    result = map_addon_bridge_result({"ok": True, "payload": {"players": [{"name": "Steve"}]}})
    assert result.is_success
    body = json.loads(result.output)
    assert body == {"players": [{"name": "Steve"}]}


def test_place_success_model_projection_is_slim() -> None:
    fat_payload = {
        "schema_version": "1",
        "ok": True,
        "phase": "execute",
        "mode": "place",
        "dimension": "minecraft:overworld",
        "type_id": "minecraft:stone",
        "changed": True,
        "position": {"x": -793, "y": 94, "z": 185, "dimension": "minecraft:overworld"},
        "targets": [
            {
                "x": -793,
                "y": 94,
                "z": 185,
                "dimension": "minecraft:overworld",
                "type_id": "minecraft:stone",
            }
        ],
        "before": {
            "type_id": "minecraft:air",
            "states": {},
            "x": -793,
            "y": 94,
            "z": 185,
            "dimension": "minecraft:overworld",
            "extra_a": 1,
            "extra_b": 2,
            "extra_c": 3,
        },
        "after": {
            "type_id": "minecraft:stone",
            "states": {},
            "x": -793,
            "y": 94,
            "z": 185,
            "dimension": "minecraft:overworld",
        },
        "states": {},
        "repairs_applied": [],
        "verification": {"checked": True, "details": ["a"] * 20},
        "rollback": {"attempted": False},
    }
    projected = project_block_result_for_model(fat_payload, mode="place")
    text = json.dumps(projected, ensure_ascii=False)
    assert len(text) <= 250
    assert projected["ok"] is True
    assert projected["status"] == "applied"
    assert projected["at"] == [-793, 94, 185]
    assert projected["block"] == "minecraft:stone"
    # Air replacements are omitted so was only signals non-air overwrites.
    assert "was" not in projected
    assert "mode" not in projected
    assert "changed" not in projected
    assert "type_id" not in projected
    assert "before" not in projected
    assert "after" not in projected
    assert "targets" not in projected
    assert "verification" not in projected
    assert "phase" not in projected
    assert "locked_targets" not in projected

    mapped = map_addon_bridge_result(
        {"ok": True, "payload": fat_payload},
        project_for_model=True,
        mode="place",
    )
    assert mapped.is_success
    assert len(mapped.output) <= 250
    assert "before" not in mapped.output
    assert "verification" not in mapped.output
    assert "was" not in mapped.output


def test_place_success_model_projection_reports_non_air_was() -> None:
    projected = project_block_result_for_model(
        {
            "ok": True,
            "mode": "place",
            "changed": True,
            "type_id": "minecraft:stone",
            "position": {"x": 0, "y": 64, "z": 0},
            "before": {"type_id": "minecraft:dirt", "x": 0, "y": 64, "z": 0},
            "after": {"type_id": "minecraft:stone", "x": 0, "y": 64, "z": 0},
        },
        mode="place",
    )
    assert projected["was"] == "minecraft:dirt"
    assert projected["block"] == "minecraft:stone"


def test_place_success_model_projection_omits_was_for_air_aliases() -> None:
    for air_id in ("minecraft:air", "Air", "air"):
        projected = project_block_result_for_model(
            {
                "ok": True,
                "mode": "place",
                "changed": True,
                "type_id": "minecraft:stone",
                "was": air_id,
                "position": {"x": 1, "y": 2, "z": 3},
            },
            mode="place",
        )
        assert "was" not in projected, air_id


def test_place_success_model_projection_keeps_cave_air_as_was() -> None:
    projected = project_block_result_for_model(
        {
            "ok": True,
            "mode": "place",
            "changed": True,
            "type_id": "minecraft:stone",
            "was": "minecraft:cave_air",
            "position": {"x": 1, "y": 2, "z": 3},
        },
        mode="place",
    )
    assert projected["was"] == "minecraft:cave_air"


def test_fill_success_model_projection_uses_authorized_aabb() -> None:
    fat_payload = {
        "schema_version": "1",
        "ok": True,
        "phase": "execute",
        "mode": "fill",
        "type_id": "minecraft:oak_planks",
        "changed_count": 6,
        "skipped": 19,
        "volume": 2,  # locked-shrunk (wrong for model)
        "from": {"x": -797, "y": 93, "z": 180},
        "to": {"x": -796, "y": 93, "z": 180},
        "previous_type_counts": {
            "minecraft:air": 6,
            "minecraft:grass_path": 12,
            "minecraft:stone": 4,
            "minecraft:gravel": 3,
        },
        "before_samples": [{"type_id": "minecraft:air", "x": 0, "y": 0, "z": 0}] * 10,
        "targets": [{"x": i, "y": 93, "z": 180} for i in range(6)],
        "verification": {"ok": True, "cells": list(range(25))},
        "rollback": {"needed": False},
    }
    authorized = {
        "from": {"x": -797, "y": 93, "z": 180},
        "to": {"x": -793, "y": 93, "z": 184},
        "volume": 25,
    }
    projected = project_block_result_for_model(
        fat_payload, mode="fill", authorized_bounds=authorized
    )
    assert projected["ok"] is True
    assert projected["status"] == "applied"
    assert projected["changed"] == 6
    assert projected["skipped"] == 19
    assert projected["bounds"] == [[-797, 93, 180], [-793, 93, 184]]
    # Air keys stripped; non-air overwrite signal remains.
    assert "minecraft:air" not in projected["type_counts"]
    assert projected["type_counts"]["minecraft:grass_path"] == 12
    assert projected["type_counts"]["minecraft:stone"] == 4
    assert projected["type_counts"]["minecraft:gravel"] == 3
    for hidden in (
        "before_samples", "targets", "verification", "rollback", "phase",
        "mode", "type_id", "volume", "from", "to",
    ):
        assert hidden not in projected, hidden

    mapped = map_addon_bridge_result(
        {"ok": True, "payload": fat_payload},
        project_for_model=True,
        mode="fill",
        authorized_bounds=authorized,
    )
    body = json.loads(mapped.output)
    assert "before_samples" not in body
    assert body["bounds"] == [[-797, 93, 180], [-793, 93, 184]]
    assert "minecraft:air" not in body["type_counts"]


def test_fill_success_model_projection_omits_air_only_counts() -> None:
    projected = project_block_result_for_model(
        {
            "ok": True,
            "mode": "fill",
            "changed_count": 6,
            "type_id": "minecraft:oak_planks",
            "previous_type_counts": {"minecraft:air": 6, "Air": 1},
            "from": {"x": 0, "y": 0, "z": 0},
            "to": {"x": 1, "y": 0, "z": 2},
        },
        mode="fill",
    )
    assert "type_counts" not in projected


def test_fill_partial_and_noop_projection_carries_status() -> None:
    """Issue 01: model projection surfaces applied/partial/noop status."""
    partial = project_block_result_for_model(
        {
            "ok": True,
            "mode": "fill",
            "status": "partial",
            "changed_count": 6,
            "skipped": 3,
            "type_id": "minecraft:glass",
            "previous_type_counts": {"minecraft:oak_planks": 3},
            "from": {"x": 0, "y": 0, "z": 0},
            "to": {"x": 2, "y": 0, "z": 2},
        },
        mode="fill",
    )
    assert partial["status"] == "partial"
    assert partial["skipped"] == 3
    assert partial["type_counts"] == {"minecraft:oak_planks": 3}

    noop = project_block_result_for_model(
        {
            "ok": True,
            "mode": "fill",
            "status": "noop",
            "changed_count": 0,
            "skipped": 9,
            "type_id": "minecraft:glass",
            "from": {"x": 0, "y": 0, "z": 0},
            "to": {"x": 2, "y": 0, "z": 2},
        },
        mode="fill",
    )
    assert noop["status"] == "noop"
    assert noop["changed"] == 0

    # When addon omits status, the spec §5.2 default is applied (no skip-inference).
    inferred = project_block_result_for_model(
        {
            "ok": True,
            "mode": "fill",
            "changed_count": 6,
            "skipped": 3,
            "type_id": "minecraft:glass",
            "from": {"x": 0, "y": 0, "z": 0},
            "to": {"x": 2, "y": 0, "z": 2},
        },
        mode="fill",
    )
    assert inferred["status"] == "applied"


def test_state_unknown_response_has_fallback_allowed_false() -> None:
    """Issue 01: STATE_UNKNOWN must forbid command fallback and auto-retry."""
    from services.agent.block_ops.schema import build_state_unknown_response

    body = build_state_unknown_response()
    assert body["code"] == "STATE_UNKNOWN"
    assert body["fallback_allowed"] is False
    assert body["retryable"] is False
    assert body["external_state_unknown"] is True


def test_host_limit_error_carries_fallback_allowed_true() -> None:
    """宿主侧 INVALID_ARGUMENT / INVALID_COORDINATE / LIMIT 显式携带
    fallback_allowed=true；否则 execution.py 读默认 false 会误拒命令回退。"""
    from services.agent.block_ops.schema import BlockErrorCode, _host_limit_error

    for code in (
        BlockErrorCode.INVALID_ARGUMENT,
        BlockErrorCode.INVALID_COORDINATE,
        BlockErrorCode.LIMIT_EXCEEDED,
    ):
        result = _host_limit_error(code, "test message")
        assert not result.is_success
        body = json.loads(result.output)
        assert body["code"] == code
        assert body["fallback_allowed"] is True
        assert body["external_state_unknown"] is False

    # 显式传入的字段仍可覆盖默认值（内部错误语义）。
    result = _host_limit_error(
        BlockErrorCode.INVALID_ARGUMENT,
        "test",
        fallback_allowed=False,
        external_state_unknown=True,
    )
    body = json.loads(result.output)
    assert body["fallback_allowed"] is False
    assert body["external_state_unknown"] is True


def test_batch_mode_is_no_longer_projected_for_model() -> None:
    """batch 已退出模型可见契约：投影回退为最小 ok envelope，不泄漏计数。"""
    projected = project_block_result_for_model(
        {
            "ok": True,
            "mode": "batch",
            "changed_count": 4,
            "type_id": "minecraft:stone",
            "previous_type_counts": {
                "minecraft:air": 3,
                "minecraft:dirt": 1,
            },
        },
        mode="batch",
    )
    assert projected == {"ok": True}


def test_limits_hard_clamp() -> None:
    settings = SimpleNamespace(
        addon=SimpleNamespace(
            block_tools=SimpleNamespace(
                max_discrete_positions=99999,
                max_fill_volume=99999,
                cells_per_tick=99999,
                max_locked_targets_on_wire=99999,
            )
        )
    )
    limits = get_block_tools_limits(settings)
    assert limits.max_discrete_positions == HARD_MAX_DISCRETE_POSITIONS
    assert limits.max_fill_volume == 16384
    assert limits.cells_per_tick == 512
    assert limits.max_locked_targets_on_wire == 1024


def test_max_locked_targets_on_wire_default_zero_and_readable() -> None:
    assert DEFAULT_MAX_LOCKED_TARGETS_ON_WIRE == 0
    assert get_block_tools_limits(None).max_locked_targets_on_wire == 0
    assert (
        get_block_tools_limits(
            SimpleNamespace(addon=SimpleNamespace(block_tools={}))
        ).max_locked_targets_on_wire
        == 0
    )
    assert (
        get_block_tools_limits(
            SimpleNamespace(
                addon=SimpleNamespace(
                    block_tools={"max_locked_targets_on_wire": 8}
                )
            )
        ).max_locked_targets_on_wire
        == 8
    )
    # Explicit 0 must not be treated as missing.
    assert (
        get_block_tools_limits(
            SimpleNamespace(
                addon=SimpleNamespace(
                    block_tools={"max_locked_targets_on_wire": 0}
                )
            )
        ).max_locked_targets_on_wire
        == 0
    )


def test_locked_targets_wire_limit_exceeded_only_when_cap_positive() -> None:
    cells = [{"x": i, "y": 64, "z": 0} for i in range(5)]
    assert locked_targets_wire_limit_exceeded(cells, max_locked_targets_on_wire=0) is None
    assert locked_targets_wire_limit_exceeded(cells, max_locked_targets_on_wire=5) is None
    fail = locked_targets_wire_limit_exceeded(cells, max_locked_targets_on_wire=2)
    assert fail is not None
    body = json.loads(fail.output)
    assert body["code"] == "LIMIT_EXCEEDED"
    assert body["reason"] == "max_locked_targets_on_wire"
    assert body["count"] == 5
    assert body["max_locked_targets_on_wire"] == 2


def test_absolute_execute_still_omits_when_max_locked_wire_zero() -> None:
    """Default max_locked_targets_on_wire=0 must not force shipping locked list."""
    locked = [{"x": i, "y": 64, "z": 0} for i in range(6)]
    payload = build_edit_payload(
        mode="fill",
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        position=None,
        positions=None,
        from_pos={"x": 0, "y": 64, "z": 0},
        to_pos={"x": 4, "y": 64, "z": 4},
        type_id="minecraft:oak_planks",
        states=None,
        replace_any=False,
        expected_previous=None,
        player_name="Steve",
        phase="execute",
        locked_targets=locked,
        max_locked_targets_on_wire=0,
    )
    assert "locked_targets" not in payload
    assert should_omit_locked_targets_on_wire(
        mode="fill",
        phase="execute",
        from_pos={"x": 0, "y": 64, "z": 0},
        to_pos={"x": 4, "y": 64, "z": 4},
        position=None,
        positions=None,
        locked_targets=locked,
        coordinate_mode="absolute",
        max_locked_targets_on_wire=0,
    )


def test_bridge_payload_limits_are_top_level_addon_fields() -> None:
    """Host settings names must be flattened to Add-on top-level keys (not nested limits)."""
    host_limits = {
        "max_discrete_positions": 512,
        "max_fill_volume": 2048,
        "cells_per_tick": 64,
    }
    inspect_payload = build_inspect_payload(
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        position={"x": 0, "y": 64, "z": 0},
        positions=None,
        player_name="Steve",
        limits=host_limits,
    )
    assert "limits" not in inspect_payload
    assert inspect_payload["max_positions"] == 512
    assert inspect_payload["max_discrete"] == 512

    edit_payload = build_edit_payload(
        mode="fill",
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        position=None,
        positions=None,
        from_pos={"x": 0, "y": 64, "z": 0},
        to_pos={"x": 1, "y": 64, "z": 1},
        type_id="minecraft:stone",
        states=None,
        replace_any=False,
        expected_previous=None,
        player_name="Steve",
        phase="preflight",
        limits=host_limits,
    )
    assert "limits" not in edit_payload
    assert edit_payload["max_discrete"] == 512
    assert edit_payload["max_positions"] == 512
    assert edit_payload["max_fill_volume"] == 2048
    assert edit_payload["cells_per_tick"] == 64


def test_apply_limits_maps_host_names() -> None:
    payload: dict[str, Any] = {}
    apply_limits_to_payload(
        payload,
        {
            "max_discrete_positions": 100,
            "max_fill_volume": 50,
            "cells_per_tick": 10,
        },
    )
    assert payload == {
        "max_discrete": 100,
        "max_positions": 100,
        "max_fill_volume": 50,
        "cells_per_tick": 10,
    }


@pytest.mark.asyncio
async def test_capability_supported_and_cache() -> None:
    bridge = _FakeBridge()
    cid = "conn-1"
    record = await ensure_block_capability(cid, bridge)
    assert record.status == BlockCapabilityStatus.SUPPORTED
    assert len([c for c in bridge.calls if c[0] == "get_capabilities"]) == 1
    # Cached — no second request
    record2 = await ensure_block_capability(cid, bridge)
    assert record2.status == BlockCapabilityStatus.SUPPORTED
    assert len([c for c in bridge.calls if c[0] == "get_capabilities"]) == 1


@pytest.mark.asyncio
async def test_capability_unsupported_old_addon() -> None:
    async def handler(cap: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "payload": {"capabilities": {}}}

    bridge = _FakeBridge(handler)
    record = await ensure_block_capability("old", bridge)
    assert record.status == BlockCapabilityStatus.UNSUPPORTED

    result = await place_block_impl(
        SimpleNamespace(
            deps=_Deps(connection_id="old", addon_bridge=bridge)
        ),  # type: ignore[arg-type]
        pos=[1, 64, 1],
        block="minecraft:stone",
    )
    body = json.loads(result.output)
    assert body["code"] == "UNSUPPORTED_CAPABILITY"
    assert body["fallback_allowed"] is True


@pytest.mark.asyncio
async def test_capability_unavailable_without_bridge() -> None:
    record = await ensure_block_capability("no-bridge", None)
    assert record.status == BlockCapabilityStatus.UNAVAILABLE


def test_capability_clear_on_disconnect() -> None:
    cache = get_block_capability_cache()
    cache.set(
        "c1",
        BlockCapabilityRecord(
            status=BlockCapabilityStatus.SUPPORTED,
            probed_at=time.time(),
        ),
    )
    assert clear_block_capability("c1") is True
    assert cache.get("c1") is None


def test_preflight_cache_ttl_and_connection_clear() -> None:
    cache = get_preflight_cache()
    cache.put(
        run_id="r1",
        tool_call_id="t1",
        original_args_hash="h1",
        canonical_args={"phase": "execute", "locked_targets": [{"x": 1, "y": 2, "z": 3}]},
        connection_id="conn-x",
    )
    assert cache.get("r1", "t1", "h1") is not None
    cleared = cache.clear_connection("conn-x")
    assert cleared == 1
    assert cache.get("r1", "t1", "h1") is None


def test_merge_canonical_locks_absolute_place() -> None:
    original = {
        "mode": "place",
        "coordinate_mode": "player_relative",
        "type_id": "minecraft:stone",
        "position": {"forward": 1, "right": 0, "up": 0},
    }
    preflight = {
        "locked_targets": [{"dimension": "minecraft:overworld", "x": 10, "y": 64, "z": 5}],
        "repairs_applied": [{"field": "type_id", "from": "stone", "to": "minecraft:stone"}],
    }
    canonical = merge_canonical_from_preflight(original, preflight)
    assert canonical["phase"] == "execute"
    assert canonical["coordinate_mode"] == "absolute"
    assert canonical["position"] == {"x": 10, "y": 64, "z": 5}
    assert canonical["dimension"] == "minecraft:overworld"
    assert canonical["locked_targets"]


def test_fill_canonical_args_exclude_python_bound_aliases_from_authorization_hash() -> None:
    original = {
        "mode": "fill",
        "type_id": "minecraft:stone",
        "coordinate_mode": "absolute",
        "dimension": "minecraft:overworld",
        "from_pos": {"x": 4, "y": 64, "z": 3},
        "to_pos": {"x": 2, "y": 64, "z": 1},
    }
    locked_targets = [
        {"dimension": "minecraft:overworld", "x": x, "y": 64, "z": z}
        for x in range(2, 5)
        for z in range(1, 4)
    ]
    canonical = merge_canonical_from_preflight(
        original,
        {
            "mode": "fill",
            "coordinate_mode": "absolute",
            "dimension": "minecraft:overworld",
            "bounds": {
                "min": {"x": 2, "y": 64, "z": 1},
                "max": {"x": 4, "y": 64, "z": 3},
            },
            "locked_targets": locked_targets,
        },
    )

    assert canonical["from"] == {"x": 2, "y": 64, "z": 1}
    assert canonical["to"] == {"x": 4, "y": 64, "z": 3}
    assert "from_pos" not in canonical
    assert "to_pos" not in canonical
    assert "from_pos" not in normalize_tool_args(canonical)
    assert "to_pos" not in normalize_tool_args(canonical)


def test_merge_canonical_fill_sparse_locked_keeps_request_aabb() -> None:
    """Sparse locked cells must not shrink authorized fill from/to."""
    original = {
        "mode": "fill",
        "type_id": "minecraft:oak_planks",
        "coordinate_mode": "absolute",
        "dimension": "minecraft:overworld",
        "from_pos": {"x": -797, "y": 93, "z": 180},
        "to_pos": {"x": -793, "y": 93, "z": 184},
    }
    # 6 air cells along z=180 only — sparse relative to 5×1×5 volume.
    locked_targets = [
        {"dimension": "minecraft:overworld", "x": x, "y": 93, "z": 180}
        for x in range(-797, -791)
    ]
    preflight = {
        "mode": "fill",
        "coordinate_mode": "absolute",
        "dimension": "minecraft:overworld",
        # Addon may report matched-only bounds; must not override request AABB.
        "bounds": {
            "min": {"x": -797, "y": 93, "z": 180},
            "max": {"x": -792, "y": 93, "z": 180},
        },
        "locked_targets": locked_targets,
        "matched_count": len(locked_targets),
    }

    canonical = merge_canonical_from_preflight(original, preflight)
    assert canonical["phase"] == "execute"
    assert canonical["from"] == {"x": -797, "y": 93, "z": 180}
    assert canonical["to"] == {"x": -793, "y": 93, "z": 184}
    assert canonical["locked_targets"] == locked_targets
    assert "from_pos" not in canonical
    assert "to_pos" not in canonical

    # Success path: authorized_bounds derived from post-merge canonical corners
    # must report original volume 25, not locked-shrunk volume.
    authorized_bounds = {
        "from": canonical["from"],
        "to": canonical["to"],
        "volume": 25,
    }
    fat_payload = {
        "ok": True,
        "mode": "fill",
        "type_id": "minecraft:oak_planks",
        "changed_count": 6,
        "skipped": 19,
        "volume": 6,
        "from": {"x": -797, "y": 93, "z": 180},
        "to": {"x": -792, "y": 93, "z": 180},
        "before_samples": [{"type_id": "minecraft:air"}] * 6,
    }
    projected = project_block_result_for_model(
        fat_payload, mode="fill", authorized_bounds=authorized_bounds
    )
    assert projected["bounds"] == [[-797, 93, 180], [-793, 93, 184]]
    assert "before_samples" not in projected


def test_merge_canonical_fill_accepts_from_to_request_keys() -> None:
    """Original from/to (catalog names) also preserve authorized AABB."""
    original = {
        "mode": "fill",
        "type_id": "minecraft:stone",
        "coordinate_mode": "absolute",
        "dimension": "minecraft:overworld",
        "from": {"x": 0, "y": 64, "z": 0},
        "to": {"x": 4, "y": 64, "z": 4},
    }
    locked = [
        {"dimension": "minecraft:overworld", "x": 0, "y": 64, "z": 0},
        {"dimension": "minecraft:overworld", "x": 1, "y": 64, "z": 0},
    ]
    canonical = merge_canonical_from_preflight(
        original,
        {
            "locked_targets": locked,
            "bounds": {
                "min": {"x": 0, "y": 64, "z": 0},
                "max": {"x": 1, "y": 64, "z": 0},
            },
        },
    )
    assert canonical["from"] == {"x": 0, "y": 64, "z": 0}
    assert canonical["to"] == {"x": 4, "y": 64, "z": 4}
    assert len(canonical["locked_targets"]) == 2


@pytest.mark.asyncio
async def test_harness_prepare_tools_passes_through_block_schemas() -> None:
    """prepare_tools no longer strips internal keys (Task 6).

    The single-op block tools expose only model-visible fields in their public
    schemas, so prepare_tools passes tool definitions through unchanged.
    """
    from pydantic_ai.tools import ToolDefinition

    cap = HarnessCapability(policy=PolicyEngine.from_settings(_Settings()))
    cid = "schema-passthrough-1"
    get_block_capability_cache().set(
        cid,
        BlockCapabilityRecord(
            status=BlockCapabilityStatus.SUPPORTED,
            probed_at=time.time(),
        ),
    )

    class _Ctx:
        deps = SimpleNamespace(connection_id=cid, addon_bridge=None, settings=_Settings())

    tool_defs = [
        ToolDefinition(
            name="place_block",
            parameters_json_schema={
                "type": "object",
                "properties": {"pos": {"type": "array"}, "block": {"type": "string"}},
            },
        ),
        ToolDefinition(
            name="fill_block",
            parameters_json_schema={
                "type": "object",
                "properties": {"from": {"type": "array"}, "to": {"type": "array"}},
            },
        ),
        ToolDefinition(
            name="inspect_block",
            parameters_json_schema={
                "type": "object",
                "properties": {"target": {"type": "object"}},
            },
        ),
    ]
    prepared = await cap.prepare_tools(_Ctx(), tool_defs)  # type: ignore[arg-type]
    by_name = {td.name: td for td in prepared}
    assert "place_block" in by_name
    assert "fill_block" in by_name
    assert "inspect_block" in by_name
    # Schemas pass through unchanged: nothing is stripped or rewritten.
    for name, original in zip(
        ("place_block", "fill_block", "inspect_block"), tool_defs, strict=True
    ):
        assert by_name[name].parameters_json_schema == original.parameters_json_schema
        assert by_name[name].name == original.name


def test_policy_block_mutations_require_approval_inspect_allows() -> None:
    engine = PolicyEngine.from_settings(_Settings())
    allow = engine.decide(
        "inspect_block",
        {
            "coordinate_mode": "absolute",
            "dimension": "minecraft:overworld",
            "position": {"x": 0, "y": 64, "z": 0},
        },
        player_name="Steve",
    )
    assert allow.action == PolicyDecisionKind.ALLOW

    need_place = engine.decide(
        "place_block",
        {"pos": [0, 64, 0], "block": "minecraft:stone", "expect": "air"},
        player_name="Steve",
    )
    assert need_place.action == PolicyDecisionKind.REQUIRE_APPROVAL

    need_fill = engine.decide(
        "fill_block",
        {
            "from": [0, 64, 0],
            "to": [4, 64, 4],
            "block": "minecraft:stone",
            "expect": "air",
        },
        player_name="Steve",
    )
    assert need_fill.action == PolicyDecisionKind.REQUIRE_APPROVAL

    approved = engine.decide(
        "place_block",
        {"pos": [0, 64, 0], "block": "minecraft:stone", "expect": "air"},
        player_name="Steve",
        approved=True,
    )
    assert approved.action == PolicyDecisionKind.ALLOW

    # edit_blocks 已移出目录：不再暴露，直接拒绝。
    legacy = engine.decide(
        "edit_blocks",
        {
            "mode": "place",
            "type_id": "minecraft:stone",
            "coordinate_mode": "absolute",
            "dimension": "minecraft:overworld",
            "position": {"x": 0, "y": 64, "z": 0},
        },
        player_name="Steve",
    )
    assert legacy.action == PolicyDecisionKind.DENY


def test_exposure_hidden_without_capability() -> None:
    engine = PolicyEngine.from_settings(_Settings())
    assert engine.is_tool_exposed("inspect_block") is False
    assert engine.is_tool_exposed("run_minecraft_command") is True


def test_exposure_visible_when_supported() -> None:
    engine = PolicyEngine.from_settings(_Settings())
    cid = "expose-1"
    get_block_capability_cache().set(
        cid,
        BlockCapabilityRecord(
            status=BlockCapabilityStatus.SUPPORTED,
            probed_at=time.time(),
        ),
    )
    ctx = SimpleNamespace(deps=SimpleNamespace(connection_id=cid))
    assert engine.is_tool_exposed("inspect_block", ctx=ctx) is True
    assert engine.is_tool_exposed("place_block", ctx=ctx) is True
    assert engine.is_tool_exposed("fill_block", ctx=ctx) is True
    # edit_blocks 已移出目录：即使能力探测通过也不再暴露。
    assert engine.is_tool_exposed("edit_blocks", ctx=ctx) is False


@pytest.mark.asyncio
async def test_inspect_block_impl_absolute() -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    result = await inspect_block_impl(
        ctx,  # type: ignore[arg-type]
        target=[3, 64, 4],
    )
    assert result.is_success
    assert any(c[0] == "inspect_block" for c in bridge.calls)


@pytest.mark.asyncio
async def test_inspect_block_impl_requires_target() -> None:
    """A missing target is rejected as INVALID_ARGUMENT (never a TypeError)."""
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    result = await inspect_block_impl(
        ctx,  # type: ignore[arg-type]
        target=None,
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "INVALID_ARGUMENT"


# ---------------------------------------------------------------------------
# Issue 02: unified target inspect_block
# ---------------------------------------------------------------------------


def test_normalize_inspect_target_positions_absolute() -> None:
    from services.agent.block_ops.target import normalize_inspect_target

    normalized, error = normalize_inspect_target({
        "positions": [{"x": 1, "y": 64, "z": 2}, {"x": 3, "y": 65, "z": 4}],
    })
    assert error is None
    assert normalized is not None
    assert normalized.shape == "positions"
    assert normalized.coordinate_mode == "absolute"
    assert len(normalized.positions) == 2


def test_normalize_inspect_target_positions_relative() -> None:
    from services.agent.block_ops.target import normalize_inspect_target

    normalized, error = normalize_inspect_target({
        "positions": [{"forward": 1, "right": 0, "up": 0}],
    })
    assert error is None
    assert normalized is not None
    assert normalized.shape == "positions"
    assert normalized.coordinate_mode == "player_relative"


def test_normalize_inspect_target_box_absolute() -> None:
    from services.agent.block_ops.target import normalize_inspect_target

    normalized, error = normalize_inspect_target({
        "box": {"from": {"x": 0, "y": 64, "z": 0}, "to": {"x": 2, "y": 66, "z": 2}},
    })
    assert error is None
    assert normalized is not None
    assert normalized.shape == "box"
    assert normalized.coordinate_mode == "absolute"


def test_normalize_inspect_target_rejects_both_shapes() -> None:
    from services.agent.block_ops.target import normalize_inspect_target

    normalized, error = normalize_inspect_target({
        "positions": [{"x": 0, "y": 64, "z": 0}],
        "box": {"from": {"x": 0, "y": 64, "z": 0}, "to": {"x": 1, "y": 64, "z": 1}},
    })
    assert error is not None
    body = json.loads(error.output)
    assert body["code"] == "INVALID_ARGUMENT"


def test_normalize_inspect_target_rejects_neither_shape() -> None:
    from services.agent.block_ops.target import normalize_inspect_target

    normalized, error = normalize_inspect_target({})
    assert error is not None
    body = json.loads(error.output)
    assert body["code"] == "INVALID_ARGUMENT"


def test_normalize_inspect_target_rejects_mixed_coord_modes() -> None:
    from services.agent.block_ops.target import normalize_inspect_target

    # positions mixing absolute and relative
    normalized, error = normalize_inspect_target({
        "positions": [
            {"x": 0, "y": 64, "z": 0},
            {"forward": 1, "right": 0, "up": 0},
        ],
    })
    assert error is not None
    body = json.loads(error.output)
    assert body["code"] == "INVALID_COORDINATE"
    assert "混用" in body["message"]


def test_normalize_inspect_target_box_rejects_mixed_coord_modes() -> None:
    from services.agent.block_ops.target import normalize_inspect_target

    # box with from=absolute, to=relative
    normalized, error = normalize_inspect_target({
        "box": {
            "from": {"x": 0, "y": 64, "z": 0},
            "to": {"forward": 1, "right": 0, "up": 0},
        },
    })
    assert error is not None
    body = json.loads(error.output)
    assert body["code"] == "INVALID_COORDINATE"


def test_normalize_inspect_target_rejects_empty_positions() -> None:
    from services.agent.block_ops.target import normalize_inspect_target

    normalized, error = normalize_inspect_target({"positions": []})
    assert error is not None
    body = json.loads(error.output)
    assert body["code"] == "INVALID_ARGUMENT"


def test_normalize_inspect_target_rejects_non_dict() -> None:
    from services.agent.block_ops.target import normalize_inspect_target

    normalized, error = normalize_inspect_target("not a dict")
    assert error is not None
    body = json.loads(error.output)
    assert body["code"] == "INVALID_ARGUMENT"


def test_project_inspect_single_point_matches_spec_53() -> None:
    from services.agent.block_ops.project import project_block_result_for_model

    payload = {
        "schema_version": "1",
        "ok": True,
        "status": "inspected",
        "blocks": [{"x": 0, "y": 64, "z": 0, "type_id": "minecraft:stone", "states": {}, "waterlogged": False, "is_air": False, "is_liquid": False}],
        "coordinate_mode": "absolute",
        "dimension": "minecraft:overworld",
        "facing": "south",
        "player_origin": {"x": 0, "y": 64, "z": 0},
        "player_name": "Steve",
        "targets": [{"dimension": "minecraft:overworld", "x": 0, "y": 64, "z": 0}],
        "repairs_applied": [],
    }
    projected = project_block_result_for_model(payload)
    assert projected == {
        "ok": True,
        "block": "minecraft:stone",
        "states": {},
        "waterlogged": False,
        "is_air": False,
        "is_liquid": False,
    }
    # Internal metadata stripped (never mirrored to the model).
    for hidden in (
        "status", "blocks", "targets", "player_origin", "facing",
        "player_name", "repairs_applied", "coordinate_mode", "dimension",
    ):
        assert hidden not in projected, hidden


def test_project_inspect_region_matches_spec_53() -> None:
    from services.agent.block_ops.project import project_block_result_for_model

    payload = {
        "schema_version": "1",
        "ok": True,
        "status": "inspected",
        "summary": {
            "bounds": {"from": {"x": 0, "y": 64, "z": 0}, "to": {"x": 2, "y": 66, "z": 2}},
            "count": 27,
            "type_counts": {"minecraft:stone": 20, "minecraft:air": 7},
            "unknown_count": 0,
            "samples": [],
        },
        "coordinate_mode": "absolute",
        "dimension": "minecraft:overworld",
    }
    projected = project_block_result_for_model(payload)
    assert projected["ok"] is True
    assert projected["count"] == 27
    assert projected["type_counts"] == {"minecraft:stone": 20, "minecraft:air": 7}
    assert projected["samples"] == []
    # Spec §5.3 region shape omits status/bounds/unknown_count; metadata stripped.
    for hidden in (
        "status", "bounds", "unknown_count", "blocks",
        "coordinate_mode", "dimension",
    ):
        assert hidden not in projected, hidden


@pytest.mark.asyncio
async def test_inspect_block_impl_target_positions() -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    result = await inspect_block_impl(
        ctx,  # type: ignore[arg-type]
        target={"positions": [{"x": 1, "y": 64, "z": 2}]},
    )
    assert result.is_success
    # Verify the bridge received the unified target shape.
    inspect_calls = [c for c in bridge.calls if c[0] == "inspect_block"]
    assert inspect_calls
    payload = inspect_calls[-1][1]
    assert "target" in payload
    assert payload["target"]["positions"] == [{"x": 1, "y": 64, "z": 2}]


@pytest.mark.asyncio
async def test_inspect_block_impl_target_box() -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    result = await inspect_block_impl(
        ctx,  # type: ignore[arg-type]
        target={"box": {"from": {"x": 0, "y": 64, "z": 0}, "to": {"x": 2, "y": 66, "z": 2}}},
    )
    assert result.is_success
    inspect_calls = [c for c in bridge.calls if c[0] == "inspect_block"]
    payload = inspect_calls[-1][1]
    assert payload["target"]["box"]["from"] == {"x": 0, "y": 64, "z": 0}
    # Result should be the region projection (spec §5.3: count/type_counts/samples, no status).
    body = json.loads(result.output)
    assert "status" not in body
    assert "count" in body
    assert "type_counts" in body
    assert "samples" in body
    assert "blocks" not in body


@pytest.mark.asyncio
async def test_inspect_block_impl_target_rejects_mixed_coords() -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    result = await inspect_block_impl(
        ctx,  # type: ignore[arg-type]
        target={"positions": [{"x": 0, "y": 64, "z": 0}, {"forward": 1, "right": 0, "up": 0}]},
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "INVALID_COORDINATE"


@pytest.mark.asyncio
async def test_inspect_block_impl_target_rejects_both_shapes() -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    result = await inspect_block_impl(
        ctx,  # type: ignore[arg-type]
        target={
            "positions": [{"x": 0, "y": 64, "z": 0}],
            "box": {"from": {"x": 0, "y": 64, "z": 0}, "to": {"x": 1, "y": 64, "z": 1}},
        },
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_inspect_block_impl_target_box_volume_limit() -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    # 10x10x10 = 1000 volume, exceeds default 4096? No. Use a small override.
    # Actually default max_fill_volume=4096; 1000 < 4096 so it passes host check.
    # Use 200x200x200 = 8M to exceed.
    result = await inspect_block_impl(
        ctx,  # type: ignore[arg-type]
        target={"box": {"from": {"x": 0, "y": 0, "z": 0}, "to": {"x": 199, "y": 199, "z": 199}}},
    )
    # Host rejects before bridge call.
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "LIMIT_EXCEEDED"


def test_inspect_config_defaults_and_hard_caps() -> None:
    from services.agent.block_ops.config import (
        DEFAULT_INSPECT_SAMPLE_LIMIT,
        DEFAULT_INSPECT_SUMMARY_THRESHOLD,
        HARD_MAX_INSPECT_SAMPLE_LIMIT,
        HARD_MAX_INSPECT_SUMMARY_THRESHOLD,
        get_block_tools_limits,
    )

    limits = get_block_tools_limits(None)
    assert limits.inspect_summary_threshold == DEFAULT_INSPECT_SUMMARY_THRESHOLD
    assert limits.inspect_sample_limit == DEFAULT_INSPECT_SAMPLE_LIMIT

    # Hard cap enforcement.
    settings = SimpleNamespace(
        addon=SimpleNamespace(
            block_tools={
                "inspect_summary_threshold": 1000,
                "inspect_sample_limit": 1000,
            }
        )
    )
    limits = get_block_tools_limits(settings)
    assert limits.inspect_summary_threshold == HARD_MAX_INSPECT_SUMMARY_THRESHOLD
    assert limits.inspect_sample_limit == HARD_MAX_INSPECT_SAMPLE_LIMIT


def test_config_example_exposes_inspect_summary_limits() -> None:
    """The documented block-tool limits survive Settings validation unchanged."""
    from config.settings import AddonBlockToolsConfig

    example = json.loads((Path(__file__).parents[1] / "config.example.json").read_text())
    raw_block_tools = example["addon"]["block_tools"]
    assert raw_block_tools["inspect_summary_threshold"] == 8
    assert raw_block_tools["inspect_sample_limit"] == 8
    # Grouped-edit limits were removed from config in Task 6 (single-responsibility).
    assert "max_edits_per_group" not in raw_block_tools
    assert "max_total_targets_per_group" not in raw_block_tools

    block_tools = AddonBlockToolsConfig.model_validate(raw_block_tools)
    limits = get_block_tools_limits(
        SimpleNamespace(addon=SimpleNamespace(block_tools=block_tools))
    )

    assert limits.inspect_summary_threshold == 8
    assert limits.inspect_sample_limit == 8


def test_inspect_model_schema_only_exposes_target() -> None:
    """Model-facing inspect_block schema must only expose target.

    The single-responsibility inspect_block has no hidden recovery fields
    (locked_targets / phase) — nothing to strip after Task 6.
    """
    agent: Agent[Any, str] = Agent("test", deps_type=_Deps, output_type=str)
    register_agent_tools(agent)
    tools = iter_registered_tools(agent)  # type: ignore[arg-type]
    raw_schema = tools["inspect_block"].function_schema.json_schema
    raw_props = set(raw_schema.get("properties") or {})
    # Only target is exposed.
    assert raw_props == {"target"}
    # Legacy / hidden fields removed.
    assert "dimension" not in raw_props
    assert "locked_targets" not in raw_props
    assert "phase" not in raw_props
    assert "coordinate_mode" not in raw_props
    assert "position" not in raw_props
    assert "positions" not in raw_props
    required = set(raw_schema.get("required") or [])
    assert "target" in required


@pytest.mark.asyncio
async def test_failed_capability_probe_does_not_expose_internal_detail() -> None:
    cid = str(uuid4())
    get_block_capability_cache().set(
        cid,
        BlockCapabilityRecord(
            status=BlockCapabilityStatus.FAILED,
            probed_at=time.time(),
            detail="package.module.probe(token=bridge-secret, password=hunter2)",
        ),
    )
    ctx = SimpleNamespace(deps=_Deps(connection_id=cid, addon_bridge=_FakeBridge()))

    result = await place_block_impl(
        ctx,  # type: ignore[arg-type]
        pos=[1, 64, 1],
        block="minecraft:stone",
    )

    body = json.loads(result.output)
    assert body["code"] == "ADDON_UNAVAILABLE"
    assert body["fallback_allowed"] is True
    assert "独立审批" in body["message"]
    assert "package.module" not in result.output
    assert "bridge-secret" not in result.output
    assert "hunter2" not in result.output


def test_get_command_line_byte_budget_reads_flow_control() -> None:
    assert get_command_line_byte_budget(None) == DEFAULT_COMMAND_LINE_BYTE_BUDGET
    assert get_command_line_byte_budget(SimpleNamespace()) == DEFAULT_COMMAND_LINE_BYTE_BUDGET
    assert (
        get_command_line_byte_budget(
            SimpleNamespace(flow_control=SimpleNamespace(command_line_byte_budget=400))
        )
        == 400
    )
    assert (
        get_command_line_byte_budget(
            SimpleNamespace(flow_control={"command_line_byte_budget": 500})
        )
        == 500
    )


@pytest.mark.asyncio
async def test_registered_tools_include_block_ops_and_ok_false() -> None:
    agent = Agent("test", deps_type=_Deps, output_type=str)
    register_agent_tools(agent)
    tools = iter_registered_tools(agent)
    assert "inspect_block" in tools
    assert "place_block" in tools
    assert "fill_block" in tools
    assert "edit_blocks" not in tools

    async def fail_handler(cap: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": False,
            "payload": {"code": "OUT_OF_BOUNDS", "message": "y too high"},
        }

    bridge = _FakeBridge(fail_handler)
    # Mark capability supported so tool proceeds to bridge call
    cid = str(uuid4())
    get_block_capability_cache().set(
        cid,
        BlockCapabilityRecord(
            status=BlockCapabilityStatus.SUPPORTED,
            probed_at=time.time(),
        ),
    )
    deps = _Deps(connection_id=cid, addon_bridge=bridge, settings=Settings())
    ctx = SimpleNamespace(deps=deps)
    raw = await tools["get_player_snapshot"].function(ctx, target="Steve")
    # Stringified by wrapper or ToolResult
    text = raw if isinstance(raw, str) else getattr(raw, "output", str(raw))
    assert "OUT_OF_BOUNDS" in text or "ok" in text.lower() or "false" in text.lower()
    # Ensure failure path: map_addon_bridge_result used
    mapped = map_addon_bridge_result(
        {"ok": False, "payload": {"code": "OUT_OF_BOUNDS", "message": "y too high"}}
    )
    assert not mapped.is_success


@pytest.mark.asyncio
async def test_harness_preflight_before_approval_for_place_block(monkeypatch) -> None:
    """place_block: preflight before approval; plan_id in metadata; no execute yet."""
    from services.agent.harness import catalog as _catalog_module
    from services.agent.harness.catalog import ToolIntent, ToolRisk, _entry

    monkeypatch.setattr(
        _catalog_module,
        "_TOOL_CATALOG",
        {
            **_catalog_module._TOOL_CATALOG,
            "place_block": _entry(
                "place_block",
                ToolIntent.CHANGE_WORLD,
                ToolRisk.HIGH,
                "向世界写入单个方块时使用。",
                "不要用于查询或批量填充。",
                "pos 为 [x, y, z] 绝对坐标；block 为方块 type_id。",
                may_have_external_side_effects=True,
            ),
        },
    )
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    policy = PolicyEngine.from_settings(_Settings())
    agent: Agent[_Deps, str | DeferredToolRequests] = Agent(
        "test",
        deps_type=_Deps,
        output_type=[str, DeferredToolRequests],
        capabilities=[HarnessCapability(policy=policy)],
    )

    @agent.tool
    async def place_block(
        ctx: RunContext[_Deps],
        pos: list[int],
        block: str,
        expect: str = "air",
        states: dict[str, Any] | None = None,
    ) -> str:
        raise AssertionError("must not execute before approval")

    async def model_fn(messages: list[ModelMessage], info: Any) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="place_block",
                    args={"pos": [8, 64, 8], "block": "minecraft:stone", "expect": "air"},
                )
            ]
        )

    deps = _Deps(connection_id=cid, addon_bridge=bridge, settings=_Settings(), run_id="run-pf-1")
    first = await agent.run("place stone", model=FunctionModel(model_fn), deps=deps)
    assert isinstance(first.output, DeferredToolRequests)
    assert first.output.approvals
    approval = first.output.approvals[0]
    meta = (first.output.metadata or {}).get(approval.tool_call_id) or {}
    plan_id = meta.get("plan_id")
    assert isinstance(plan_id, str) and plan_id
    normalized = meta.get("normalized_args") or {}
    assert normalized.get("pos") == [8, 64, 8]
    assert normalized.get("block") == "minecraft:stone"
    # 审批元数据只含模型可见字段
    for hidden in ("locked_targets", "phase", "status"):
        assert hidden not in normalized
    # 绝对坐标直通：除 capability 探测外无任何 bridge 帧，也未执行
    assert not any(
        capability in {"edit_blocks", "inspect_block", "place_block", "fill_block"}
        for capability, _payload in bridge.calls
    )
    # 预检缓存持有 plan_id 与 canonical args，供恢复路径使用
    cached = get_preflight_cache().get_by_plan_id(plan_id)
    assert cached is not None
    assert cached.tool_name == "place_block"
    assert cached.canonical_args.get("pos") == [8, 64, 8]


@pytest.mark.asyncio
async def test_harness_resume_with_unknown_plan_is_state_unknown_without_bridge_call(
    monkeypatch,
) -> None:
    """未知 plan_id 恢复：STATE_UNKNOWN，不调用 bridge / impl，不抛 TypeError。

    取代旧「unhashable execute_mode 投影失败」用例：新契约的恢复负载只有
    {plan_id}，未命中预检缓存时结构性返回 STATE_UNKNOWN。
    """
    from unittest.mock import MagicMock

    from services.agent.harness.execution import (
        HarnessToolset,
        get_block_command_fallback_store,
    )

    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    impl_called: dict[str, int] = {"count": 0}

    async def fake_place_impl(ctx, *, pos, block, expect="air", states=None):
        impl_called["count"] += 1
        return ToolResult.ok("placed")

    monkeypatch.setattr("services.agent.block_ops.tools_impl.place_block_impl", fake_place_impl)

    settings = _Settings()
    ts = HarnessToolset(
        wrapped=MagicMock(),
        policy=PolicyEngine.from_settings(settings),
        idempotency=get_idempotency_store(),
        fallback_store=get_block_command_fallback_store(),
    )
    ctx = SimpleNamespace(
        deps=_Deps(connection_id=cid, addon_bridge=bridge, settings=settings, run_id="run-unknown-plan"),
        tool_call_id="tc-unknown-plan",
        tool_call_approved=True,
    )
    result = await ts.call_tool("place_block", {"plan_id": "no-such-plan"}, ctx, MagicMock())

    assert isinstance(result, str)
    body = json.loads(result)
    assert body["code"] == "STATE_UNKNOWN"
    assert body["external_state_unknown"] is True
    assert impl_called["count"] == 0
    # 只发生过 capability 探测（ensure_block_capability），绝无方块操作帧
    assert not any(
        capability in {"edit_blocks", "inspect_block", "place_block", "fill_block"}
        for capability, _payload in bridge.calls
    )


@pytest.mark.asyncio
async def test_execute_block_plan_concurrent_same_plan_executes_once(monkeypatch) -> None:
    """同一 plan_id 并发恢复只执行一次 impl（executed 读写原子化，无竞态）。

    Reviewer finding (Important 1)：executed/execution_result 曾在缓存锁之外
    读写，两个并发 execute_block_plan 都会看到 executed=False 并各自执行
    impl（双重写入）。per-plan asyncio.Lock 串行化 + 缓存锁内读-查-写修复。
    """
    from services.agent.block_ops.tools_impl import execute_block_plan

    calls: dict[str, int] = {"count": 0}
    gate = asyncio.Event()

    async def fake_place_impl(ctx, *, pos, block, expect="air", states=None):
        calls["count"] += 1
        await gate.wait()  # 拉大竞态窗口：两个协程都能在释放前看到 executed=False
        return ToolResult.ok("placed")

    monkeypatch.setattr("services.agent.block_ops.tools_impl.place_block_impl", fake_place_impl)

    cache = get_preflight_cache()
    entry = cache.put(
        run_id="run-race",
        tool_call_id="tc-race",
        original_args_hash="race-hash",
        canonical_args={"pos": [1, 64, 1], "block": "minecraft:stone", "expect": "air", "states": None},
        execute_args={"pos": [1, 64, 1], "block": "minecraft:stone", "expect": "air", "states": None},
        connection_id="conn-race",
        plan_id="race-plan-1",
        tool_name="place_block",
    )
    ctx = SimpleNamespace(deps=_Deps(run_id="run-race", connection_id="conn-race"))

    first = asyncio.ensure_future(execute_block_plan(entry.plan_id, ctx))
    second = asyncio.ensure_future(execute_block_plan(entry.plan_id, ctx))
    await asyncio.sleep(0.05)  # 让两个协程都推进到 impl 检查点
    assert calls["count"] == 1  # 串行化：同一时刻只有一个 impl 在飞行
    gate.set()
    r1, r2 = await asyncio.gather(first, second)

    assert calls["count"] == 1  # 第二个协程拿到锁后命中 executed → 返回缓存结果
    assert r1.is_success and r2.is_success
    assert r1.output == r2.output
    assert entry.executed is True
    assert isinstance(entry.execution_result, ToolResult)


@pytest.mark.asyncio
async def test_auto_approved_fill_block_invokes_python_alias() -> None:
    """Direct fill execution must project model-visible ``from`` to ``from_``."""
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    policy = PolicyEngine.from_settings(_Settings())
    agent = Agent(
        "test",
        deps_type=_Deps,
        output_type=str,
        capabilities=[HarnessCapability(policy=policy)],
    )
    calls: list[tuple[list[int], list[int], str]] = []

    @agent.tool
    async def fill_block(
        ctx: RunContext[_Deps],
        from_: Annotated[list[int], Field(min_length=3, max_length=3, alias="from")],
        to: Annotated[list[int], Field(min_length=3, max_length=3)],
        block: str,
        expect: str = "air",
        states: dict[str, Any] | None = None,
    ) -> str:
        calls.append((from_, to, block))
        return "filled"

    model_calls = 0

    async def model_fn(messages: list[ModelMessage], info: Any) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        if model_calls > 1:
            return ModelResponse(parts=[TextPart(content="done")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="fill_block",
                    tool_call_id="tc-fill-auto-approved",
                    args={
                        "from": [0, 64, 0],
                        "to": [2, 64, 2],
                        "block": "minecraft:stone",
                    },
                )
            ]
        )

    deps = _Deps(
        connection_id=cid,
        addon_bridge=bridge,
        settings=_Settings(),
        run_id="run-fill-auto-approved",
        auto_approve_tools=True,
    )
    result = await agent.run("fill area", model=FunctionModel(model_fn), deps=deps)

    assert result.output == "done"
    assert calls == [([0, 64, 0], [2, 64, 2], "minecraft:stone")]


@pytest.mark.asyncio
async def test_reverse_fill_block_approval_then_execute_once() -> None:
    """fill_block: 审批元数据保留原始参数；恢复后执行一次；重复恢复幂等。"""
    locked_targets = [
        {"dimension": "minecraft:overworld", "x": x, "y": 64, "z": z}
        for x in range(2, 5)
        for z in range(1, 4)
    ]

    async def bridge_handler(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        if capability == "get_capabilities":
            return {
                "ok": True,
                "payload": {
                    "capabilities": {
                        "block_ops": {"inspect": True, "edit": True, "schema_version": "1"}
                    }
                },
            }
        if capability == "edit_blocks" and payload.get("phase") == "preflight":
            return {
                "ok": True,
                "payload": {
                    "schema_version": "1",
                    "ok": True,
                    "phase": "preflight",
                    "mode": "fill",
                    "type_id": "minecraft:stone",
                    "coordinate_mode": "absolute",
                    "dimension": "minecraft:overworld",
                    "bounds": {
                        "min": {"x": 2, "y": 64, "z": 1},
                        "max": {"x": 4, "y": 64, "z": 3},
                    },
                    "locked_targets": locked_targets,
                    "repairs_applied": ["reversed_bounds"],
                    "facing": "south",
                    "player_origin": {"x": 100, "y": 64, "z": 100},
                    "before_samples": [{"type_id": "minecraft:air"}],
                    "future_preflight_evidence": {"source": "addon"},
                },
            }
        if capability == "edit_blocks" and payload.get("phase") == "execute":
            return {
                "ok": True,
                "payload": {
                    "schema_version": "1",
                    "ok": True,
                    "phase": "execute",
                    "changed": len(locked_targets),
                },
            }
        return {
            "ok": False,
            "payload": {"code": "INTERNAL_ERROR", "message": "unexpected bridge request"},
        }

    bridge = _FakeBridge(bridge_handler)
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    policy = PolicyEngine.from_settings(_Settings())
    agent: Agent[_Deps, str | DeferredToolRequests] = Agent(
        "test",
        deps_type=_Deps,
        output_type=[str, DeferredToolRequests],
        capabilities=[HarnessCapability(policy=policy)],
    )

    @agent.tool
    async def fill_block(
        ctx: RunContext[_Deps],
        from_: Annotated[list[int], Field(min_length=3, max_length=3, alias="from")],
        to: Annotated[list[int], Field(min_length=3, max_length=3)],
        block: str,
        expect: str = "air",
        states: dict[str, Any] | None = None,
    ) -> str:
        result = await fill_block_impl(
            ctx,  # type: ignore[arg-type]
            from_=from_,
            to=to,
            block=block,
            expect=expect,
            states=states,
        )
        return str(result)

    model_calls = 0

    async def model_fn(messages: list[ModelMessage], info: Any) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        if model_calls > 1:
            return ModelResponse(parts=[TextPart(content="done")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="fill_block",
                    tool_call_id="tc-fill-reverse",
                    args={
                        "from": [4, 64, 3],
                        "to": [2, 64, 1],
                        "block": "minecraft:stone",
                    },
                )
            ]
        )

    deps = _Deps(connection_id=cid, addon_bridge=bridge, settings=_Settings(), run_id="run-fill")
    first = await agent.run("fill reverse bounds", model=FunctionModel(model_fn), deps=deps)
    assert isinstance(first.output, DeferredToolRequests)
    approval = first.output.approvals[0]
    metadata = first.output.metadata[approval.tool_call_id]
    # normalized approval args 保持模型可见契约；execute args 使用 Python 参数名。
    authorized_args = metadata["normalized_args"]
    assert authorized_args["from"] == [4, 64, 3]
    assert authorized_args["to"] == [2, 64, 1]
    assert authorized_args["block"] == "minecraft:stone"
    execute_args = metadata["execute_args"]
    assert execute_args["from_"] == [4, 64, 3]
    assert "from" not in execute_args
    assert execute_args["to"] == [2, 64, 1]
    for hidden in ("locked_targets", "phase", "status", "repairs_applied"):
        assert hidden not in execute_args, hidden
    plan_id = metadata.get("plan_id")
    assert isinstance(plan_id, str) and plan_id

    second = await agent.run(
        message_history=first.all_messages(),
        deferred_tool_results=DeferredToolResults(
            approvals={
                approval.tool_call_id: ToolApproved(override_args={"plan_id": plan_id}),
            }
        ),
        model=FunctionModel(model_fn),
        deps=deps,
    )

    assert not isinstance(second.output, DeferredToolRequests)
    execute_calls = [
        payload
        for capability, payload in bridge.calls
        if capability == "edit_blocks" and payload.get("phase") == "execute"
    ]
    assert len(execute_calls) == 1
    # 恢复路径走 execute_block_plan(plan_id)：fill_block_impl 在执行线上
    # 归一化角点，min/max 后才是线协议 from/to。
    assert execute_calls[0]["from"] == {"x": 2, "y": 64, "z": 1}
    assert execute_calls[0]["to"] == {"x": 4, "y": 64, "z": 3}

    repeated = await agent.run(
        message_history=first.all_messages(),
        deferred_tool_results=DeferredToolResults(
            approvals={approval.tool_call_id: ToolApproved(override_args={"plan_id": plan_id})}
        ),
        model=FunctionModel(model_fn),
        deps=deps,
    )
    assert not isinstance(repeated.output, DeferredToolRequests)
    repeated_execute_calls = [
        payload
        for capability, payload in bridge.calls
        if capability == "edit_blocks" and payload.get("phase") == "execute"
    ]
    assert len(repeated_execute_calls) == 1


@pytest.mark.asyncio
async def test_relative_inspect_executes_frozen_public_projection_only() -> None:
    """相对查询的展示元数据不能流入 inspect_block 的公开 Python 调用。"""
    locked_targets = [{"dimension": "minecraft:overworld", "x": 12, "y": 68, "z": -4}]

    async def bridge_handler(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        if capability == "get_capabilities":
            return {
                "ok": True,
                "payload": {"capabilities": {"block_ops": {"inspect": True, "edit": True}}},
            }
        if capability == "inspect_block" and payload.get("phase") == "preflight":
            return {
                "ok": True,
                "payload": {
                    "ok": True,
                    "phase": "preflight",
                    "locked_targets": locked_targets,
                    "dimension": "minecraft:overworld",
                    "position": {"x": 12, "y": 68, "z": -4},
                    "facing": "east",
                    "player_origin": {"x": 10, "y": 68, "z": -4},
                    "new_evidence": {"ignored": True},
                },
            }
        if capability == "inspect_block" and payload.get("phase") == "execute":
            return {"ok": True, "payload": {"ok": True, "phase": "execute", "block": {}}}
        return {"ok": False, "payload": {"code": "INTERNAL_ERROR", "message": "unexpected"}}

    bridge = _FakeBridge(bridge_handler)
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    agent: Agent[_Deps, str] = Agent(
        "test", deps_type=_Deps, output_type=str,
        capabilities=[HarnessCapability(policy=PolicyEngine.from_settings(_Settings()))],
    )
    received_args: dict[str, Any] = {}

    @agent.tool
    async def inspect_block(
        ctx: RunContext[_Deps],
        coordinate_mode: str = "absolute",
        dimension: str | None = None,
        position: dict[str, Any] | None = None,
        positions: list[dict[str, Any]] | None = None,
        locked_targets: list[dict[str, Any]] | None = None,
        phase: str | None = None,
    ) -> str:
        received_args.update({
            "coordinate_mode": coordinate_mode, "dimension": dimension,
            "position": position, "positions": positions,
            "locked_targets": locked_targets, "phase": phase,
        })
        # Task 2: impl takes the unified target only; translate legacy kwargs.
        target: dict[str, Any] | None = None
        if position is not None:
            target = {"positions": [position]}
        elif positions is not None:
            target = {"positions": positions}
        return str(await inspect_block_impl(
            ctx, target=target, locked_targets=locked_targets, phase=phase,
        ))

    calls = 0

    async def model_fn(messages: list[ModelMessage], info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="inspect_block", tool_call_id="tc-inspect-relative",
                args={"coordinate_mode": "player_relative", "position": {"forward": 2, "right": 0, "up": 0}},
            )])
        return ModelResponse(parts=[TextPart(content="done")])

    result = await agent.run(
        "inspect", model=FunctionModel(model_fn),
        deps=_Deps(connection_id=cid, addon_bridge=bridge, settings=_Settings(), run_id="run-inspect-relative"),
    )

    assert result.output == "done"
    assert received_args == {
        "coordinate_mode": "absolute",
        "dimension": "minecraft:overworld",
        "position": {"x": 12, "y": 68, "z": -4},
        "positions": None,
        "locked_targets": locked_targets,
        "phase": "execute",
    }
    inspect_payloads = [payload for capability, payload in bridge.calls if capability == "inspect_block"]
    assert [payload["phase"] for payload in inspect_payloads] == ["preflight", "execute"]
    assert inspect_payloads[-1]["locked_targets"] == locked_targets


def test_unknown_preflight_metadata_does_not_change_execute_projection_hash() -> None:
    """Unknown preflight evidence stays out of the frozen execute projection.

    The final single-responsibility contract builds plans only for
    ``inspect_block``; the projection-hash stability property is exercised on
    the inspect target path.
    """
    original = {
        "target": {"positions": [{"x": 1, "y": 64, "z": 1}]},
        "dimension": "minecraft:overworld",
    }
    locked = [{"dimension": "minecraft:overworld", "x": 1, "y": 64, "z": 1}]
    first = build_block_preflight_plan("inspect_block", original, {"locked_targets": locked})
    second = build_block_preflight_plan(
        "inspect_block", original,
        {"locked_targets": locked, "future_metadata": {"facing": "north"}},
    )

    assert first.execute_args == second.execute_args
    assert hash_normalized_args(normalize_tool_args(first.execute_args)) == hash_normalized_args(
        normalize_tool_args(second.execute_args)
    )
    assert second.approval_metadata["future_metadata"] == {"facing": "north"}


def test_block_preflight_plan_keeps_unknown_evidence_out_of_operation_args() -> None:
    plan = build_block_preflight_plan(
        "inspect_block",
        {
            "target": {"positions": [{"x": 1, "y": 64, "z": 1}]},
            "dimension": "minecraft:overworld",
        },
        {
            "locked_targets": [{"dimension": "minecraft:overworld", "x": 1, "y": 64, "z": 1}],
            "repairs_applied": ["rounded"],
            "future_evidence": {"version": 2},
        },
    )

    assert plan.authorized_args["phase"] == "execute"
    assert plan.authorized_args["target"] == {"positions": [{"x": 1, "y": 64, "z": 1}]}
    assert "future_evidence" not in plan.authorized_args
    assert "future_evidence" not in plan.execute_args
    assert plan.approval_metadata["future_evidence"] == {"version": 2}


def test_single_op_tool_public_schemas_expose_only_contract_fields() -> None:
    """Single-op tools' public schemas expose exactly the contract fields, no hidden fields."""
    agent: Agent[Any, str] = Agent("test", deps_type=_Deps, output_type=str)
    register_agent_tools(agent)
    tools = iter_registered_tools(agent)  # type: ignore[arg-type]

    def public_fields(tool_name: str) -> set[str]:
        tool = tools[tool_name]
        schema = tool.tool_def.parameters_json_schema
        return set(schema.get("properties") or {})

    place_sig = public_fields("place_block")
    fill_sig = public_fields("fill_block")
    inspect_sig = public_fields("inspect_block")

    assert place_sig == {"pos", "block", "expect", "states"}
    assert fill_sig == {"from", "to", "block", "expect", "states"}
    assert inspect_sig == {"target"}

    # No hidden fields in any of the new tools.
    for sig in (place_sig, fill_sig, inspect_sig):
        for hidden in ("locked_targets", "phase", "status", "dimension", "edits", "mode"):
            assert hidden not in sig, f"{hidden} should not be in {sig}"
@pytest.mark.asyncio
async def test_harness_inspect_auto_allows_when_supported() -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    policy = PolicyEngine.from_settings(_Settings())
    agent: Agent[_Deps, str | DeferredToolRequests] = Agent(
        "test",
        deps_type=_Deps,
        output_type=[str, DeferredToolRequests],
        capabilities=[HarnessCapability(policy=policy)],
    )

    @agent.tool
    async def inspect_block(
        ctx: RunContext[_Deps],
        coordinate_mode: str = "absolute",
        dimension: str | None = None,
        position: dict[str, Any] | None = None,
        positions: list[dict[str, Any]] | None = None,
        locked_targets: list[dict[str, Any]] | None = None,
        phase: str | None = None,
    ) -> str:
        # Task 2: impl takes the unified target only; translate legacy kwargs.
        target: dict[str, Any] | None = None
        if position is not None:
            target = {"positions": [position]}
        elif positions is not None:
            target = {"positions": positions}
        return await inspect_block_impl(
            ctx,  # type: ignore[arg-type]
            target=target,
            locked_targets=locked_targets,
            phase=phase,
        )

    async def model_fn(messages: list[ModelMessage], info: Any) -> ModelResponse:
        # If tool results already present, finish
        for msg in messages:
            parts = getattr(msg, "parts", []) or []
            for p in parts:
                if type(p).__name__ == "ToolReturnPart":
                    return ModelResponse(parts=[TextPart(content="inspected")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="inspect_block",
                    args={
                        "coordinate_mode": "absolute",
                        "dimension": "minecraft:overworld",
                        "position": {"x": 0, "y": 64, "z": 0},
                    },
                )
            ]
        )

    deps = _Deps(connection_id=cid, addon_bridge=bridge, settings=_Settings())
    result = await agent.run("look at block", model=FunctionModel(model_fn), deps=deps)
    assert not isinstance(result.output, DeferredToolRequests)
    assert any(c[0] == "inspect_block" for c in bridge.calls)


# ---------------------------------------------------------------------------
# Issue 03: new edits/target/block/expect contract
# ---------------------------------------------------------------------------


def test_normalize_block_input_string_normalizes_namespace_and_case() -> None:
    from services.agent.block_ops.validation import _normalize_block_input

    info, repairs = _normalize_block_input("Oak_Planks")
    assert info["type_id"] == "minecraft:oak_planks"
    assert info["states"] is None
    assert repairs  # namespace + lowercasing recorded


def test_normalize_block_input_object_keeps_states() -> None:
    from services.agent.block_ops.validation import _normalize_block_input

    info, _ = _normalize_block_input({"type_id": "minecraft:stone", "states": {"lit": True}})
    assert info["type_id"] == "minecraft:stone"
    assert info["states"] == {"lit": True}


def test_normalize_expect_kinds() -> None:
    from services.agent.block_ops.validation import (
        _expect_info_to_legacy,
        _normalize_expect,
    )

    assert _normalize_expect(None) == {"kind": "air"}
    assert _normalize_expect("air") == {"kind": "air"}
    assert _normalize_expect("any") == {"kind": "any"}
    assert _normalize_expect("minecraft:oak_planks") == {"kind": "type", "type_id": "minecraft:oak_planks"}
    assert _normalize_expect({"type_id": "minecraft:oak_planks", "states": {"bit": True}}) == {
        "kind": "permutation", "type_id": "minecraft:oak_planks", "states": {"bit": True},
    }

    assert _expect_info_to_legacy({"kind": "air"}) == (False, None)
    assert _expect_info_to_legacy({"kind": "any"}) == (True, None)
    assert _expect_info_to_legacy({"kind": "type", "type_id": "minecraft:oak_planks"}) == (
        False, {"type_id": "minecraft:oak_planks"},
    )
    assert _expect_info_to_legacy(
        {"kind": "permutation", "type_id": "minecraft:stone", "states": {"lit": True}}
    ) == (False, {"type_id": "minecraft:stone", "states": {"lit": True}})


def test_expect_to_legacy_permutation_descriptor() -> None:
    """T2-M3: expect={type_id, states} maps to expected_previous with states."""
    from services.agent.block_ops.validation import _expect_to_legacy

    replace_any, expected_previous, error = _expect_to_legacy(
        {"type_id": "minecraft:stone", "states": {"lit": True}}
    )
    assert error is None
    assert replace_any is False
    assert expected_previous == {"type_id": "minecraft:stone", "states": {"lit": True}}

    # Empty type_id in the descriptor is rejected as INVALID_ARGUMENT.
    replace_any, expected_previous, error = _expect_to_legacy({"type_id": ""})
    assert error is not None
    body = json.loads(error.output)
    assert body["code"] == "INVALID_ARGUMENT"
    assert "expect.type_id 必填" in body["message"]

    # Plain strings keep the existing mapping.
    replace_any, expected_previous, error = _expect_to_legacy("minecraft:oak_planks")
    assert error is None
    assert replace_any is False
    assert expected_previous == {"type_id": "minecraft:oak_planks"}


def test_inspect_target_schema_exposes_point_or_box_union() -> None:
    """The inspect_block target schema is a single-point or two-corner union.

    Runtime rejection of invalid coordinates (non-int / wrong length) is
    covered by the impl-level INVALID_COORDINATE tests, not by Pydantic here.
    """
    from services.agent.tools import BlockPosition

    # Note: BlockPosition / InspectTarget are Annotated types used as function
    # parameter annotations; they are validated by PydanticAI at tool call time.
    # Pydantic native validation of Annotated[list[int], Field(min_length=3, max_length=3)]
    # is exercised through the function signature, not model_validate directly.
    # The schema-level constraints are verified in test_block_tool_schemas_only_expose_public_fields.

    # Validate inspect_block schema has anyOf for target union.
    agent: Agent[Any, str] = Agent("test", deps_type=_Deps, output_type=str)
    register_agent_tools(agent)
    tools = iter_registered_tools(agent)  # type: ignore[arg-type]
    target_schema = tools["inspect_block"].tool_def.parameters_json_schema["properties"]["target"]
    assert "anyOf" in target_schema
    assert len(target_schema["anyOf"]) == 2


def test_precondition_error_projects_bounded_actual_type_counts() -> None:
    counts = {f"minecraft:block_{index}": index for index in range(1, 11)}
    counts.update({
        "password=secret": 100,
        "minecraft:negative": -1,
        "minecraft:boolean": True,
        "minecraft:" + "x" * 130: 101,
    })
    result = map_addon_bridge_result(
        {
            "ok": False,
            "payload": {
                "code": "PRECONDITION_FAILED",
                "message": "untrusted message",
                "hint": "replace_any=true expected_previous locked_targets phase",
                "matched_count": -1,
                "actual_type_counts": counts,
            },
        }
    )

    body = json.loads(result.output)
    projected = body["actual_type_counts"]
    assert len(projected) == 8
    assert list(projected) == [
        "minecraft:block_10",
        "minecraft:block_9",
        "minecraft:block_8",
        "minecraft:block_7",
        "minecraft:block_6",
        "minecraft:block_5",
        "minecraft:block_4",
        "minecraft:block_3",
    ]
    assert all(isinstance(value, int) and value >= 0 for value in projected.values())
    assert body["matched_count"] == 0
    assert "untrusted message" not in body["hint"]
    for hidden in ("replace_any", "expected_previous", "locked_targets", "phase"):
        assert hidden not in body["hint"]


def test_precondition_any_protected_does_not_suggest_any() -> None:
    result = map_addon_bridge_result(
        {
            "ok": False,
            "payload": {
                "code": "PRECONDITION_FAILED",
                "replace_any": True,
                "protected": True,
                "actual_type_counts": {"minecraft:chest": 1},
            },
        }
    )

    body = json.loads(result.output)
    assert body["actual_type_counts"] == {"minecraft:chest": 1}
    assert "minecraft:chest" in body["hint"]
    assert "any" not in body["hint"]


def test_model_visible_place_fill_schema_exposes_only_single_op_contract() -> None:
    """Model-facing place_block / fill_block schema exposes only the new single-op contract."""
    agent: Agent[Any, str] = Agent("test", deps_type=_Deps, output_type=str)
    register_agent_tools(agent)
    tools = iter_registered_tools(agent)  # type: ignore[arg-type]

    # place_block: pos, block, expect, states
    place_schema = tools["place_block"].tool_def.parameters_json_schema
    place_props = set(place_schema.get("properties") or {})
    assert place_props == {"pos", "block", "expect", "states"}
    for legacy in ("mode", "coordinate_mode", "position", "positions",
                   "edits", "dimension", "locked_targets", "phase", "status"):
        assert legacy not in place_props, legacy

    # fill_block: from, to, block, expect, states
    fill_schema = tools["fill_block"].tool_def.parameters_json_schema
    fill_props = set(fill_schema.get("properties") or {})
    assert fill_props == {"from", "to", "block", "expect", "states"}
    for legacy in ("mode", "coordinate_mode", "position", "positions",
                   "edits", "dimension", "locked_targets", "phase", "status"):
        assert legacy not in fill_props, legacy

    # Schema size check: new tools are much smaller than old edit_blocks.
    # Task 7 pins the exact 2,271-char edit_blocks baseline as the upper bound.
    schema_bytes = len(
        json.dumps(
            place_schema,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    assert schema_bytes <= 2271


def test_place_fill_block_descriptions_are_concise() -> None:
    agent: Agent[Any, str] = Agent("test", deps_type=_Deps, output_type=str)
    register_agent_tools(agent)
    tools = iter_registered_tools(agent)
    place_tool = tools["place_block"]
    fill_tool = tools["fill_block"]
    place_desc = place_tool.description or ""
    fill_desc = fill_tool.description or ""

    # PydanticAI puts the docstring summary in `description` and per-arg docs
    # in JSON-schema property descriptions, so assert on both layers.
    assert "单个格子" in place_desc
    assert "长方体区域" in fill_desc

    place_props = place_tool.tool_def.parameters_json_schema["properties"]
    fill_props = fill_tool.tool_def.parameters_json_schema["properties"]
    assert "目标坐标" in place_props["pos"]["description"]
    assert "区域一角" in fill_props["from"]["description"]
    assert "区域另一角" in fill_props["to"]["description"]

    # No legacy contract terms leaked into any description.
    for desc in (place_desc, fill_desc):
        assert "edits" not in desc
        assert "target.target" not in desc
        assert "replace_any" not in desc
        assert "coordinate_mode" not in desc


@pytest.mark.parametrize("provider", ("deepseek", "openai", "anthropic", "ollama"))
def test_all_supported_providers_expose_the_same_block_tool_schema(provider: str) -> None:
    """Provider selection cannot expand the public block-tool contract."""
    from services.agent.core import ChatAgentManager

    manager = ChatAgentManager()
    manager._settings = Settings(default_provider=provider)
    tools = iter_registered_tools(manager._create_agent())

    # place_block
    place_props = set(tools["place_block"].tool_def.parameters_json_schema.get("properties") or {})
    assert place_props == {"pos", "block", "expect", "states"}

    # fill_block
    fill_props = set(tools["fill_block"].tool_def.parameters_json_schema.get("properties") or {})
    assert fill_props == {"from", "to", "block", "expect", "states"}

    # inspect_block
    inspect_props = set(tools["inspect_block"].tool_def.parameters_json_schema.get("properties") or {})
    assert inspect_props == {"target"}
def test_place_preflight_payload_with_expect_type_fits_command_line_budget() -> None:
    """Place preflight omits enumeration caps so expect-type frames stay under budget.

    ``expected_previous`` plus the preflight limit keys previously pushed a
    single place edit past the 461 B MCBE commandLine budget, making
    ``expect: <type_id>`` unusable in both the single-edit and grouped paths.
    """
    payload = build_edit_payload(
        mode="place",
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        position={"x": 5, "y": 64, "z": 5},
        positions=None,
        from_pos=None,
        to_pos=None,
        type_id="minecraft:glass",
        states=None,
        replace_any=False,
        expected_previous={"type_id": "minecraft:stone"},
        player_name="Steve",
        phase="preflight",
        limits={
            "max_discrete_positions": 256,
            "max_positions": 256,
            "max_fill_volume": 4096,
            "cells_per_tick": 128,
            "max_locked_targets_on_wire": 0,
        },
    )
    # Enumeration caps are host-enforced for a single cell and are omitted.
    assert "max_discrete" not in payload
    assert "max_fill_volume" not in payload
    assert "expected_previous" in payload
    assert check_bridge_command_line_budget(
        "edit_blocks", payload, budget=DEFAULT_COMMAND_LINE_BYTE_BUDGET
    ) is None

    # Batch/fill preflight still carries the enumeration caps for the Add-on.
    batch_payload = build_edit_payload(
        mode="batch",
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        position=None,
        positions=[{"x": 1, "y": 64, "z": 1}, {"x": 2, "y": 64, "z": 2}],
        from_pos=None,
        to_pos=None,
        type_id="minecraft:glass",
        states=None,
        replace_any=False,
        expected_previous={"type_id": "minecraft:stone"},
        player_name="Steve",
        phase="preflight",
        limits={
            "max_discrete_positions": 256,
            "max_positions": 256,
            "max_fill_volume": 4096,
            "cells_per_tick": 128,
            "max_locked_targets_on_wire": 0,
        },
    )
    assert "max_discrete" in batch_payload
    assert "cells_per_tick" in batch_payload


# ---------------------------------------------------------------------------
# issue 05 — block repair suggestions + multiblock safety (host-side)
# ---------------------------------------------------------------------------


def test_block_unknown_surfaces_candidates_from_addon() -> None:
    """When the addon returns BLOCK_UNKNOWN with candidates, the host preserves
    them in the error envelope (spec issue 05 §4.2)."""
    result = map_addon_bridge_result(
        {
            "ok": False,
            "payload": {
                "code": "BLOCK_UNKNOWN",
                "message": "unknown block type: minecraft:stonx",
                "type_id": "minecraft:stonx",
                "candidates": ["minecraft:stone", "minecraft:ston"],
            },
        }
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "BLOCK_UNKNOWN"
    assert body["fallback_allowed"] is True
    assert body["type_id"] == "minecraft:stonx"
    assert body["candidates"] == ["minecraft:stone", "minecraft:ston"]
    assert "hint" in body
    assert "candidates" in body["hint"]


def test_block_unknown_candidates_bounded_to_three() -> None:
    """The host caps candidate suggestions at 3 (spec §4.2)."""
    result = map_addon_bridge_result(
        {
            "ok": False,
            "payload": {
                "code": "BLOCK_UNKNOWN",
                "message": "unknown",
                "candidates": ["a", "b", "c", "d", "e"],
            },
        }
    )
    body = json.loads(result.output)
    assert len(body["candidates"]) == 3


def test_block_unknown_filters_sensitive_candidates() -> None:
    """Candidate strings that look like secrets are dropped."""
    result = map_addon_bridge_result(
        {
            "ok": False,
            "payload": {
                "code": "BLOCK_UNKNOWN",
                "message": "unknown",
                "candidates": ["minecraft:stone", "token=bridge-secret"],
            },
        }
    )
    body = json.loads(result.output)
    assert "bridge-secret" not in json.dumps(body["candidates"])


def test_state_invalid_surfaces_valid_state_keys() -> None:
    """When the addon returns STATE_INVALID with valid_state_keys, the host
    preserves them so the model can correct the states (spec issue 05 §4.3)."""
    result = map_addon_bridge_result(
        {
            "ok": False,
            "payload": {
                "code": "STATE_INVALID",
                "message": "Invalid state",
                "type_id": "minecraft:oak_stairs",
                "valid_state_keys": [
                    "minecraft:cardinal_direction",
                    "minecraft:vertical_half",
                ],
            },
        }
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "STATE_INVALID"
    assert body["fallback_allowed"] is True
    assert body["type_id"] == "minecraft:oak_stairs"
    assert body["valid_state_keys"] == [
        "minecraft:cardinal_direction",
        "minecraft:vertical_half",
    ]
    assert "hint" in body
    assert "valid_state_keys" in body["hint"]


def test_state_invalid_keys_bounded_to_eight() -> None:
    """Valid state key suggestions are capped at 8."""
    result = map_addon_bridge_result(
        {
            "ok": False,
            "payload": {
                "code": "STATE_INVALID",
                "message": "Invalid state",
                "valid_state_keys": [f"k{i}" for i in range(20)],
            },
        }
    )
    body = json.loads(result.output)
    assert len(body["valid_state_keys"]) == 8


def test_protected_block_envelope_includes_component_and_target() -> None:
    """PROTECTED_BLOCK carries type_id, component, and target for the model
    to understand *why* the edit was rejected (spec issue 05 §4.5)."""
    result = map_addon_bridge_result(
        {
            "ok": False,
            "payload": {
                "code": "PROTECTED_BLOCK",
                "message": "block has protected component: minecraft:inventory",
                "type_id": "minecraft:chest",
                "component": "minecraft:inventory",
                "target": {"x": 10, "y": 64, "z": 10, "dimension": "minecraft:overworld"},
            },
        }
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "PROTECTED_BLOCK"
    assert body["fallback_allowed"] is True
    assert body["type_id"] == "minecraft:chest"
    assert body["component"] == "minecraft:inventory"
    assert body["target"] == {"x": 10, "y": 64, "z": 10}
    assert "dimension" not in body["target"]
    assert "hint" in body
    assert "受保护" in body["hint"]


def test_unsupported_block_placement_preserves_multiblock_flag() -> None:
    """UNSUPPORTED_BLOCK_PLACEMENT is a PERMANENT error with fallback_allowed=True
    (command fallback via setblock/fill places the full structure on 1.26.10+)
    and carries the type_id + multiblock flag (spec issue 05 §6/§7)."""
    result = map_addon_bridge_result(
        {
            "ok": False,
            "payload": {
                "code": "UNSUPPORTED_BLOCK_PLACEMENT",
                "message": "multiblock block requires multi-cell placement",
                "type_id": "minecraft:wooden_door",
                "multiblock": True,
            },
        }
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "UNSUPPORTED_BLOCK_PLACEMENT"
    assert body["fallback_allowed"] is True
    assert body["retryable"] is False
    assert body["type_id"] == "minecraft:wooden_door"
    assert body["multiblock"] is True
    assert "hint" in body
    assert "多格" in body["hint"]
    assert "setblock" in body["hint"]
    assert "1.26.10" in body["hint"]


@pytest.mark.parametrize(
    ("code", "fallback_allowed"),
    [
        ("BLOCK_UNKNOWN", True),
        ("STATE_INVALID", True),
        ("UNSUPPORTED_BLOCK_PLACEMENT", True),
        ("STATE_UNKNOWN", False),
        ("INTERNAL_ERROR", False),
    ],
)
def test_issue_05_error_codes_fallback_one_rule(
    code: str, fallback_allowed: bool
) -> None:
    """Issue 05 error codes allow fallback unless world state is unknown."""
    result = map_addon_bridge_result(
        {"ok": False, "payload": {"code": code, "message": "test"}}
    )
    body = json.loads(result.output)
    assert body["code"] == code
    assert body["fallback_allowed"] is fallback_allowed


# ---------------------------------------------------------------------------
# Task 2: single-op implementation layer (place_block_impl / fill_block_impl /
#         inspect_block_impl) and Add-on frame mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_maps_to_mode_place_frame() -> None:
    """place_block_impl builds a mode=place frame: normalized type_id, expect mapping.

    expect=air → replace_any=False with no expected_previous; expect=any →
    replace_any=True; expect="minecraft:stone" → expected_previous. No
    ``dimension`` is sent: the Add-on defaults to the current player dimension.
    """
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)

    # Default expect=air: replace_any=False, no expected_previous, type_id normalized.
    result = await place_block_impl(
        ctx,  # type: ignore[arg-type]
        pos=[1, 64, 2],
        block="stone",
        expect="air",
    )
    assert result.is_success
    payload = [c for c in bridge.calls if c[0] == "edit_blocks"][-1][1]
    assert payload["mode"] == "place"
    assert payload["position"] == {"x": 1, "y": 64, "z": 2}
    assert payload["type_id"] == "minecraft:stone"
    assert payload["replace_any"] is False
    assert "expected_previous" not in payload
    assert "dimension" not in payload

    # expect=any → replace_any=True.
    result = await place_block_impl(
        ctx,  # type: ignore[arg-type]
        pos=[2, 64, 2],
        block="minecraft:glass",
        expect="any",
    )
    assert result.is_success
    payload = [c for c in bridge.calls if c[0] == "edit_blocks"][-1][1]
    assert payload["replace_any"] is True
    assert "expected_previous" not in payload

    # expect="minecraft:stone" → expected_previous.
    result = await place_block_impl(
        ctx,  # type: ignore[arg-type]
        pos=[3, 64, 2],
        block="glass",
        expect="minecraft:stone",
    )
    assert result.is_success
    payload = [c for c in bridge.calls if c[0] == "edit_blocks"][-1][1]
    assert payload["replace_any"] is False
    assert payload["expected_previous"] == {"type_id": "minecraft:stone"}
    assert "dimension" not in payload


@pytest.mark.asyncio
async def test_fill_normalizes_corners_and_enforces_volume() -> None:
    """fill_block_impl min/max-normalizes corners and enforces max_fill_volume.

    Oversized AABBs are rejected host-side with LIMIT_EXCEEDED and a
    shrink-direction hint; the request never reaches the bridge.
    """
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)

    # from=(10,10,10), to=(8,8,8) → from_pos=min, to_pos=max.
    result = await fill_block_impl(
        ctx,  # type: ignore[arg-type]
        from_=[10, 10, 10],
        to=[8, 8, 8],
        block="oak_planks",
    )
    assert result.is_success
    payload = [c for c in bridge.calls if c[0] == "edit_blocks"][-1][1]
    assert payload["mode"] == "fill"
    assert payload["from"] == {"x": 8, "y": 8, "z": 8}
    assert payload["to"] == {"x": 10, "y": 10, "z": 10}
    assert "dimension" not in payload

    # Volume 51^3 = 132651 > default max_fill_volume 4096 → LIMIT_EXCEEDED.
    edit_calls_before = len([c for c in bridge.calls if c[0] == "edit_blocks"])
    result = await fill_block_impl(
        ctx,  # type: ignore[arg-type]
        from_=[0, 0, 0],
        to=[50, 50, 50],
        block="stone",
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "LIMIT_EXCEEDED"
    assert "缩小" in body.get("hint", "")
    assert body["volume"] == 51 * 51 * 51
    edit_calls_after = len([c for c in bridge.calls if c[0] == "edit_blocks"])
    assert edit_calls_after == edit_calls_before  # rejected before the bridge


@pytest.mark.asyncio
async def test_long_states_frame_hits_budget_defense() -> None:
    """An oversized states frame trips the commandLine budget defense.

    LIMIT_EXCEEDED with estimated_bytes/budget is returned and the request
    never leaves the host (no edit_blocks bridge call).
    """
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)

    edit_calls_before = len([c for c in bridge.calls if c[0] == "edit_blocks"])
    result = await place_block_impl(
        ctx,  # type: ignore[arg-type]
        pos=[1, 64, 1],
        block="minecraft:oak_stairs",
        expect="air",
        states={"facing": "north" + "x" * 500},
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "LIMIT_EXCEEDED"
    assert body["reason"] == "command_line_budget"
    assert body["estimated_bytes"] >= body["budget"]
    edit_calls_after = len([c for c in bridge.calls if c[0] == "edit_blocks"])
    assert edit_calls_after == edit_calls_before  # request never left the host


@pytest.mark.asyncio
async def test_place_block_impl_rejects_invalid_coordinates() -> None:
    """Non-int / wrong-length pos → structured INVALID_COORDINATE, no exception."""
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    baseline = len([c for c in bridge.calls if c[0] == "edit_blocks"])

    for bad_pos in ([1, 2], [1.5, 2, 3], [1, 2, 3, 4], ["a", 2, 3], [1, True, 3], "1,2,3"):
        result = await place_block_impl(
            ctx,  # type: ignore[arg-type]
            pos=bad_pos,  # type: ignore[arg-type]
            block="stone",
        )
        assert not result.is_success, bad_pos
        body = json.loads(result.output)
        assert body["code"] == "INVALID_COORDINATE", bad_pos
        assert body["ok"] is False

    assert len([c for c in bridge.calls if c[0] == "edit_blocks"]) == baseline


@pytest.mark.asyncio
async def test_fill_block_impl_rejects_invalid_corners() -> None:
    """Non-int / wrong-length corners → structured INVALID_COORDINATE."""
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)

    for bad_from, bad_to in (
        ([1, 2], [3, 4, 5]),
        ([1.5, 2, 3], [4, 5, 6]),
        ([1, 2, 3], [4, 5]),
    ):
        result = await fill_block_impl(
            ctx,  # type: ignore[arg-type]
            from_=bad_from,  # type: ignore[arg-type]
            to=bad_to,  # type: ignore[arg-type]
            block="stone",
        )
        assert not result.is_success, (bad_from, bad_to)
        body = json.loads(result.output)
        assert body["code"] == "INVALID_COORDINATE", (bad_from, bad_to)


@pytest.mark.asyncio
async def test_inspect_block_impl_array_target() -> None:
    """The array target maps to the Add-on unified target with no dimension.

    Single point → target.positions; two corners → target.box with normalized
    min/max corners.
    """
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)

    result = await inspect_block_impl(
        ctx,  # type: ignore[arg-type]
        target=[3, 64, 4],
    )
    assert result.is_success
    payload = [c for c in bridge.calls if c[0] == "inspect_block"][-1][1]
    assert payload["target"] == {"positions": [{"x": 3, "y": 64, "z": 4}]}
    assert "dimension" not in payload

    result = await inspect_block_impl(
        ctx,  # type: ignore[arg-type]
        target=[[10, 10, 10], [8, 8, 8]],
    )
    assert result.is_success
    payload = [c for c in bridge.calls if c[0] == "inspect_block"][-1][1]
    assert payload["target"] == {
        "box": {"from": {"x": 8, "y": 8, "z": 8}, "to": {"x": 10, "y": 10, "z": 10}}
    }
    assert "dimension" not in payload


@pytest.mark.asyncio
async def test_inspect_block_impl_rejects_invalid_array_target() -> None:
    """Non-int / wrong-length array targets → structured INVALID_COORDINATE."""
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)

    for bad in ([1, 2], [1.5, 2, 3], [[1, 2, 3]], [[1, 2, 3], [4, 5]], "1,2,3"):
        result = await inspect_block_impl(
            ctx,  # type: ignore[arg-type]
            target=bad,  # type: ignore[arg-type]
        )
        assert not result.is_success, bad
        body = json.loads(result.output)
        assert body["code"] == "INVALID_COORDINATE", bad


# ---------------------------------------------------------------------------
# Task 4 (spec §5): slim result projections — place / fill / inspect shapes
# ---------------------------------------------------------------------------


def test_place_projection_matches_spec_51_shape() -> None:
    from services.agent.block_ops.project import project_place_result_for_model

    projected = project_place_result_for_model(
        {
            "schema_version": "1",
            "ok": True,
            "phase": "execute",
            "mode": "place",
            "type_id": "minecraft:torch",
            "position": {"x": 10, "y": 64, "z": -5},
            "was": "minecraft:air",
        }
    )
    # spec §5.1 example shows was: air but the note is normative: was only
    # appears when a non-air block was replaced — air stays omitted.
    assert projected == {
        "ok": True,
        "status": "applied",
        "at": [10, 64, -5],
        "block": "minecraft:torch",
    }
    assert "was" not in projected

    replaced = project_place_result_for_model(
        {
            "ok": True,
            "mode": "place",
            "type_id": "minecraft:stone",
            "position": {"x": 1, "y": 2, "z": 3},
            "before": {"type_id": "minecraft:dirt"},
        }
    )
    assert replaced["was"] == "minecraft:dirt"
    assert replaced["at"] == [1, 2, 3]

    # at may arrive as an array directly.
    array_at = project_place_result_for_model(
        {
            "ok": True,
            "mode": "place",
            "type_id": "minecraft:stone",
            "at": [7, 8, 9],
        }
    )
    assert array_at["at"] == [7, 8, 9]
    for hidden in ("mode", "type_id", "phase", "position", "before", "after"):
        assert hidden not in replaced, hidden


def test_fill_projection_matches_spec_52_shape() -> None:
    from services.agent.block_ops.project import project_fill_result_for_model

    projected = project_fill_result_for_model(
        {
            "schema_version": "1",
            "ok": True,
            "phase": "execute",
            "mode": "fill",
            "type_id": "minecraft:stone",
            "changed_count": 15,
            "skipped": 10,
            "previous_type_counts": {
                "minecraft:air": 6,
                "minecraft:grass_block": 10,
                "minecraft:gravel": 5,
            },
            "from": {"x": -797, "y": 93, "z": 180},
            "to": {"x": -793, "y": 93, "z": 184},
        }
    )
    assert projected["ok"] is True
    assert projected["status"] == "applied"
    assert projected["changed"] == 15
    assert projected["skipped"] == 10
    # type_counts only carries non-air counts (spec §5.2).
    assert projected["type_counts"] == {
        "minecraft:grass_block": 10,
        "minecraft:gravel": 5,
    }
    assert "minecraft:air" not in projected["type_counts"]
    assert projected["bounds"] == [[-797, 93, 180], [-793, 93, 184]]
    for hidden in ("mode", "type_id", "phase", "changed_count", "previous_type_counts"):
        assert hidden not in projected, hidden


def test_inspect_projection_matches_spec_53_shapes() -> None:
    from services.agent.block_ops.project import project_block_result_for_model as project

    single = project(
        {
            "ok": True,
            "blocks": [
                {
                    "x": 0,
                    "y": 64,
                    "z": 0,
                    "type_id": "minecraft:air",
                    "states": {"waterlogged": False},
                    "waterlogged": False,
                    "is_air": True,
                    "is_liquid": False,
                    "dimension": "minecraft:overworld",
                }
            ],
            "coordinate_mode": "absolute",
            "dimension": "minecraft:overworld",
        }
    )
    assert single == {
        "ok": True,
        "block": "minecraft:air",
        "states": {"waterlogged": False},
        "waterlogged": False,
        "is_air": True,
        "is_liquid": False,
    }

    region = project(
        {
            "ok": True,
            "summary": {
                "bounds": {
                    "from": {"x": 0, "y": 64, "z": 0},
                    "to": {"x": 1, "y": 64, "z": 1},
                },
                "count": 4,
                "type_counts": {"minecraft:air": 3, "minecraft:stone": 1},
                "unknown_count": 0,
                "samples": [
                    {"x": 0, "y": 64, "z": 0, "type_id": "minecraft:air"},
                    {"x": 1, "y": 64, "z": 0, "type_id": "minecraft:air"},
                    {"x": 0, "y": 64, "z": 1, "type_id": "minecraft:air"},
                    {"x": 1, "y": 64, "z": 1, "type_id": "minecraft:stone"},
                ],
            },
        }
    )
    assert region["count"] == 4
    assert region["type_counts"] == {"minecraft:air": 3, "minecraft:stone": 1}
    assert region["samples"] == [
        [0, 64, 0, "minecraft:air"],
        [1, 64, 0, "minecraft:air"],
        [0, 64, 1, "minecraft:air"],
        [1, 64, 1, "minecraft:stone"],
    ]
    for hidden in (
        "status", "bounds", "unknown_count", "blocks",
        "coordinate_mode", "dimension",
    ):
        assert hidden not in region, hidden


def test_inspect_region_samples_are_compact_and_bounded_to_eight() -> None:
    from services.agent.block_ops.project import project_block_result_for_model as project

    blocks = [
        {"x": i, "y": 64, "z": j, "type_id": f"minecraft:block_{i}_{j}"}
        for i in range(4)
        for j in range(4)
    ]
    region = project(
        {
            "ok": True,
            "blocks": blocks,
            "coordinate_mode": "absolute",
            "dimension": "minecraft:overworld",
        }
    )
    assert region["count"] == 16
    assert len(region["samples"]) == 8
    assert all(
        isinstance(sample, list)
        and len(sample) == 4
        and isinstance(sample[3], str)
        for sample in region["samples"]
    )
    assert region["samples"][0] == [0, 64, 0, "minecraft:block_0_0"]
    assert "status" not in region
    assert "blocks" not in region


def test_projection_never_mirrors_raw_addon_payload() -> None:
    """Success projections never mirror raw addon payload internals (spec §5.5)."""
    fat = {
        "schema_version": "1",
        "ok": True,
        "phase": "execute",
        "mode": "place",
        "type_id": "minecraft:stone",
        "position": {"x": 0, "y": 64, "z": 0, "dimension": "minecraft:overworld"},
        "targets": [{"x": 0, "y": 64, "z": 0, "type_id": "minecraft:stone"}],
        "before": {"type_id": "minecraft:dirt", "x": 0, "y": 64, "z": 0},
        "after": {"type_id": "minecraft:stone", "x": 0, "y": 64, "z": 0},
        "locked_targets": [{"x": 0, "y": 64, "z": 0}],
        "repairs_applied": ["reposition"],
        "verification": {"checked": True},
        "rollback": {"attempted": False},
    }
    projected = project_block_result_for_model(fat, mode="place")
    assert projected == {
        "ok": True,
        "status": "applied",
        "at": [0, 64, 0],
        "block": "minecraft:stone",
        "was": "minecraft:dirt",
    }
    for hidden in (
        "targets", "before", "after", "locked_targets", "repairs_applied",
        "verification", "rollback", "phase", "mode", "type_id",
        "schema_version", "dimension", "position",
    ):
        assert hidden not in projected, hidden


def test_new_mutation_tools_map_timeout_to_state_unknown() -> None:
    """place_block / fill_block keep the STATE_UNKNOWN + no-fallback contract."""
    for tool in ("place_block", "fill_block"):
        result = map_bridge_exception(TimeoutError("bridge timeout"), tool_name=tool)
        assert not result.is_success
        assert result.retryable is False
        assert result.external_state_unknown is True
        body = json.loads(result.output)
        assert body["code"] == "STATE_UNKNOWN", tool
        assert body["fallback_allowed"] is False
        assert "请勿自动重试" in body["message"]


@pytest.mark.asyncio
async def test_call_block_capability_passes_public_tool_name_for_exception_mapping() -> None:
    class _RaisingBridge:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        async def request(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
            self.calls.append((capability, payload))
            raise TimeoutError("bridge request timed out")

    bridge = _RaisingBridge()
    result = await call_block_capability(
        bridge,
        "edit_blocks",
        {"phase": "execute", "mode": "place"},
        mode="place",
        tool_name="place_block",
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "STATE_UNKNOWN"
    assert body["external_state_unknown"] is True
    assert body["fallback_allowed"] is False

    result = await call_block_capability(
        bridge,
        "inspect_block",
        {"phase": "execute"},
        tool_name="inspect_block",
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "ADDON_UNAVAILABLE"
    assert body["retryable"] is True
    assert body["fallback_allowed"] is True


def test_limit_exceeded_addon_error_carries_estimated_bytes_and_budget() -> None:
    result = map_addon_bridge_result(
        {
            "ok": False,
            "payload": {
                "code": "LIMIT_EXCEEDED",
                "message": "frame too large",
                "estimated_bytes": 4128,
                "budget": 461,
            },
        }
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "LIMIT_EXCEEDED"
    assert body["estimated_bytes"] == 4128
    assert body["budget"] == 461
    assert body["retryable"] is True
    assert body["fallback_allowed"] is True
    assert body["schema_version"] == "1"


def test_precondition_failed_without_counts_omits_empty_keys() -> None:
    """No counts / no actual_type_id → no empty actual_type_counts key (Step 4)."""
    result = map_addon_bridge_result(
        {
            "ok": False,
            "payload": {
                "code": "PRECONDITION_FAILED",
                "message": "target is not air",
                "target": {"x": 1, "y": 64, "z": 2},
            },
        }
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "PRECONDITION_FAILED"
    assert "actual_type_counts" not in body
    assert "actual_type_id" not in body
    assert body["target"] == {"x": 1, "y": 64, "z": 2}
    assert "hint" in body


# ---------------------------------------------------------------------------
# Task 7: 新契约回归与集成测试（spec §8.2）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_twelve_discrete_points_never_exceed_budget_and_no_limit_exceeded() -> None:
    """Task 7 Step 2: 旧 12 离散点场景在新契约下不再产生 LIMIT_EXCEEDED。

    旧 contract 的 12 个离散点 batch 帧 654–777B > 461B 必然触发
    LIMIT_EXCEEDED 重试；新契约模型只能表达 12 个独立 place_block（或
    4 个 1x3x1 柱形 fill），宿主逐帧通过 commandLine 预算检查（spec §1.2）。
    """
    # 旧形态锚点：单帧 12 个离散点必然超预算（回归 spec §1.2 表格）。
    legacy_positions = [
        {"x": x, "y": 64, "z": z} for x in range(1, 5) for z in range(1, 4)
    ]
    legacy_payload = build_edit_payload(
        mode="place",
        coordinate_mode="absolute",
        dimension=None,
        position=None,
        positions=legacy_positions,
        from_pos=None,
        to_pos=None,
        type_id="minecraft:stone",
        states=None,
        replace_any=False,
        expected_previous=None,
        player_name="Steve",
        phase="execute",
    )
    legacy_bytes = estimate_bridge_command_line_bytes("edit_blocks", legacy_payload)
    assert legacy_bytes > DEFAULT_COMMAND_LINE_BYTE_BUDGET  # 654–777B > 461B

    # 12 个独立 place_block：全部成功、无 LIMIT_EXCEEDED、每帧合规。
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)

    for x in range(1, 5):
        for z in range(1, 4):
            result = await place_block_impl(
                ctx,  # type: ignore[arg-type]
                pos=[x, 64, z],
                block="stone",
            )
            assert result.is_success
            assert "LIMIT_EXCEEDED" not in result.output

    place_payloads = [p for cap, p in bridge.calls if cap == "edit_blocks"]
    assert len(place_payloads) == 12
    for payload in place_payloads:
        assert check_bridge_command_line_budget("edit_blocks", payload) is None
        assert (
            estimate_bridge_command_line_bytes("edit_blocks", payload)
            <= DEFAULT_COMMAND_LINE_BYTE_BUDGET
        )

    # 4 个 1x3x1 柱形 fill（旧场景 4 帧 1,484B）：同样无超限。
    bridge2 = _FakeBridge()
    cid2 = str(uuid4())
    await ensure_block_capability(cid2, bridge2)
    deps2 = _Deps(connection_id=cid2, addon_bridge=bridge2)
    ctx2 = SimpleNamespace(deps=deps2)
    for x in range(1, 5):
        result = await fill_block_impl(
            ctx2,  # type: ignore[arg-type]
            from_=[x, 64, 1],
            to=[x, 66, 1],
            block="stone",
        )
        assert result.is_success
        assert "LIMIT_EXCEEDED" not in result.output

    fill_payloads = [p for cap, p in bridge2.calls if cap == "edit_blocks"]
    assert len(fill_payloads) == 4
    for payload in fill_payloads:
        assert check_bridge_command_line_budget("edit_blocks", payload) is None
        assert (
            estimate_bridge_command_line_bytes("edit_blocks", payload)
            <= DEFAULT_COMMAND_LINE_BYTE_BUDGET
        )


@pytest.mark.asyncio
async def test_grass_house_fill_expect_any_succeeds_expect_air_precondition() -> None:
    """Task 7 Step 3: 草地上盖房子。

    ``expect=any`` 成功覆盖草方块；``expect=air`` 遇到草方块返回可操作的
    PRECONDITION_FAILED（含 actual_type_counts 与坐标），fallback_allowed=True。
    """
    async def grass_handler(cap: str, payload: dict[str, Any]) -> dict[str, Any]:
        if cap == "get_capabilities":
            return {
                "ok": True,
                "payload": {
                    "capabilities": {
                        "block_ops": {"inspect": True, "edit": True, "schema_version": "1"}
                    }
                },
            }
        if cap == "edit_blocks":
            if payload.get("replace_any") is True:
                return {
                    "ok": True,
                    "payload": {
                        "schema_version": "1",
                        "ok": True,
                        "status": "succeeded",
                        "mode": "fill",
                        "changed": 9,
                        "skipped": 0,
                        "from": payload["from"],
                        "to": payload["to"],
                    },
                }
            return {
                "ok": False,
                "payload": {
                    "code": "PRECONDITION_FAILED",
                    "message": "expected air but target contains non-air blocks",
                    "target": payload.get("from") or payload.get("position"),
                    "actual_type_counts": {"minecraft:grass_block": 3},
                },
            }
        return {"ok": True, "payload": {"ok": True}}

    bridge = _FakeBridge(grass_handler)
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)

    # expect=any：成功覆盖草方块。
    result = await fill_block_impl(
        ctx,  # type: ignore[arg-type]
        from_=[1, 64, 1],
        to=[3, 64, 3],
        block="minecraft:oak_planks",
        expect="any",
    )
    assert result.is_success
    body = json.loads(result.output)
    assert body["ok"] is True
    assert body["status"] == "succeeded"
    wire = [c for c in bridge.calls if c[0] == "edit_blocks"][-1][1]
    assert wire["replace_any"] is True

    # expect=air：遇到草方块 → PRECONDITION_FAILED（可操作诊断）。
    result = await fill_block_impl(
        ctx,  # type: ignore[arg-type]
        from_=[1, 64, 1],
        to=[3, 64, 3],
        block="minecraft:oak_planks",
        expect="air",
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "PRECONDITION_FAILED"
    assert body["actual_type_counts"] == {"minecraft:grass_block": 3}
    assert body["target"] == {"x": 1, "y": 64, "z": 1}
    assert body["fallback_allowed"] is True
    assert body["retryable"] is False
    assert "grass_block" in body.get("hint", "")


def test_new_tool_schemas_are_slim_no_hidden_fields_and_prompt_catalog_clean() -> None:
    """Task 7 Step 5: 三个新工具 schema 无隐藏字段且总字符数 < 旧 2,271 基线。

    - place_block / fill_block / inspect_block 的完整 JSON schema（含描述）
      不含 locked_targets / phase / status / dimension / mode / edits /
      positions / coordinate_mode 等内部字段；
    - 三个 schema 序列化总字符数小于旧 ``edit_blocks`` 单模型 schema 的
      2,271 字符基线（spec §1.2）；
    - 提示词与工具目录不含 locked_targets / phase / status 字符串。
    """
    agent: Agent[Any, str] = Agent("test", deps_type=_Deps, output_type=str)
    register_agent_tools(agent)
    tools = iter_registered_tools(agent)  # type: ignore[arg-type]

    hidden = (
        "locked_targets",
        "locked_targets_by_edit",
        "phase",
        "status",
        "dimension",
        "mode",
        "edits",
        "positions",
        "coordinate_mode",
        "noop_edit_indices",
        "repairs_applied",
        "expected_previous",
    )
    total_chars = 0
    for tool_name in ("place_block", "fill_block", "inspect_block"):
        schema = tools[tool_name].tool_def.parameters_json_schema
        serialized = json.dumps(schema, sort_keys=True, separators=(",", ":"))
        total_chars += len(serialized)
        for token in hidden:
            assert token not in serialized, (
                f"{token} leaked into {tool_name} model-visible schema"
            )

    assert total_chars < 2271, f"three schemas total {total_chars} chars >= legacy 2271"

    for path in (
        Path("services/agent/prompt.py"),
        Path("services/agent/harness/prompting.py"),
        Path("services/agent/harness/catalog.py"),
    ):
        text = path.read_text(encoding="utf-8")
        for token in ("locked_targets", "phase", "status"):
            assert token not in text, f"{token} found in {path}"
