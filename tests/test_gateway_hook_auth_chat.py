"""HostConnectionHook auth + chat dispatch tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcbe_ws_sdk import FlowControlSettings
from mcbe_ws_sdk.command.registry import ParsedCommand
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.protocol.minecraft import PlayerMessageEvent

from core.queue import MessageBroker
from services.gateway.broker_bridge import BrokerResponseBridge
from services.gateway.command_handlers import CommandHandlers
from services.gateway.hook import HostConnectionHook
from services.gateway.session_store import HostSessionStore
from services.gateway.ws_command_runner import WsCommandRunner
from services.gateway.settings_map import build_protocol_handler


class _FakeJwt:
    def __init__(self, password: str = "secret") -> None:
        self._password = password
        self._tokens: dict[str, str] = {}

    def verify_password(self, provided: str) -> bool:
        return provided == self._password

    def is_token_valid(self, connection_uuid: str) -> bool:
        return connection_uuid in self._tokens

    def generate_token(self) -> str:
        return "tok"

    def save_token(self, connection_uuid: str, token: str) -> None:
        self._tokens[connection_uuid] = token

    def get_stored_token(self, connection_uuid: str) -> str | None:
        return self._tokens.get(connection_uuid)

    def verify_token(self, token: str) -> bool:
        return token == "tok"


def _minecraft_config() -> SimpleNamespace:
    return SimpleNamespace(
        welcome_message_template=(
            "v{version} id={connection_id} {provider}/{model} "
            "ctx={context_status} help={help_command}"
        ),
        context_enabled_text="on",
        context_disabled_text="off",
        error_prefix="[E]",
        error_color="red",
        info_prefix="[I]",
        info_color="white",
        success_prefix="[OK]",
        success_color="green",
        commands={},
        get_command_description=lambda _t: ("", ""),
        ai_broadcast_default=True,
    )


def _settings(*, dev_mode: bool = False) -> MagicMock:
    s = MagicMock()
    s.dev_mode = dev_mode
    s.default_provider = "deepseek"
    s.enable_ws_raw_log = False
    s.max_history_turns = 20
    s.compression_enabled = False
    s.compression_trigger_ratio = 0.8
    s.compression_keep_recent_turns = 4
    s.run_command_timeout = 5.0
    s.minecraft = _minecraft_config()
    s.get_provider_config.return_value = SimpleNamespace(
        model="test-model", context_window=None
    )
    s.list_available_providers.return_value = ["deepseek"]
    s.mcp = SimpleNamespace(enabled=False, servers={})
    return s


def _build_hook(*, settings=None, jwt=None, broker=None):
    settings = settings or _settings()
    jwt = jwt or _FakeJwt()
    broker = broker or MessageBroker(max_size=10)
    sessions = HostSessionStore()
    flow = FlowControlSettings()
    ws_commands = WsCommandRunner(flow, timeout=1.0)
    addon = MagicMock()
    bridge = BrokerResponseBridge(broker, flow, ws_commands)
    protocol = build_protocol_handler(settings)
    handlers = CommandHandlers(
        broker,
        settings,
        jwt,
        sessions,
        ws_commands,
        addon,
        protocol,
        flow,
    )
    hook = HostConnectionHook(
        broker=broker,
        settings=settings,
        sessions=sessions,
        bridge=bridge,
        ws_commands=ws_commands,
        addon=addon,
        handlers=handlers,
        protocol=protocol,
        flow=flow,
    )
    return hook, sessions, broker, handlers


@pytest.mark.asyncio
async def test_login_success_marks_session_authenticated():
    hook, sessions, _broker, _handlers = _build_hook()
    cid = uuid4()
    sent: list[str] = []

    async def send_payload(payload: str) -> None:
        sent.append(payload)

    state = ConnectionState(id=cid, send_payload=send_payload)
    sessions.create(cid, authenticated=False)

    await hook._dispatch(
        state,
        PlayerMessageEvent(sender="Steve", message="#登录 secret"),
        ParsedCommand(
            type="login",
            content="secret",
            prefix="#登录",
            raw="#登录 secret",
        ),
    )

    host = sessions.get(cid)
    assert host is not None
    assert host.authenticated is True
    assert sent, "expected login success tellraw"


@pytest.mark.asyncio
async def test_chat_submits_chat_request_without_blocking():
    settings = _settings(dev_mode=True)
    broker = MessageBroker(max_size=10)
    hook, sessions, broker, _handlers = _build_hook(settings=settings, broker=broker)
    cid = uuid4()
    sent: list[str] = []

    async def send_payload(payload: str) -> None:
        sent.append(payload)

    state = ConnectionState(id=cid, send_payload=send_payload)
    await hook.on_connected(state)

    await hook.on_player_message(
        state,
        PlayerMessageEvent(sender="Steve", message="AGENT 聊天 你好"),
        ParsedCommand(
            type="chat",
            content="你好",
            prefix="AGENT 聊天",
            raw="AGENT 聊天 你好",
        ),
    )

    # on_player_message must return immediately; drain the background task
    for _ in range(50):
        if not hook._background_tasks:
            break
        await asyncio.sleep(0.02)
    # Also allow any stragglers one yield
    await asyncio.sleep(0)

    item = await asyncio.wait_for(broker.get_request(), timeout=1.0)
    req = item.payload
    assert req.player_name == "Steve"
    assert req.content == "你好"
    assert req.connection_id == cid


@pytest.mark.asyncio
async def test_chat_ingress_assigns_trace_id_before_enqueue():
    settings = _settings(dev_mode=True)
    broker = MessageBroker(max_size=10)
    _hook, sessions, broker, handlers = _build_hook(settings=settings, broker=broker)
    cid = uuid4()
    sent: list[str] = []

    async def send_payload(payload: str) -> None:
        sent.append(payload)

    state = ConnectionState(id=cid, send_payload=send_payload)
    sessions.create(cid, authenticated=True)

    await handlers.handle_chat(state, "hello", "tellraw", player_name="alex")
    item = await asyncio.wait_for(broker.get_request(), timeout=1.0)
    request = item.payload
    assert request.trace_id == request.run_id
    assert request.attempt_id
    assert item.trace_context is not None
    assert item.trace_context.trace_id == request.trace_id
    assert item.trace_context.player_name == "alex"
    assert item.enqueued_at_ns > 0


@pytest.mark.asyncio
async def test_approval_resume_keeps_trace_and_uses_new_attempt():
    settings = _settings(dev_mode=True)
    broker = MessageBroker(max_size=10)
    _hook, sessions, broker, handlers = _build_hook(settings=settings, broker=broker)
    cid = uuid4()
    sent: list[str] = []

    async def send_payload(payload: str) -> None:
        sent.append(payload)

    state = ConnectionState(id=cid, send_payload=send_payload)
    sessions.create(cid, authenticated=True)

    pending = SimpleNamespace(
        run_id="trace-original",
        use_context=True,
        provider="deepseek",
        delivery="tellraw",
        broadcast_ai_chat=False,
        batch_id="batch-1",
        messages=[],
        tool_name="run_minecraft_command",
    )
    completed_item = SimpleNamespace(
        tool_call_id="tc1",
        tool_name="run_minecraft_command",
        decision=True,
    )

    await handlers._resume_from_completed_batch(
        state,
        completed_batch=[completed_item],
        pending=pending,
        owner="alex",
        conversation_id="default",
        player_name="alex",
        source="tool_approve",
    )
    item = await asyncio.wait_for(broker.get_request(), timeout=1.0)
    resumed = item.payload
    assert resumed.trace_id == "trace-original"
    assert resumed.run_id == "trace-original"
    assert resumed.attempt_id
    assert resumed.attempt_id != "attempt-original"
    assert item.trace_context is not None
    assert item.trace_context.trace_id == "trace-original"
    assert item.trace_context.attempt_id == resumed.attempt_id
    assert item.trace_context.player_name == "alex"
