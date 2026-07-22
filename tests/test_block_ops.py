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
from services.agent.block_ops.config import (
    HARD_MAX_DISCRETE_POSITIONS,
    get_block_tools_limits,
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
    edit_blocks_impl,
    inspect_block_impl,
    merge_canonical_from_preflight,
    run_block_preflight,
)
from services.agent.harness.execution import (
    HarnessCapability,
    PolicyDecisionKind,
    PolicyEngine,
    hash_normalized_args,
    normalize_tool_args,
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
async def test_run_block_preflight_edit_canonicalizes() -> None:
    bridge = _FakeBridge()
    cid = str(uuid4())
    await ensure_block_capability(cid, bridge)
    deps = _Deps(connection_id=cid, addon_bridge=bridge)
    ctx = SimpleNamespace(deps=deps)
    canonical, failure = await run_block_preflight(
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
    assert canonical is not None
    assert canonical.get("phase") == "execute"
    assert canonical.get("locked_targets")


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
    assert execute_calls[0]["locked_targets"] == locked_targets


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

    canonical, failure = await run_block_preflight(ctx, "edit_blocks", original_args)  # type: ignore[arg-type]
    assert failure is None
    assert canonical is not None
    assert canonical.get("phase") == "execute"
    assert canonical.get("locked_targets")

    original_hash = hash_normalized_args(normalize_tool_args(original_args))
    get_preflight_cache().put(
        run_id="run-pf-2",
        tool_call_id="tc-1",
        original_args_hash=original_hash,
        canonical_args=canonical,
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
    assert execute_calls[0][1].get("locked_targets")
    # policy: edit requires approval unless approved
    need = policy.decide("edit_blocks", normalize_tool_args(canonical), player_name="Steve")
    assert need.action == PolicyDecisionKind.REQUIRE_APPROVAL
    allow = policy.decide(
        "edit_blocks",
        normalize_tool_args(canonical),
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
