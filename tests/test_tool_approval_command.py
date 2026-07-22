"""Tool approve/deny command registration + PendingApprovalStore boundaries.

覆盖：
- 命令注册 / 别名
- owner / 过期 / 跨玩家边界
- 同批多工具齐套决策
- 省略 id 的智能解析
- 会话级自动同意（对话 / 永远）作用域
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import DeferredToolRequests

from config.settings import MinecraftConfig
from mcbe_ws_sdk.command.registry import CommandRegistry
from services.agent.harness.approvals import PendingApproval, PendingApprovalStore
from services.agent.harness.execution import hash_normalized_args, normalize_tool_args
from services.gateway.session_store import HostSessionStore
from services.gateway.command_handlers import CommandHandlers


def _make_pending(
    *,
    approval_id: str,
    connection_id: str = "conn-1",
    player_name: str = "Steve",
    conversation_id: str = "conv-1",
    tool_call_id: str = "tc1",
    batch_id: str = "",
    sibling_ids: list[str] | None = None,
    ttl: float = 120.0,
    cmd: str = "x",
) -> PendingApproval:
    now = time.time()
    reqs = DeferredToolRequests(
        approvals=[
            ToolCallPart(
                tool_name="run_minecraft_command",
                args={"command": cmd},
                tool_call_id=tool_call_id,
            )
        ]
    )
    return PendingApproval(
        approval_id=approval_id,
        connection_id=connection_id,
        player_name=player_name,
        conversation_id=conversation_id,
        run_id="run-1",
        tool_call_id=tool_call_id,
        tool_name="run_minecraft_command",
        normalized_args={"command": cmd},
        args_summary=f"command={cmd}",
        args_hash="h",
        policy_version="v",
        messages=[],
        requests=reqs,
        provider="test",
        delivery="tellraw",
        use_context=True,
        broadcast_ai_chat=False,
        created_at=now,
        expires_at=now + ttl,
        batch_id=batch_id or approval_id,
        sibling_approval_ids=sibling_ids or [approval_id],
    )


def test_tool_approve_and_deny_commands_are_registered_in_defaults() -> None:
    registry = CommandRegistry(MinecraftConfig().commands)

    approve = registry.resolve_parsed("AGENT 同意 abcd")
    assert approve is not None
    assert approve.type == "tool_approve"
    assert approve.content == "abcd"

    deny = registry.resolve_parsed("AGENT 拒绝 abcd")
    assert deny is not None
    assert deny.type == "tool_deny"
    assert deny.content == "abcd"

    approve_alias = registry.resolve_parsed("AGENT approve abcd")
    assert approve_alias is not None
    assert approve_alias.type == "tool_approve"
    assert approve_alias.content == "abcd"

    deny_alias = registry.resolve_parsed("AI deny abcd")
    assert deny_alias is not None
    assert deny_alias.type == "tool_deny"
    assert deny_alias.content == "abcd"

    # 省略 id / 对话 / 永远 也应被解析为 tool_approve，content 保留子命令
    bare = registry.resolve_parsed("AGENT 同意")
    assert bare is not None
    assert bare.type == "tool_approve"
    assert bare.content == ""

    forever = registry.resolve_parsed("AGENT 同意 永远")
    assert forever is not None
    assert forever.type == "tool_approve"
    assert forever.content == "永远"

    conv = registry.resolve_parsed("AGENT 同意 对话")
    assert conv is not None
    assert conv.type == "tool_approve"
    assert conv.content == "对话"


def test_pending_approval_owner_expired_cross_player_boundaries() -> None:
    """审批边界：owner / 过期 / 跨玩家。"""
    store = PendingApprovalStore(default_ttl_seconds=0.05)
    pending = _make_pending(approval_id="ap1", ttl=0.05)
    store.put(pending)

    owner, reason = store.get_for_owner(
        connection_id="conn-1",
        player_name="Steve",
        conversation_id="conv-1",
        approval_id="ap1",
    )
    assert owner is not None and reason is None

    cross, cross_reason = store.get_for_owner(
        connection_id="conn-1",
        player_name="Alex",
        conversation_id="conv-1",
        approval_id="ap1",
    )
    assert cross is None
    assert "跨玩家" in (cross_reason or "")

    time.sleep(0.06)
    expired, expired_reason = store.get_for_owner(
        connection_id="conn-1",
        player_name="Steve",
        conversation_id="conv-1",
        approval_id="ap1",
    )
    assert expired is None
    assert expired_reason is not None


def test_pending_approval_batch_waits_until_all_decisions() -> None:
    """同批多工具：单项决策不 pop；齐套后一次返回完整 batch。"""
    store = PendingApprovalStore(default_ttl_seconds=120.0)
    batch_id = "batch-1"
    for ap_id, tc_id, cmd in (("ap1", "tc1", "a"), ("ap2", "tc2", "b")):
        store.put(
            _make_pending(
                approval_id=ap_id,
                tool_call_id=tc_id,
                batch_id=batch_id,
                sibling_ids=["ap1", "ap2"],
                cmd=cmd,
            )
        )

    p1, r1, done1 = store.record_decision(
        connection_id="conn-1",
        player_name="Steve",
        conversation_id="conv-1",
        approval_id="ap1",
        approved=True,
    )
    assert p1 is not None and r1 is None and done1 is None
    assert len(store) == 2

    p_cross, r_cross, done_cross = store.record_decision(
        connection_id="conn-1",
        player_name="Alex",
        conversation_id="conv-1",
        approval_id="ap2",
        approved=False,
    )
    assert p_cross is None and done_cross is None
    assert "跨玩家" in (r_cross or "")

    p2, r2, done2 = store.record_decision(
        connection_id="conn-1",
        player_name="Steve",
        conversation_id="conv-1",
        approval_id="ap2",
        approved=False,
    )
    assert p2 is not None and r2 is None and done2 is not None
    assert {item.approval_id for item in done2} == {"ap1", "ap2"}
    assert {item.tool_call_id: item.decision for item in done2} == {
        "tc1": True,
        "tc2": False,
    }
    assert len(store) == 0


def test_resolve_target_approval_id_omitted_picks_single_batch() -> None:
    """省略 id：仅有一批时自动取最早未决策项。"""
    store = PendingApprovalStore(default_ttl_seconds=120.0)
    store.put(
        _make_pending(
            approval_id="ap1",
            tool_call_id="tc1",
            batch_id="batch-1",
            sibling_ids=["ap1", "ap2"],
            cmd="a",
        )
    )
    # 稍晚创建的同批项
    later = _make_pending(
        approval_id="ap2",
        tool_call_id="tc2",
        batch_id="batch-1",
        sibling_ids=["ap1", "ap2"],
        cmd="b",
    )
    later.created_at += 0.01
    store.put(later)

    resolved, reason = store.resolve_target_approval_id(
        connection_id="conn-1",
        player_name="Steve",
        conversation_id="conv-1",
        approval_id=None,
    )
    assert reason is None
    assert resolved == "ap1"


def test_resolve_target_approval_id_empty_and_multi_batch() -> None:
    store = PendingApprovalStore(default_ttl_seconds=120.0)

    empty_id, empty_reason = store.resolve_target_approval_id(
        connection_id="conn-1",
        player_name="Steve",
        conversation_id="conv-1",
    )
    assert empty_id is None
    assert "没有待审批" in (empty_reason or "")

    store.put(_make_pending(approval_id="ap1", batch_id="b1", tool_call_id="tc1"))
    store.put(
        _make_pending(
            approval_id="ap2",
            batch_id="b2",
            tool_call_id="tc2",
            cmd="y",
        )
    )
    multi_id, multi_reason = store.resolve_target_approval_id(
        connection_id="conn-1",
        player_name="Steve",
        conversation_id="conv-1",
    )
    assert multi_id is None
    assert "多个待审批批次" in (multi_reason or "")


def test_resolve_target_approval_id_explicit_still_works() -> None:
    store = PendingApprovalStore(default_ttl_seconds=120.0)
    store.put(_make_pending(approval_id="ap9"))
    resolved, reason = store.resolve_target_approval_id(
        connection_id="conn-1",
        player_name="Steve",
        conversation_id="conv-1",
        approval_id="ap9",
    )
    assert reason is None
    assert resolved == "ap9"

    missing, missing_reason = store.resolve_target_approval_id(
        connection_id="conn-1",
        player_name="Steve",
        conversation_id="conv-1",
        approval_id="nope",
    )
    assert missing is None
    assert missing_reason is not None


def test_session_auto_approve_conversation_vs_forever() -> None:
    """对话级不跨对话；永远跨对话但按玩家隔离。"""
    store = HostSessionStore()
    cid = uuid4()
    host = store.create(cid, authenticated=True)

    assert host.should_auto_approve_tools("Steve", "conv-a") is False

    host.enable_auto_approve_tools_conversation("Steve", "conv-a")
    assert host.should_auto_approve_tools("Steve", "conv-a") is True
    assert host.should_auto_approve_tools("Steve", "conv-b") is False
    assert host.should_auto_approve_tools("Alex", "conv-a") is False

    host.enable_auto_approve_tools_forever("Steve")
    assert host.should_auto_approve_tools("Steve", "conv-a") is True
    assert host.should_auto_approve_tools("Steve", "conv-b") is True
    assert host.should_auto_approve_tools("Alex", "conv-b") is False

    host.clear_auto_approve_tools("Steve", forever=True)
    # 连接级清掉后，对话级仍在
    assert host.should_auto_approve_tools("Steve", "conv-a") is True
    assert host.should_auto_approve_tools("Steve", "conv-b") is False

    host.clear_auto_approve_tools("Steve", conversation_id="conv-a")
    assert host.should_auto_approve_tools("Steve", "conv-a") is False


@pytest.mark.asyncio
async def test_gateway_approval_resume_uses_override_only_for_block_tools() -> None:
    """Approved block ops resume with saved execute args; regular tools retain boolean approval."""
    handler = object.__new__(CommandHandlers)
    handler.protocol = MagicMock()
    handler.protocol.create_success_message.return_value = "ok"
    handler._send_player_reply = AsyncMock()
    handler.broker = MagicMock()
    handler.broker.get_conversation_generation.return_value = 1
    handler.broker.get_conversation_invalidation_epoch.return_value = 1
    handler.broker.submit_request = AsyncMock()
    session = SimpleNamespace(current_provider="test")
    handler._require_host = lambda _state: SimpleNamespace(
        get_player_session=lambda _owner: session,
        should_auto_approve_tools=lambda *_args: False,
    )
    state = SimpleNamespace(id=uuid4())
    block = _make_pending(approval_id="b", tool_call_id="tc-block")
    block.connection_id = str(state.id)
    block.tool_name = "edit_blocks"
    block.normalized_args = {
        "type_id": "minecraft:stone", "mode": "place", "coordinate_mode": "absolute",
        "dimension": "minecraft:overworld", "position": {"x": 1, "y": 64, "z": 1},
        "locked_targets": [{"x": 1, "y": 64, "z": 1}], "phase": "execute",
    }
    block.execute_args = dict(block.normalized_args)
    block.execution_args_hash = hash_normalized_args(normalize_tool_args(block.execute_args))
    regular = _make_pending(approval_id="r", tool_call_id="tc-regular")
    regular.connection_id = str(state.id)
    block.batch_id = regular.batch_id = "batch"
    block.sibling_approval_ids = regular.sibling_approval_ids = ["b", "r"]
    block.requests.approvals.append(
        ToolCallPart(
            tool_name="run_minecraft_command",
            args={"command": "x"},
            tool_call_id="tc-regular",
        )
    )
    regular.requests = block.requests
    block.decision = regular.decision = True

    await handler._resume_from_completed_batch(
        state, completed_batch=[block, regular], pending=block, owner="Steve",
        conversation_id="conv-1", player_name="Steve", source="test",
    )

    payload = handler.broker.submit_request.await_args.args[1].deferred_tool_results["approvals"]
    assert payload["tc-block"] == {"kind": "tool-approved", "override_args": block.execute_args}
    assert payload["tc-regular"] is True


@pytest.mark.asyncio
async def test_gateway_rejects_tampered_block_execution_hash_before_resume() -> None:
    """已批准方块操作的执行投影哈希必须与审批记录一致。"""
    handler = object.__new__(CommandHandlers)
    handler.protocol = MagicMock()
    handler.protocol.create_error_message.return_value = "invalid"
    handler._send_player_reply = AsyncMock()
    handler.broker = MagicMock()
    handler.broker.submit_request = AsyncMock()
    handler._require_host = lambda _state: SimpleNamespace(
        get_player_session=lambda _owner: SimpleNamespace(current_provider="test"),
        should_auto_approve_tools=lambda *_args: False,
    )
    state = SimpleNamespace(id=uuid4())
    block = _make_pending(approval_id="b", tool_call_id="tc-block")
    block.connection_id = str(state.id)
    block.tool_name = "edit_blocks"
    block.normalized_args = {
        "type_id": "minecraft:stone", "mode": "place", "coordinate_mode": "absolute",
        "dimension": "minecraft:overworld", "position": {"x": 1, "y": 64, "z": 1},
        "locked_targets": [{"dimension": "minecraft:overworld", "x": 1, "y": 64, "z": 1}],
        "phase": "execute",
    }
    block.execute_args = dict(block.normalized_args)
    block.execution_args_hash = "tampered"
    block.decision = True

    await handler._resume_from_completed_batch(
        state, completed_batch=[block], pending=block, owner="Steve",
        conversation_id="conv-1", player_name="Steve", source="test",
    )

    handler.broker.submit_request.assert_not_awaited()
    handler._send_player_reply.assert_awaited_once()
