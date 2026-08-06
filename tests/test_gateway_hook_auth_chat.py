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
from services.gateway.settings_map import build_protocol_handler
from services.gateway.ws_command_runner import WsCommandRunner


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
        sibling_approval_ids=["ap-1"],
        requests=SimpleNamespace(
            approvals=[
                SimpleNamespace(
                    tool_call_id="tc1",
                    tool_name="run_minecraft_command",
                )
            ],
            calls=[],
        ),
    )
    completed_item = SimpleNamespace(
        approval_id="ap-1",
        tool_call_id="tc1",
        expected_tool_call_id="tc1",
        tool_name="run_minecraft_command",
        decision=True,
        connection_id=str(cid),
        player_name="alex",
        conversation_id="default",
        batch_id="batch-1",
        run_id="trace-original",
        authorized_args={"command": "say hi"},
        execute_args={"command": "say hi"},
        execution_args_hash="",
    )
    completed_item.is_expired = lambda: False

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
    assert resumed.attempt_id != resumed.trace_id
    assert resumed.attempt_id != "attempt-original"
    assert item.trace_context is not None
    assert item.trace_context.trace_id == "trace-original"
    assert item.trace_context.attempt_id == resumed.attempt_id
    assert item.trace_context.player_name == "alex"


# ---------------------------------------------------------------------------
# 方案二安全门：同一连接下双玩家任务交错，身份必须来自当前事件
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_players_interleave_same_connection_no_identity_leak():
    """同一 connection_id 下玩家 A/B 的命令交错执行，请求与界面同步不串扰。

    通过 `MessageBroker` 的请求队列与响应队列分别核对两人的
    `ChatRequest.player_name` 与 `ai_response_sync` 界面同步目标：
    即使任务交错，每人的请求身份与界面同步都以当前事件 `sender` 为准，
    不落入对方。
    """
    settings = _settings(dev_mode=True)
    broker = MessageBroker(max_size=10)
    hook, sessions, broker, _handlers = _build_hook(settings=settings, broker=broker)
    cid = uuid4()
    sent: list[str] = []

    async def send_payload(payload: str) -> None:
        sent.append(payload)

    state = ConnectionState(id=cid, send_payload=send_payload)
    await hook.on_connected(state)
    response_queue = broker.get_response_queue(cid)
    assert response_queue is not None

    # 两名玩家在同一连接上提交（各自带当前事件来源 sender）。
    # 通过请求队列 / 响应队列核对身份，顺序无关。
    await hook._dispatch(
        state,
        PlayerMessageEvent(sender="Alice", message="AGENT 聊天 你好"),
        ParsedCommand(type="chat", content="你好", prefix="AGENT 聊天", raw="AGENT 聊天 你好"),
    )
    await hook._dispatch(
        state,
        PlayerMessageEvent(sender="Bob", message="AGENT 聊天 嗨"),
        ParsedCommand(type="chat", content="嗨", prefix="AGENT 聊天", raw="AGENT 聊天 嗨"),
    )

    # 请求身份：两人各取一条，player_name 各自正确（顺序无关）。
    reqs = [
        (await asyncio.wait_for(broker.get_request(), timeout=1.0)).payload
        for _ in range(2)
    ]
    assert {r.player_name for r in reqs} == {"Alice", "Bob"}
    assert {r.content for r in reqs} == {"你好", "嗨"}

    # 界面同步：两人各发一条 ai_response_sync，player_name 各自正确。
    syncs = [
        await asyncio.wait_for(response_queue.get(), timeout=1.0)
        for _ in range(2)
    ]
    assert all(s["type"] == "ai_response_sync" for s in syncs)
    assert {s["player_name"] for s in syncs} == {"Alice", "Bob"}


@pytest.mark.asyncio
async def test_approval_ownership_interleaved_same_connection():
    """同一连接下两名玩家的待审批归属不串扰。

    为 Alice / Bob 各放一条待审批记录，令两人的审批命令交错执行，
    断言各自只会命中自己 owner 的批次，且不会彼此覆盖决策。
    """
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import DeferredToolRequests

    from services.agent.harness.approvals import (
        PendingApproval,
        PendingApprovalStore,
    )
    from services.agent.harness.execution import (
        hash_normalized_args,
        normalize_tool_args,
    )

    store = PendingApprovalStore(default_ttl_seconds=120.0)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "services.agent.runtime.get_agent_runtime",
        lambda: SimpleNamespace(get_pending_approval_store=lambda _s: store),
    )
    handlers = object.__new__(CommandHandlers)
    handlers.protocol = MagicMock()
    handlers.protocol.create_success_message.return_value = "ok"
    handlers.protocol.create_info_message.return_value = "info"
    handlers.protocol.create_error_message.return_value = "invalid"
    handlers._send_player_reply = AsyncMock()
    handlers.broker = MagicMock()
    handlers.broker.get_active_conversation_id.return_value = "conv-1"
    handlers.broker.submit_request = AsyncMock()
    handlers.settings = MagicMock()
    session = SimpleNamespace(current_provider="test")
    handlers._require_host = lambda _state: SimpleNamespace(
        get_player_session=lambda _owner: session,
        should_auto_approve_tools=lambda *_args: False,
    )
    cid = uuid4()
    state = SimpleNamespace(id=cid)

    now = __import__("time").time()
    canonical = {"command": "say hi"}
    alice_pending = PendingApproval(
        approval_id="ap-alice",
        connection_id=str(cid),
        player_name="Alice",
        conversation_id="conv-1",
        run_id="run-alice",
        tool_call_id="tc-alice",
        tool_name="run_minecraft_command",
        normalized_args=canonical,
        args_summary="command=say hi",
        args_hash=hash_normalized_args(normalize_tool_args(canonical)),
        policy_version="v",
        plan_id="",
        messages=[],
        requests=DeferredToolRequests(
            approvals=[
                ToolCallPart(
                    tool_name="run_minecraft_command",
                    args=canonical,
                    tool_call_id="tc-alice",
                )
            ]
        ),
        provider="test",
        delivery="tellraw",
        use_context=False,
        broadcast_ai_chat=False,
        created_at=now,
        expires_at=now + 120,
        batch_id="ap-alice",
        sibling_approval_ids=["ap-alice"],
    )
    from dataclasses import replace

    bob_pending = replace(
        alice_pending,
        approval_id="ap-bob",
        player_name="Bob",
        run_id="run-bob",
        tool_call_id="tc-bob",
        batch_id="ap-bob",
        sibling_approval_ids=["ap-bob"],
        requests=DeferredToolRequests(
            approvals=[
                ToolCallPart(
                    tool_name="run_minecraft_command",
                    args=canonical,
                    tool_call_id="tc-bob",
                )
            ]
        ),
    )
    store.put(alice_pending)
    store.put(bob_pending)

    # 交错执行两人审批命令：Alice 同意自己的，Bob 同意自己的。
    await handlers.handle_tool_approval(
        state, "ap-alice", approved=True, player_name="Alice"
    )
    await handlers.handle_tool_approval(
        state, "ap-bob", approved=True, player_name="Bob"
    )

    # 各自批次的决策已记录，且互不错位。
    alice = store.get(str(cid), "Alice", "conv-1", "ap-alice")
    bob = store.get(str(cid), "Bob", "conv-1", "ap-bob")
    # 决策后批次已消费（record_decision 返回完成后被移除），验证归属正确：
    assert alice is None  # 已消费
    assert bob is None  # 已消费
    # 归属验证：对方看不到彼此批次
    assert store.get(str(cid), "Alice", "conv-1", "ap-bob") is None
    assert store.get(str(cid), "Bob", "conv-1", "ap-alice") is None
    monkeypatch.undo()


@pytest.mark.asyncio
async def test_fallback_with_player_name_none_does_not_leak_to_other_player():
    """安全门：缺少显式 player_name 时，回退不得读取连接级可变状态。

    玩家 B 先覆盖连接状态 `state._player_name='Bob'`，随后玩家 A 的路径
    以 `player_name=None` 走到回退分支。回退必须明确失败或使用非业务性
    显示默认值，绝不能落入 Bob 的桶。
    """
    settings = _settings(dev_mode=True)
    broker = MessageBroker(max_size=10)
    _hook, sessions, broker, handlers = _build_hook(settings=settings, broker=broker)
    cid = uuid4()
    sessions.create(cid, authenticated=True)
    state = ConnectionState(id=cid, send_payload=AsyncMock())

    # 玩家 B 覆盖连接级状态（模拟 B 的任务先写身份）
    state._player_name = "Bob"

    # 玩家 A 的路径以 player_name=None 回退：直接调用 _build_chat_request，
    # 验证 ChatRequest.player_name 不再读取连接级 state._player_name（非业务来源）。
    handlers._require_host(state)
    req = handlers._build_chat_request(
        state,
        content="你好",
        delivery="tellraw",
        player_name=None,
        conversation_id="default",
    )
    assert req.player_name != "Bob", (
        "回退分支读取了连接级 state._player_name，玩家 A 的身份落入了 Bob 的桶"
    )

    # 回复目标同样不得回退到连接级状态：缺失身份时广播到全体（非业务默认），
    # 而不是落到 Bob。
    assert handlers._reply_target(None) == "@a"
    assert handlers._reply_target("Alice") == "Alice"
