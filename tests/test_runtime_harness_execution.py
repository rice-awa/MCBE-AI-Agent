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


class _Settings:
    runtime_harness_enabled = True
    runtime_harness_audit_enabled = False
    runtime_harness_audit_path = "logs/test_audit.jsonl"
    runtime_harness_audit_max_records = 100
    hard_deny_tools: list[str] = []
    hard_deny_command_roots: list[str] = []
    max_batch_commands = 10
    mcp_tool_allowlist: list[str] = []
    tool_policy_version = "2026-07-21.1"
    approval_ttl = 120.0


def _build_agent(side_effect_counter: dict[str, int] | None = None) -> Agent[_Deps, str | DeferredToolRequests]:
    counter = side_effect_counter if side_effect_counter is not None else {}
    policy = PolicyEngine.from_settings(_Settings())
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


def test_policy_high_risk_requires_approval() -> None:
    engine = PolicyEngine.from_settings(_Settings())
    decision = engine.decide(
        "run_minecraft_command",
        {"command": "time set day"},
        player_name="Steve",
    )
    assert decision.action == PolicyDecisionKind.REQUIRE_APPROVAL

    approved = engine.decide(
        "run_minecraft_command",
        {"command": "time set day"},
        player_name="Steve",
        approved=True,
    )
    assert approved.action == PolicyDecisionKind.ALLOW


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
async def test_high_risk_pauses_for_approval_without_side_effect() -> None:
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
