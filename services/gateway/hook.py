"""Host ConnectionHook: connect/disconnect + command dispatch into CommandHandlers."""

from __future__ import annotations

import asyncio

from mcbe_ws_sdk import FlowControlSettings, McbeOutboundDelivery, NoOpHook
from mcbe_ws_sdk.addon import AddonBridgeService
from mcbe_ws_sdk.command.registry import ParsedCommand
from mcbe_ws_sdk.gateway.connection import ConnectionState
from mcbe_ws_sdk.protocol.minecraft import (
    MinecraftCommandResponse,
    MinecraftErrorFrame,
    PlayerMessageEvent,
)

from config.logging import get_logger
from config.settings import Settings
from core.queue import MessageBroker
from services.agent.prompt import get_prompt_manager
from services.gateway.broker_bridge import BrokerResponseBridge
from services.gateway.command_handlers import CommandHandlers
from services.gateway.session_store import HostSessionStore
from services.gateway.ws_command_runner import WsCommandRunner
from mcbe_ws_sdk import MinecraftProtocolHandler

logger = get_logger(__name__)

_EXTERNAL_SENDERS = frozenset({"外部", "External"})


class HostConnectionHook(NoOpHook):
    """SDK ConnectionHook that wires host sessions, broker, and command handlers."""

    def __init__(
        self,
        *,
        broker: MessageBroker,
        settings: Settings,
        sessions: HostSessionStore,
        bridge: BrokerResponseBridge,
        ws_commands: WsCommandRunner,
        addon: AddonBridgeService,
        handlers: CommandHandlers,
        protocol: MinecraftProtocolHandler,
        flow: FlowControlSettings,
        log_raw: bool = False,
    ) -> None:
        self.broker = broker
        self.settings = settings
        self.sessions = sessions
        self.bridge = bridge
        self.ws_commands = ws_commands
        self.addon = addon
        self.handlers = handlers
        self.protocol = protocol
        self.flow = flow
        self.log_raw = log_raw
        self._background_tasks: set[asyncio.Task[None]] = set()

    def _track(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.add(task)

        def _done(done: asyncio.Task[None]) -> None:
            self._background_tasks.discard(done)
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                logger.error(
                    "host_hook_background_error",
                    error=str(exc),
                    exc_info=exc,
                )

        task.add_done_callback(_done)

    async def on_connected(self, state: ConnectionState) -> None:
        self.sessions.create(
            state.id,
            authenticated=self.settings.dev_mode,
        )
        self.broker.register_connection(state.id)
        await self.bridge.start(state)

        if self.settings.dev_mode:
            logger.info(
                "dev_mode_auto_auth",
                connection_id=str(state.id),
                message="开发模式: 自动认证",
            )

        help_prefix = (
            self.protocol.command_registry.get_command_prefix("help") or "帮助"
        )
        mc = self.settings.minecraft
        welcome_text = mc.welcome_message_template.format(
            version=getattr(self.settings, "version", "")
            or __import__("_version", fromlist=["__version__"]).__version__,
            connection_id=str(state.id)[:8],
            provider=self.settings.default_provider,
            model=self.settings.get_provider_config().model,
            context_status=mc.context_enabled_text,
            help_command=help_prefix,
        )
        welcome = self.protocol.create_info_message(welcome_text)
        if state.send_payload is not None:
            delivery = McbeOutboundDelivery(
                connection_id=state.id,
                send_payload=state.send_payload,
                settings=self.flow,
                log_raw_payloads=self.log_raw,
            )
            await delivery.send_tellraw(
                welcome.text,
                color=welcome.color,
                source="welcome",
                target=welcome.target,
            )

        logger.info("client_connected", connection_id=str(state.id))

    async def on_disconnected(self, state: ConnectionState) -> None:
        await self.bridge.stop(state.id)
        self.ws_commands.close_connection(state.id)
        self.addon.close_connection(state.id)
        self.broker.unregister_connection(state.id)
        self.sessions.remove(state.id)
        try:
            get_prompt_manager().clear_connection(str(state.id))
        except Exception as exc:
            logger.warning(
                "prompt_clear_connection_failed",
                connection_id=str(state.id),
                error=str(exc),
            )
        try:
            from services.agent.runtime import get_agent_runtime

            cleared = get_agent_runtime().pending_approvals.clear_connection(
                str(state.id)
            )
            if cleared:
                logger.info(
                    "pending_approvals_cleared_on_disconnect",
                    connection_id=str(state.id),
                    cleared=cleared,
                )
        except Exception as exc:
            logger.warning(
                "pending_approvals_clear_failed",
                connection_id=str(state.id),
                error=str(exc),
            )
        logger.info("client_disconnected", connection_id=str(state.id))

    async def on_player_message(
        self,
        state: ConnectionState,
        player_event: PlayerMessageEvent,
        parsed: ParsedCommand | None = None,
    ) -> None:
        if player_event.sender in _EXTERNAL_SENDERS:
            return
        task = asyncio.create_task(
            self._dispatch(state, player_event, parsed),
            name=f"host-dispatch:{state.id}",
        )
        self._track(task)

    async def on_ui_chat_reassembled(
        self,
        state: ConnectionState,
        player_name: str,
        message: str,
    ) -> None:
        task = asyncio.create_task(
            self.handlers.handle_ui_chat(state, player_name, message),
            name=f"host-ui-chat:{state.id}",
        )
        self._track(task)

    async def on_command_response(
        self,
        state: ConnectionState,
        response: MinecraftCommandResponse,
    ) -> None:
        self.ws_commands.resolve(state, response)

    async def on_error(self, state: ConnectionState, error: MinecraftErrorFrame) -> None:
        logger.error(
            "mcbe_protocol_error",
            connection_id=str(state.id),
            error=str(error),
        )

    async def _dispatch(
        self,
        state: ConnectionState,
        event: PlayerMessageEvent,
        parsed: ParsedCommand | None,
    ) -> None:
        if parsed is None:
            return

        player_name = event.sender
        if parsed.type == "login":
            await self.handlers.handle_login(
                state, parsed.content, player_name=player_name
            )
            return

        if not self.settings.dev_mode and not await self.handlers.check_auth(state):
            error_msg = self.protocol.create_error_message("请先登录")
            await self.handlers._send_player_reply(
                state, error_msg, source="auth", player_name=player_name
            )
            return

        await self.handlers.handle_command(
            state,
            parsed.type,
            parsed.content,
            player_name=player_name,
        )
