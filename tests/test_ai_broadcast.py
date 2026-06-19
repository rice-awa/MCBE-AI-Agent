"""AI chat broadcast control regression tests."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.settings import Settings
from core.queue import MessageBroker
from models.agent import StreamEvent
from models.messages import ChatRequest, StreamChunk, SystemNotification
from services.agent.worker import AgentWorker
from services.auth.jwt_handler import JWTHandler
from services.websocket.connection import ConnectionManager, ConnectionState
from services.websocket.server import WebSocketServer


class DummyWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


def _server(tmp_path, monkeypatch) -> tuple[WebSocketServer, MessageBroker, ConnectionState]:
    monkeypatch.chdir(tmp_path)
    settings = Settings(default_provider="ollama", dev_mode=True, tool_response_verbose=True)
    monkeypatch.setattr("services.websocket.flow_control.get_settings", lambda: settings)
    broker = MessageBroker()
    state = ConnectionState(websocket=DummyWebSocket())
    state.authenticated = True
    broker.register_connection(state.id)
    return WebSocketServer(broker, settings, JWTHandler(settings)), broker, state


def _sent_command_lines(state: ConnectionState) -> list[str]:
    return [json.loads(payload)["body"]["commandLine"] for payload in state.websocket.sent]


def test_ai_broadcast_command_updates_connection_policy(tmp_path, monkeypatch) -> None:
    async def _run() -> None:
        server, _broker, state = _server(tmp_path, monkeypatch)

        await server.handle_command(state, "ai_broadcast", "状态", player_name="Admin")
        assert _sent_command_lines(state)[-1].startswith("tellraw Admin ")
        assert "全服广播: 关闭" in _sent_command_lines(state)[-1]

        await server.handle_command(state, "ai_broadcast", "全服 开启", player_name="Admin")
        assert state.ai_broadcast_all is True
        assert state.should_broadcast_ai_chat("Alice") is True
        assert _sent_command_lines(state)[-1].startswith("tellraw Admin ")

        await server.handle_command(state, "ai_broadcast", "全服 关闭", player_name="Admin")
        assert state.ai_broadcast_all is False

        await server.handle_command(state, "ai_broadcast", "玩家 Alice 开启", player_name="Admin")
        assert state.ai_broadcast_players == {"Alice"}
        assert state.should_broadcast_ai_chat("Alice") is True
        assert state.should_broadcast_ai_chat("Bob") is False

        await server.handle_command(state, "ai_broadcast", "玩家 Alice 关闭", player_name="Admin")
        assert state.ai_broadcast_players == set()

        await server.handle_command(state, "ai_broadcast", "关闭", player_name="Admin")
        assert state.ai_broadcast_all is False
        assert state.ai_broadcast_players == set()

    asyncio.run(_run())


def test_handle_chat_captures_broadcast_policy_only_for_tellraw(tmp_path, monkeypatch) -> None:
    async def _run() -> None:
        server, broker, state = _server(tmp_path, monkeypatch)
        submitted: list[ChatRequest] = []

        async def _capture_request(_connection_id, request, priority=0) -> None:
            submitted.append(request)

        monkeypatch.setattr(broker, "submit_request", _capture_request)

        await server.handle_chat(state, "default", delivery="tellraw", player_name="Alice")
        assert submitted[-1].broadcast_ai_chat is False

        state.ai_broadcast_all = True
        await server.handle_chat(state, "broadcast", delivery="tellraw", player_name="Alice")
        assert submitted[-1].broadcast_ai_chat is True

        await server.handle_chat(state, "script", delivery="scriptevent", player_name="Alice")
        assert submitted[-1].broadcast_ai_chat is False

    asyncio.run(_run())


def test_connection_manager_uses_stream_target_for_visible_chunks() -> None:
    async def _run() -> None:
        broker = MessageBroker()
        manager = ConnectionManager(broker, dev_mode=True)
        state = ConnectionState(websocket=DummyWebSocket())
        chunk = StreamChunk(
            connection_id=state.id,
            chunk_type="content",
            content="hello",
            sequence=0,
            player_name="Alice",
            target="@a",
        )

        await manager._send_stream_chunk(state, chunk)

        command_line = _sent_command_lines(state)[-1]
        assert command_line.startswith("tellraw @a ")
        assert not command_line.startswith("tellraw Alice ")

    asyncio.run(_run())


def test_worker_broadcasts_visible_chunks_and_keeps_addon_player(monkeypatch) -> None:
    async def _run() -> None:
        broker = MessageBroker()
        connection_id = uuid4()
        broker.register_connection(connection_id)
        settings = Settings(default_provider="ollama", dev_mode=True, tool_response_verbose=True)
        worker = AgentWorker(broker, settings)

        async def fake_stream_chat(*_args, **_kwargs):
            yield StreamEvent(
                event_type="tool_call",
                content="tool",
                sequence=0,
                metadata={"tool_name": "run_minecraft_command", "args": {"command": "say hi"}},
            )
            yield StreamEvent(
                event_type="tool_result",
                content="ok",
                sequence=1,
                metadata={"tool_name": "run_minecraft_command"},
            )
            yield StreamEvent(
                event_type="content",
                content="answer",
                sequence=2,
                metadata={"is_complete": True, "all_messages": []},
            )

        class FakeAgentManager:
            def get_agent(self):
                return object()

        monkeypatch.setattr("services.agent.worker.stream_chat", fake_stream_chat)
        monkeypatch.setattr("services.agent.worker.ProviderRegistry.get_model", lambda *_args: object())
        monkeypatch.setattr("services.agent.core.get_agent_manager", lambda: FakeAgentManager())
        monkeypatch.setattr("services.agent.mcp.get_mcp_manager", lambda _settings: SimpleNamespace(servers={}))
        monkeypatch.setattr(
            worker,
            "_schedule_title_generation",
            lambda *_args, **_kwargs: asyncio.sleep(0),
        )

        request = ChatRequest(
            connection_id=connection_id,
            content="hello",
            player_name="Alice",
            use_context=False,
            broadcast_ai_chat=True,
        )
        await worker._process_request_locked(request, connection_id)

        queue = broker.get_response_queue(connection_id)
        assert queue is not None
        responses = []
        while not queue.empty():
            responses.append(queue.get_nowait())

        assert isinstance(responses[0], SystemNotification)
        assert responses[0].player_name == "@a"
        assert responses[0].message == "AI正在为Alice思考"

        visible_chunks = [response for response in responses if isinstance(response, StreamChunk)]
        assert visible_chunks
        assert all(chunk.player_name == "Alice" for chunk in visible_chunks)
        assert all(chunk.target == "@a" for chunk in visible_chunks)

        addon_sync = [
            response for response in responses
            if isinstance(response, dict) and response.get("type") == "ai_response_sync"
        ]
        assert addon_sync == [{
            "type": "ai_response_sync",
            "player_name": "Alice",
            "role": "assistant",
            "text": "answer",
        }]

    asyncio.run(_run())
