"""Addon 桥接服务测试。"""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from services.addon.service import AddonBridgeService


async def _send_successful_command(command: str) -> str:
    assert command.startswith("scriptevent mcbeai:bridge_request ")
    return "命令执行成功"


@pytest.mark.asyncio
async def test_send_request_should_complete_after_chunk_reassembly() -> None:
    service = AddonBridgeService(timeout_seconds=1.0)
    connection_id = uuid4()

    task = asyncio.create_task(
        service.request_capability(
            connection_id=connection_id,
            capability="get_player_snapshot",
            payload={"target": "Steve"},
            send_command=_send_successful_command,
        )
    )

    await asyncio.sleep(0)
    handled = service.handle_player_message(
        connection_id,
        "MCBEAI_TOOL",
        'MCBEAI|RESP|addon-1|1/1|{"ok":true,"payload":{"name":"Steve"}}',
    )
    if not handled:
        session = service._sessions[connection_id]
        request_id = next(iter(session._pending_requests))
        handled = service.handle_player_message(
            connection_id,
            "MCBEAI_TOOL",
            f'MCBEAI|RESP|{request_id}|1/1|{{"ok":true,"payload":{{"name":"Steve"}}}}',
        )

    assert handled is True
    result = await task
    assert result["payload"]["name"] == "Steve"


@pytest.mark.asyncio
async def test_request_capability_should_timeout_when_no_response_arrives() -> None:
    service = AddonBridgeService(timeout_seconds=0.01)

    with pytest.raises(RuntimeError, match="Addon 桥接响应超时"):
        await service.request_capability(
            connection_id=uuid4(),
            capability="get_player_snapshot",
            payload={"target": "@a"},
            send_command=_send_successful_command,
        )


@pytest.mark.asyncio
async def test_handle_player_message_should_isolate_concurrent_connections() -> None:
    service = AddonBridgeService(timeout_seconds=1.0)
    connection_a = uuid4()
    connection_b = uuid4()

    task_a = asyncio.create_task(
        service.request_capability(
            connection_id=connection_a,
            capability="get_player_snapshot",
            payload={"target": "Steve"},
            send_command=_send_successful_command,
        )
    )
    task_b = asyncio.create_task(
        service.request_capability(
            connection_id=connection_b,
            capability="get_inventory_snapshot",
            payload={"target": "Alex"},
            send_command=_send_successful_command,
        )
    )

    await asyncio.sleep(0)
    request_id_a = next(iter(service._sessions[connection_a]._pending_requests))
    request_id_b = next(iter(service._sessions[connection_b]._pending_requests))

    assert service.handle_player_message(
        connection_a,
        "MCBEAI_TOOL",
        f'MCBEAI|RESP|{request_id_a}|1/1|{{"ok":true,"payload":{{"name":"Steve"}}}}',
    )
    assert service.handle_player_message(
        connection_b,
        "MCBEAI_TOOL",
        f'MCBEAI|RESP|{request_id_b}|1/1|{{"ok":true,"payload":{{"items":[{{"id":"diamond"}}]}}}}',
    )

    result_a = await task_a
    result_b = await task_b
    assert result_a["payload"]["name"] == "Steve"
    assert result_b["payload"]["items"][0]["id"] == "diamond"


def test_is_bridge_chat_message_should_match_tool_player_and_prefix() -> None:
    service = AddonBridgeService()

    assert service.is_bridge_chat_message("MCBEAI_TOOL", "MCBEAI|RESP|req-1|1/1|{}") is True
    assert service.is_bridge_chat_message("Steve", "MCBEAI|RESP|req-1|1/1|{}") is False
    assert service.is_bridge_chat_message("MCBEAI_TOOL", "hello") is False

