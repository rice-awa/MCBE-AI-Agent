import json
import sys
from pathlib import Path

import pytest

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
    payload = json.loads(command.removeprefix("scriptevent mcbeai:bridge_request "))
    assert payload == {
        "request_id": "req-1",
        "capability": "get_player_snapshot",
        "payload": {"target": "@a"},
    }


def test_reassemble_bridge_chunks_should_restore_payload() -> None:
    chunks = [
        "MCBEAI|RESP|req-1|1/2|{\"ok\":true,",
        "MCBEAI|RESP|req-1|2/2|\"players\":[\"Steve\"]}",
    ]
    parsed = [decode_bridge_chat_chunk(chunk) for chunk in chunks]
    response = reassemble_bridge_chunks(parsed)
    assert response.request_id == "req-1"
    assert response.payload["players"] == ["Steve"]


def test_decode_bridge_chat_chunk_should_reject_invalid_namespace_prefix() -> None:
    with pytest.raises(ValueError, match="Invalid bridge chunk namespace"):
        decode_bridge_chat_chunk("WRONG|RESP|req-1|1/1|{\"ok\":true}")


def test_decode_bridge_chat_chunk_should_reject_invalid_response_prefix() -> None:
    with pytest.raises(ValueError, match="Invalid bridge chunk prefix"):
        decode_bridge_chat_chunk("MCBEAI|BAD|req-1|1/1|{\"ok\":true}")


def test_reassemble_bridge_chunks_should_reject_empty_list() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        reassemble_bridge_chunks([])


def test_decode_bridge_chat_chunk_should_reject_invalid_part_metadata() -> None:
    with pytest.raises(ValueError, match="Invalid bridge chunk metadata"):
        decode_bridge_chat_chunk("MCBEAI|RESP|req-1|x/2|{\"ok\":true}")


def test_reassemble_bridge_chunks_should_reject_invalid_json_payload() -> None:
    chunks = [decode_bridge_chat_chunk("MCBEAI|RESP|req-1|1/1|{\"ok\":}")]
    with pytest.raises(ValueError, match="Invalid bridge payload JSON"):
        reassemble_bridge_chunks(chunks)


def test_reassemble_bridge_chunks_should_reject_request_id_mismatch() -> None:
    chunks = [
        decode_bridge_chat_chunk("MCBEAI|RESP|req-1|1/2|{\"ok\":true,"),
        decode_bridge_chat_chunk("MCBEAI|RESP|req-2|2/2|\"value\":1}"),
    ]
    with pytest.raises(ValueError, match="Bridge chunks request_id mismatch"):
        reassemble_bridge_chunks(chunks)


def test_reassemble_bridge_chunks_should_reject_total_chunks_mismatch() -> None:
    chunks = [
        decode_bridge_chat_chunk("MCBEAI|RESP|req-1|1/2|{\"ok\":true,"),
        decode_bridge_chat_chunk("MCBEAI|RESP|req-1|2/3|\"value\":1}"),
    ]
    with pytest.raises(ValueError, match="Bridge chunks total_chunks mismatch"):
        reassemble_bridge_chunks(chunks)


def test_reassemble_bridge_chunks_should_reject_incomplete_or_duplicate_chunks() -> None:
    chunks = [
        decode_bridge_chat_chunk("MCBEAI|RESP|req-1|1/3|{\"ok\":"),
        decode_bridge_chat_chunk("MCBEAI|RESP|req-1|2/3|true,"),
        decode_bridge_chat_chunk("MCBEAI|RESP|req-1|2/3|\"value\":1}"),
    ]
    with pytest.raises(ValueError, match="Bridge chunks are incomplete or out of sequence"):
        reassemble_bridge_chunks(chunks)
