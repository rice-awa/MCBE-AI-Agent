"""Map host ``Settings`` onto mcbe-ws-sdk gateway configuration types."""

from __future__ import annotations

from mcbe_ws_sdk import (
    AddonBridgeSettings,
    CommandRegistry,
    FlowControlSettings,
    GatewaySettings,
    McbewsV1Profile,
    WebsocketTransportConfig,
)

from config.settings import Settings


def _text_resp_delay(settings: Settings) -> float:
    delays = settings.flow_control.chunk_delays
    value = getattr(delays, "text_resp", None)
    if value is None:
        value = getattr(delays, "ai_resp", 0.15)
    return float(value)


def _text_resp_prelude_delay(settings: Settings) -> float:
    delays = settings.flow_control.chunk_delays
    value = getattr(delays, "text_resp_prelude", None)
    if value is None:
        value = getattr(delays, "ai_resp_prelude", 0.5)
    return float(value)


def build_gateway_settings(settings: Settings) -> GatewaySettings:
    """Build SDK ``GatewaySettings`` from host application settings.

    Protocol names always come from ``McbewsV1Profile`` defaults (mcbews v1).
    Host ``addon.protocol`` is documentation/compat only and is not copied into
    the profile, so old mcbeai namespaces cannot override runtime wire IDs.
    """
    text_resp = _text_resp_delay(settings)
    delays = {
        "tellraw": float(settings.flow_control.chunk_delays.tellraw),
        "scriptevent": float(settings.flow_control.chunk_delays.scriptevent),
        "text_resp": text_resp,
    }
    flow = FlowControlSettings(
        command_line_byte_budget=settings.flow_control.command_line_byte_budget,
        max_chunk_content_length=settings.max_chunk_content_length,
        chunk_sentence_mode=settings.chunk_sentence_mode,
        chunk_delays=delays,
    )
    profile = McbewsV1Profile(
        response_chunk_delay=text_resp,
        response_prelude_delay=_text_resp_prelude_delay(settings),
    )
    websocket = WebsocketTransportConfig(
        host=settings.host,
        port=settings.port,
        ping_interval=float(settings.websocket.ping_interval),
        ping_timeout=float(settings.websocket.ping_timeout),
        close_timeout=float(settings.websocket.close_timeout),
        max_size=settings.websocket.max_size,
        max_queue=settings.websocket.max_queue,
    )
    addon = AddonBridgeSettings(profile=profile)
    return GatewaySettings(flow=flow, addon=addon, websocket=websocket)


def build_command_registry(settings: Settings) -> CommandRegistry:
    """Construct SDK ``CommandRegistry`` from ``settings.minecraft.commands``."""
    return CommandRegistry(commands_config=settings.minecraft.commands)
