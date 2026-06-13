"""MCBE 出站投递 Adapter。"""

import asyncio
import json
from collections.abc import Callable, Awaitable
from dataclasses import dataclass
from typing import Any

from config.logging import get_logger
from services.websocket.flow_control import FlowControlMiddleware

logger = get_logger(__name__)
ws_raw_logger = get_logger("websocket.raw")
PayloadSender = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class AiResponseSync:
    player_name: str
    role: str
    text: str


class McbeOutboundDelivery:
    """统一执行 MCBE commandRequest 分片、节流、发送和 raw 日志。"""

    def __init__(
        self,
        *,
        connection_id: Any,
        send_payload: PayloadSender,
    ) -> None:
        self.connection_id = connection_id
        self._send_payload = send_payload

    async def send_payload(self, payload: str, source: str) -> None:
        """发送已构造好的 payload，用于订阅、初始化等非长文本路径。"""
        await self._send_one(payload, source)

    async def send_tellraw(self, message: str, color: str, source: str) -> int:
        """发送 tellraw 文本并返回实际 payload 数量。"""
        payloads = FlowControlMiddleware.chunk_tellraw(message, color=color)
        await self._send_chunked(payloads, "tellraw", source)
        return len(payloads)

    async def send_scriptevent(
        self,
        content: str,
        message_id: str = "server:data",
        source: str = "scriptevent",
    ) -> int:
        """发送 scriptevent 文本并返回实际 payload 数量。"""
        payloads = FlowControlMiddleware.chunk_scriptevent(content, message_id)
        await self._send_chunked(payloads, "scriptevent", source)
        return len(payloads)

    async def send_ai_response(self, response: AiResponseSync, source: str = "ai_response_sync") -> int:
        """发送 Addon AI 响应同步分片。"""
        if response.role == "assistant":
            await asyncio.sleep(FlowControlMiddleware.chunk_delay_for("ai_resp_prelude"))
        payloads = FlowControlMiddleware.chunk_ai_response(
            response.player_name,
            response.role,
            response.text,
        )
        await self._send_chunked(payloads, "ai_resp", source)
        return len(payloads)

    async def send_raw_command(
        self,
        command: str,
        source: str = "raw_command",
        before_send: Callable[[str], None] | None = None,
    ) -> str:
        """发送不可语义分片的原始命令，超长时由 FlowControlMiddleware 拒绝。"""
        payload = FlowControlMiddleware.chunk_raw_command(command)[0]
        request_id = _request_id_from_payload(payload)
        if before_send is not None:
            before_send(request_id)
        await self._send_one(payload, source)
        return request_id

    async def _send_chunked(self, payloads: list[str], delay_kind: str, source: str) -> None:
        chunk_delay = FlowControlMiddleware.chunk_delay_for(delay_kind)
        for idx, payload in enumerate(payloads):
            await self._send_one(
                payload,
                source=f"{source}_chunked" if len(payloads) > 1 else source,
            )
            if len(payloads) > 1 and idx < len(payloads) - 1:
                await asyncio.sleep(chunk_delay)

    async def _send_one(self, payload: str, source: str) -> None:
        await self._send_payload(payload)
        self._log_ws_send(payload, source)

    def _log_ws_send(self, payload: str, source: str) -> None:
        command_line_bytes = 0
        try:
            data = json.loads(payload)
            command_line_bytes = len(
                data.get("body", {}).get("commandLine", "").encode("utf-8")
            )
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

        ws_raw_logger.info(
            "websocket_response_sent",
            connection_id=str(self.connection_id),
            source=source,
            payload=payload,
            command_line_bytes=command_line_bytes,
        )


def _request_id_from_payload(payload: str) -> str:
    data = json.loads(payload)
    return data["header"]["requestId"]
