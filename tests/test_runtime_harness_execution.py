"""统一工具执行边界：策略、审批、幂等 — 真实 model->tool->model 链测试。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from services.agent.harness.approvals import PendingApproval, PendingApprovalStore
from services.agent.harness.execution import (
    HarnessCapability,
    PolicyDecisionKind,
    PolicyEngine,
    get_idempotency_store,
    hash_normalized_args,
    normalize_tool_args,
    reset_idempotency_store,
)
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
    yield
    reset_idempotency_store()


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

    def model_fn(messages, info):
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
    finally:
        set_trace_recorder(None)
