"""WebSocket 对话管理命令回归测试。"""

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from config.settings import Settings
from core.queue import MessageBroker
from services.auth.jwt_handler import JWTHandler
from services.websocket.connection import ConnectionState
from services.websocket.server import WebSocketServer


def _build_turn(index: int) -> list[ModelRequest | ModelResponse]:
    return [
        ModelRequest(parts=[UserPromptPart(content=f"user-{index}")]),
        ModelResponse(parts=[TextPart(content=f"assistant-{index}")]),
    ]


def _server(tmp_path, monkeypatch) -> tuple[WebSocketServer, MessageBroker, ConnectionState]:
    monkeypatch.chdir(tmp_path)
    settings = Settings(default_provider="ollama")
    broker = MessageBroker()
    state = ConnectionState()
    broker.register_connection(state.id)
    return WebSocketServer(broker, settings, JWTHandler(settings)), broker, state


def test_conversation_new_rejects_existing_id_without_overwrite(tmp_path, monkeypatch):
    async def _run() -> None:
        server, broker, state = _server(tmp_path, monkeypatch)
        broker.set_conversation_history(state.id, "alice", _build_turn(1), "build")

        msg = await server._handle_conversation_locked(state, "new build", "alice")

        history = broker.get_conversation_history(state.id, "alice", "build")
        assert "已存在" in msg.text
        assert len(history) == 2
        assert history[0].parts[0].content == "user-1"

    asyncio.run(_run())


def test_conversation_new_generates_unique_ids(tmp_path, monkeypatch):
    async def _run() -> None:
        server, _broker, state = _server(tmp_path, monkeypatch)

        first = await server._handle_conversation_locked(state, "new", "alice")
        first_id = state.get_player_session("alice").active_conversation_id
        second = await server._handle_conversation_locked(state, "new", "alice")
        second_id = state.get_player_session("alice").active_conversation_id

        assert "已新建" in first.text
        assert "已新建" in second.text
        assert first_id != second_id

    asyncio.run(_run())


def test_switch_model_clears_all_player_runtime_conversations(tmp_path, monkeypatch):
    async def _run() -> None:
        server, broker, state = _server(tmp_path, monkeypatch)
        broker.set_conversation_history(state.id, "alice", _build_turn(1), "default")
        broker.set_conversation_history(state.id, "alice", _build_turn(2), "other")
        broker.set_conversation_history(state.id, "bob", _build_turn(3), "default")

        msg = await server._handle_switch_model_locked(state, "ollama", "alice")

        assert "已切换" in msg.text
        assert broker.get_conversation_history(state.id, "alice", "default") == []
        assert broker.get_conversation_history(state.id, "alice", "other") == []
        assert broker.get_conversation_history(state.id, "bob", "default") != []

    asyncio.run(_run())
