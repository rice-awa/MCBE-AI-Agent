"""WebSocket command parsing tests."""

from services.websocket.command import CommandRegistry, ParsedCommand


COMMANDS = {
    "AGENT 聊天": {
        "type": "chat",
        "aliases": ["ask"],
        "description": "聊天",
        "usage": "<消息>",
    },
    "#登录": {
        "type": "login",
        "aliases": [],
        "description": "登录",
        "usage": "<密码>",
    },
}


def test_resolve_parsed_command_keeps_prefix_type_and_content() -> None:
    registry = CommandRegistry(COMMANDS)

    parsed = registry.resolve_parsed("AGENT 聊天 hello")

    assert parsed == ParsedCommand(
        type="chat",
        content="hello",
        prefix="AGENT 聊天",
        raw="AGENT 聊天 hello",
        matched_alias=None,
    )


def test_resolve_parsed_command_records_alias() -> None:
    registry = CommandRegistry(COMMANDS)

    parsed = registry.resolve_parsed("ask hello")

    assert parsed == ParsedCommand(
        type="chat",
        content="hello",
        prefix="AGENT 聊天",
        raw="ask hello",
        matched_alias="ask",
    )


def test_resolve_parsed_command_returns_none_for_non_command() -> None:
    registry = CommandRegistry(COMMANDS)

    assert registry.resolve_parsed("hello") is None
    assert registry.resolve("hello") == (None, "hello")


def test_resolve_parsed_command_requires_prefix_boundary() -> None:
    registry = CommandRegistry(COMMANDS)

    assert registry.resolve_parsed("AGENT 聊天室 hello") is None
    assert registry.resolve_parsed("#登录密码") is None
    assert registry.resolve_parsed("AGENT 聊天\thello").content == "hello"


def test_resolve_parsed_command_requires_alias_boundary() -> None:
    registry = CommandRegistry(COMMANDS)

    assert registry.resolve_parsed("askew question") is None
    assert registry.resolve_parsed("ask\tquestion").content == "question"



def test_tool_approval_command_is_registered_in_defaults() -> None:
    from config.settings import MinecraftConfig
    from services.websocket.command import CommandRegistry

    registry = CommandRegistry(MinecraftConfig().commands)
    parsed = registry.resolve_parsed("AGENT 工具审批 abcd 允许")
    assert parsed is not None
    assert parsed.type == "tool_approval"
    assert parsed.content == "abcd 允许"


def test_pending_approval_owner_expired_cross_player_boundaries() -> None:
    """WebSocket 审批边界：owner / 过期 / 跨玩家。"""
    import time
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import DeferredToolRequests
    from services.agent.harness.approvals import PendingApproval, PendingApprovalStore

    store = PendingApprovalStore(default_ttl_seconds=0.05)
    reqs = DeferredToolRequests(
        approvals=[ToolCallPart(tool_name="run_minecraft_command", args={"command": "x"}, tool_call_id="tc1")]
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
