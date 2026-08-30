"""Authenticated Host adapter for SDK MCBEWS/1 inbound DTOs."""

from __future__ import annotations

from mcbe_ws_sdk import MCBEWS_V1, McbewsV1Profile
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.profiles.mcbews_v1.classifier import ToolPlayerMessage
from mcbe_ws_sdk.profiles.mcbews_v1.models import ApprovalDecision, UiChatMessage

from config.logging import get_logger
from services.agent.runtime import get_agent_runtime
from services.gateway.command_handlers import CommandHandlers

logger = get_logger(__name__)


class HostAddonIngressAdapter:
    """Translate SDK-authenticated control messages into Host operations.

    The SDK has already decoded the wire payload and applied the trusted
    ToolPlayer sender gate.  The adapter repeats the cheap sender assertion at
    the Host boundary, then keeps transport ``sender`` out of business owner
    resolution.  Slow session/approval work is awaited by a task created by
    :class:`HostConnectionHook`, never by the SDK receive loop.
    """

    def __init__(
        self,
        handlers: CommandHandlers,
        *,
        profile: McbewsV1Profile = MCBEWS_V1,
    ) -> None:
        self.handlers = handlers
        self.profile = profile

    async def handle_ui_chat(
        self,
        state: ConnectionState,
        message: UiChatMessage,
    ) -> None:
        if not isinstance(message, UiChatMessage):
            logger.warning("host_ui_chat_invalid_dto", connection_id=str(state.id))
            return
        await self.handlers.handle_ui_chat(state, message)

    async def handle_control_message(
        self,
        state: ConnectionState,
        message: ToolPlayerMessage,
    ) -> bool:
        """Handle one SDK typed session/approval message.

        Returns ``True`` when the message was recognized and consumed.  A
        malformed or unsupported DTO is dropped fail-closed and does not fall
        through to ordinary player command handling.
        """

        if not isinstance(message, ToolPlayerMessage):
            return False
        if message.sender != self.profile.trusted_bridge_player_name:
            logger.warning(
                "host_addon_control_untrusted_sender",
                connection_id=str(state.id),
                sender=message.sender,
                channel=message.channel,
            )
            return False

        if message.channel == "session":
            request = message.session_request
            if request is None or not request.player_name.strip():
                logger.warning(
                    "host_session_request_invalid",
                    connection_id=str(state.id),
                    reason="missing_typed_request",
                )
                return True
            await self.handlers.handle_session_request(state, request)
            return True

        if message.channel == "approval":
            decision = message.approval_decision
            if decision is None:
                logger.warning(
                    "host_approval_invalid",
                    connection_id=str(state.id),
                    reason="missing_typed_decision",
                )
                return True
            await self.handle_approval_decision(state, decision)
            return True

        # Bridge/UI frames are consumed by SDK AddonBridgeService and should
        # never reach this callback.
        logger.warning(
            "host_addon_control_unexpected_channel",
            connection_id=str(state.id),
            channel=message.channel,
        )
        return False

    async def handle_approval_decision(
        self,
        state: ConnectionState,
        decision: ApprovalDecision,
    ) -> bool:
        """Resolve an approval owner and delegate the decision to the Host.

        New payloads must claim both owner dimensions and are checked against
        the pending record.  Legacy id-only payloads are accepted only when a
        single pending record with that id exists on this connection.
        """

        runtime = get_agent_runtime()
        store = runtime.get_pending_approval_store(self.handlers.settings)
        connection_id = str(state.id)
        if decision.legacy:
            pending, reason = store.resolve_legacy(
                connection_id=connection_id,
                approval_id=decision.approval_id,
            )
            if pending is None:
                logger.warning(
                    "host_approval_legacy_rejected",
                    connection_id=connection_id,
                    approval_id=decision.approval_id,
                    reason=reason,
                )
                return False
            player_name = pending.player_name
            conversation_id = pending.conversation_id
        else:
            player_name = str(decision.player_name or "").strip()
            conversation_id = str(decision.conversation_id or "").strip()
            if not player_name or not conversation_id:
                return False
            pending, reason = store.get_for_owner(
                connection_id=connection_id,
                player_name=player_name,
                conversation_id=conversation_id,
                approval_id=decision.approval_id,
            )
            if pending is None:
                logger.warning(
                    "host_approval_owner_rejected",
                    connection_id=connection_id,
                    approval_id=decision.approval_id,
                    reason=reason,
                )
                return False

        await self.handlers.handle_tool_approval(
            state,
            decision.approval_id,
            approved=decision.approved,
            player_name=player_name,
            conversation_id=conversation_id,
        )
        return True

    # Compatibility spellings used by early Host adapters.
    async def handle(self, state: ConnectionState, message: ToolPlayerMessage) -> bool:
        return await self.handle_control_message(state, message)

    async def on_addon_control_message(
        self, state: ConnectionState, message: ToolPlayerMessage
    ) -> bool:
        return await self.handle_control_message(state, message)


__all__ = ["HostAddonIngressAdapter"]
