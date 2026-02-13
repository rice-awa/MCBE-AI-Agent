"""ConnectionManager 连接关闭行为测试。"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from core.queue import MessageBroker
from services.websocket.connection import ConnectionManager, ConnectionState


def test_unregister_should_resolve_pending_and_queued_command_futures() -> None:
    async def _run() -> None:
        broker = MessageBroker()
        manager = ConnectionManager(broker)

        state = ConnectionState()
        state.response_queue = broker.register_connection(state.id)

        loop = asyncio.get_running_loop()
        pending_future: asyncio.Future[str] = loop.create_future()
        queued_future: asyncio.Future[str] = loop.create_future()

        state.pending_command_futures["req-1"] = pending_future
        await state.response_queue.put(
            {
                "type": "run_command",
                "command": "say hi",
                "result_future": queued_future,
            }
        )

        manager._connections[state.id] = state
        manager._sender_tasks[state.id] = asyncio.create_task(asyncio.sleep(60))

        await manager.unregister(state.id)

        assert pending_future.done()
        assert pending_future.result() == "命令执行失败: 连接已关闭"
        assert queued_future.done()
        assert queued_future.result() == "命令执行失败: 连接已关闭"
        assert manager.get_connection(state.id) is None
        assert broker.get_response_queue(state.id) is None

    asyncio.run(_run())


class _DummyWebSocket:
    async def send(self, payload: str) -> None:
        return None


def test_run_command_should_drop_cancelled_future_from_pending_map() -> None:
    async def _run() -> None:
        broker = MessageBroker()
        manager = ConnectionManager(broker)
        state = ConnectionState(websocket=_DummyWebSocket())

        loop = asyncio.get_running_loop()
        result_future: asyncio.Future[str] = loop.create_future()

        await manager._run_command(state, "say hi", result_future)
        assert len(state.pending_command_futures) == 1

        result_future.cancel()
        await asyncio.sleep(0)

        assert state.pending_command_futures == {}

    asyncio.run(_run())


def test_unregister_should_fail_inflight_run_command_future_immediately() -> None:
    async def _run() -> None:
        broker = MessageBroker()
        manager = ConnectionManager(broker)

        state = ConnectionState()
        state.response_queue = broker.register_connection(state.id)
        manager._connections[state.id] = state

        loop = asyncio.get_running_loop()
        queued_future: asyncio.Future[str] = loop.create_future()
        await state.response_queue.put(
            {
                "type": "run_command",
                "command": "say hi",
                "result_future": queued_future,
            }
        )

        gate = asyncio.Event()

        async def _blocked_handle_response(_state: ConnectionState, _response: object) -> None:
            gate.set()
            await asyncio.sleep(60)

        manager._handle_response = _blocked_handle_response  # type: ignore[method-assign]
        task = asyncio.create_task(manager._response_sender(state))
        manager._sender_tasks[state.id] = task

        await gate.wait()
        await manager.unregister(state.id)

        assert queued_future.done()
        assert queued_future.result() == "命令执行失败: 连接已关闭"

    asyncio.run(_run())
