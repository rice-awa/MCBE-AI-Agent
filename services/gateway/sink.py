"""SDK ResponseSink that delivers OutboundText / SystemNotification to MCBE."""

from __future__ import annotations

from mcbe_ws_sdk import (
    DefaultResponseSink,
    FlowControlSettings,
    McbeOutboundDelivery,
    OutboundText,
    SystemNotification,
)
from mcbe_ws_sdk.gateway.connection import ConnectionState


class HostResponseSink(DefaultResponseSink):
    """Deliver SDK response messages via McbeOutboundDelivery."""

    def __init__(
        self,
        flow: FlowControlSettings,
        *,
        log_raw_payloads: bool = False,
    ) -> None:
        self._flow = flow
        self._log_raw_payloads = log_raw_payloads

    def _delivery(self, state: ConnectionState) -> McbeOutboundDelivery | None:
        if state.send_payload is None:
            return None
        return McbeOutboundDelivery(
            connection_id=state.id,
            send_payload=state.send_payload,
            settings=self._flow,
            log_raw_payloads=self._log_raw_payloads,
        )

    async def on_outbound_text(self, state: ConnectionState, message: OutboundText) -> None:
        delivery = self._delivery(state)
        if delivery is not None:
            await delivery.send_outbound_text(message)

    async def on_system_notification(
        self,
        state: ConnectionState,
        message: SystemNotification,
    ) -> None:
        delivery = self._delivery(state)
        if delivery is not None:
            await delivery.send_system_notification(message)
