import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.addon.protocol import (
    AI_RESP_MESSAGE_ID,
    BRIDGE_MESSAGE_ID,
    BRIDGE_PREFIX,
    BRIDGE_TOOL_PLAYER_NAME,
    UI_CHAT_PREFIX,
    decode_bridge_chat_chunk,
    decode_ui_chat_chunk,
    encode_bridge_request,
    reassemble_bridge_chunks,
    reassemble_ui_chat_chunks,
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


def test_reassemble_bridge_chunks_should_reject_non_object_json_payload() -> None:
    chunks = [decode_bridge_chat_chunk('MCBEAI|RESP|req-1|1/1|["not","an","object"]')]
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


# ─── UI Chat 协议测试 ───────────────────────────────────────────


def test_decode_ui_chat_chunk_should_parse_single_chunk() -> None:
    chunk = decode_ui_chat_chunk('MCBEAI|UI_CHAT|ui-1|1/1|{"player":"Steve","message":"hello"}')
    assert chunk.msg_id == "ui-1"
    assert chunk.chunk_index == 1
    assert chunk.total_chunks == 1


def test_reassemble_ui_chat_chunks_should_restore_message() -> None:
    chunks = [
        decode_ui_chat_chunk('MCBEAI|UI_CHAT|ui-2|1/2|{"player":"Steve","mes'),
        decode_ui_chat_chunk('MCBEAI|UI_CHAT|ui-2|2/2|sage":"你好世界"}'),
    ]
    ui_msg = reassemble_ui_chat_chunks(chunks)
    assert ui_msg.msg_id == "ui-2"
    assert ui_msg.player_name == "Steve"
    assert ui_msg.message == "你好世界"


def test_decode_ui_chat_chunk_should_reject_wrong_prefix() -> None:
    with pytest.raises(ValueError, match="Invalid UI chat chunk prefix"):
        decode_ui_chat_chunk("MCBEAI|RESP|ui-1|1/1|{}")


def test_reassemble_ui_chat_chunks_should_reject_missing_message() -> None:
    chunks = [decode_ui_chat_chunk('MCBEAI|UI_CHAT|ui-3|1/1|{"player":"Steve"}')]
    with pytest.raises(ValueError, match="missing message"):
        reassemble_ui_chat_chunks(chunks)


# ─── 配置驱动测试 ────────────────────────────────────────────────


def _fake_settings(**protocol_overrides) -> SimpleNamespace:
    defaults = dict(
        bridge_message_id="mcbeai:bridge_request",
        bridge_prefix="MCBEAI|RESP",
        ui_chat_prefix="MCBEAI|UI_CHAT",
        bridge_tool_player_name="MCBEAI_TOOL",
        ai_resp_message_id="mcbeai:ai_resp",
    )
    defaults.update(protocol_overrides)
    return SimpleNamespace(addon=SimpleNamespace(protocol=SimpleNamespace(**defaults)))


def test_module_level_constants_still_exported_for_backward_compat() -> None:
    """模块级常量应保留，供 service.py / tools_mcbe_simulator.py 等外部导入。"""
    assert BRIDGE_MESSAGE_ID == "mcbeai:bridge_request"
    assert BRIDGE_PREFIX == "MCBEAI|RESP"
    assert UI_CHAT_PREFIX == "MCBEAI|UI_CHAT"
    assert BRIDGE_TOOL_PLAYER_NAME == "MCBEAI_TOOL"
    assert AI_RESP_MESSAGE_ID == "mcbeai:ai_resp"


def test_encode_bridge_request_reads_message_id_from_settings() -> None:
    """encode_bridge_request 应从 Settings.addon.protocol.bridge_message_id 读取。"""
    fake = _fake_settings(bridge_message_id="custom:bridge_req")
    with patch("services.addon.protocol.get_settings", return_value=fake):
        command = encode_bridge_request(
            request_id="req-9",
            capability="get_player_snapshot",
            payload={"target": "@a"},
        )
    assert command.startswith("scriptevent custom:bridge_req ")
    payload = json.loads(command.removeprefix("scriptevent custom:bridge_req "))
    assert payload["request_id"] == "req-9"


def test_decode_bridge_chat_chunk_reads_prefix_from_settings() -> None:
    """decode_bridge_chat_chunk 应从 Settings.addon.protocol.bridge_prefix 读取前缀校验。"""
    fake = _fake_settings(bridge_prefix="CUSTOM|RESP")
    with patch("services.addon.protocol.get_settings", return_value=fake):
        chunk = decode_bridge_chat_chunk("CUSTOM|RESP|req-1|1/1|{\"ok\":true}")
    assert chunk.request_id == "req-1"
    assert chunk.content == '{"ok":true}'


def test_decode_bridge_chat_chunk_rejects_old_prefix_when_config_changed() -> None:
    """配置变更后，旧前缀分片应被拒绝。"""
    fake = _fake_settings(bridge_prefix="CUSTOM|RESP")
    with patch("services.addon.protocol.get_settings", return_value=fake):
        with pytest.raises(ValueError, match="Invalid bridge chunk namespace"):
            decode_bridge_chat_chunk("MCBEAI|RESP|req-1|1/1|{\"ok\":true}")


def test_default_config_preserves_original_behavior() -> None:
    """默认配置值应与原始硬编码行为一致。"""
    fake = _fake_settings()
    with patch("services.addon.protocol.get_settings", return_value=fake):
        command = encode_bridge_request("req-1", "cap", {"k": "v"})
        assert command.startswith("scriptevent mcbeai:bridge_request ")

        chunk = decode_bridge_chat_chunk("MCBEAI|RESP|req-1|1/1|{\"ok\":true}")
        assert chunk.request_id == "req-1"
