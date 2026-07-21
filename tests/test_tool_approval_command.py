"""Tool approve/deny command registration + PendingApprovalStore boundaries."""

from __future__ import annotations

import time

from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import DeferredToolRequests

from config.settings import MinecraftConfig
from mcbe_ws_sdk.command.registry import CommandRegistry
from services.agent.harness.approvals import PendingApproval, PendingApprovalStore


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


def test_pending_approval_owner_expired_cross_player_boundaries() -> None:
    """审批边界：owner / 过期 / 跨玩家。"""
    store = PendingApprovalStore(default_ttl_seconds=0.05)
    reqs = DeferredToolRequests(
        approvals=[
            ToolCallPart(
                tool_name="run_minecraft_command",
                args={"command": "x"},
                tool_call_id="tc1",
            )
        ]
    )
    now = time.time()
    pending = PendingApproval(
        approval_id="ap1",
        connection_id="conn-1",
        player_name="Steve",
        conversation_id="conv-1",
        run_id="run-1",
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
    reqs = DeferredToolRequests(
        approvals=[
            ToolCallPart(
                tool_name="run_minecraft_command",
                args={"command": "a"},
                tool_call_id="tc1",
            ),
            ToolCallPart(
                tool_name="run_minecraft_command",
                args={"command": "b"},
                tool_call_id="tc2",
            ),
        ]
    )
    now = time.time()
    batch_id = "batch-1"
    for ap_id, tc_id, cmd in (("ap1", "tc1", "a"), ("ap2", "tc2", "b")):
        store.put(
            PendingApproval(
                approval_id=ap_id,
                connection_id="conn-1",
                player_name="Steve",
                conversation_id="conv-1",
                run_id="run-1",
                tool_call_id=tc_id,
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
                expires_at=now + 120,
                batch_id=batch_id,
                sibling_approval_ids=["ap1", "ap2"],
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
