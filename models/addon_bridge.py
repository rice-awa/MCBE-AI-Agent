"""Addon 桥接协议模型。"""

from typing import Any

from pydantic import BaseModel


class AddonBridgeChunk(BaseModel):
    """从聊天消息解析出的桥接响应分片。"""

    request_id: str
    chunk_index: int
    total_chunks: int
    content: str


class AddonBridgeResponse(BaseModel):
    """重组后的桥接响应。"""

    request_id: str
    payload: dict[str, Any]


class UiChatChunk(BaseModel):
    """从聊天消息解析出的 UI 聊天分片。"""

    msg_id: str
    chunk_index: int
    total_chunks: int
    content: str


class UiChatMessage(BaseModel):
    """重组后的 UI 聊天消息。"""

    msg_id: str
    player_name: str
    message: str
