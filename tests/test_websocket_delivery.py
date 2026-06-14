"""MCBE outbound delivery adapter tests."""

import json

import pytest

from services.websocket.delivery import AiResponseSync, McbeOutboundDelivery


class DummySender:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    async def send(self, payload: str) -> None:
        self.payloads.append(payload)


@pytest.mark.asyncio
async def test_delivery_chunks_tellraw_through_flow_control(monkeypatch) -> None:
    monkeypatch.setattr("services.websocket.delivery.asyncio.sleep", _no_sleep)
    sender = DummySender()
    delivery = McbeOutboundDelivery(connection_id="conn", send_payload=sender.send)

    count = await delivery.send_tellraw("中" * 500, color="§a", source="test_tellraw")

    assert count == len(sender.payloads)
    assert count >= 2
    for payload in sender.payloads:
        data = json.loads(payload)
        command_line = data["body"]["commandLine"]
        assert "tellraw" in command_line
        assert len(command_line.encode("utf-8")) <= 461


@pytest.mark.asyncio
async def test_delivery_chunks_tellraw_to_target_player(monkeypatch) -> None:
    monkeypatch.setattr("services.websocket.delivery.asyncio.sleep", _no_sleep)
    sender = DummySender()
    delivery = McbeOutboundDelivery(connection_id="conn", send_payload=sender.send)

    count = await delivery.send_tellraw(
        "只给 Alice",
        color="§a",
        source="test_tellraw",
        target="Alice",
    )

    assert count == 1
    command_line = json.loads(sender.payloads[0])["body"]["commandLine"]
    assert command_line.startswith("tellraw Alice ")
    assert "@a" not in command_line


@pytest.mark.asyncio
async def test_delivery_syncs_ai_response_with_chunk_metadata(monkeypatch) -> None:
    monkeypatch.setattr("services.websocket.delivery.asyncio.sleep", _no_sleep)
    sender = DummySender()
    delivery = McbeOutboundDelivery(connection_id="conn", send_payload=sender.send)

    count = await delivery.send_ai_response(
        AiResponseSync(player_name="Alice", role="assistant", text="X" * 1000)
    )

    assert count == len(sender.payloads)
    assert count >= 2
    ids: set[str] = set()
    for index, payload in enumerate(sender.payloads, start=1):
        command_line = json.loads(payload)["body"]["commandLine"]
        assert "scriptevent mcbeai:ai_resp" in command_line
        inner = json.loads(command_line[command_line.find("{"):])
        ids.add(inner["id"])
        assert inner["p"] == "Alice"
        assert inner["r"] == "assistant"
        assert inner["i"] == index
        assert inner["n"] == count
    assert len(ids) == 1


@pytest.mark.asyncio
async def test_delivery_rejects_too_long_raw_command() -> None:
    sender = DummySender()
    delivery = McbeOutboundDelivery(connection_id="conn", send_payload=sender.send)

    with pytest.raises(ValueError, match="raw command too long"):
        await delivery.send_raw_command("say " + "X" * 1000)

    assert sender.payloads == []


@pytest.mark.asyncio
async def test_delivery_rejects_raw_command_over_byte_budget() -> None:
    sender = DummySender()
    delivery = McbeOutboundDelivery(connection_id="conn", send_payload=sender.send)

    with pytest.raises(ValueError, match="byte budget"):
        await delivery.send_raw_command("say " + "中" * 200)

    assert sender.payloads == []


@pytest.mark.asyncio
async def test_delivery_registers_raw_command_before_send() -> None:
    events: list[str] = []

    async def send(_payload: str) -> None:
        events.append("send")

    delivery = McbeOutboundDelivery(connection_id="conn", send_payload=send)

    request_id = await delivery.send_raw_command(
        "say hi",
        before_send=lambda req_id: events.append(f"register:{req_id}"),
    )

    assert events == [f"register:{request_id}", "send"]


async def _no_sleep(_delay: float) -> None:
    return None
