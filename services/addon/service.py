"""Addon 桥接服务。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol
from uuid import UUID

from services.addon.protocol import (
    BRIDGE_PREFIX,
    BRIDGE_TOOL_PLAYER_NAME,
    UI_CHAT_PREFIX,
    encode_bridge_request,
)
from services.addon.session import AddonBridgeSession
from config.logging import get_logger

logger = get_logger(__name__)


CommandSender = Callable[[str], Awaitable[str]]
UiChatCallback = Callable[[UUID, str, str], Awaitable[None]]


class AddonBridgeClient(Protocol):
    """面向 Agent 的 addon 桥接客户端接口。"""

    async def request(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        """发起能力请求并返回重组后的 payload。"""


@dataclass
class _ConnectionAddonBridgeClient:
    service: "AddonBridgeService"
    connection_id: UUID
    send_command: CommandSender

    async def request(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.service.request_capability(
            connection_id=self.connection_id,
            capability=capability,
            payload=payload,
            send_command=self.send_command,
        )


class AddonBridgeService:
    """管理 Python 与 addon 之间的请求/响应生命周期。"""

    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self._sessions: dict[UUID, AddonBridgeSession] = {}
        self._timeout_seconds = timeout_seconds
        self._ui_chat_callback: UiChatCallback | None = None

    def create_client(
        self,
        connection_id: UUID,
        send_command: CommandSender,
    ) -> AddonBridgeClient:
        """为指定连接创建 Agent 可用客户端。"""
        return _ConnectionAddonBridgeClient(self, connection_id, send_command)

    async def request_capability(
        self,
        connection_id: UUID,
        capability: str,
        payload: dict[str, Any],
        send_command: CommandSender,
    ) -> dict[str, Any]:
        """发送桥接请求并等待 addon 聊天回传。"""
        session = self._session_for(connection_id)
        request = session.create_request(capability=capability, payload=payload)
        command = encode_bridge_request(
            request_id=request.request_id,
            capability=capability,
            payload=payload,
        )

        command_result = await send_command(command)
        if command_result.startswith("命令执行失败") or command_result.startswith("命令执行超时"):
            session.fail_request(request.request_id, command_result)
            raise RuntimeError(command_result)

        try:
            return await asyncio.wait_for(
                request.future,
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            session.fail_request(request.request_id, "Addon 桥接响应超时")
            raise RuntimeError("Addon 桥接响应超时") from exc

    def is_bridge_chat_message(self, sender: str, message: str) -> bool:
        """判断玩家聊天消息是否为 addon 回传分片。"""
        return sender == BRIDGE_TOOL_PLAYER_NAME and message.startswith(BRIDGE_PREFIX)

    def is_ui_chat_message(self, sender: str, message: str) -> bool:
        """判断玩家聊天消息是否为 UI 聊天分片。"""
        return sender == BRIDGE_TOOL_PLAYER_NAME and message.startswith(UI_CHAT_PREFIX)

    def handle_player_message(self, connection_id: UUID, sender: str, message: str) -> bool:
        """处理来自模拟玩家的回传消息。"""
        if self.is_bridge_chat_message(sender, message):
            session = self._sessions.get(connection_id)
            if session is None:
                logger.warning(
                    "bridge_chat_no_session",
                    connection_id=str(connection_id),
                    sender=sender,
                    message_prefix=message[:50] if message else "",
                )
                return False

            return session.handle_chat_chunk(message)

        if self.is_ui_chat_message(sender, message):
            logger.debug(
                "ui_chat_chunk_received",
                connection_id=str(connection_id),
                sender=sender,
                message_prefix=message[:50] if message else "",
            )
            session = self._session_for(connection_id)

            result = session.handle_ui_chat_chunk(message)
            if result is not None and self._ui_chat_callback is not None:
                player_name, chat_message = result
                logger.info(
                    "ui_chat_reassembled",
                    connection_id=str(connection_id),
                    player=player_name,
                    message_length=len(chat_message),
                    callback_registered=self._ui_chat_callback is not None,
                )
                asyncio.create_task(
                    self._ui_chat_callback(connection_id, player_name, chat_message)
                )
            elif result is None:
                logger.debug(
                    "ui_chat_chunk_buffered",
                    connection_id=str(connection_id),
                    message_prefix=message[:50] if message else "",
                )
            return True

        return False

    def set_ui_chat_callback(self, callback: UiChatCallback) -> None:
        """注册 UI 聊天消息回调。"""
        self._ui_chat_callback = callback

    def close_connection(self, connection_id: UUID) -> None:
        """在连接关闭时清理关联会话。"""
        session = self._sessions.pop(connection_id, None)
        if session is not None:
            session.close("Addon 桥接连接已关闭")

    def _session_for(self, connection_id: UUID) -> AddonBridgeSession:
        session = self._sessions.get(connection_id)
        if session is None:
            session = AddonBridgeSession()
            self._sessions[connection_id] = session
        return session


_addon_bridge_service: AddonBridgeService | None = None


def get_addon_bridge_service() -> AddonBridgeService:
    """获取全局 addon 桥接服务实例。"""
    global _addon_bridge_service
    if _addon_bridge_service is None:
        _addon_bridge_service = AddonBridgeService()
    return _addon_bridge_service

