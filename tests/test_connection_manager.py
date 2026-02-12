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
