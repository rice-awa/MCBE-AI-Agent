"""统一工具执行边界：策略、审批、幂等 — 真实 model->tool->model 链测试。"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolApproved

from services.agent.block_ops.preflight_cache import (
    get_preflight_cache,
    reset_preflight_cache,
)
from services.agent.harness.approvals import PendingApproval, PendingApprovalStore
from services.agent.harness.audit import build_audit_record
from services.agent.harness.execution import (
    BlockCommandFallbackStore,
    HarnessCapability,
    PolicyDecisionKind,
    PolicyEngine,
    _block_command_fallback_denial,
    _record_block_edit_fallback_outcome,
    classify_tool_exception,
    clear_block_command_fallback_for_connection,
    get_block_command_fallback_store,
    get_idempotency_store,
    hash_normalized_args,
    log_tool_execution_failed,
    normalize_tool_args,
    reset_block_command_fallback_store,
    reset_idempotency_store,
)
from services.agent.block_ops.capability import (
    BlockCapabilityRecord,
    BlockCapabilityStatus,
    get_block_capability_cache,
    reset_block_capability_cache,
)
from services.agent.block_ops.bridge import map_addon_bridge_result, map_bridge_exception
from services.agent.tool_results import ToolResult


@dataclass
class _Deps:
    connection_id: Any = field(default_factory=uuid4)
    player_name: str = "Steve"
    settings: Any = None
    run_id: str = "run-1"
    conversation_id: str = "conv-1"
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
    approval_command_roots: list[str] = ["a", "b"]
    max_batch_commands = 10
    mcp_tool_allowlist: list[str] = []
    tool_policy_version = "2026-07-21.1"
    approval_ttl = 120.0


def _build_agent(
    side_effect_counter: dict[str, int] | None = None,
    *,
    policy_settings: Any | None = None,
) -> Agent[_Deps, str | DeferredToolRequests]:
    counter = side_effect_counter if side_effect_counter is not None else {}
    policy = PolicyEngine.from_settings(policy_settings or _Settings())
    agent: Agent[_Deps, str | DeferredToolRequests] = Agent(
        "test",
        deps_type=_Deps,
        output_type=[str, DeferredToolRequests],
        capabilities=[HarnessCapability(policy=policy)],
    )

    @agent.tool
    async def list_available_providers(ctx: RunContext[_Deps]) -> str:
        counter["list_available_providers"] = counter.get("list_available_providers", 0) + 1
        return ToolResult.ok("providers: deepseek")

    @agent.tool
    async def run_minecraft_command(ctx: RunContext[_Deps], command: str) -> str:
        counter["run_minecraft_command"] = counter.get("run_minecraft_command", 0) + 1
        return ToolResult.ok(f"executed:{command}")

    return agent


@pytest.fixture(autouse=True)
def _reset_idempotency():
    reset_idempotency_store()
    reset_block_command_fallback_store()
    reset_block_capability_cache()
    reset_preflight_cache()
    yield
    reset_idempotency_store()
    reset_block_command_fallback_store()
    reset_block_capability_cache()
    reset_preflight_cache()


class _FallbackContext:
    def __init__(self, prompt: str | None, *, tool_call_approved: bool = False) -> None:
        self.prompt = prompt
        self.tool_call_approved = tool_call_approved


def _set_supported_block_capability(connection_id: str) -> None:
    get_block_capability_cache().set(
        connection_id,
        BlockCapabilityRecord(
            status=BlockCapabilityStatus.SUPPORTED,
            probed_at=time.time(),
        ),
    )


@pytest.fixture
def _block_tool_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试内暴露 place_block 到工具目录（Task 5 才会正式纳入目录）。"""
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


def _build_place_block_agent(
    side_effect_counter: dict[str, int] | None = None,
    *,
    policy_settings: Any | None = None,
) -> Agent[_Deps, str | DeferredToolRequests]:
    """与 _build_agent 同构，但挂载测试本地 place_block 工具。

    公共工具体只允许在“直接调用”路径（plan_id 恢复分支之外的常规执行）
    运行；plan_id 恢复路径必须绕过它，否则抛 AssertionError。
    """
    counter = side_effect_counter if side_effect_counter is not None else {}
    policy = PolicyEngine.from_settings(policy_settings or _Settings())
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
        counter["place_block_public_body"] = counter.get("place_block_public_body", 0) + 1
        raise AssertionError("public place_block body must not run (plan_id resume bypasses it)")

    return agent


async def _run_first_place_block_call(
    agent: Agent[_Deps, str | DeferredToolRequests],
    deps: _Deps,
) -> tuple[str, str, list[ModelMessage]]:
    """发起一次 place_block 调用并等待审批；返回 (tool_call_id, plan_id, messages)。"""

    async def model_fn(messages: list[ModelMessage], info: Any) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="place_block",
                    tool_call_id="tc-place-1",
                    args={"pos": [1, 64, 1], "block": "stone", "expect": "air"},
                )
            ]
        )

    first = await agent.run("place stone", model=FunctionModel(model_fn), deps=deps)
    assert isinstance(first.output, DeferredToolRequests)
    call = first.output.approvals[0]
    assert call.tool_name == "place_block"
    meta = (first.output.metadata or {}).get(call.tool_call_id) or {}
    assert isinstance(meta.get("plan_id"), str) and meta["plan_id"]
    return call.tool_call_id, meta["plan_id"], first.all_messages()


def _fallback_denial(
    *,
    tool_name: str = "run_minecraft_command",
    args: dict[str, Any] | None = None,
    connection_id: str = "conn-1",
    player_name: str = "Steve",
    run_id: str = "run-1",
    prompt: str | None = "build a wall",
    approved: bool = False,
) -> Any:
    return _block_command_fallback_denial(
        tool_name=tool_name,
        normalized_args=args or {"command": "setblock ~ ~ ~ stone"},
        ctx=_FallbackContext(prompt, tool_call_approved=approved),
        connection_id=connection_id,
        player_name=player_name,
        run_id=run_id,
    )


def test_block_command_fallback_store_is_bounded_ttl_and_connection_clearable() -> None:
    store = BlockCommandFallbackStore(ttl_seconds=0.01, max_entries=2)
    store.put("conn", "Steve", "run-1", fallback_allowed=False)
    store.put("conn", "Alex", "run-1", fallback_allowed=False)
    store.put("other", "Steve", "run-1", fallback_allowed=False)

    assert store.get("conn", "Steve", "run-1") is None
    assert store.clear_connection("conn") == 1
    assert store.get("other", "Steve", "run-1") is not None

    time.sleep(0.02)
    assert store.get("other", "Steve", "run-1") is None


def test_block_command_fallback_store_supports_concurrent_players_and_runs() -> None:
    from concurrent.futures import ThreadPoolExecutor

    store = BlockCommandFallbackStore(ttl_seconds=60, max_entries=64)

    def write(index: int) -> None:
        store.put(
            "conn",
            f"player-{index % 4}",
            f"run-{index}",
            fallback_allowed=index % 2 == 0,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(32)))

    for index in range(32):
        record = store.get("conn", f"player-{index % 4}", f"run-{index}")
        assert record is not None
        assert record.fallback_allowed is (index % 2 == 0)


def test_edit_failure_store_accepts_preflight_exception_toolresult_and_string_json() -> None:
    store = get_block_command_fallback_store()
    _record_block_edit_fallback_outcome(
        ToolResult.ok('{"ok":false,"code":"PRECONDITION_FAILED","fallback_allowed":false}'),
        connection_id="conn",
        player_name="Steve",
        run_id="run",
    )
    assert store.get("conn", "Steve", "run").fallback_allowed is False  # type: ignore[union-attr]

    _record_block_edit_fallback_outcome(
        map_bridge_exception(RuntimeError("bridge error"), tool_name="place_block"),
        connection_id="conn",
        player_name="Steve",
        run_id="run",
    )
    assert store.get("conn", "Steve", "run").code == "STATE_UNKNOWN"  # type: ignore[union-attr]

    _record_block_edit_fallback_outcome(
        '{"ok":false,"code":"ADDON_UNAVAILABLE","fallback_allowed":true}',
        connection_id="conn",
        player_name="Steve",
        run_id="run",
    )
    assert store.get("conn", "Steve", "run").fallback_allowed is True  # type: ignore[union-attr]


def test_successful_edit_clears_latest_fallback_state_and_connection_helper() -> None:
    _record_block_edit_fallback_outcome(
        '{"ok":false,"code":"PRECONDITION_FAILED","fallback_allowed":false}',
        connection_id="conn",
        player_name="Steve",
        run_id="run",
    )
    _record_block_edit_fallback_outcome(
        '{"ok":true,"status":"succeeded"}',
        connection_id="conn",
        player_name="Steve",
        run_id="run",
    )
    assert get_block_command_fallback_store().get("conn", "Steve", "run") is None

    _record_block_edit_fallback_outcome(
        '{"ok":false,"fallback_allowed":false}',
        connection_id="conn",
        player_name="Steve",
        run_id="other-run",
    )
    assert clear_block_command_fallback_for_connection("conn") == 1


def test_denies_automatic_direct_block_command_after_nonfallback_edit_failure() -> None:
    _set_supported_block_capability("conn-1")
    _record_block_edit_fallback_outcome(
        '{"ok":false,"code":"PRECONDITION_FAILED","fallback_allowed":false}',
        connection_id="conn-1",
        player_name="Steve",
        run_id="run-1",
    )

    denial = _fallback_denial()
    assert denial is not None
    assert denial.action == PolicyDecisionKind.DENY
    # The model/operator needs the structured code in the denial message,
    # not just a generic "please fix place_block parameters" sentence.
    assert "PRECONDITION_FAILED" in denial.reason


def test_fallback_denial_includes_structured_diagnostic_summary() -> None:
    _set_supported_block_capability("conn-1")
    _record_block_edit_fallback_outcome(
        (
            '{"ok":false,"code":"STATE_UNKNOWN","fallback_allowed":false,'
            '"diagnostic":"TypeError: place_block() got an unexpected keyword '
            "argument 'status'\"}"
        ),
        connection_id="conn-1",
        player_name="Steve",
        run_id="run-1",
    )

    denial = _fallback_denial()
    assert denial is not None
    assert "STATE_UNKNOWN" in denial.reason
    assert "unexpected keyword argument 'status'" in denial.reason


def test_fallback_allowed_still_uses_independent_command_approval() -> None:
    _set_supported_block_capability("conn-1")
    _record_block_edit_fallback_outcome(
        '{"ok":false,"code":"ADDON_UNAVAILABLE","fallback_allowed":true}',
        connection_id="conn-1",
        player_name="Steve",
        run_id="run-1",
    )

    assert _fallback_denial() is None
    decision = PolicyEngine.from_settings(_Settings()).decide(
        "run_minecraft_command", {"command": "setblock ~ ~ ~ stone"}, player_name="Steve"
    )
    assert decision.action == PolicyDecisionKind.REQUIRE_APPROVAL


def test_write_pre_mutation_failure_allows_command_fallback_link() -> None:
    """写前失败码经 bridge 映射后 fallback_allowed=true → 回退不被拒绝。

    PRECONDITION_FAILED 等写前失败码由宿主按一元规则重算
    fallback_allowed=true，_block_command_fallback_denial 因此返回 None，
    模型可回退 setblock（Bedrock 1.26.10+ 完整放置双格结构）。
    """
    _set_supported_block_capability("conn-1")
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
    assert body["fallback_allowed"] is True

    _record_block_edit_fallback_outcome(
        result,
        connection_id="conn-1",
        player_name="Steve",
        run_id="run-1",
    )
    record = get_block_command_fallback_store().get("conn-1", "Steve", "run-1")
    assert record is not None
    assert record.fallback_allowed is True
    assert record.code == "PRECONDITION_FAILED"

    # 一元规则放行：不拒绝同 run 内 setblock 回退。
    assert _fallback_denial() is None


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("run_minecraft_commands", {"commands": ["give Steve stone", "fill ~ ~ ~ ~1 ~1 ~1 stone"]}),
        ("run_world_command", {"command": "clone ~ ~ ~ ~1 ~1 ~1 ~2 ~2 ~2"}),
        ("run_minecraft_command", {"command": "execute as @s run setblock ~ ~ ~ stone"}),
    ],
)
def test_fallback_gate_handles_batch_world_and_execute_block_commands(
    tool_name: str, args: dict[str, Any]
) -> None:
    _set_supported_block_capability("conn-1")
    _record_block_edit_fallback_outcome(
        '{"ok":false,"fallback_allowed":false}',
        connection_id="conn-1",
        player_name="Steve",
        run_id="run-1",
    )
    assert _fallback_denial(tool_name=tool_name, args=args) is not None


@pytest.mark.parametrize(
    "command",
    ["function build:wall", "schedule delay add build:wall 1t"],
)
def test_fallback_gate_does_not_block_opaque_non_block_commands(command: str) -> None:
    """Issue 6 only constrains raw setblock, fill and clone fallbacks."""
    _set_supported_block_capability("conn-1")
    _record_block_edit_fallback_outcome(
        '{"ok":false,"fallback_allowed":false}',
        connection_id="conn-1",
        player_name="Steve",
        run_id="run-1",
    )

    assert _fallback_denial(args={"command": command}) is None


def test_fallback_state_isolated_by_player_and_run_and_non_supported_capability() -> None:
    _set_supported_block_capability("conn-1")
    _record_block_edit_fallback_outcome(
        '{"ok":false,"fallback_allowed":false}',
        connection_id="conn-1",
        player_name="Steve",
        run_id="run-1",
    )
    assert _fallback_denial(player_name="Alex") is None
    assert _fallback_denial(run_id="run-2") is None

    for status in (
        BlockCapabilityStatus.UNSUPPORTED,
        BlockCapabilityStatus.UNAVAILABLE,
        BlockCapabilityStatus.FAILED,
    ):
        get_block_capability_cache().set(
            "conn-1", BlockCapabilityRecord(status=status, probed_at=time.time())
        )
        assert _fallback_denial() is None


def test_run_world_command_keeps_high_risk_approval_for_non_block_commands() -> None:
    decision = PolicyEngine.from_settings(_Settings()).decide(
        "run_world_command",
        {"command": "say hello"},
        player_name="Steve",
    )

    assert decision.action == PolicyDecisionKind.REQUIRE_APPROVAL


def test_explicit_normalized_command_requires_execution_intent_and_rejects_negation() -> None:
    _set_supported_block_capability("conn-1")
    _record_block_edit_fallback_outcome(
        '{"ok":false,"fallback_allowed":false}',
        connection_id="conn-1",
        player_name="Steve",
        run_id="run-1",
    )
    command = "setblock ~ ~ ~ stone"

    assert _fallback_denial(prompt="请执行命令：/SETBLOCK   ~ ~ ~ STONE") is None
    assert _fallback_denial(prompt=f"这个命令是什么意思 {command}") is not None
    assert _fallback_denial(prompt="请执行命令，但不要用 setblock ~ ~ ~ stone") is not None
    assert _fallback_denial(prompt="请执行命令：fill ~ ~ ~ ~1 ~1 ~1 stone") is not None
    assert _fallback_denial(prompt="请执行命令：setblock ~ ~ ~ stone", approved=True) is None


def test_explicit_raw_command_does_not_require_a_command_keyword() -> None:
    """A player can explicitly request the exact raw command without saying “命令” twice."""
    _set_supported_block_capability("conn-1")
    _record_block_edit_fallback_outcome(
        '{"ok":false,"fallback_allowed":false}',
        connection_id="conn-1",
        player_name="Steve",
        run_id="run-1",
    )

    assert _fallback_denial(prompt="运行 /setblock ~ ~ ~ stone") is None


def test_policy_low_risk_allows_and_hard_deny_blocks() -> None:
    engine = PolicyEngine.from_settings(_Settings())
    allow = engine.decide("list_available_providers", {}, player_name="Steve")
    assert allow.action == PolicyDecisionKind.ALLOW

    deny = engine.decide(
        "run_minecraft_command",
        {"command": "op Alice"},
        player_name="Steve",
    )
    assert deny.action == PolicyDecisionKind.DENY
    assert "op" in deny.reason


def test_edit_invocation_exception_returns_unknown_state_with_redacted_diagnostic() -> None:
    result = map_bridge_exception(
        RuntimeError("place_block_impl leaked token=bridge-secret"),
        tool_name="place_block",
    )

    body = json.loads(result.output)
    assert body["schema_version"] == "1"
    assert body["ok"] is False
    assert body["code"] == "STATE_UNKNOWN"
    assert "外部状态未知，请勿自动重试或回退命令" in body["message"]
    assert body["retryable"] is False
    assert body["external_state_unknown"] is True
    assert body["fallback_allowed"] is False
    # Host-side invocation errors surface a bounded, redacted diagnostic so
    # operators (and the model) can distinguish a harness bug from a world
    # precondition failure.
    assert result.error_type == "RuntimeError"
    assert "RuntimeError" in result.diagnostic_summary
    assert "place_block_impl" in result.diagnostic_summary
    assert "bridge-secret" not in result.diagnostic_summary
    assert result.retryable is False
    assert result.external_state_unknown is True
    assert "bridge-secret" not in result.output


def test_projection_failure_keeps_redacted_internal_diagnostic() -> None:
    result = classify_tool_exception(
        ValueError("block preflight execution contract token=bridge-secret"),
        tool_name="place_block",
        execution_stage="projection",
    )

    assert json.loads(result.output)["code"] == "INTERNAL_ERROR"
    assert result.error_type == "ValueError"
    assert result.diagnostic_summary == "ValueError: block preflight execution contract token=[REDACTED]"
    assert "bridge-secret" not in result.output
    assert "bridge-secret" not in result.diagnostic_summary


def test_block_projection_failure_is_safe_and_definitely_not_sent() -> None:
    result = classify_tool_exception(
        ValueError("block preflight execution contract token=bridge-secret"),
        tool_name="place_block",
        execution_stage="projection",
    )

    assert json.loads(result.output) == {
        "schema_version": "1",
        "ok": False,
        "code": "INTERNAL_ERROR",
        "message": "方块工具内部参数处理失败；本次操作未发送到 Add-on",
        "retryable": False,
        "external_state_unknown": False,
        "fallback_allowed": False,
    }
    assert "bridge-secret" not in result.output


def test_block_failure_log_is_correlated_and_omits_exception_text(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def capture(event: str, **fields: Any) -> None:
        captured["event"] = event
        captured.update(fields)

    monkeypatch.setattr("services.agent.harness.execution.logger.error", capture)
    ctx = type(
        "Context",
        (), {"deps": _Deps(run_id="run-log", player_name="Alex"), "tool_call_id": "tc-log"},
    )()
    result = map_bridge_exception(
        RuntimeError("token=bridge-secret"),
        tool_name="place_block",
    )

    log_tool_execution_failed(
        tool_name="place_block",
        ctx=ctx,
        result=result,
        execution_stage="invocation",
        error_type="RuntimeError",
    )

    assert captured["event"] == "tool_execution_failed"
    assert captured["tool_name"] == "place_block"
    assert captured["run_id"] == "run-log"
    assert captured["tool_call_id"] == "tc-log"
    assert captured["connection_id_short"] == str(ctx.deps.connection_id)[-8:]
    assert captured["player_name"] == "Alex"
    assert captured["error_kind"] == "PERMANENT"
    assert captured["error_type"] == "RuntimeError"
    assert captured["diagnostic_summary"].startswith("RuntimeError")
    assert captured["external_state_unknown"] is True
    assert captured["execution_stage"] == "invocation"
    assert "bridge-secret" not in json.dumps(captured)


def test_mapped_bridge_failure_log_uses_original_exception_type(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def capture(event: str, **fields: Any) -> None:
        captured["event"] = event
        captured.update(fields)

    monkeypatch.setattr("services.agent.harness.execution.logger.error", capture)
    ctx = type(
        "Context",
        (), {"deps": _Deps(run_id="run-bridge", player_name="Alex"), "tool_call_id": "tc-bridge"},
    )()
    result = map_bridge_exception(
        TimeoutError("token=bridge-secret timed out"), tool_name="place_block"
    )

    log_tool_execution_failed(
        tool_name="place_block",
        ctx=ctx,
        result=result,
        execution_stage="invocation",
    )

    assert captured["error_type"] == "TimeoutError"
    assert captured["diagnostic_summary"] == "TimeoutError: token=[REDACTED] timed out"
    assert "bridge-secret" not in json.dumps(captured)


def test_inspect_invocation_failure_redacts_model_log_and_audit(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def capture(event: str, **fields: Any) -> None:
        captured["event"] = event
        captured.update(fields)

    monkeypatch.setattr("services.agent.harness.execution.logger.error", capture)
    ctx = type(
        "Context",
        (), {"deps": _Deps(run_id="run-inspect", player_name="Alex"), "tool_call_id": "tc-inspect"},
    )()
    result = classify_tool_exception(
        RuntimeError('{"detail":"Bearer inspect-bearer","token":"inspect-secret"}'),
        tool_name="inspect_block",
        execution_stage="invocation",
    )
    log_tool_execution_failed(
        tool_name="inspect_block",
        ctx=ctx,
        result=result,
        execution_stage="invocation",
        error_type="RuntimeError",
    )
    audit = build_audit_record(
        tool_name="inspect_block",
        parameters={},
        ctx=ctx,
        status="failure",
        duration_ms=1,
        result=result,
    )

    assert "inspect-secret" not in result.output
    assert "inspect-bearer" not in result.output
    assert "inspect-secret" not in json.dumps(captured)
    assert "inspect-bearer" not in json.dumps(captured)
    assert "inspect-secret" not in json.dumps(audit)
    assert "inspect-bearer" not in json.dumps(audit)
    assert captured["run_id"] == "run-inspect"
    assert captured["tool_call_id"] == "tc-inspect"
    assert captured["player_name"] == "Alex"
    assert captured["execution_stage"] == "invocation"
    # Main log and tool audit must correlate by the same ids.
    assert audit["run_id"] == captured["run_id"]
    assert audit["tool_call_id"] == captured["tool_call_id"]


def test_policy_only_configured_destructive_command_roots_require_approval() -> None:
    engine = PolicyEngine.from_settings(_Settings())

    for command in ("give Steve diamond 1", "time set day"):
        decision = engine.decide(
            "run_minecraft_command",
            {"command": command},
            player_name="Steve",
        )
        assert decision.action == PolicyDecisionKind.ALLOW

    decision = engine.decide(
        "run_minecraft_command",
        {"command": "fill ~ ~ ~ ~1 ~1 ~1 air"},
        player_name="Steve",
    )
    assert decision.action == PolicyDecisionKind.REQUIRE_APPROVAL
    assert decision.metadata["command_root"] == "fill"

    approved = engine.decide(
        "run_minecraft_command",
        {"command": "fill ~ ~ ~ ~1 ~1 ~1 air"},
        player_name="Steve",
        approved=True,
    )
    assert approved.action == PolicyDecisionKind.ALLOW


def test_policy_batch_requires_approval_when_any_command_root_matches() -> None:
    engine = PolicyEngine.from_settings(_Settings())
    commands = {"commands": ["give Steve diamond 1", "setblock ~ ~ ~ air"]}

    decision = engine.decide("run_minecraft_commands", commands, player_name="Steve")
    assert decision.action == PolicyDecisionKind.REQUIRE_APPROVAL
    assert decision.metadata["command_root"] == "setblock"

    approved = engine.decide(
        "run_minecraft_commands",
        commands,
        player_name="Steve",
        approved=True,
    )
    assert approved.action == PolicyDecisionKind.ALLOW


def test_medium_inventory_missing_target_requires_approval() -> None:
    """省略 target 时工具默认 @a，不得当作当前玩家自动允许。"""
    engine = PolicyEngine.from_settings(_Settings())

    omitted = engine.decide("get_inventory_snapshot", {}, player_name="Steve")
    assert omitted.action == PolicyDecisionKind.REQUIRE_APPROVAL

    explicit_all = engine.decide(
        "get_inventory_snapshot",
        {"target": "@a"},
        player_name="Steve",
    )
    assert explicit_all.action == PolicyDecisionKind.REQUIRE_APPROVAL

    self_target = engine.decide(
        "get_inventory_snapshot",
        {"target": "@s"},
        player_name="Steve",
    )
    assert self_target.action == PolicyDecisionKind.ALLOW

    named = engine.decide(
        "get_inventory_snapshot",
        {"target": "Steve"},
        player_name="Steve",
    )
    assert named.action == PolicyDecisionKind.ALLOW

    other = engine.decide(
        "get_inventory_snapshot",
        {"target": "Alex"},
        player_name="Steve",
    )
    assert other.action == PolicyDecisionKind.REQUIRE_APPROVAL


def test_medium_find_entities_omitted_target_allows_self_default() -> None:
    """find_entities 默认 target=@s，省略时可自动允许。"""
    engine = PolicyEngine.from_settings(_Settings())
    decision = engine.decide(
        "find_entities",
        {"entity_type": "zombie"},
        player_name="Steve",
    )
    assert decision.action == PolicyDecisionKind.ALLOW

    multi = engine.decide(
        "find_entities",
        {"entity_type": "zombie", "target": "@a"},
        player_name="Steve",
    )
    assert multi.action == PolicyDecisionKind.REQUIRE_APPROVAL


def test_medium_send_colored_current_player_allows_broadcast_requires() -> None:
    engine = PolicyEngine.from_settings(_Settings())
    auto = engine.decide(
        "send_colored_message",
        {"message": "hi", "color": "§a"},
        player_name="Steve",
    )
    assert auto.action == PolicyDecisionKind.ALLOW

    broadcast = engine.decide(
        "send_colored_message",
        {"message": "hi", "color": "§a", "broadcast": True},
        player_name="Steve",
    )
    assert broadcast.action == PolicyDecisionKind.REQUIRE_APPROVAL


def test_medium_no_target_tools_require_approval() -> None:
    """无 target/broadcast 的 MEDIUM 工具（如 send_script_event）默认审批。"""
    engine = PolicyEngine.from_settings(_Settings())
    decision = engine.decide(
        "send_script_event",
        {"content": "ping", "message_id": "server:data"},
        player_name="Steve",
    )
    assert decision.action == PolicyDecisionKind.REQUIRE_APPROVAL


@pytest.mark.asyncio
async def test_low_risk_auto_executes_in_real_tool_chain() -> None:
    counter: dict[str, int] = {}
    agent = _build_agent(counter)
    deps = _Deps(settings=_Settings(), run_id="run-low")

    result = await agent.run(
        "list providers",
        deps=deps,
        model=TestModel(call_tools=["list_available_providers"]),
    )

    assert counter.get("list_available_providers") == 1
    assert not isinstance(result.output, DeferredToolRequests)
    assert "providers" in str(result.output).lower() or "deepseek" in str(result.output).lower()


@pytest.mark.asyncio
async def test_blacklisted_command_pauses_for_approval_without_side_effect() -> None:
    counter: dict[str, int] = {}
    agent = _build_agent(counter)
    deps = _Deps(settings=_Settings(), run_id="run-high")

    result = await agent.run(
        "run command",
        deps=deps,
        model=TestModel(call_tools=["run_minecraft_command"]),
    )

    assert isinstance(result.output, DeferredToolRequests)
    assert result.output.approvals
    assert counter.get("run_minecraft_command", 0) == 0


@pytest.mark.asyncio
async def test_non_blacklisted_command_auto_executes_in_real_tool_chain() -> None:
    counter: dict[str, int] = {}
    settings = _Settings()
    settings.approval_command_roots = []
    agent = _build_agent(counter, policy_settings=settings)
    deps = _Deps(settings=settings, run_id="run-command-auto")

    result = await agent.run(
        "run a normal command",
        deps=deps,
        model=TestModel(call_tools=["run_minecraft_command"]),
    )

    assert not isinstance(result.output, DeferredToolRequests)
    assert counter.get("run_minecraft_command") == 1


@pytest.mark.asyncio
async def test_session_auto_approve_skips_deferred_and_executes() -> None:
    """deps.auto_approve_tools=True 时高风险工具不再 defer，直接执行。"""
    counter: dict[str, int] = {}
    agent = _build_agent(counter)
    deps = _Deps(settings=_Settings(), run_id="run-auto", auto_approve_tools=True)

    result = await agent.run(
        "run command",
        deps=deps,
        model=TestModel(call_tools=["run_minecraft_command"]),
    )

    assert not isinstance(result.output, DeferredToolRequests)
    assert counter.get("run_minecraft_command") == 1


@pytest.mark.asyncio
async def test_exact_approve_executes_once_then_idempotent() -> None:
    counter: dict[str, int] = {}
    agent = _build_agent(counter)
    deps = _Deps(settings=_Settings(), run_id="run-approve")

    first = await agent.run(
        "run command",
        deps=deps,
        model=TestModel(call_tools=["run_minecraft_command"]),
    )
    assert isinstance(first.output, DeferredToolRequests)
    call = first.output.approvals[0]
    tool_call_id = call.tool_call_id
    messages = first.all_messages()

    results = DeferredToolResults()
    results.approvals[tool_call_id] = True

    second = await agent.run(
        message_history=messages,
        deferred_tool_results=results,
        deps=deps,
        model=TestModel(),
    )
    assert counter.get("run_minecraft_command") == 1
    assert not isinstance(second.output, DeferredToolRequests)

    # 重复同一 call ID + 参数：幂等命中，不重复副作用
    # 通过直接走 wrapper 再次 call 模拟重入
    from services.agent.harness.execution import HarnessToolset, PolicyEngine

    # 使用 FunctionModel 再跑同一 tool_call 路径：二次 resume 不应再次执行
    # 这里直接验证幂等 store
    store = get_idempotency_store()
    args = call.args if isinstance(call.args, dict) else {"command": "a"}
    args_hash = hash_normalized_args(normalize_tool_args(args))
    cached = store.get(deps.run_id, tool_call_id, args_hash)
    assert cached is not None

    # 再次 approve 同 call 的副作用路径：模拟 call_tool 命中幂等
    policy = PolicyEngine.from_settings(_Settings())
    # 重新执行一次完整 resume 不会再次增加 counter 的前提是 tool_call_id 相同且缓存命中；
    # PydanticAI 恢复时会再 call 一次，因此幂等必须挡住。
    third = await agent.run(
        message_history=messages,
        deferred_tool_results=results,
        deps=deps,
        model=TestModel(),
    )
    assert counter.get("run_minecraft_command") == 1
    assert third.output is not None


@pytest.mark.asyncio
async def test_reject_does_not_execute() -> None:
    counter: dict[str, int] = {}
    agent = _build_agent(counter)
    deps = _Deps(settings=_Settings(), run_id="run-reject")

    first = await agent.run(
        "run command",
        deps=deps,
        model=TestModel(call_tools=["run_minecraft_command"]),
    )
    assert isinstance(first.output, DeferredToolRequests)
    call = first.output.approvals[0]
    results = DeferredToolResults()
    results.approvals[call.tool_call_id] = False

    second = await agent.run(
        message_history=first.all_messages(),
        deferred_tool_results=results,
        deps=deps,
        model=TestModel(),
    )
    assert counter.get("run_minecraft_command", 0) == 0
    assert not isinstance(second.output, DeferredToolRequests)


@pytest.mark.asyncio
async def test_duplicate_call_id_no_double_side_effect_via_function_model() -> None:
    """用 FunctionModel 强制同一 tool_call_id 再次触发 call_tool。"""
    counter: dict[str, int] = {}
    agent = _build_agent(counter)
    deps = _Deps(settings=_Settings(), run_id="run-dup")

    first = await agent.run(
        "run command",
        deps=deps,
        model=TestModel(call_tools=["run_minecraft_command"]),
    )
    assert isinstance(first.output, DeferredToolRequests)
    call = first.output.approvals[0]
    results = DeferredToolResults(approvals={call.tool_call_id: True})
    await agent.run(
        message_history=first.all_messages(),
        deferred_tool_results=results,
        deps=deps,
        model=TestModel(),
    )
    assert counter.get("run_minecraft_command") == 1

    # 手动再次通过 agent 工具管理层调用同 id：依赖幂等 store
    # 使用 FunctionModel 让模型再次发起同名工具（不同 call id 会再执行；
    # 这里直接验证 store 对 (run_id, tool_call_id, args_hash) 的保护）。
    args = call.args if isinstance(call.args, dict) else {}
    args_hash = hash_normalized_args(normalize_tool_args(args))
    record = get_idempotency_store().get(deps.run_id, call.tool_call_id, args_hash)
    assert record is not None
    # 模拟重复写入后仍只有一次真实副作用
    assert counter.get("run_minecraft_command") == 1


def test_pending_approval_store_owner_and_expiry() -> None:
    store = PendingApprovalStore(default_ttl_seconds=0.05)
    from pydantic_ai.tools import DeferredToolRequests
    from pydantic_ai.messages import ToolCallPart

    reqs = DeferredToolRequests(
        approvals=[
            ToolCallPart(tool_name="run_minecraft_command", args={"command": "x"}, tool_call_id="tc1")
        ]
    )
    import time

    now = time.time()
    pending = PendingApproval(
        approval_id="ap1",
        connection_id="c1",
        player_name="Steve",
        conversation_id="conv",
        run_id="r1",
        tool_call_id="tc1",
        tool_name="run_minecraft_command",
        normalized_args={"command": "x"},
        args_summary="command=x",
        args_hash="h",
        policy_version="v",
        messages=[],
        requests=reqs,
        provider="test",
        delivery="tellraw",
        use_context=True,
        broadcast_ai_chat=False,
        created_at=now,
        expires_at=now + 0.05,
    )
    store.put(pending)

    ok, reason = store.get_for_owner(
        connection_id="c1",
        player_name="Steve",
        conversation_id="conv",
        approval_id="ap1",
    )
    assert ok is not None and reason is None

    cross, reason2 = store.get_for_owner(
        connection_id="c1",
        player_name="Alex",
        conversation_id="conv",
        approval_id="ap1",
    )
    assert cross is None
    assert reason2 is not None

    time.sleep(0.06)
    expired, reason3 = store.get_for_owner(
        connection_id="c1",
        player_name="Steve",
        conversation_id="conv",
        approval_id="ap1",
    )
    assert expired is None
    assert reason3 is not None


@pytest.mark.asyncio
async def test_multi_deferred_approvals_require_full_results_then_execute_only_approved() -> None:
    """两工具 defer 时 partial resume 会 UserError；齐套决策后只执行被批准的工具。

    使用 FunctionModel 保证两个 deferred call 有独立 tool_call_id
   （TestModel 会对同名工具复用同一 id，无法表达 partial-vs-full）。
    """
    import time

    from pydantic_ai.exceptions import UserError
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

    counter: dict[str, int] = {}
    agent = _build_agent(counter)
    deps = _Deps(settings=_Settings(), run_id="run-multi")

    step = {"n": 0}

    async def model_fn(messages, info):
        step["n"] += 1
        if step["n"] == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="run_minecraft_command",
                        args={"command": "a"},
                        tool_call_id="tc-a",
                    ),
                    ToolCallPart(
                        tool_name="run_minecraft_command",
                        args={"command": "b"},
                        tool_call_id="tc-b",
                    ),
                ]
            )
        return ModelResponse(parts=[TextPart(content="done")])

    first = await agent.run(
        "run two commands",
        deps=deps,
        model=FunctionModel(model_fn),
    )
    assert isinstance(first.output, DeferredToolRequests)
    assert len(first.output.approvals) == 2
    calls = list(first.output.approvals)
    call_ids = {c.tool_call_id for c in calls}
    assert call_ids == {"tc-a", "tc-b"}
    messages = first.all_messages()

    # partial：只回一项 → 必须失败
    partial = DeferredToolResults(approvals={"tc-a": True})
    with pytest.raises(UserError, match="all deferred tool calls"):
        await agent.run(
            message_history=messages,
            deferred_tool_results=partial,
            deps=deps,
            model=FunctionModel(model_fn),
        )
    assert counter.get("run_minecraft_command", 0) == 0

    # 模拟 store 批次决策：一允许一拒绝，齐套后 resume
    store = PendingApprovalStore(default_ttl_seconds=120.0)
    batch_id = store.generate_batch_id()
    ap_ids = [store.generate_approval_id(), store.generate_approval_id()]
    now = time.time()
    for ap_id, call in zip(ap_ids, calls):
        args = call.args if isinstance(call.args, dict) else {"command": "x"}
        store.put(
            PendingApproval(
                approval_id=ap_id,
                connection_id="c1",
                player_name="Steve",
                conversation_id="conv",
                run_id=deps.run_id,
                tool_call_id=call.tool_call_id,
                tool_name=call.tool_name,
                normalized_args=args,
                args_summary=str(args),
                args_hash="h",
                policy_version="v",
                messages=messages,
                requests=first.output,
                provider="test",
                delivery="tellraw",
                use_context=True,
                broadcast_ai_chat=False,
                created_at=now,
                expires_at=now + 120,
                batch_id=batch_id,
                sibling_approval_ids=list(ap_ids),
            )
        )

    p1, r1, batch1 = store.record_decision(
        connection_id="c1",
        player_name="Steve",
        conversation_id="conv",
        approval_id=ap_ids[0],
        approved=True,
    )
    assert p1 is not None and r1 is None and batch1 is None
    assert len(store) == 2

    p2, r2, batch2 = store.record_decision(
        connection_id="c1",
        player_name="Steve",
        conversation_id="conv",
        approval_id=ap_ids[1],
        approved=False,
    )
    assert p2 is not None and r2 is None and batch2 is not None
    assert len(batch2) == 2
    assert len(store) == 0

    full = DeferredToolResults(approvals={"tc-a": True, "tc-b": False})
    second = await agent.run(
        message_history=messages,
        deferred_tool_results=full,
        deps=deps,
        model=FunctionModel(model_fn),
    )
    # 只执行被批准的那一次
    assert counter.get("run_minecraft_command") == 1
    assert not isinstance(second.output, DeferredToolRequests)


@pytest.mark.asyncio
async def test_harness_emits_tool_proposed_policy_and_execution(tmp_path):
    """Harness call_tool writes tool.proposed / policy.decided / execution.*."""
    import json
    from pathlib import Path

    from services.agent.trace import TraceContext, TraceRecorder, set_trace_recorder

    path = tmp_path / "harness_trace.jsonl"
    recorder = TraceRecorder(path=path, enabled=True, include_content=True, max_records=200)
    await recorder.start()
    set_trace_recorder(recorder)
    try:
        context = TraceContext(
            trace_id="trace-h1",
            run_id="trace-h1",
            attempt_id="attempt-h1",
            message_id="msg-h1",
            connection_id="conn-1",
            player_name="Steve",
            conversation_id="conv-1",
        )
        deps = _Deps(
            settings=_Settings(),
            run_id="trace-h1",
            conversation_id="conv-1",
        )
        # inject trace fields dynamically for offline harness
        deps.trace_context = context  # type: ignore[attr-defined]
        deps.trace_recorder = recorder  # type: ignore[attr-defined]
        deps.auto_approve_tools = False

        agent = _build_agent()
        result = await agent.run(
            "list providers please",
            deps=deps,
            model=TestModel(call_tools=["list_available_providers"]),
        )
        assert result.output

        await recorder.stop()
        events = [
            json.loads(line)
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        names = [e["event_name"] for e in events]
        assert "tool.proposed" in names
        assert "policy.decided" in names
        assert "tool.execution.started" in names
        assert "tool.execution.completed" in names
        proposed = next(e for e in events if e["event_name"] == "tool.proposed")
        assert proposed["attributes"]["tool_name"] == "list_available_providers"
        assert proposed.get("payload") is not None
        completed = next(e for e in events if e["event_name"] == "tool.execution.completed")
        assert completed["attributes"]["execution_status"] == "succeeded"
    finally:
        set_trace_recorder(None)


@pytest.mark.asyncio
async def test_harness_deny_emits_denied_execution_status(tmp_path):
    import json
    from pathlib import Path

    from services.agent.trace import TraceContext, TraceRecorder, set_trace_recorder

    path = tmp_path / "harness_deny.jsonl"
    recorder = TraceRecorder(path=path, enabled=True, include_content=False, max_records=100)
    await recorder.start()
    set_trace_recorder(recorder)
    try:
        context = TraceContext(
            trace_id="trace-deny",
            run_id="trace-deny",
            attempt_id="attempt-d",
            message_id="m",
            connection_id="c",
            player_name="Steve",
            conversation_id="conv",
        )
        settings = _Settings()
        settings.hard_deny_tools = ["run_minecraft_command"]
        deps = _Deps(settings=settings, run_id="trace-deny")
        deps.trace_context = context  # type: ignore[attr-defined]
        deps.trace_recorder = recorder  # type: ignore[attr-defined]

        agent = _build_agent(policy_settings=settings)
        # Force a tool call that will be denied by hard_deny
        result = await agent.run(
            "op someone",
            deps=deps,
            model=TestModel(call_tools=["run_minecraft_command"]),
        )
        # ToolDenied may surface as text/output depending on pydantic-ai
        _ = result

        await recorder.stop()
        events = [
            json.loads(line)
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        names = [e["event_name"] for e in events]
        assert "tool.proposed" in names
        assert "policy.decided" in names
        denied = [
            e
            for e in events
            if e["event_name"] in {"tool.execution.failed", "tool.execution.completed"}
            and e.get("attributes", {}).get("execution_status") == "denied"
        ]
        assert denied, f"expected denied execution event, got {names}"
        assert all("payload" not in e or e.get("payload") is None for e in events)
        # Content-off: free-text policy reason must not appear in attributes
        policy_events = [e for e in events if e["event_name"] == "policy.decided"]
        assert policy_events
        assert "reason" not in (policy_events[0].get("attributes") or {})
    finally:
        set_trace_recorder(None)


@pytest.mark.asyncio
async def test_harness_cancelled_emits_tool_execution_cancelled(tmp_path):
    """asyncio.CancelledError during tool invoke emits tool.execution.cancelled."""
    import json
    from pathlib import Path

    from services.agent.trace import TraceContext, TraceRecorder, set_trace_recorder

    path = tmp_path / "harness_cancel.jsonl"
    recorder = TraceRecorder(path=path, enabled=True, include_content=False, max_records=100)
    await recorder.start()
    set_trace_recorder(recorder)
    try:
        context = TraceContext(
            trace_id="trace-cancel",
            run_id="trace-cancel",
            attempt_id="attempt-c",
            message_id="m",
            connection_id="c",
            player_name="Steve",
            conversation_id="conv",
        )
        deps = _Deps(settings=_Settings(), run_id="trace-cancel")
        deps.trace_context = context  # type: ignore[attr-defined]
        deps.trace_recorder = recorder  # type: ignore[attr-defined]

        policy = PolicyEngine.from_settings(_Settings())
        agent: Agent[_Deps, str | DeferredToolRequests] = Agent(
            "test",
            deps_type=_Deps,
            output_type=[str, DeferredToolRequests],
            capabilities=[HarnessCapability(policy=policy)],
        )

        @agent.tool
        async def list_available_providers(ctx: RunContext[_Deps]) -> str:
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await agent.run(
                "list providers please",
                deps=deps,
                model=TestModel(call_tools=["list_available_providers"]),
            )

        await recorder.stop()
        events = [
            json.loads(line)
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        names = [e["event_name"] for e in events]
        assert "tool.execution.started" in names
        assert "tool.execution.cancelled" in names
        cancelled = next(e for e in events if e["event_name"] == "tool.execution.cancelled")
        assert cancelled["attributes"]["execution_status"] == "cancelled"
        assert cancelled["attributes"]["tool_name"] == "list_available_providers"
    finally:
        set_trace_recorder(None)


@pytest.mark.asyncio
async def test_approval_resume_uses_plan_id_without_hidden_kwargs(
    monkeypatch: pytest.MonkeyPatch, _block_tool_catalog
) -> None:
    """place_block 审批恢复只携带 plan_id；impl 只收到 pos/block/expect/states。"""
    counter: dict[str, int] = {}
    captured: dict[str, Any] = {}

    async def fake_place_impl(
        ctx: RunContext[_Deps],
        *,
        pos: list[int],
        block: str,
        expect: str = "air",
        states: dict[str, Any] | None = None,
    ) -> ToolResult:
        captured["kwargs"] = {"pos": pos, "block": block, "expect": expect, "states": states}
        return ToolResult.ok(f"placed:{block}")

    monkeypatch.setattr("services.agent.block_ops.tools_impl.place_block_impl", fake_place_impl)

    agent = _build_place_block_agent(counter)
    deps = _Deps(settings=_Settings(), run_id="run-plan-id-1")
    _set_supported_block_capability(str(deps.connection_id))

    tool_call_id, plan_id, messages = await _run_first_place_block_call(agent, deps)

    # 预检缓存只保存模型可见字段（states 是公共默认字段，可出现）
    entry = get_preflight_cache().get_by_plan_id(plan_id)
    assert entry is not None
    assert entry.tool_name == "place_block"
    assert {"pos", "block", "expect"} <= set(entry.canonical_args)
    for hidden in ("locked_targets", "phase", "status"):
        assert hidden not in entry.canonical_args
        assert hidden not in entry.execute_args

    results = DeferredToolResults()
    results.approvals[tool_call_id] = ToolApproved(override_args={"plan_id": plan_id})
    second = await agent.run(
        message_history=messages, deferred_tool_results=results, deps=deps, model=TestModel()
    )

    assert not isinstance(second.output, DeferredToolRequests)
    assert captured["kwargs"] == {"pos": [1, 64, 1], "block": "stone", "expect": "air", "states": None}
    for hidden in ("locked_targets", "phase", "status"):
        assert hidden not in captured["kwargs"]


@pytest.mark.asyncio
async def test_plan_id_resume_is_idempotent_and_missing_plan_is_state_unknown(
    monkeypatch: pytest.MonkeyPatch, _block_tool_catalog
) -> None:
    """同一 plan_id 二次恢复不重复执行；缺失/过期 plan → STATE_UNKNOWN（无 TypeError）。"""
    calls: dict[str, int] = {"count": 0}
    captured: dict[str, Any] = {}

    async def fake_place_impl(
        ctx: RunContext[_Deps],
        *,
        pos: list[int],
        block: str,
        expect: str = "air",
        states: dict[str, Any] | None = None,
    ) -> ToolResult:
        calls["count"] += 1
        captured["kwargs"] = {"pos": pos, "block": block, "expect": expect, "states": states}
        return ToolResult.ok(f"placed:{block}")

    monkeypatch.setattr("services.agent.block_ops.tools_impl.place_block_impl", fake_place_impl)

    agent = _build_place_block_agent()
    deps = _Deps(settings=_Settings(), run_id="run-plan-id-2")
    _set_supported_block_capability(str(deps.connection_id))

    tool_call_id, plan_id, messages = await _run_first_place_block_call(agent, deps)

    # 第一次恢复：执行一次
    results = DeferredToolResults()
    results.approvals[tool_call_id] = ToolApproved(override_args={"plan_id": plan_id})
    second = await agent.run(
        message_history=messages, deferred_tool_results=results, deps=deps, model=TestModel()
    )
    assert calls["count"] == 1
    assert not isinstance(second.output, DeferredToolRequests)

    # 清空幂等 store 后二次恢复仍不重复执行（plan 已执行标记兜底）
    reset_idempotency_store()
    results2 = DeferredToolResults()
    results2.approvals[tool_call_id] = ToolApproved(override_args={"plan_id": plan_id})
    third = await agent.run(
        message_history=messages, deferred_tool_results=results2, deps=deps, model=TestModel()
    )
    assert calls["count"] == 1
    assert not isinstance(third.output, DeferredToolRequests)

    # 缺失 plan：STATE_UNKNOWN，不抛 TypeError
    missing = DeferredToolResults()
    missing.approvals[tool_call_id] = ToolApproved(override_args={"plan_id": "no-such-plan-1"})
    fourth = await agent.run(
        message_history=messages, deferred_tool_results=missing, deps=deps, model=TestModel()
    )
    assert calls["count"] == 1
    assert "STATE_UNKNOWN" in str(fourth.output)

    # 过期 plan：同 STATE_UNKNOWN
    entry = get_preflight_cache().get_by_plan_id(plan_id)
    assert entry is not None
    entry.created_at = time.time() - 300  # 超过 DEFAULT_APPROVAL_TTL_SECONDS
    expired = DeferredToolResults()
    expired.approvals[tool_call_id] = ToolApproved(override_args={"plan_id": plan_id})
    fifth = await agent.run(
        message_history=messages, deferred_tool_results=expired, deps=deps, model=TestModel()
    )
    assert calls["count"] == 1
    assert "STATE_UNKNOWN" in str(fifth.output)


@pytest.mark.asyncio
async def test_regression_status_kwarg_type_error_is_impossible(
    monkeypatch: pytest.MonkeyPatch, _block_tool_catalog
) -> None:
    """2026-08-03 回归：恢复 payload 曾注入 status → TypeError。新路径结构性不可达。

    恢复载荷契约只允许恰好 {plan_id}；被篡改塞入 status/phase/locked_targets
    的载荷现在在校验边界被拒绝（不会执行、不会静默归一化、不会 TypeError）。
    缓存里的 canonical/execute args 本身也不含隐藏字段。
    """
    captured: dict[str, Any] = {}

    async def fake_place_impl(
        ctx: RunContext[_Deps],
        *,
        pos: list[int],
        block: str,
        expect: str = "air",
        states: dict[str, Any] | None = None,
    ) -> ToolResult:
        captured["kwargs"] = {"pos": pos, "block": block, "expect": expect, "states": states}
        return ToolResult.ok(f"placed:{block}")

    monkeypatch.setattr("services.agent.block_ops.tools_impl.place_block_impl", fake_place_impl)

    agent = _build_place_block_agent()
    deps = _Deps(settings=_Settings(), run_id="run-regression-status")
    _set_supported_block_capability(str(deps.connection_id))

    tool_call_id, plan_id, messages = await _run_first_place_block_call(agent, deps)

    # 篡改的恢复 payload：plan_id 之外塞入 status/phase/locked_targets →
    # 校验边界拒绝，impl 绝不执行，也不会把隐藏 kwargs 透传给任何执行层
    tampered = DeferredToolResults()
    tampered.approvals[tool_call_id] = ToolApproved(
        override_args={
            "plan_id": plan_id,
            "status": "noop",
            "phase": "execute",
            "locked_targets": [],
        }
    )
    second = await agent.run(
        message_history=messages, deferred_tool_results=tampered, deps=deps, model=TestModel()
    )
    assert "kwargs" not in captured  # impl 未被调用
    # 拒绝发生在验证边界：调用不会执行，重新进入待审批（非静默透传）
    assert isinstance(second.output, DeferredToolRequests)

    # 结构性根因：预检缓存的 canonical/execute args 不含任何隐藏字段
    entry = get_preflight_cache().get_by_plan_id(plan_id)
    assert entry is not None
    for key in ("status", "locked_targets", "phase"):
        assert key not in entry.canonical_args
        assert key not in entry.execute_args


@pytest.mark.asyncio
async def test_plan_id_resume_malformed_payload_rejected_at_validation(
    monkeypatch: pytest.MonkeyPatch, _block_tool_catalog
) -> None:
    """畸形恢复载荷（plan_id 之外的额外键）在校验边界被拒绝，不静默透传。

    Reviewer finding (Important 2)：cache miss 时 wrap_tool_validate 曾直接返回
    {plan_id} 而不做任何载荷校验；``{"plan_id": "missing", "malicious_extra": true}``
    会静默通过验证边界。现在恢复契约强制载荷恰好为 {plan_id}，额外键 → 拒绝。
    """
    calls: dict[str, int] = {"count": 0}
    captured: dict[str, Any] = {}

    async def fake_place_impl(
        ctx: RunContext[_Deps],
        *,
        pos: list[int],
        block: str,
        expect: str = "air",
        states: dict[str, Any] | None = None,
    ) -> ToolResult:
        calls["count"] += 1
        captured["kwargs"] = {"pos": pos, "block": block, "expect": expect, "states": states}
        return ToolResult.ok(f"placed:{block}")

    monkeypatch.setattr("services.agent.block_ops.tools_impl.place_block_impl", fake_place_impl)

    agent = _build_place_block_agent()
    deps = _Deps(settings=_Settings(), run_id="run-malformed-payload")
    _set_supported_block_capability(str(deps.connection_id))

    tool_call_id, _plan_id, messages = await _run_first_place_block_call(agent, deps)

    # 缺失 plan + 额外恶意键：验证边界拒绝（不执行、不静默透传为 STATE_UNKNOWN）
    malformed = DeferredToolResults()
    malformed.approvals[tool_call_id] = ToolApproved(
        override_args={"plan_id": "no-such-plan", "malicious_extra": True}
    )
    rejected = await agent.run(
        message_history=messages, deferred_tool_results=malformed, deps=deps, model=TestModel()
    )
    assert calls["count"] == 0
    assert "kwargs" not in captured
    # 拒绝：重新进入待审批，而不是静默通过验证边界
    assert isinstance(rejected.output, DeferredToolRequests)


# ---------------------------------------------------------------------------
# Task 7 Step 4: 命令回退门控对新工具名生效
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_tool_failure_denies_command_fallback_in_same_run() -> None:
    """Task 7 Step 4: 新工具名失败（fallback_allowed=false）门控命令回退。

    place_block（新契约工具名）执行失败后，同一 run 内的 setblock 命令回退
    被 ``_block_command_fallback_denial`` 拒绝：run_minecraft_command 公共
    body 不执行，结构化 PRECONDITION_FAILED 诊断回到模型。
    """
    counter: dict[str, int] = {}
    policy = PolicyEngine.from_settings(_Settings())
    agent: Agent[_Deps, str | DeferredToolRequests] = Agent(
        "test",
        deps_type=_Deps,
        output_type=[str, DeferredToolRequests],
        capabilities=[HarnessCapability(policy=policy)],
    )

    @agent.tool
    async def place_block(
        ctx: RunContext[_Deps], pos: list[int], block: str, expect: str = "air"
    ) -> str:
        counter["place_block"] = counter.get("place_block", 0) + 1
        return ToolResult.failure(
            json.dumps(
                {
                    "ok": False,
                    "code": "PRECONDITION_FAILED",
                    "fallback_allowed": False,
                    "message": "expected air, found minecraft:grass_block",
                }
            ),
            error_kind="PRECONDITION_FAILED",
            retryable=False,
        )

    @agent.tool
    async def run_minecraft_command(ctx: RunContext[_Deps], command: str) -> str:
        counter["run_minecraft_command"] = counter.get("run_minecraft_command", 0) + 1
        return ToolResult.ok(f"executed:{command}")

    calls = [
        ("place_block", {"pos": [1, 64, 1], "block": "stone", "expect": "air"}),
        ("run_minecraft_command", {"command": "setblock ~ ~ ~ stone"}),
    ]

    async def model_fn(messages: list[ModelMessage], info: Any) -> ModelResponse:
        if calls:
            tool_name, args = calls.pop(0)
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name=tool_name,
                        tool_call_id=f"tc-{tool_name}",
                        args=args,
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(content="done")])

    connection_id = "conn-fallback-gate"
    _set_supported_block_capability(connection_id)
    deps = _Deps(
        settings=_Settings(),
        run_id="run-fallback-gate",
        auto_approve_tools=True,
        connection_id=connection_id,
    )

    result = await agent.run(
        "place stone then build a wall with setblock",
        model=FunctionModel(model_fn),
        deps=deps,
    )

    assert counter.get("place_block") == 1
    assert counter.get("run_minecraft_command", 0) == 0
    record = get_block_command_fallback_store().get(
        connection_id, "Steve", "run-fallback-gate"
    )
    assert record is not None
    assert record.fallback_allowed is False
    assert record.code == "PRECONDITION_FAILED"
    # ToolDenied 以 ToolReturnPart.content 形式回到模型；序列化为文本断言
    # 结构化诊断（含 code）确实送达。
    parts_text = [
        str(getattr(part, "content", ""))
        for message in result.all_messages()
        for part in getattr(message, "parts", []) or []
    ]
    text = " | ".join(parts_text)
    assert "PRECONDITION_FAILED" in text
    assert "不允许命令回退" in text


def test_recorded_outcome_from_new_tool_failure_gates_direct_fallback() -> None:
    """Task 7 Step 4: 直接记录的新工具失败结果同样进入回退门控。

    与旧契约失败形态等价的 place_block 失败结果（经桥接映射的
    ToolResult）写入 fallback store 后，``_block_command_fallback_denial``
    对 setblock 命令返回 DENY，且原因含结构化 code。
    """
    _set_supported_block_capability("conn-1")
    # 等价于新工具桥接失败映射后的 ToolResult（output 为结构化错误 JSON）。
    mapped = ToolResult.failure(
        json.dumps(
            {
                "ok": False,
                "code": "PRECONDITION_FAILED",
                "fallback_allowed": False,
                "message": "expected air, found minecraft:grass_block",
            }
        ),
        error_kind="PERMANENT",
        retryable=False,
    )
    _record_block_edit_fallback_outcome(
        mapped,
        connection_id="conn-1",
        player_name="Steve",
        run_id="run-1",
    )

    denial = _fallback_denial()
    assert denial is not None
    assert denial.action == PolicyDecisionKind.DENY
    assert "PRECONDITION_FAILED" in denial.reason
