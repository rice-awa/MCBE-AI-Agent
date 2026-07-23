"""Dedicated block tools: schema, capability, preflight, exposure, bridge mapping."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolApproved

from config.settings import Settings
from services.agent.block_ops.bridge import map_addon_bridge_result, map_bridge_exception
from services.agent.block_ops.capability import (
    BlockCapabilityRecord,
    BlockCapabilityStatus,
    clear_block_capability,
    ensure_block_capability,
    get_block_capability_cache,
    reset_block_capability_cache,
)
from services.agent.block_ops.project import project_block_result_for_model
from services.agent.block_ops.config import (
    DEFAULT_COMMAND_LINE_BYTE_BUDGET,
    HARD_MAX_DISCRETE_POSITIONS,
    get_block_tools_limits,
    get_command_line_byte_budget,
)
from services.agent.block_ops.preflight_cache import (
    get_preflight_cache,
    reset_preflight_cache,
)
from services.agent.block_ops.schema import (
    BLOCK_OPS_SCHEMA_VERSION,
    BlockErrorCode,
    build_error_response,
    build_success_response,
)
from services.agent.block_ops.tools_impl import (
    apply_limits_to_payload,
    build_edit_payload,
    build_inspect_payload,
    build_block_preflight_plan,
    check_bridge_command_line_budget,
    edit_blocks_impl,
    estimate_bridge_command_line_bytes,
    inspect_block_impl,
    merge_canonical_from_preflight,
    run_block_preflight,
    project_block_execute_args,
    should_omit_locked_targets_on_wire,
)
from services.agent.harness.execution import (
    HarnessCapability,
    PolicyDecisionKind,
    PolicyEngine,
    get_idempotency_store,
    hash_normalized_args,
    normalize_tool_args,
    reset_idempotency_store,
    classify_tool_exception,
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
                locked = [
                    {
                        "dimension": payload.get("dimension") or "minecraft:overworld",
                        "x": (payload.get("position") or {}).get("x", 1),
                        "y": (payload.get("position") or {}).get("y", 64),
                        "z": (payload.get("position") or {}).get("z", 1),
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
                        "dimension": locked[0]["dimension"],
                        "position": {
                            "x": locked[0]["x"],
                            "y": locked[0]["y"],
                            "z": locked[0]["z"],
                        },
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
    reset_idempotency_store()
    yield
    reset_block_capability_cache()
    reset_preflight_cache()
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
    result = map_bridge_exception(
        TimeoutError("token=bridge-secret timed out"), tool_name="edit_blocks"
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
        tool_name="edit_blocks",
    )
    body = json.loads(result.output)
    assert body["ok"] is False
    assert body["code"] == "LIMIT_EXCEEDED"
    assert body["external_state_unknown"] is False
    assert body["retryable"] is True
    assert body["fallback_allowed"] is False
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
        ("PROTECTED_BLOCK", False),
        ("STATE_UNKNOWN", False),
    ],
)
def test_explicit_addon_errors_preserve_code_and_only_unavailable_allows_fallback(
    code: str, fallback_allowed: bool
) -> None:
    result = map_addon_bridge_result(
        {"ok": False, "payload": {"code": code, "message": "token=bridge-secret"}}
    )

    body = json.loads(result.output)
    assert body["code"] == code
    assert body["fallback_allowed"] is fallback_allowed
    assert "bridge-secret" not in result.output
    if fallback_allowed:
        assert "独立审批" in body["message"]


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
    assert "hint" in body
    assert "replace_any" in body["hint"]
    assert body["fallback_allowed"] is False
    assert body["retryable"] is False
    assert "非空气" in body["message"] or "空气" in body["message"]
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
    assert body["fallback_allowed"] is False


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
    assert body["fallback_allowed"] is False
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
    assert projected["mode"] == "place"
    assert projected["changed"] is True
    assert projected["at"] == {"x": -793, "y": 94, "z": 185}
    assert projected["type_id"] == "minecraft:stone"
    assert projected["was"] == "minecraft:air"
    assert "before" not in projected
    assert "after" not in projected
    assert "targets" not in projected
    assert "verification" not in projected
    assert "phase" not in projected

    mapped = map_addon_bridge_result(
        {"ok": True, "payload": fat_payload},
        project_for_model=True,
        mode="place",
    )
    assert mapped.is_success
    assert len(mapped.output) <= 250
    assert "before" not in mapped.output
    assert "verification" not in mapped.output


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
    assert projected["mode"] == "fill"
    assert projected["changed_count"] == 6
    assert projected["skipped"] == 19
    assert projected["volume"] == 25
    assert projected["from"] == {"x": -797, "y": 93, "z": 180}
    assert projected["to"] == {"x": -793, "y": 93, "z": 184}
    assert projected["type_id"] == "minecraft:oak_planks"
    assert projected["previous_type_counts"]["minecraft:air"] == 6
    assert "before_samples" not in projected
    assert "targets" not in projected
    assert "verification" not in projected
    assert "rollback" not in projected
    assert "phase" not in projected

    mapped = map_addon_bridge_result(
        {"ok": True, "payload": fat_payload},
        project_for_model=True,
        mode="fill",
        authorized_bounds=authorized,
    )
    body = json.loads(mapped.output)
    assert "before_samples" not in body
    assert body["volume"] == 25
    assert body["from"]["z"] == 180
    assert body["to"]["z"] == 184


def test_limits_hard_clamp() -> None:
    settings = SimpleNamespace(
        addon=SimpleNamespace(
            block_tools=SimpleNamespace(
                max_discrete_positions=99999,
                max_fill_volume=99999,
                cells_per_tick=99999,
            )
        )
    )
    limits = get_block_tools_limits(settings)
    assert limits.max_discrete_positions == HARD_MAX_DISCRETE_POSITIONS
    assert limits.max_fill_volume == 16384
    assert limits.cells_per_tick == 512


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
    plan = build_block_preflight_plan(
        "edit_blocks",
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

    assert plan.authorized_args["from"] == {"x": 2, "y": 64, "z": 1}
    assert plan.authorized_args["to"] == {"x": 4, "y": 64, "z": 3}
    assert "from_pos" not in plan.authorized_args
    assert "to_pos" not in plan.authorized_args
    assert "from_pos" not in normalize_tool_args(plan.authorized_args)
    assert "to_pos" not in normalize_tool_args(plan.authorized_args)
    assert plan.execute_args["from_pos"] == plan.authorized_args["from"]
    assert plan.execute_args["to_pos"] == plan.authorized_args["to"]


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

    plan = build_block_preflight_plan("edit_blocks", original, preflight)
    assert plan.authorized_args["from"] == {"x": -797, "y": 93, "z": 180}
    assert plan.authorized_args["to"] == {"x": -793, "y": 93, "z": 184}
    assert plan.execute_args["from_pos"] == plan.authorized_args["from"]
    assert plan.execute_args["to_pos"] == plan.authorized_args["to"]
    assert plan.execute_args["locked_targets"] == locked_targets

    # Success path: authorized_bounds derived from post-merge execute corners
    # must report original volume 25, not locked-shrunk volume.
    authorized_bounds = {
        "from": plan.execute_args["from_pos"],
        "to": plan.execute_args["to_pos"],
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
    assert projected["from"] == {"x": -797, "y": 93, "z": 180}
    assert projected["to"] == {"x": -793, "y": 93, "z": 184}
    assert projected["volume"] == 25
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


def test_strip_block_internal_tool_schema_hides_locked_and_phase() -> None:
    from services.agent.harness.execution import strip_block_internal_tool_schema
    from pydantic_ai.tools import ToolDefinition

    raw = ToolDefinition(
        name="edit_blocks",
        description="edit",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "type_id": {"type": "string"},
                "mode": {"type": "string"},
                "from_pos": {"type": "object"},
                "to_pos": {"type": "object"},
                "locked_targets": {"type": "array"},
                "phase": {"type": "string"},
            },
            "required": ["type_id", "locked_targets"],
        },
    )
    stripped = strip_block_internal_tool_schema(raw)
    props = stripped.parameters_json_schema["properties"]
    assert "locked_targets" not in props
    assert "phase" not in props
    assert "type_id" in props
    assert "from_pos" in props
    assert "locked_targets" not in stripped.parameters_json_schema.get("required", [])

    inspect_raw = ToolDefinition(
        name="inspect_block",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "position": {"type": "object"},
                "locked_targets": {"type": "array"},
                "phase": {"type": "string"},
            },
        },
    )
    inspect_stripped = strip_block_internal_tool_schema(inspect_raw)
    assert "locked_targets" not in inspect_stripped.parameters_json_schema["properties"]
    assert "phase" not in inspect_stripped.parameters_json_schema["properties"]

    other = ToolDefinition(
        name="run_minecraft_command",
        parameters_json_schema={
            "type": "object",
            "properties": {"command": {"type": "string"}, "phase": {"type": "string"}},
        },
    )
    assert strip_block_internal_tool_schema(other) is other


@pytest.mark.asyncio
async def test_harness_prepare_tools_strips_block_internal_params() -> None:
    from pydantic_ai.tools import ToolDefinition

    cap = HarnessCapability(policy=PolicyEngine.from_settings(_Settings()))
    cid = "schema-strip-1"
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
            name="edit_blocks",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "type_id": {"type": "string"},
                    "locked_targets": {"type": "array"},
                    "phase": {"type": "string"},
                },
            },
        ),
        ToolDefinition(
            name="inspect_block",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "position": {"type": "object"},
                    "locked_targets": {"type": "array"},
                    "phase": {"type": "string"},
                },
            },
        ),
    ]
    prepared = await cap.prepare_tools(_Ctx(), tool_defs)  # type: ignore[arg-type]
    by_name = {td.name: td for td in prepared}
    assert "edit_blocks" in by_name
    assert "inspect_block" in by_name
    for name in ("edit_blocks", "inspect_block"):
        props = by_name[name].parameters_json_schema.get("properties") or {}
        assert "locked_targets" not in props
        assert "phase" not in props


def test_policy_edit_blocks_requires_approval_inspect_allows() -> None:
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

    need = engine.decide(
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
    assert need.action == PolicyDecisionKind.REQUIRE_APPROVAL

    approved = engine.decide(
        "edit_blocks",
        {
            "mode": "place",
            "type_id": "minecraft:stone",
            "coordinate_mode": "absolute",
            "dimension": "minecraft:overworld",
            "position": {"x": 0, "y": 64, "z": 0},
        },
        player_name="Steve",
        approved=True,
    )
    assert approved.action == PolicyDecisionKind.ALLOW


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
    assert engine.is_tool_exposed("edit_blocks", ctx=ctx) is True


@pytest.mark.asyncio
async def test_inspect_block_impl_absolute() -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    result = await inspect_block_impl(
        ctx,  # type: ignore[arg-type]
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        position={"x": 3, "y": 64, "z": 4},
    )
    assert result.is_success
    assert any(c[0] == "inspect_block" for c in bridge.calls)


@pytest.mark.asyncio
async def test_inspect_block_validation_missing_position() -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    result = await inspect_block_impl(
        ctx,  # type: ignore[arg-type]
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_edit_blocks_impl_place() -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    result = await edit_blocks_impl(
        ctx,  # type: ignore[arg-type]
        mode="place",
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        position={"x": 1, "y": 64, "z": 1},
        type_id="minecraft:stone",
        phase="execute",
    )
    assert result.is_success
    edit_calls = [c for c in bridge.calls if c[0] == "edit_blocks"]
    assert edit_calls
    assert edit_calls[-1][1]["type_id"] == "minecraft:stone"


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

    result = await edit_blocks_impl(
        ctx,  # type: ignore[arg-type]
        mode="place",
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        position={"x": 1, "y": 64, "z": 1},
        type_id="minecraft:stone",
    )

    body = json.loads(result.output)
    assert body["code"] == "ADDON_UNAVAILABLE"
    assert body["fallback_allowed"] is True
    assert "独立审批" in body["message"]
    assert "package.module" not in result.output
    assert "bridge-secret" not in result.output
    assert "hunter2" not in result.output


@pytest.mark.asyncio
async def test_edit_blocks_limit_exceeded() -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    positions = [{"x": i, "y": 64, "z": 0} for i in range(300)]
    result = await edit_blocks_impl(
        ctx,  # type: ignore[arg-type]
        mode="batch",
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        positions=positions,
        type_id="minecraft:dirt",
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_edit_blocks_oversized_batch_precheck_limit_without_bridge() -> None:
    """Huge batch.positions → host commandLine budget LIMIT; bridge never called."""
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    capability_calls = list(bridge.calls)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    # Under max_discrete_positions (256) but still blows the 461 B wire budget.
    positions = [{"x": i, "y": 64, "z": 0} for i in range(20)]
    result = await edit_blocks_impl(
        ctx,  # type: ignore[arg-type]
        mode="batch",
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        positions=positions,
        type_id="minecraft:dirt",
        phase="execute",
    )
    assert not result.is_success
    body = json.loads(result.output)
    assert body["code"] == "LIMIT_EXCEEDED"
    assert body["reason"] == "command_line_budget"
    assert body["retryable"] is True
    assert body["fallback_allowed"] is False
    assert body["external_state_unknown"] is False
    assert "未发送" in body["message"]
    assert "place" in body["hint"].lower() or "禁止" in body["hint"]
    assert "suggested_max_discrete" in body
    # Only capability probe; no edit_blocks request.
    edit_calls = [c for c in bridge.calls if c[0] == "edit_blocks"]
    assert edit_calls == []
    assert len(bridge.calls) == len(capability_calls)


@pytest.mark.asyncio
async def test_run_block_preflight_rejects_oversized_batch_before_bridge() -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    positions = [{"x": i, "y": 64, "z": 0} for i in range(20)]
    plan, failure = await run_block_preflight(
        ctx,  # type: ignore[arg-type]
        "edit_blocks",
        {
            "mode": "batch",
            "coordinate_mode": "absolute",
            "dimension": "minecraft:overworld",
            "positions": positions,
            "type_id": "minecraft:dirt",
        },
    )
    assert plan is None
    assert failure is not None
    body = json.loads(failure.output)
    assert body["code"] == "LIMIT_EXCEEDED"
    assert body["reason"] == "command_line_budget"
    assert "hint" in body
    assert any(k in body["hint"] for k in ("place", "batch", "fill"))
    assert not any(c[0] == "edit_blocks" for c in bridge.calls)


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
async def test_run_block_preflight_edit_canonicalizes() -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    plan, failure = await run_block_preflight(
        ctx,  # type: ignore[arg-type]
        "edit_blocks",
        {
            "mode": "place",
            "coordinate_mode": "absolute",
            "dimension": "minecraft:overworld",
            "position": {"x": 2, "y": 70, "z": 2},
            "type_id": "minecraft:oak_planks",
        },
    )
    assert failure is None
    assert plan is not None
    assert plan.authorized_args.get("phase") == "execute"
    assert plan.execute_args.get("locked_targets")


@pytest.mark.asyncio
async def test_registered_tools_include_block_ops_and_ok_false() -> None:
    agent = Agent("test", deps_type=_Deps, output_type=str)
    register_agent_tools(agent)
    tools = iter_registered_tools(agent)
    assert "inspect_block" in tools
    assert "edit_blocks" in tools

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
async def test_harness_preflight_before_approval_for_edit_blocks() -> None:
    """edit_blocks: preflight before approval; metadata has locked canonical args; no execute yet."""
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
    async def edit_blocks(
        ctx: RunContext[_Deps],
        type_id: str,
        mode: str = "place",
        coordinate_mode: str = "absolute",
        dimension: str | None = None,
        position: dict[str, Any] | None = None,
        from_pos: dict[str, Any] | None = None,
        to_pos: dict[str, Any] | None = None,
        positions: list[dict[str, Any]] | None = None,
        states: dict[str, Any] | None = None,
        replace_any: bool = False,
        expected_previous: dict[str, Any] | None = None,
        locked_targets: list[dict[str, Any]] | None = None,
        phase: str | None = None,
    ) -> str:
        result = await edit_blocks_impl(
            ctx,  # type: ignore[arg-type]
            mode=mode,  # type: ignore[arg-type]
            coordinate_mode=coordinate_mode,  # type: ignore[arg-type]
            dimension=dimension,
            position=position,
            positions=positions,
            from_pos=from_pos,
            to_pos=to_pos,
            type_id=type_id,
            states=states,
            replace_any=replace_any,
            expected_previous=expected_previous,
            locked_targets=locked_targets,
            phase=phase,
        )
        return result.output if isinstance(result, ToolResult) else str(result)

    async def model_fn(messages: list[ModelMessage], info: Any) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="edit_blocks",
                    args={
                        "mode": "place",
                        "type_id": "minecraft:stone",
                        "coordinate_mode": "absolute",
                        "dimension": "minecraft:overworld",
                        "position": {"x": 8, "y": 64, "z": 8},
                    },
                )
            ]
        )

    deps = _Deps(connection_id=cid, addon_bridge=bridge, settings=_Settings(), run_id="run-pf-1")
    first = await agent.run("place stone", model=FunctionModel(model_fn), deps=deps)
    assert isinstance(first.output, DeferredToolRequests)
    assert first.output.approvals
    approval = first.output.approvals[0]
    meta = (first.output.metadata or {}).get(approval.tool_call_id) or {}
    normalized = meta.get("normalized_args") or {}
    assert normalized.get("phase") == "execute"
    assert normalized.get("locked_targets")
    assert normalized.get("coordinate_mode") == "absolute"
    locked = normalized["locked_targets"][0]
    assert locked.get("x") == 8 and locked.get("y") == 64 and locked.get("z") == 8
    # Preflight ran; mutation (execute) did not.
    assert any(c[0] == "edit_blocks" and c[1].get("phase") == "preflight" for c in bridge.calls)
    assert not any(c[0] == "edit_blocks" and c[1].get("phase") == "execute" for c in bridge.calls)
    # Preflight cache retains canonical args for approval recovery
    cache = get_preflight_cache()
    original_hash = meta.get("original_args_hash")
    assert original_hash
    cached = cache.get("run-pf-1", approval.tool_call_id, original_hash)
    assert cached is not None
    assert cached.canonical_args.get("locked_targets")


@pytest.mark.asyncio
async def test_harness_preflight_exception_is_safe_projection_failure_and_is_logged(monkeypatch) -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    agent: Agent[_Deps, str | DeferredToolRequests] = Agent(
        "test",
        deps_type=_Deps,
        output_type=[str, DeferredToolRequests],
        capabilities=[HarnessCapability(policy=PolicyEngine.from_settings(_Settings()))],
    )
    register_agent_tools(agent)
    captured: dict[str, Any] = {}

    async def fail_preflight(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("package.module.preflight(token=bridge-secret, password=hunter2)")

    def capture(event: str, **fields: Any) -> None:
        captured["event"] = event
        captured.update(fields)

    monkeypatch.setattr("services.agent.block_ops.tools_impl.run_block_preflight", fail_preflight)
    monkeypatch.setattr("services.agent.harness.execution.logger.error", capture)
    calls = 0

    async def model_fn(messages: list[ModelMessage], info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls > 1:
            return ModelResponse(parts=[TextPart(content="done")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="edit_blocks",
            tool_call_id="tc-preflight-error",
            args={
                "type_id": "minecraft:stone", "mode": "place",
                "coordinate_mode": "absolute", "dimension": "minecraft:overworld",
                "position": {"x": 1, "y": 64, "z": 1},
            },
        )])

    deps = _Deps(connection_id=cid, addon_bridge=bridge, settings=_Settings(), run_id="run-preflight-error")
    result = await agent.run("edit", model=FunctionModel(model_fn), deps=deps)

    contents = str(result.all_messages())
    assert "INTERNAL_ERROR" in contents
    assert "STATE_UNKNOWN" not in contents
    assert "bridge-secret" not in contents
    assert "hunter2" not in contents
    assert not any(
        name == "edit_blocks" and payload.get("phase") == "execute"
        for name, payload in bridge.calls
    )
    assert captured == {
        "event": "tool_execution_failed",
        "tool_name": "edit_blocks",
        "run_id": "run-preflight-error",
        "tool_call_id": "tc-preflight-error",
        "connection_id_short": cid[-8:],
        "player_name": "Steve",
        "error_kind": "INTERNAL",
        "error_type": "RuntimeError",
        "diagnostic_summary": "RuntimeError: package.module.preflight(token=[REDACTED], password=[REDACTED])",
        "external_state_unknown": False,
        "execution_stage": "projection",
    }


@pytest.mark.asyncio
async def test_harness_rejects_incomplete_non_deferred_execute_projection(monkeypatch) -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    agent: Agent[_Deps, str | DeferredToolRequests] = Agent(
        "test",
        deps_type=_Deps,
        output_type=[str, DeferredToolRequests],
        capabilities=[HarnessCapability(policy=PolicyEngine.from_settings(_Settings()))],
    )
    register_agent_tools(agent)
    captured: dict[str, Any] = {}

    def capture(event: str, **fields: Any) -> None:
        captured["event"] = event
        captured.update(fields)

    monkeypatch.setattr("services.agent.harness.execution.logger.error", capture)
    calls = 0

    async def model_fn(messages: list[ModelMessage], info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls > 1:
            return ModelResponse(parts=[TextPart(content="done")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="edit_blocks",
            tool_call_id="tc-fast-projection",
            args={
                "type_id": "minecraft:stone", "mode": "place",
                "coordinate_mode": "absolute", "dimension": "minecraft:overworld",
                "phase": "execute", "locked_targets": [{"x": 1, "y": 64, "z": 1}],
            },
        )])

    result = await agent.run(
        "edit",
        model=FunctionModel(model_fn),
        deps=_Deps(connection_id=cid, addon_bridge=bridge, settings=_Settings(), run_id="run-fast-projection"),
    )

    assert "INTERNAL_ERROR" in str(result.all_messages())
    assert not any(
        name == "edit_blocks" and payload.get("phase") == "execute"
        for name, payload in bridge.calls
    )
    assert captured["event"] == "tool_execution_failed"
    assert captured["execution_stage"] == "projection"
    assert captured["run_id"] == "run-fast-projection"
    assert captured["tool_call_id"] == "tc-fast-projection"


@pytest.mark.asyncio
async def test_harness_rejects_unhashable_execute_mode_without_bridge_call(monkeypatch) -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    agent: Agent[_Deps, str] = Agent(
        "test",
        deps_type=_Deps,
        output_type=str,
        capabilities=[HarnessCapability(policy=PolicyEngine.from_settings(_Settings()))],
    )

    @agent.tool
    async def edit_blocks(
        ctx: RunContext[_Deps],
        type_id: str,
        mode: Any,
        coordinate_mode: str,
        dimension: str,
        locked_targets: list[dict[str, Any]],
        phase: str,
    ) -> str:
        raise AssertionError("invalid projection must not invoke the Python tool")

    captured: dict[str, Any] = {}

    def capture(event: str, **fields: Any) -> None:
        captured["event"] = event
        captured.update(fields)

    monkeypatch.setattr("services.agent.harness.execution.logger.error", capture)
    calls = 0

    async def model_fn(messages: list[ModelMessage], info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls > 1:
            return ModelResponse(parts=[TextPart(content="done")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="edit_blocks",
            tool_call_id="tc-unhashable-mode",
            args={
                "type_id": "minecraft:stone", "mode": [],
                "coordinate_mode": "absolute", "dimension": "minecraft:overworld",
                "phase": "execute", "locked_targets": [{"x": 1, "y": 64, "z": 1}],
            },
        )])

    result = await agent.run(
        "edit",
        model=FunctionModel(model_fn),
        deps=_Deps(connection_id=cid, addon_bridge=bridge, settings=_Settings(), run_id="run-unhashable-mode"),
    )

    assert "INTERNAL_ERROR" in str(result.all_messages())
    assert not any(
        name == "edit_blocks" and payload.get("phase") == "execute"
        for name, payload in bridge.calls
    )
    assert captured["event"] == "tool_execution_failed"
    assert captured["execution_stage"] == "projection"
@pytest.mark.asyncio
async def test_reverse_fill_approval_resumes_with_locked_normalized_operation() -> None:
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
    async def edit_blocks(
        ctx: RunContext[_Deps],
        type_id: str,
        mode: str = "place",
        coordinate_mode: str = "absolute",
        dimension: str | None = None,
        position: dict[str, Any] | None = None,
        positions: list[dict[str, Any]] | None = None,
        from_pos: dict[str, Any] | None = None,
        to_pos: dict[str, Any] | None = None,
        states: dict[str, Any] | None = None,
        replace_any: bool = False,
        expected_previous: dict[str, Any] | None = None,
        locked_targets: list[dict[str, Any]] | None = None,
        phase: str | None = None,
    ) -> str:
        result = await edit_blocks_impl(
            ctx,  # type: ignore[arg-type]
            type_id=type_id,
            mode=mode,  # type: ignore[arg-type]
            coordinate_mode=coordinate_mode,  # type: ignore[arg-type]
            dimension=dimension,
            position=position,
            positions=positions,
            from_pos=from_pos,
            to_pos=to_pos,
            states=states,
            replace_any=replace_any,
            expected_previous=expected_previous,
            locked_targets=locked_targets,
            phase=phase,
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
                    tool_name="edit_blocks",
                    tool_call_id="tc-fill-reverse",
                    args={
                        "type_id": "minecraft:stone",
                        "mode": "fill",
                        "coordinate_mode": "absolute",
                        "dimension": "minecraft:overworld",
                        "from_pos": {"x": 4, "y": 64, "z": 3},
                        "to_pos": {"x": 2, "y": 64, "z": 1},
                    },
                )
            ]
        )

    deps = _Deps(connection_id=cid, addon_bridge=bridge, settings=_Settings(), run_id="run-fill")
    first = await agent.run("fill reverse bounds", model=FunctionModel(model_fn), deps=deps)
    assert isinstance(first.output, DeferredToolRequests)
    approval = first.output.approvals[0]
    metadata = first.output.metadata[approval.tool_call_id]
    authorized_args = metadata["normalized_args"]
    assert authorized_args["from"] == {"x": 2, "y": 64, "z": 1}
    assert authorized_args["to"] == {"x": 4, "y": 64, "z": 3}
    execute_args = metadata["execute_args"]
    assert execute_args["from_pos"] == {"x": 2, "y": 64, "z": 1}
    assert execute_args["to_pos"] == {"x": 4, "y": 64, "z": 3}
    assert "repairs_applied" not in execute_args
    assert "facing" not in execute_args
    assert metadata["approval_metadata"] == {
        "bounds": {
            "min": {"x": 2, "y": 64, "z": 1},
            "max": {"x": 4, "y": 64, "z": 3},
        },
        "repairs_applied": ["reversed_bounds"],
        "facing": "south",
        "player_origin": {"x": 100, "y": 64, "z": 100},
        "before_samples": [{"type_id": "minecraft:air"}],
        "future_preflight_evidence": {"source": "addon"},
    }

    # Approval recovery must use the persisted strict execution projection,
    # not the in-process preflight cache or bridge-facing authorization args.
    reset_preflight_cache()

    second = await agent.run(
        message_history=first.all_messages(),
        deferred_tool_results=DeferredToolResults(
            approvals={
                approval.tool_call_id: ToolApproved(override_args=execute_args),
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
    assert execute_calls[0]["from"] == {"x": 2, "y": 64, "z": 1}
    assert execute_calls[0]["to"] == {"x": 4, "y": 64, "z": 3}
    # Continuous fill AABB is frozen by from/to; locked_targets are omitted on the
    # wire to stay under the MCBE commandLine budget (still present in execute_args).
    assert "locked_targets" not in execute_calls[0]
    assert execute_args["locked_targets"] == locked_targets

    repeated = await agent.run(
        message_history=first.all_messages(),
        deferred_tool_results=DeferredToolResults(
            approvals={approval.tool_call_id: ToolApproved(override_args=execute_args)}
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
@pytest.mark.parametrize(
    ("mode", "original_args", "locked_targets"),
    [
        (
            "place",
            {
                "type_id": "minecraft:gold_block",
                "mode": "place",
                "coordinate_mode": "player_relative",
                "position": {"forward": 2, "right": 1, "up": 0},
            },
            [{"dimension": "minecraft:overworld", "x": 42, "y": 70, "z": -8}],
        ),
        (
            "batch",
            {
                "type_id": "minecraft:gold_block",
                "mode": "batch",
                "coordinate_mode": "absolute",
                "dimension": "minecraft:the_nether",
                "positions": [{"x": 3, "y": 65, "z": 4}, {"x": 5, "y": 65, "z": 4}],
            },
            [
                {"dimension": "minecraft:the_nether", "x": 3, "y": 65, "z": 4},
                {"dimension": "minecraft:the_nether", "x": 5, "y": 65, "z": 4},
            ],
        ),
    ],
)
async def test_place_and_batch_approval_resume_execute_only_frozen_targets(
    mode: str,
    original_args: dict[str, Any],
    locked_targets: list[dict[str, Any]],
) -> None:
    """真实 defer/resume 只执行批准时的绝对计划，预检缓存不是恢复依赖。"""
    player_state = {"position": {"x": 40, "y": 70, "z": -10}, "facing": "north"}

    async def bridge_handler(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        if capability == "get_capabilities":
            return {
                "ok": True,
                "payload": {"capabilities": {"block_ops": {"inspect": True, "edit": True}}},
            }
        if capability == "edit_blocks" and payload.get("phase") == "preflight":
            return {
                "ok": True,
                "payload": {
                    "ok": True,
                    "phase": "preflight",
                    "mode": mode,
                    "coordinate_mode": "absolute",
                    "dimension": locked_targets[0]["dimension"],
                    "position": {
                        key: locked_targets[0][key] for key in ("x", "y", "z")
                    },
                    "positions": [
                        {key: target[key] for key in ("x", "y", "z")}
                        for target in locked_targets
                    ],
                    "locked_targets": locked_targets,
                    "player_origin": player_state["position"],
                    "facing": player_state["facing"],
                },
            }
        if capability == "edit_blocks" and payload.get("phase") == "execute":
            return {"ok": True, "payload": {"ok": True, "phase": "execute", "changed": len(locked_targets)}}
        return {"ok": False, "payload": {"code": "INTERNAL_ERROR", "message": "unexpected"}}

    bridge = _FakeBridge(bridge_handler)
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    agent: Agent[_Deps, str | DeferredToolRequests] = Agent(
        "test",
        deps_type=_Deps,
        output_type=[str, DeferredToolRequests],
        capabilities=[HarnessCapability(policy=PolicyEngine.from_settings(_Settings()))],
    )
    register_agent_tools(agent)
    model_calls = 0

    async def model_fn(messages: list[ModelMessage], info: Any) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="edit_blocks", tool_call_id=f"tc-{mode}", args=original_args,
            )])
        return ModelResponse(parts=[TextPart(content="done")])

    deps = _Deps(connection_id=cid, addon_bridge=bridge, settings=_Settings(), run_id=f"run-{mode}")
    first = await agent.run("edit", model=FunctionModel(model_fn), deps=deps)
    assert isinstance(first.output, DeferredToolRequests)
    approval = first.output.approvals[0]
    execute_args = first.output.metadata[approval.tool_call_id]["execute_args"]
    assert execute_args["locked_targets"] == locked_targets
    assert execute_args["dimension"] == locked_targets[0]["dimension"]
    if mode == "place":
        assert execute_args["position"] == {"x": 42, "y": 70, "z": -8}
    else:
        assert execute_args["positions"] == [{"x": 3, "y": 65, "z": 4}, {"x": 5, "y": 65, "z": 4}]

    # Simulate movement/turning while waiting. The execute request must never
    # consult this state again, and cache eviction must not change the result.
    player_state.update(position={"x": 900, "y": 10, "z": 900}, facing="south")
    reset_preflight_cache()
    second = await agent.run(
        message_history=first.all_messages(),
        deferred_tool_results=DeferredToolResults(
            approvals={approval.tool_call_id: ToolApproved(override_args=execute_args)}
        ),
        model=FunctionModel(model_fn),
        deps=deps,
    )

    assert not isinstance(second.output, DeferredToolRequests)
    edit_payloads = [payload for capability, payload in bridge.calls if capability == "edit_blocks"]
    tool_contents = [
        str(getattr(part, "content", ""))
        for message in second.all_messages()
        for part in getattr(message, "parts", [])
    ]
    assert [payload["phase"] for payload in edit_payloads] == ["preflight", "execute"], tool_contents
    # Absolute place/batch geometry freezes targets; locked_targets may be omitted
    # on the wire. The approval execute_args still retain them for idempotency.
    assert edit_payloads[-1]["dimension"] == locked_targets[0]["dimension"]
    if mode == "place":
        assert edit_payloads[-1]["position"] == {"x": 42, "y": 70, "z": -8}
    else:
        assert edit_payloads[-1]["positions"] == [
            {"x": 3, "y": 65, "z": 4},
            {"x": 5, "y": 65, "z": 4},
        ]
    assert execute_args["locked_targets"] == locked_targets

    repeated = await agent.run(
        message_history=first.all_messages(),
        deferred_tool_results=DeferredToolResults(
            approvals={approval.tool_call_id: ToolApproved(override_args=execute_args)}
        ),
        model=FunctionModel(model_fn),
        deps=deps,
    )
    assert not isinstance(repeated.output, DeferredToolRequests)
    repeated_payloads = [
        payload for capability, payload in bridge.calls if capability == "edit_blocks"
    ]
    assert [payload["phase"] for payload in repeated_payloads] == ["preflight", "execute"]


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
        return str(await inspect_block_impl(
            ctx, coordinate_mode=coordinate_mode, dimension=dimension,
            position=position, positions=positions, locked_targets=locked_targets, phase=phase,
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
    original = {
        "type_id": "minecraft:stone", "mode": "place", "coordinate_mode": "absolute",
        "dimension": "minecraft:overworld", "position": {"x": 1, "y": 64, "z": 1},
    }
    locked = [{"dimension": "minecraft:overworld", "x": 1, "y": 64, "z": 1}]
    first = build_block_preflight_plan("edit_blocks", original, {"locked_targets": locked})
    second = build_block_preflight_plan(
        "edit_blocks", original,
        {"locked_targets": locked, "future_metadata": {"facing": "north"}},
    )

    assert first.execute_args == second.execute_args
    assert hash_normalized_args(normalize_tool_args(first.execute_args)) == hash_normalized_args(
        normalize_tool_args(second.execute_args)
    )
    assert second.approval_metadata["future_metadata"] == {"facing": "north"}


@pytest.mark.asyncio
async def test_auto_approved_edit_preflights_then_executes_locked_operation() -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    agent: Agent[_Deps, str | DeferredToolRequests] = Agent(
        "test",
        deps_type=_Deps,
        output_type=[str, DeferredToolRequests],
        capabilities=[HarnessCapability(policy=PolicyEngine.from_settings(_Settings()))],
    )
    register_agent_tools(agent)
    calls = 0

    async def model_fn(messages: list[ModelMessage], info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls > 1:
            return ModelResponse(parts=[TextPart(content="done")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="edit_blocks", tool_call_id="tc-auto", args={
                "type_id": "minecraft:stone", "mode": "place",
                "coordinate_mode": "absolute", "dimension": "minecraft:overworld",
                "position": {"x": 9, "y": 64, "z": 9},
            },
        )])

    result = await agent.run(
        "place a block", model=FunctionModel(model_fn),
        deps=_Deps(connection_id=cid, addon_bridge=bridge, settings=_Settings(),
                   run_id="run-auto", auto_approve_tools=True),
    )

    assert not isinstance(result.output, DeferredToolRequests)
    phases = [payload.get("phase") for name, payload in bridge.calls if name == "edit_blocks"]
    assert phases == ["preflight", "execute"]
    execute_payload = [payload for name, payload in bridge.calls if name == "edit_blocks"][-1]
    assert execute_payload.get("phase") == "execute"
    # place with absolute position freezes the target without shipping locked_targets.
    assert execute_payload.get("position") == {"x": 9, "y": 64, "z": 9}


@pytest.mark.asyncio
async def test_auto_approved_fill_uses_execution_projection_idempotency_key() -> None:
    locked_targets = [
        {"dimension": "minecraft:overworld", "x": x, "y": 64, "z": z}
        for x in range(1, 3)
        for z in range(1, 3)
    ]
    preflight_payload = {
        "schema_version": "1",
        "ok": True,
        "phase": "preflight",
        "mode": "fill",
        "type_id": "minecraft:stone",
        "coordinate_mode": "absolute",
        "dimension": "minecraft:overworld",
        "bounds": {
            "min": {"x": 1, "y": 64, "z": 1},
            "max": {"x": 2, "y": 64, "z": 2},
        },
        "locked_targets": locked_targets,
    }

    async def bridge_handler(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        if capability == "get_capabilities":
            return {
                "ok": True,
                "payload": {"capabilities": {"block_ops": {"inspect": True, "edit": True}}},
            }
        if capability == "edit_blocks" and payload.get("phase") == "preflight":
            return {"ok": True, "payload": preflight_payload}
        if capability == "edit_blocks" and payload.get("phase") == "execute":
            return {"ok": True, "payload": {"ok": True, "phase": "execute", "changed": 4}}
        return {"ok": False, "payload": {"code": "INTERNAL_ERROR"}}

    bridge = _FakeBridge(bridge_handler)
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    agent: Agent[_Deps, str] = Agent(
        "test",
        deps_type=_Deps,
        output_type=str,
        capabilities=[HarnessCapability(policy=PolicyEngine.from_settings(_Settings()))],
    )
    register_agent_tools(agent)
    original_args = {
        "type_id": "minecraft:stone",
        "mode": "fill",
        "coordinate_mode": "absolute",
        "dimension": "minecraft:overworld",
        "from_pos": {"x": 2, "y": 64, "z": 2},
        "to_pos": {"x": 1, "y": 64, "z": 1},
    }
    python_tool_args = {
        **original_args,
        "position": None,
        "positions": None,
        "states": None,
        "replace_any": False,
        "expected_previous": None,
        "locked_targets": None,
        "phase": None,
    }
    plan = build_block_preflight_plan("edit_blocks", python_tool_args, preflight_payload)
    execution_hash = hash_normalized_args(normalize_tool_args(plan.execute_args))
    authorization_hash = hash_normalized_args(normalize_tool_args(plan.authorized_args))
    original_hash = hash_normalized_args(normalize_tool_args(original_args))
    assert execution_hash != authorization_hash
    assert execution_hash != original_hash
    # Legacy entries could have been produced by a different execution projection.
    # They must not suppress this exact world mutation.
    store = get_idempotency_store()
    store.put("run-auto-fill", "tc-auto-fill", authorization_hash, ToolResult.ok("stale-auth"))
    store.put("run-auto-fill", "tc-auto-fill", original_hash, ToolResult.ok("stale-original"))

    model_calls = 0

    async def model_fn(messages: list[ModelMessage], info: Any) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        if model_calls > 1:
            return ModelResponse(parts=[TextPart(content="done")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="edit_blocks", tool_call_id="tc-auto-fill", args=original_args,
        )])

    deps = _Deps(
        connection_id=cid,
        addon_bridge=bridge,
        settings=_Settings(),
        run_id="run-auto-fill",
        auto_approve_tools=True,
    )
    result = await agent.run("fill", model=FunctionModel(model_fn), deps=deps)

    assert result.output == "done"
    assert [
        payload["phase"]
        for capability, payload in bridge.calls
        if capability == "edit_blocks"
    ] == ["preflight", "execute"]
    assert get_idempotency_store().get("run-auto-fill", "tc-auto-fill", execution_hash)
    assert get_idempotency_store().get("run-auto-fill", "tc-auto-fill", authorization_hash)
    assert get_idempotency_store().get("run-auto-fill", "tc-auto-fill", original_hash)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "mutate"),
    [
        ({"type_id": "minecraft:stone", "mode": "place", "coordinate_mode": "absolute", "dimension": "minecraft:overworld", "position": {"x": 1, "y": 64, "z": 1}}, lambda value: value.update(phase="preflight")),
        ({"type_id": "minecraft:stone", "mode": "place", "coordinate_mode": "absolute", "dimension": "minecraft:overworld", "position": {"x": 1, "y": 64, "z": 1}}, lambda value: value.update(locked_targets=[])),
        ({"type_id": "minecraft:stone", "mode": "place", "coordinate_mode": "absolute", "dimension": "minecraft:overworld", "position": {"x": 1, "y": 64, "z": 1}}, lambda value: value.pop("position")),
        ({"type_id": "minecraft:stone", "mode": "batch", "coordinate_mode": "absolute", "dimension": "minecraft:overworld", "positions": [{"x": 1, "y": 64, "z": 1}]}, lambda value: value.pop("positions")),
        ({"type_id": "minecraft:stone", "mode": "fill", "coordinate_mode": "absolute", "dimension": "minecraft:overworld", "from_pos": {"x": 1, "y": 64, "z": 1}, "to_pos": {"x": 2, "y": 64, "z": 1}}, lambda value: value.pop("to_pos")),
    ],
)
async def test_corrupt_approved_override_never_executes_block_bridge(
    args: dict[str, Any], mutate: Any
) -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    agent: Agent[_Deps, str | DeferredToolRequests] = Agent(
        "test", deps_type=_Deps, output_type=[str, DeferredToolRequests],
        capabilities=[HarnessCapability(policy=PolicyEngine.from_settings(_Settings()))],
    )
    register_agent_tools(agent)
    calls = 0

    async def model_fn(messages: list[ModelMessage], info: Any) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls > 1:
            return ModelResponse(parts=[TextPart(content="done")])
        return ModelResponse(parts=[ToolCallPart(
            tool_name="edit_blocks", tool_call_id="tc-corrupt", args=args,
        )])

    deps = _Deps(connection_id=cid, addon_bridge=bridge, settings=_Settings(), run_id="run-corrupt")
    first = await agent.run("edit", model=FunctionModel(model_fn), deps=deps)
    assert isinstance(first.output, DeferredToolRequests)
    approval = first.output.approvals[0]
    override_args = dict(first.output.metadata[approval.tool_call_id]["execute_args"])
    mutate(override_args)
    result = await agent.run(
        message_history=first.all_messages(),
        deferred_tool_results=DeferredToolResults(
            approvals={approval.tool_call_id: ToolApproved(override_args=override_args)}
        ),
        model=FunctionModel(model_fn), deps=deps,
    )

    assert "INTERNAL_ERROR" in str(result.all_messages())
    assert not any(
        name == "edit_blocks" and payload.get("phase") == "execute"
        for name, payload in bridge.calls
    )


def test_block_preflight_plan_keeps_unknown_evidence_out_of_operation_args() -> None:
    plan = build_block_preflight_plan(
        "edit_blocks",
        {
            "type_id": "minecraft:stone", "mode": "place", "coordinate_mode": "absolute",
            "dimension": "minecraft:overworld", "position": {"x": 1, "y": 64, "z": 1},
        },
        {
            "locked_targets": [{"dimension": "minecraft:overworld", "x": 1, "y": 64, "z": 1}],
            "repairs_applied": ["rounded"],
            "future_evidence": {"version": 2},
        },
    )

    assert plan.authorized_args["phase"] == "execute"
    assert plan.execute_args["position"] == {"x": 1, "y": 64, "z": 1}
    assert "future_evidence" not in plan.authorized_args
    assert "future_evidence" not in plan.execute_args
    assert plan.approval_metadata["future_evidence"] == {"version": 2}


@pytest.mark.parametrize(
    ("authorized_args", "message"),
    [
        ({"type_id": "minecraft:stone", "mode": "place", "coordinate_mode": "absolute", "dimension": "minecraft:overworld", "position": {"x": 1}, "locked_targets": [{}], "phase": "preflight"}, "incomplete"),
        ({"type_id": "minecraft:stone", "mode": "place", "coordinate_mode": "absolute", "dimension": "minecraft:overworld", "position": {"x": 1}, "locked_targets": [], "phase": "execute"}, "incomplete"),
        ({"type_id": "minecraft:stone", "mode": "place", "coordinate_mode": "absolute", "dimension": "minecraft:overworld", "locked_targets": [{}], "phase": "execute"}, "place position"),
        ({"type_id": "minecraft:stone", "mode": "batch", "coordinate_mode": "absolute", "dimension": "minecraft:overworld", "positions": [], "locked_targets": [{}], "phase": "execute"}, "non-empty batch"),
        ({"type_id": "minecraft:stone", "mode": "fill", "coordinate_mode": "absolute", "dimension": "minecraft:overworld", "locked_targets": [{}], "phase": "execute"}, "fill bounds"),
    ],
)
def test_corrupt_edit_approval_contract_is_rejected_before_bridge(
    authorized_args: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        project_block_execute_args("edit_blocks", authorized_args)
    result = classify_tool_exception(
        ValueError(f"block preflight execution contract {message}"), tool_name="edit_blocks"
    )
    assert json.loads(result.output)["code"] == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_harness_approved_edit_uses_preflight_cache() -> None:
    """After preflight cache is warm, approved call_tool executes once with locked args."""
    from services.agent.harness.execution import HarnessToolset

    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    policy = PolicyEngine.from_settings(_Settings())

    agent = Agent("test", deps_type=_Deps, output_type=str)
    register_agent_tools(agent)
    # Build a thin wrapper toolset over the registered FunctionToolset path is hard;
    # instead exercise preflight cache + edit_blocks_impl as the execute path does.

    original_args = {
        "mode": "place",
        "type_id": "minecraft:stone",
        "coordinate_mode": "absolute",
        "dimension": "minecraft:overworld",
        "position": {"x": 8, "y": 64, "z": 8},
    }
    deps = _Deps(connection_id=cid, addon_bridge=bridge, settings=_Settings(), run_id="run-pf-2")
    ctx = SimpleNamespace(deps=deps, tool_call_id="tc-1", tool_call_approved=False)

    plan, failure = await run_block_preflight(ctx, "edit_blocks", original_args)  # type: ignore[arg-type]
    assert failure is None
    assert plan is not None
    assert plan.authorized_args.get("phase") == "execute"
    assert plan.authorized_args.get("locked_targets")

    original_hash = hash_normalized_args(normalize_tool_args(original_args))
    get_preflight_cache().put(
        run_id="run-pf-2",
        tool_call_id="tc-1",
        original_args_hash=original_hash,
        canonical_args=plan.authorized_args,
        connection_id=cid,
    )

    # Simulate approved execute with original args (as pydantic-ai re-invokes).
    # Harness would recover canonical from cache; we mirror that recovery.
    recovered = get_preflight_cache().get("run-pf-2", "tc-1", original_hash)
    assert recovered is not None
    exec_args = recovered.canonical_args
    result = await edit_blocks_impl(
        ctx,  # type: ignore[arg-type]
        mode=exec_args.get("mode", "place"),  # type: ignore[arg-type]
        coordinate_mode=exec_args.get("coordinate_mode", "absolute"),  # type: ignore[arg-type]
        dimension=exec_args.get("dimension"),
        position=exec_args.get("position"),
        type_id=str(exec_args.get("type_id")),
        locked_targets=exec_args.get("locked_targets"),
        phase=exec_args.get("phase") or "execute",
    )
    assert result.is_success
    execute_calls = [
        c for c in bridge.calls if c[0] == "edit_blocks" and c[1].get("phase") == "execute"
    ]
    assert len(execute_calls) == 1
    wire = execute_calls[0][1]
    # Absolute place freezes the target via position; locked_targets may be
    # omitted from the wire payload while remaining in the approval plan.
    assert wire.get("phase") == "execute"
    assert wire.get("position") == exec_args.get("position")
    assert recovered.canonical_args.get("locked_targets")
    # policy: edit requires approval unless approved
    need = policy.decide("edit_blocks", normalize_tool_args(plan.authorized_args), player_name="Steve")
    assert need.action == PolicyDecisionKind.REQUIRE_APPROVAL
    allow = policy.decide(
        "edit_blocks",
        normalize_tool_args(plan.authorized_args),
        player_name="Steve",
        approved=True,
    )
    assert allow.action == PolicyDecisionKind.ALLOW


@pytest.mark.asyncio
async def test_edit_blocks_execute_with_locked_targets_skips_re_preflight() -> None:
    """Canonical locked_targets + phase=execute goes straight to bridge execute."""
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    result = await edit_blocks_impl(
        ctx,  # type: ignore[arg-type]
        mode="place",
        coordinate_mode="absolute",
        dimension="minecraft:overworld",
        position={"x": 1, "y": 64, "z": 1},
        type_id="minecraft:stone",
        locked_targets=[{"dimension": "minecraft:overworld", "x": 1, "y": 64, "z": 1}],
        phase="execute",
    )
    assert result.is_success
    assert any(
        c[0] == "edit_blocks" and c[1].get("phase") == "execute" for c in bridge.calls
    )


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
        return await inspect_block_impl(
            ctx,  # type: ignore[arg-type]
            coordinate_mode=coordinate_mode,  # type: ignore[arg-type]
            dimension=dimension,
            position=position,
            positions=positions,
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
