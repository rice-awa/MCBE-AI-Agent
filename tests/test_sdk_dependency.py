import mcbe_ws_sdk
from mcbe_ws_sdk import MCBEWS_V1, AddonBridgeService, McbeServerFacade
from mcbe_ws_sdk.protocol.minecraft import MinecraftCommand


def test_mcbe_ws_sdk_importable():
    assert McbeServerFacade is not None
    assert MCBEWS_V1.bridge_request_message_id == "mcbews:bridge_req"
    assert MCBEWS_V1.response_message_id == "mcbews:text_resp"
    assert MCBEWS_V1.bridge_response_prefix == "MCBEWS|BRIDGE"
    assert MCBEWS_V1.ui_chat_prefix == "MCBEWS|UI_CHAT"
    assert MCBEWS_V1.bridge_sender == "MCBEWS_BRIDGE"
    assert MCBEWS_V1.request_version == 2
    assert AddonBridgeService is not None
    assert getattr(mcbe_ws_sdk, "__version__", None)

    tellraw = MinecraftCommand.create_tellraw("多人消息", target="Steve")
    assert tellraw.body.origin.type == "player"
