"""WebSocket 对话管理命令回归测试。"""

import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from tools_mcbe_simulator import build_player_message, build_command_response

from config.settings import Settings
from core.queue import MessageBroker
from services.auth.jwt_handler import JWTHandler
from services.websocket.connection import ConnectionState
from services.websocket.server import WebSocketServer


class DummyWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


def _build_turn(index: int) -> list[ModelRequest | ModelResponse]:
    return [
        ModelRequest(parts=[UserPromptPart(content=f"user-{index}")]),
        ModelResponse(parts=[TextPart(content=f"assistant-{index}")]),
    ]


def _server(tmp_path, monkeypatch) -> tuple[WebSocketServer, MessageBroker, ConnectionState]:
    monkeypatch.chdir(tmp_path)
    settings = Settings(default_provider="ollama", dev_mode=True)
    broker = MessageBroker()
    state = ConnectionState()
    state.authenticated = True
    state.websocket = DummyWebSocket()
    broker.register_connection(state.id)
    return WebSocketServer(broker, settings, JWTHandler(settings)), broker, state


def _sent_command_lines(state: ConnectionState) -> list[str]:
    return [json.loads(payload)["body"]["commandLine"] for payload in state.websocket.sent]


def test_conversation_status_does_not_block_command_response(tmp_path, monkeypatch):
    async def _run() -> None:
        server, broker, state = _server(tmp_path, monkeypatch)
        lock = broker.get_session_lock(state.id, "alice")
        result_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        state.pending_command_futures["req-give"] = result_future

        async with lock:
            status_task = asyncio.create_task(
                server.handle_message(state, build_player_message("alice", "AI 对话 status"))
            )
            await asyncio.sleep(0)
            await server.handle_message(
                state,
                build_command_response(
                    "req-give",
                    {"statusCode": 0, "statusMessage": "给予 alice 钻石 * 64 效果"},
                ),
            )

            assert result_future.done()
            assert result_future.result() == "给予 alice 钻石 * 64 效果"
            assert status_task.done()

        await status_task

    asyncio.run(_run())


def test_conversation_new_rejects_existing_id_without_overwrite(tmp_path, monkeypatch):
    async def _run() -> None:
        server, broker, state = _server(tmp_path, monkeypatch)
        broker.set_conversation_history(state.id, "alice", _build_turn(1), "build")

        msg = await server._handle_conversation(state, "new build", "alice")

        history = broker.get_conversation_history(state.id, "alice", "build")
        assert "已存在" in msg.text
        assert len(history) == 2
        assert history[0].parts[0].content == "user-1"

    asyncio.run(_run())


def test_conversation_new_generates_unique_ids_with_short_ids(tmp_path, monkeypatch):
    async def _run() -> None:
        server, broker, state = _server(tmp_path, monkeypatch)

        first = await server._handle_conversation(state, "new", "alice")
        first_id = broker.get_active_conversation_id(state.id, "alice")
        second = await server._handle_conversation(state, "new", "alice")
        second_id = broker.get_active_conversation_id(state.id, "alice")

        assert "已新建" in first.text
        assert f"#1 {first_id}" in first.text
        assert "已新建" in second.text
        assert f"#2 {second_id}" in second.text
        assert first_id != second_id

    asyncio.run(_run())


def test_conversation_list_displays_short_ids_titles_current_and_counts(tmp_path, monkeypatch):
    async def _run() -> None:
        server, broker, state = _server(tmp_path, monkeypatch)
        broker.set_active_conversation_id(state.id, "alice", "build")
        broker.set_conversation_history(state.id, "alice", _build_turn(1), "build")
        broker.set_conversation_history(
            state.id,
            "alice",
            [*_build_turn(2), *_build_turn(3)],
            "mine",
        )
        broker.ensure_conversation_metadata(state.id, "alice", "build")
        broker.set_conversation_title(state.id, "alice", "mine", "挖矿计划")

        msg = await server._handle_conversation(state, "list", "alice")

        assert "#1 build * - 未命名 - 2 条消息" in msg.text
        assert "#2 mine - 挖矿计划 - 4 条消息" in msg.text

    asyncio.run(_run())


def test_conversation_switch_short_id_uses_mapped_long_id(tmp_path, monkeypatch):
    async def _run() -> None:
        server, broker, state = _server(tmp_path, monkeypatch)
        broker.set_active_conversation_id(state.id, "alice", "first")
        broker.set_conversation_history(state.id, "alice", _build_turn(1), "first")
        broker.set_conversation_history(
            state.id,
            "alice",
            [*_build_turn(2), *_build_turn(3)],
            "build",
        )
        broker.ensure_conversation_metadata(state.id, "alice", "first")
        broker.ensure_conversation_metadata(state.id, "alice", "build")

        msg = await server._handle_conversation(state, "switch #2", "alice")

        assert broker.get_active_conversation_id(state.id, "alice") == "build"
        assert "已切换到对话: #2 build" in msg.text
        assert "（2轮）" in msg.text

    asyncio.run(_run())


def test_conversation_switch_new_id_creates_empty_conversation_with_short_id(tmp_path, monkeypatch):
    async def _run() -> None:
        server, broker, state = _server(tmp_path, monkeypatch)

        msg = await server._handle_conversation(state, "switch build", "alice")

        assert broker.get_active_conversation_id(state.id, "alice") == "build"
        assert broker.get_conversation_history(state.id, "alice", "build") == []
        assert "已创建并切换到新会话" in msg.text
        assert "#1 build" in msg.text

    asyncio.run(_run())


def test_conversation_switch_short_id_is_isolated_by_player(tmp_path, monkeypatch):
    async def _run() -> None:
        server, broker, state = _server(tmp_path, monkeypatch)
        broker.set_conversation_history(state.id, "alice", _build_turn(1), "alice-build")
        broker.set_conversation_history(state.id, "bob", _build_turn(2), "bob-build")

        alice_msg = await server._handle_conversation(state, "switch #1", "alice")
        bob_msg = await server._handle_conversation(state, "switch #1", "bob")

        assert broker.get_active_conversation_id(state.id, "alice") == "alice-build"
        assert broker.get_active_conversation_id(state.id, "bob") == "bob-build"
        assert "#1 alice-build" in alice_msg.text
        assert "#1 bob-build" in bob_msg.text

    asyncio.run(_run())

def test_send_ws_payload_preserves_broadcast_tellraw_target(tmp_path, monkeypatch):
    async def _run() -> None:
        server, _broker, state = _server(tmp_path, monkeypatch)
        state.player_name = "Alice"
        msg = server.protocol_handler.create_info_message("broadcast")

        await server._send_ws_payload(state, msg, source="broadcast_test")

        command_line = json.loads(state.websocket.sent[-1])["body"]["commandLine"]
        assert command_line.startswith("tellraw @a ")
        assert not command_line.startswith("tellraw Alice ")

    asyncio.run(_run())


def test_player_command_replies_are_sent_to_sender(tmp_path, monkeypatch):
    async def _run() -> None:
        server, _broker, state = _server(tmp_path, monkeypatch)

        commands = [
            ("login", "wrong"),
            ("chat", ""),
            ("context", "状态"),
            ("conversation", "status"),
            ("template", ""),
            ("setting", ""),
            ("mcp", ""),
            ("switch_model", "missing-provider"),
            ("help", ""),
            ("run_command", ""),
        ]
        for cmd_type, content in commands:
            before = len(state.websocket.sent)
            await server.handle_command(state, cmd_type, content, player_name="Alice")
            command_lines = _sent_command_lines(state)[before:]
            assert command_lines, cmd_type
            assert all(line.startswith("tellraw Alice ") for line in command_lines), cmd_type
            assert not any(line.startswith("tellraw @a ") for line in command_lines), cmd_type
            assert not any(line.startswith("tellraw Bob ") for line in command_lines), cmd_type

    asyncio.run(_run())


def test_chat_queue_full_error_is_sent_to_sender(tmp_path, monkeypatch):
    async def _run() -> None:
        server, broker, state = _server(tmp_path, monkeypatch)

        async def _raise_queue_full(*_args, **_kwargs) -> None:
            raise asyncio.QueueFull

        monkeypatch.setattr(broker, "submit_request", _raise_queue_full)

        await server.handle_command(state, "chat", "hello", player_name="Alice")

        command_line = _sent_command_lines(state)[-1]
        assert command_line.startswith("tellraw Alice ")
        assert "服务器繁忙" in command_line
        assert not command_line.startswith("tellraw @a ")
        assert not command_line.startswith("tellraw Bob ")

    asyncio.run(_run())


def test_auth_error_is_sent_to_triggering_player(tmp_path, monkeypatch):
    async def _run() -> None:
        monkeypatch.chdir(tmp_path)
        settings = Settings(default_provider="ollama", dev_mode=False)
        broker = MessageBroker()
        state = ConnectionState()
        state.websocket = DummyWebSocket()
        broker.register_connection(state.id)
        server = WebSocketServer(broker, settings, JWTHandler(settings))

        await server.handle_command(state, "chat", "hello", player_name="Alice")

        command_line = _sent_command_lines(state)[-1]
        assert command_line.startswith("tellraw Alice ")
        assert "请先登录" in command_line
        assert not command_line.startswith("tellraw @a ")
        assert not command_line.startswith("tellraw Bob ")

    asyncio.run(_run())


def test_player_state_replies_do_not_leak_between_players(tmp_path, monkeypatch):
    async def _run() -> None:
        server, _broker, state = _server(tmp_path, monkeypatch)

        await server.handle_command(state, "context", "关闭", player_name="Bob")
        await server.handle_command(state, "context", "状态", player_name="Alice")

        command_line = _sent_command_lines(state)[-1]
        assert command_line.startswith("tellraw Alice ")
        assert "上下文状态: 启用" in command_line
        assert "上下文状态: 关闭" not in command_line
        assert not command_line.startswith("tellraw @a ")
        assert not command_line.startswith("tellraw Bob ")

    asyncio.run(_run())


def test_handle_run_command_reports_too_long_raw_command(tmp_path, monkeypatch):
    async def _run() -> None:
        server, _broker, state = _server(tmp_path, monkeypatch)

        await server.handle_run_command(state, "say " + "X" * 1000)

        assert state.websocket.sent
        command_line = json.loads(state.websocket.sent[-1])["body"]["commandLine"]
        assert "raw command too long" in command_line

    asyncio.run(_run())


def test_handle_run_command_reports_multibyte_raw_command_byte_limit(tmp_path, monkeypatch):
    async def _run() -> None:
        server, _broker, state = _server(tmp_path, monkeypatch)

        await server.handle_run_command(state, "say " + "中" * 200)

        assert state.websocket.sent
        command_line = json.loads(state.websocket.sent[-1])["body"]["commandLine"]
        assert "raw command too long in bytes" in command_line
        assert "604 > 461" in command_line

    asyncio.run(_run())

def test_run_command_length_error_is_sent_to_sender(tmp_path, monkeypatch):
    async def _run() -> None:
        server, _broker, state = _server(tmp_path, monkeypatch)

        await server.handle_command(
            state,
            "run_command",
            "say " + "X" * 1000,
            player_name="Alice",
        )

        command_line = json.loads(state.websocket.sent[-1])["body"]["commandLine"]
        assert command_line.startswith("tellraw Alice ")
        assert "raw command too long" in command_line

    asyncio.run(_run())


def test_switch_model_clears_all_player_runtime_conversations(tmp_path, monkeypatch):
    async def _run() -> None:
        server, broker, state = _server(tmp_path, monkeypatch)
        broker.set_conversation_history(state.id, "alice", _build_turn(1), "default")
        broker.set_conversation_history(state.id, "alice", _build_turn(2), "other")
        broker.set_conversation_history(state.id, "bob", _build_turn(3), "default")
        broker.set_conversation_title(state.id, "alice", "other", "旧标题")

        msg = await server._handle_switch_model(state, "ollama", "alice")
        new_msg = await server._handle_conversation(state, "new fresh", "alice")

        assert "已切换" in msg.text
        assert broker.get_conversation_history(state.id, "alice", "default") == []
        assert broker.get_conversation_history(state.id, "alice", "other") == []
        assert broker.list_player_conversation_metadata(state.id, "alice") == [
            broker.get_conversation_metadata(state.id, "alice", "fresh")
        ]
        assert "#1 fresh" in new_msg.text
        assert broker.get_conversation_history(state.id, "bob", "default") != []
        assert broker.resolve_conversation_short_id(state.id, "bob", "#1") == "default"

    asyncio.run(_run())
