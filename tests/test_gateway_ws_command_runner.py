"""WsCommandRunner unit tests."""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcbe_ws_sdk import FlowControlSettings, MinecraftCommandResponse
from mcbe_ws_sdk.gateway.connection import ConnectionState

from services.gateway.ws_command_runner import WsCommandRunner


@pytest.mark.asyncio
async def test_ws_command_runner_resolve_completes_future():
    flow = FlowControlSettings()
    runner = WsCommandRunner(flow, timeout=1.0)
    sent: list[str] = []

    async def send_payload(payload: str) -> None:
        sent.append(payload)

    state = ConnectionState(id=uuid4(), send_payload=send_payload)
    task = asyncio.create_task(runner.run(state, "say hello"))
    await asyncio.sleep(0)  # let run() register + send
    assert sent, "expected raw command payload"
    bucket = runner._pending[state.id]
    request_id = next(iter(bucket.keys()))
    ok = runner.resolve(
        state,
        MinecraftCommandResponse(
            request_id=request_id,
            header={"requestId": request_id, "messagePurpose": "commandResponse"},
            body={"statusCode": 0, "statusMessage": "hello"},
        ),
    )
    assert ok is True
    resp = await task
    assert resp.request_id == request_id
