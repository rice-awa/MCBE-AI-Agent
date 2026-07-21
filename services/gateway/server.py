"""Host gateway server: wires SDK McbeServerFacade with host brokers and hooks."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from mcbe_ws_sdk import AddonBridgeService, McbeServerFacade

from config.logging import get_logger
from config.settings import Settings
from core.queue import MessageBroker
from services.auth.jwt_handler import JWTHandler
from services.gateway.broker_bridge import BrokerResponseBridge
from services.gateway.command_handlers import CommandHandlers
from services.gateway.hook import HostConnectionHook
from services.gateway.session_store import HostSessionStore
from services.gateway.settings_map import build_command_registry, build_gateway_settings
from services.gateway.sink import HostResponseSink
from services.gateway.ws_command_runner import WsCommandRunner
from services.websocket.minecraft import MinecraftProtocolHandler

logger = get_logger(__name__)


class HostGatewayServer:
    """Host-owned facade over ``McbeServerFacade`` with the same start/stop surface
    as the legacy ``WebSocketServer``.
    """

    def __init__(
        self,
        broker: MessageBroker,
        settings: Settings,
        jwt_handler: JWTHandler,
    ) -> None:
        self._broker = broker
        self._settings = settings
        self._jwt = jwt_handler

        self._gateway_settings = build_gateway_settings(settings)
        self._addon = AddonBridgeService(self._gateway_settings.addon)
        self._ws_commands = WsCommandRunner(
            self._gateway_settings.flow,
            timeout=settings.run_command_timeout,
        )
        self._sessions = HostSessionStore()
        self._bridge = BrokerResponseBridge(
            broker,
            self._gateway_settings.flow,
            self._ws_commands,
            profile=self._gateway_settings.addon.profile,
            log_raw=settings.enable_ws_raw_log,
        )
        self._protocol = MinecraftProtocolHandler(settings.minecraft)
        self._handlers = CommandHandlers(
            broker,
            settings,
            jwt_handler,
            self._sessions,
            self._ws_commands,
            self._addon,
            self._protocol,
            self._gateway_settings.flow,
            log_raw=settings.enable_ws_raw_log,
        )
        self._hook = HostConnectionHook(
            broker=broker,
            settings=settings,
            sessions=self._sessions,
            bridge=self._bridge,
            ws_commands=self._ws_commands,
            addon=self._addon,
            handlers=self._handlers,
            protocol=self._protocol,
            flow=self._gateway_settings.flow,
            log_raw=settings.enable_ws_raw_log,
        )
        self._sink = HostResponseSink(
            self._gateway_settings.flow,
            log_raw_payloads=settings.enable_ws_raw_log,
        )
        self._registry = build_command_registry(settings)
        self._facade = McbeServerFacade(
            settings=self._gateway_settings,
            hook=self._hook,
            sink=self._sink,
            addon=self._addon,
            registry=self._registry,
        )
        self._task: asyncio.Task[None] | None = None

    @property
    def addon(self) -> AddonBridgeService:
        return self._addon

    @property
    def sessions(self) -> HostSessionStore:
        return self._sessions

    @property
    def facade(self) -> McbeServerFacade:
        return self._facade

    async def start(self) -> None:
        if self._settings.dev_mode:
            logger.warning(
                "dev_mode_enabled",
                message="开发模式已启用 - 身份验证已跳过，仅用于本地开发调试！",
            )
        if self._task is not None and not self._task.done():
            logger.warning("host_gateway_already_running")
            return
        self._task = asyncio.create_task(
            self._facade.run_lifetime(),
            name="mcbe-facade",
        )
        logger.info(
            "host_gateway_started",
            host=self._settings.host,
            port=self._settings.port,
            dev_mode=self._settings.dev_mode,
        )

    async def stop(self) -> None:
        logger.info("stopping_host_gateway")
        await self._facade.stop()
        if self._task is not None:
            if not self._task.done():
                self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        # Best-effort cleanup of any lingering sessions if stop raced connections.
        for session in list(self._sessions._sessions.values()):
            cid = session.connection_id
            with suppress(Exception):
                await self._bridge.stop(cid)
            with suppress(Exception):
                self._ws_commands.close_connection(cid)
            with suppress(Exception):
                self._addon.close_connection(cid)
            with suppress(Exception):
                self._broker.unregister_connection(cid)
            self._sessions.remove(cid)

        logger.info("host_gateway_stopped")
