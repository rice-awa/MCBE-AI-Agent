"""Addon 桥接会话管理。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from models.addon_bridge import AddonBridgeChunk, UiChatChunk
from services.addon.protocol import (
    decode_bridge_chat_chunk,
    decode_ui_chat_chunk,
    reassemble_bridge_chunks,
    reassemble_ui_chat_chunks,
)


@dataclass
class PendingAddonRequest:
    """单个桥接请求的挂起状态。"""

    request_id: str
    capability: str
    payload: dict[str, Any]
    future: asyncio.Future[dict[str, Any]]


class AddonBridgeSession:
    """按连接维度维护桥接请求与分片缓冲。"""

    def __init__(self) -> None:
        self._pending_requests: dict[str, PendingAddonRequest] = {}
        self._chunk_buffers: dict[str, dict[int, AddonBridgeChunk]] = {}
        self._ui_chat_chunk_buffers: dict[str, dict[int, UiChatChunk]] = {}

    def create_request(
        self,
        capability: str,
        payload: dict[str, Any],
    ) -> PendingAddonRequest:
        """创建挂起请求并返回。"""
        loop = asyncio.get_running_loop()
        request_id = f"addon-{uuid4().hex}"
        request = PendingAddonRequest(
            request_id=request_id,
            capability=capability,
            payload=payload,
            future=loop.create_future(),
        )
        self._pending_requests[request_id] = request
        return request

    def handle_chat_chunk(self, chunk_message: str) -> bool:
        """消费单条聊天分片，若完成重组则结束对应 future。"""
        chunk = decode_bridge_chat_chunk(chunk_message)
        if chunk.request_id not in self._pending_requests:
            return False

        buffer = self._chunk_buffers.setdefault(chunk.request_id, {})
        buffer[chunk.chunk_index] = chunk

        if len(buffer) < chunk.total_chunks:
            return True

        try:
            response = reassemble_bridge_chunks(list(buffer.values()))
        except ValueError:
            self._chunk_buffers.pop(chunk.request_id, None)
            return True

        request = self._pending_requests.pop(chunk.request_id)
        self._chunk_buffers.pop(chunk.request_id, None)
        if not request.future.done():
            request.future.set_result(response.payload)
        return True

    def fail_request(self, request_id: str, reason: str) -> None:
        """结束指定请求。"""
        request = self._pending_requests.pop(request_id, None)
        self._chunk_buffers.pop(request_id, None)
        if request and not request.future.done():
            request.future.set_exception(RuntimeError(reason))

    def close(self, reason: str) -> None:
        """关闭会话并失败所有挂起请求。"""
        pending_ids = list(self._pending_requests)
        for request_id in pending_ids:
            self.fail_request(request_id, reason)
        self._ui_chat_chunk_buffers.clear()

    def handle_ui_chat_chunk(self, chunk_message: str) -> tuple[str, str] | None:
        """消费单条 UI 聊天分片，若完成重组则返回 (player_name, message)。"""
        chunk = decode_ui_chat_chunk(chunk_message)

        buffer = self._ui_chat_chunk_buffers.setdefault(chunk.msg_id, {})
        buffer[chunk.chunk_index] = chunk

        if len(buffer) < chunk.total_chunks:
            return None

        try:
            ui_msg = reassemble_ui_chat_chunks(list(buffer.values()))
        except ValueError:
            self._ui_chat_chunk_buffers.pop(chunk.msg_id, None)
            return None
        finally:
            self._ui_chat_chunk_buffers.pop(chunk.msg_id, None)

        return (ui_msg.player_name, ui_msg.message)
