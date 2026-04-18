import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.addon.protocol import (
    decode_bridge_chat_chunk,
    encode_bridge_request,
    reassemble_bridge_chunks,
)


def test_encode_bridge_request_should_use_namespaced_message_id() -> None:
    command = encode_bridge_request(
        request_id="req-1",
        capability="get_player_snapshot",
        payload={"target": "@a"},
    )
    assert command.startswith("scriptevent mcbeai:bridge_request ")


def test_reassemble_bridge_chunks_should_restore_payload() -> None:
    chunks = [
        "MCBEAI|RESP|req-1|1/2|{\"ok\":true,",
        "MCBEAI|RESP|req-1|2/2|\"players\":[\"Steve\"]}",
    ]
    parsed = [decode_bridge_chat_chunk(chunk) for chunk in chunks]
    response = reassemble_bridge_chunks(parsed)
    assert response.request_id == "req-1"
    assert response.payload["players"] == ["Steve"]
