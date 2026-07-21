"""Host Settings → SDK GatewaySettings / CommandRegistry mapping."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import Settings
from services.gateway.settings_map import build_command_registry, build_gateway_settings


def test_build_gateway_settings_maps_host_and_port():
    s = Settings()
    s.host = "127.0.0.1"
    s.port = 18080
    gw = build_gateway_settings(s)
    assert gw.websocket.host == "127.0.0.1"
    assert gw.websocket.port == 18080
    assert gw.flow.command_line_byte_budget == 461
    assert "text_resp" in gw.flow.chunk_delays
    assert "ai_resp" not in gw.flow.chunk_delays
    assert gw.addon.profile.bridge_request_message_id == "mcbews:bridge_req"
    assert gw.addon.profile.response_message_id == "mcbews:text_resp"


def test_build_gateway_settings_maps_flow_and_websocket_knobs():
    s = Settings()
    s.max_chunk_content_length = 321
    s.chunk_sentence_mode = False
    s.flow_control.chunk_delays.text_resp = 0.25
    s.flow_control.chunk_delays.text_resp_prelude = 0.75
    s.websocket.ping_interval = 40
    s.websocket.ping_timeout = 12
    s.websocket.close_timeout = 9
    s.websocket.max_size = 1024
    s.websocket.max_queue = 8

    gw = build_gateway_settings(s)

    assert gw.flow.max_chunk_content_length == 321
    assert gw.flow.chunk_sentence_mode is False
    assert gw.flow.chunk_delays["text_resp"] == 0.25
    assert gw.flow.chunk_delays["tellraw"] == s.flow_control.chunk_delays.tellraw
    assert gw.flow.chunk_delays["scriptevent"] == s.flow_control.chunk_delays.scriptevent
    assert gw.addon.profile.response_chunk_delay == 0.25
    assert gw.addon.profile.response_prelude_delay == 0.75
    assert gw.websocket.ping_interval == 40.0
    assert gw.websocket.ping_timeout == 12.0
    assert gw.websocket.close_timeout == 9.0
    assert gw.websocket.max_size == 1024
    assert gw.websocket.max_queue == 8


def test_build_command_registry_registers_login_prefix():
    s = Settings()
    reg = build_command_registry(s)
    parsed = reg.resolve_parsed("#登录 123456")
    assert parsed is not None
    assert parsed.type == "login"
    assert parsed.content == "123456"


def test_flow_control_delay_config_defaults_and_legacy_load():
    from config.settings import FlowControlDelayConfig

    defaults = FlowControlDelayConfig()
    assert defaults.text_resp == 0.15
    assert defaults.text_resp_prelude == 0.5
    # Host delivery still looks up legacy keys via model_dump this cycle.
    dumped = defaults.model_dump()
    assert dumped["text_resp"] == 0.15
    assert dumped["ai_resp"] == 0.15
    assert dumped["ai_resp_prelude"] == 0.5

    legacy = FlowControlDelayConfig.model_validate(
        {"tellraw": 0.01, "scriptevent": 0.02, "ai_resp": 0.33, "ai_resp_prelude": 0.66}
    )
    assert legacy.text_resp == 0.33
    assert legacy.text_resp_prelude == 0.66
    assert legacy.ai_resp == 0.33
    assert legacy.ai_resp_prelude == 0.66


def test_addon_protocol_defaults_are_mcbews():
    from config.settings import AddonProtocolConfig

    p = AddonProtocolConfig()
    assert p.bridge_message_id == "mcbews:bridge_req"
    assert p.bridge_prefix == "MCBEWS|BRIDGE"
    assert p.ui_chat_prefix == "MCBEWS|UI_CHAT"
    assert p.bridge_tool_player_name == "MCBEWS_BRIDGE"
    assert p.ai_resp_message_id == "mcbews:text_resp"
