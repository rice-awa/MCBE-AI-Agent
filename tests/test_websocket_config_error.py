import asyncio

from config.settings import Settings
from core.queue import MessageBroker
from services.auth.jwt_handler import JWTHandler
from services.websocket.connection import ConnectionState
from services.websocket.server import WebSocketServer


class _DummyWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def test_unconfigured_server_sends_game_message_on_connect() -> None:
    async def _run() -> None:
        settings = Settings()
        broker = MessageBroker()
        server = WebSocketServer(
            broker,
            settings,
            JWTHandler(settings),
            configuration_error=FileNotFoundError("config.json is required"),
        )
        websocket = _DummyWebSocket()

        await server.handle_connection(websocket)  # type: ignore[arg-type]

        assert any("服务未配置" in payload for payload in websocket.sent)
        assert any("python cli.py init" in payload for payload in websocket.sent)

    asyncio.run(_run())


def test_unconfigured_server_rejects_commands_with_game_message() -> None:
    async def _run() -> None:
        settings = Settings()
        broker = MessageBroker()
        server = WebSocketServer(
            broker,
            settings,
            JWTHandler(settings),
            configuration_error=FileNotFoundError("config.json is required"),
        )
        state = ConnectionState(websocket=_DummyWebSocket())

        await server.handle_command(state, "chat", "hello", player_name="Steve")

        websocket = state.websocket
        assert websocket is not None
        assert any("服务未配置" in payload for payload in websocket.sent)
        assert broker._request_queue.qsize() == 0

    asyncio.run(_run())
