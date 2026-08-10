"""Phase 2 Host contract regression tests.

These tests intentionally exercise the Host seams rather than the SDK's wire
codec.  The SDK repository owns framing and validation; the Host owns player,
conversation and approval state.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from mcbe_ws_sdk import MCBEWS_V1, FlowControlSettings, McbewsV1Profile
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.profiles.mcbews_v1.classifier import ToolPlayerMessage
from mcbe_ws_sdk.profiles.mcbews_v1.models import (
    ApprovalDecision,
    SessionResponse,
    UiChatMessage,
)
from pydantic_ai.messages import ModelRequest, ModelResponse

from core.queue import MessageBroker
from models.messages import (
    GameMessageOutbound,
    RunCommandOutbound,
    SessionResponseOutbound,
    TextResponseOutbound,
)
from services.agent.harness.approvals import PendingApproval, PendingApprovalStore
from services.gateway.broker_bridge import BrokerResponseBridge
from services.gateway.command_handlers import CommandHandlers
from services.gateway.conversation_operations import (
    ConversationOperationResult,
    ConversationOperations,
)
from services.gateway.ingress import HostAddonIngressAdapter
from services.gateway.ws_command_runner import WsCommandRunner


def test_typed_outbound_messages_require_business_identity() -> None:
    with pytest.raises(ValueError):
        TextResponseOutbound(
            player_name="",
            conversation_id="chat-a",
            correlation_id="resp-a",
            role="assistant",
            text="hello",
        )

    message = GameMessageOutbound(
        player_name="Alex",
        conversation_id="chat-a",
        correlation_id="run-a",
        content="hello",
    )
    assert message["type"] == "game_message"
    assert message.player_name == "Alex"


def test_run_command_outbound_mapping_keeps_legacy_read_contract() -> None:
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        message = RunCommandOutbound(
            command="say hi",
            result_future=future,
            player_name="Alex",
            conversation_id="chat-a",
            correlation_id="run-a",
        )
        assert message.get("command") == "say hi"
        assert message["result_future"] is future
    finally:
        loop.close()


def test_legacy_approval_lookup_is_unique_per_connection() -> None:
    store = PendingApprovalStore(default_ttl_seconds=120)
    now = __import__("time").time()

    def pending(approval_id: str, player: str, conversation: str) -> PendingApproval:
        return PendingApproval(
            approval_id=approval_id,
            connection_id="conn-a",
            player_name=player,
            conversation_id=conversation,
            run_id="run-" + approval_id,
            tool_call_id="tool-" + approval_id,
            tool_name="run_command",
            normalized_args={},
            args_summary="",
            args_hash="",
            policy_version="v1",
            messages=[],
            requests=SimpleNamespace(approvals=[], calls=[]),
            provider="deepseek",
            delivery="tellraw",
            use_context=True,
            broadcast_ai_chat=False,
            created_at=now,
            expires_at=now + 120,
        )

    store.put(pending("ap-a", "Alice", "chat-a"))
    resolved, reason = store.resolve_legacy(connection_id="conn-a", approval_id="ap-a")
    assert resolved is not None
    assert reason is None
    assert (resolved.player_name, resolved.conversation_id) == ("Alice", "chat-a")


def test_legacy_approval_lookup_rejects_ambiguous_owner() -> None:
    store = PendingApprovalStore(default_ttl_seconds=120)
    now = __import__("time").time()

    def pending(player: str, conversation: str) -> PendingApproval:
        return PendingApproval(
            approval_id="same-id",
            connection_id="conn-a",
            player_name=player,
            conversation_id=conversation,
            run_id="run-" + player,
            tool_call_id="tool-" + player,
            tool_name="run_command",
            normalized_args={},
            args_summary="",
            args_hash="",
            policy_version="v1",
            messages=[],
            requests=SimpleNamespace(approvals=[], calls=[]),
            provider="deepseek",
            delivery="tellraw",
            use_context=True,
            broadcast_ai_chat=False,
            created_at=now,
            expires_at=now + 120,
        )

    store.put(pending("Alice", "chat-a"))
    store.put(pending("Bob", "chat-b"))
    resolved, reason = store.resolve_legacy(connection_id="conn-a", approval_id="same-id")
    assert resolved is None
    assert reason == "审批 id 在当前连接内不唯一"


def test_conversation_operation_result_is_typed() -> None:
    result = ConversationOperationResult.success(
        "list",
        "ok",
        data={"conversations": [{"message_count": 2}]},
    )
    assert result.ok is True
    assert result.code == "OK"
    assert result.data["conversations"][0]["message_count"] == 2


@pytest.mark.asyncio
async def test_ui_chat_dto_preserves_cid_into_request_and_echo() -> None:
    # This is a narrow smoke test for the public DTO shape used by the Host
    # adapter.  Full dispatch wiring is covered by the existing gateway tests.
    message = UiChatMessage(
        msg_id="ui-1",
        player_name="Alex",
        message="hello",
        cid="chat-a",
    )
    assert message.conversation_id == "chat-a"


@pytest.mark.asyncio
async def test_conversation_operations_report_real_message_count() -> None:
    broker = MessageBroker(max_size=5)
    connection_id = uuid4()
    broker.register_connection(connection_id)
    history = [ModelRequest(parts=[]), ModelResponse(parts=[])]
    broker.set_conversation_history(connection_id, "Alex", history, "chat-a")
    broker.set_active_conversation_id(connection_id, "Alex", "chat-a")

    operations = ConversationOperations(broker, SimpleNamespace(), sessions=None)
    listing = await operations.execute(
        connection_id,
        player_name="Alex",
        action="list",
    )
    row = next(item for item in listing.data["conversations"] if item["id"] == "chat-a")
    assert row["message_count"] == 2

    status = await operations.execute(
        connection_id,
        player_name="Alex",
        action="status",
    )
    assert status.ok is True
    assert status.data["message_count"] == 2


@pytest.mark.asyncio
async def test_conversation_operations_switch_default_preserves_player_isolation() -> None:
    broker = MessageBroker(max_size=5)
    connection_id = uuid4()
    broker.register_connection(connection_id)
    broker.set_conversation_history(connection_id, "Alice", [ModelRequest(parts=[])], "chat-a")
    broker.set_conversation_history(connection_id, "Bob", [ModelRequest(parts=[])], "chat-b")
    broker.set_active_conversation_id(connection_id, "Alice", "chat-a")
    broker.set_active_conversation_id(connection_id, "Bob", "chat-b")

    operations = ConversationOperations(broker, SimpleNamespace(), sessions=None)
    switched = await operations.execute(
        connection_id,
        player_name="Alice",
        action="switch",
        conversation_id="default",
    )

    assert switched.ok is True
    assert switched.data["conversation_id"] == "default"
    assert broker.get_active_conversation_id(connection_id, "Alice") == "default"
    assert broker.get_active_conversation_id(connection_id, "Bob") == "chat-b"

    missing = await operations.execute(
        connection_id,
        player_name="Alice",
        action="switch",
        conversation_id=None,
    )
    assert missing.ok is False
    assert missing.code == "INVALID_ARGUMENT"

    blank = await operations.execute(
        connection_id,
        player_name="Alice",
        action="switch",
        conversation_id=" \t",
    )
    assert blank.ok is False
    assert blank.code == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_typed_bridge_forwards_text_metadata_and_atomic_session() -> None:
    broker = MessageBroker(max_size=5)
    connection_id = uuid4()
    broker.register_connection(connection_id)
    sent: list[str] = []

    async def send_payload(payload: str) -> None:
        sent.append(payload)

    state = ConnectionState(id=connection_id, send_payload=send_payload)
    flow = FlowControlSettings()
    bridge = BrokerResponseBridge(
        broker,
        flow,
        WsCommandRunner(flow),
        profile=McbewsV1Profile(response_prelude_delay=0, response_chunk_delay=0),
    )
    await bridge._handle(
        state,
        TextResponseOutbound(
            player_name="Alex",
            conversation_id="chat-a",
            correlation_id="run-a",
            role="assistant",
            text="hello",
            title="Greeting",
            usage={"input_tokens": 2, "output_tokens": 3},
        ),
    )
    text_frame = json.loads(sent.pop(0))
    text_payload = json.loads(text_frame["body"]["commandLine"].split(" ", 2)[2])
    assert text_payload["cid"] == "chat-a"
    assert text_payload["t"] == "Greeting"
    assert text_payload["u"] == {"i": 2, "o": 3}

    response = SessionResponse(
        request_id="session-a",
        action="status",
        ok=True,
        data={"message_count": 2},
    )
    await bridge._handle(
        state,
        SessionResponseOutbound(
            player_name="Alex",
            conversation_id="chat-a",
            correlation_id="session-a",
            response=response,
        ),
    )
    assert len(sent) == 1
    session_command = json.loads(sent[0])["body"]["commandLine"]
    assert session_command.startswith("scriptevent mcbews:session_resp ")


@pytest.mark.asyncio
async def test_ingress_uses_approval_claim_owner_and_trusted_sender(monkeypatch) -> None:
    calls: list[dict[str, str | bool]] = []

    async def handle_approval(*_args, **kwargs) -> None:
        calls.append(kwargs)

    async def handle_session(*_args, **_kwargs) -> None:
        return None

    class Store:
        def get_for_owner(self, **kwargs):
            if kwargs["player_name"] == "Alice" and kwargs["conversation_id"] == "chat-a":
                return SimpleNamespace(player_name="Alice", conversation_id="chat-a"), None
            return None, "owner mismatch"

    runtime = SimpleNamespace(get_pending_approval_store=lambda _settings: Store())
    monkeypatch.setattr("services.gateway.ingress.get_agent_runtime", lambda: runtime)
    handlers = SimpleNamespace(
        settings=SimpleNamespace(),
        handle_tool_approval=handle_approval,
        handle_session_request=handle_session,
    )
    adapter = HostAddonIngressAdapter(handlers)
    state = ConnectionState(id=uuid4(), send_payload=None)
    decision = ApprovalDecision(approval_id="ap-a", player_name="Alice", cid="chat-a")
    message = ToolPlayerMessage(
        channel="approval",
        sender=MCBEWS_V1.trusted_bridge_player_name,
        raw_message="",
        approval_decision=decision,
    )

    assert await adapter.handle_control_message(state, message) is True
    assert calls == [
        {
            "approved": True,
            "player_name": "Alice",
            "conversation_id": "chat-a",
        }
    ]

    spoofed = ApprovalDecision(approval_id="ap-a", player_name="Bob", cid="chat-b")
    assert (
        await adapter.handle_control_message(
            state,
            ToolPlayerMessage(
                channel="approval",
                sender=MCBEWS_V1.trusted_bridge_player_name,
                raw_message="",
                approval_decision=spoofed,
            ),
        )
        is True
    )
    assert len(calls) == 1
    assert (
        await adapter.handle_control_message(
            state,
            ToolPlayerMessage(
                channel="approval",
                sender="FakeBridge",
                raw_message="",
                approval_decision=decision,
            ),
        )
        is False
    )


@pytest.mark.asyncio
async def test_ui_chat_handler_passes_cid_to_request_and_user_echo() -> None:
    handlers = object.__new__(CommandHandlers)
    handlers.dev_mode = True
    handlers.broker = SimpleNamespace(send_response=AsyncMock())
    handlers.handle_chat = AsyncMock()
    state = SimpleNamespace(id=uuid4())
    message = UiChatMessage(
        msg_id="ui-correlation-1",
        player_name="Alex",
        message="hello",
        cid="chat-a",
    )

    await handlers.handle_ui_chat(state, message)

    echo = handlers.broker.send_response.await_args.args[1]
    assert isinstance(echo, TextResponseOutbound)
    assert echo.player_name == "Alex"
    assert echo.conversation_id == "chat-a"
    assert echo.correlation_id == "ui-correlation-1"
    handlers.handle_chat.assert_awaited_once_with(
        state,
        "hello",
        delivery="tellraw",
        player_name="Alex",
        conversation_id="chat-a",
        correlation_id="ui-correlation-1",
    )
