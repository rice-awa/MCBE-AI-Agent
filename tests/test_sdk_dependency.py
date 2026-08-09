import inspect

import mcbe_ws_sdk
from mcbe_ws_sdk import (
    MCBEWS_V1,
    MCBEWS_V1_MANIFEST,
    MCBEWS_V1_WIRE_VECTORS,
    AddonBridgeService,
    McbeServerFacade,
    McbewsV1Delivery,
)
from mcbe_ws_sdk.profiles.mcbews_v1 import (
    decode_text_response_frame,
    decode_ui_chat_chunk,
    reassemble_ui_chat_chunks,
)
from mcbe_ws_sdk.protocol.minecraft import MinecraftCommand


def test_mcbe_ws_sdk_importable():
    assert McbeServerFacade is not None
    assert McbewsV1Delivery is not None
    assert MCBEWS_V1.protocol_line == "MCBEWS/1"
    assert MCBEWS_V1.capability_request_script_event_id == "mcbews:bridge_req"
    assert MCBEWS_V1.text_response_script_event_id == "mcbews:text_resp"
    assert MCBEWS_V1.capability_response_chat_prefix == "MCBEWS|BRIDGE"
    assert MCBEWS_V1.ui_chat_chunk_prefix == "MCBEWS|UI_CHAT"
    assert MCBEWS_V1.trusted_bridge_player_name == "MCBEWS_BRIDGE"
    assert MCBEWS_V1.capability_request_schema_version == 2
    assert MCBEWS_V1.session_schema_version == 1
    assert MCBEWS_V1.text_response_framing_version == 1
    assert MCBEWS_V1.ddui_persistence_version == 2
    assert MCBEWS_V1.command_line_byte_budget == 461

    # Deprecated aliases remain readable for one migration cycle but are not
    # the contract source used by Host code.
    assert MCBEWS_V1.bridge_request_message_id == MCBEWS_V1.capability_request_script_event_id
    assert MCBEWS_V1.request_version == MCBEWS_V1.capability_request_schema_version

    assert MCBEWS_V1_MANIFEST["protocol_line"] == "MCBEWS/1"
    assert MCBEWS_V1_MANIFEST["versions"] == {
        "capability_request_schema": 2,
        "session_schema": 1,
        "text_response_framing": 1,
        "ddui_persistence": 2,
    }
    assert MCBEWS_V1_MANIFEST["limits"]["command_line_budget_source"] == "empirical"
    assert set(MCBEWS_V1_WIRE_VECTORS) >= {
        "bridge_requests",
        "ui_chat",
        "text_response",
        "session",
        "approval",
        "behavior",
    }

    ui_vector = MCBEWS_V1_WIRE_VECTORS["ui_chat"][0]
    ui_chunk = decode_ui_chat_chunk(ui_vector["message"])
    ui_message = reassemble_ui_chat_chunks([ui_chunk])
    assert (ui_message.player_name, ui_message.conversation_id, ui_message.message) == (
        "Steve",
        "chat-a",
        "你好 😀",
    )

    text_vector = MCBEWS_V1_WIRE_VECTORS["text_response"][0]["frame"]
    text_chunk = decode_text_response_frame(dict(text_vector))
    assert text_chunk.conversation_id == "chat-a"
    assert text_chunk.usage is not None
    assert (text_chunk.usage.input_tokens, text_chunk.usage.output_tokens) == (3, 5)

    delivery_params = inspect.signature(McbewsV1Delivery.send_response).parameters
    assert {"player_name", "conversation_id", "title", "usage"} <= set(delivery_params)

    assert AddonBridgeService is not None
    assert getattr(mcbe_ws_sdk, "__version__", None)

    tellraw = MinecraftCommand.create_tellraw("多人消息", target="Steve")
    assert tellraw.body.origin.type == "player"
