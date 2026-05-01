"""Addon 桥接协议编解码函数。"""

import json
from json import JSONDecodeError

from models.addon_bridge import AddonBridgeChunk, AddonBridgeResponse, UiChatChunk, UiChatMessage

BRIDGE_MESSAGE_ID = "mcbeai:bridge_request"
BRIDGE_PREFIX = "MCBEAI|RESP"
UI_CHAT_PREFIX = "MCBEAI|UI_CHAT"
BRIDGE_TOOL_PLAYER_NAME = "MCBEAI_TOOL"

AI_RESP_MESSAGE_ID = "mcbeai:ai_resp"


def _default_ai_resp_chunk_length() -> int:
    """单分片字符上限默认值，按需读取 FlowControlMiddleware 的运行时配置。

    使用函数而非模块常量，避免在 import 期固化值，让 .env 注入的
    MAX_CHUNK_CONTENT_LENGTH 在 configure() 之后真正生效。
    """
    from services.websocket.flow_control import FlowControlMiddleware

    return FlowControlMiddleware.DEFAULT_MAX_CONTENT_LENGTH


# 向后兼容：保留旧名，但仅作为 import 期快照，避免新代码引用此常量
AI_RESP_MAX_CHUNK_LENGTH = 400


def encode_bridge_request(request_id: str, capability: str, payload: dict) -> str:
    """编码桥接请求为 scriptevent 命令字符串。"""
    body = {
        "request_id": request_id,
        "capability": capability,
        "payload": payload,
    }
    return f"scriptevent {BRIDGE_MESSAGE_ID} {json.dumps(body, ensure_ascii=False)}"


def decode_bridge_chat_chunk(chunk: str) -> AddonBridgeChunk:
    """解析聊天中的桥接响应分片。"""
    parts = chunk.split("|", 4)
    if len(parts) != 5:
        raise ValueError("Invalid bridge chunk format")

    namespace, prefix, request_id, part, content = parts
    if namespace != "MCBEAI":
        raise ValueError("Invalid bridge chunk namespace")
    if prefix != "RESP":
        raise ValueError("Invalid bridge chunk prefix")

    if not request_id:
        raise ValueError("Invalid bridge chunk metadata")

    try:
        index_str, total_str = part.split("/", 1)
        chunk_index = int(index_str)
        total_chunks = int(total_str)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid bridge chunk metadata") from exc

    if chunk_index <= 0 or total_chunks <= 0 or chunk_index > total_chunks:
        raise ValueError("Invalid bridge chunk metadata")

    return AddonBridgeChunk(
        request_id=request_id,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
        content=content,
    )


def reassemble_bridge_chunks(chunks: list[AddonBridgeChunk]) -> AddonBridgeResponse:
    """重组分片并解析 JSON payload。"""
    if not chunks:
        raise ValueError("Bridge chunks must not be empty")

    sorted_chunks = sorted(chunks, key=lambda item: item.chunk_index)
    request_id = sorted_chunks[0].request_id
    total_chunks = sorted_chunks[0].total_chunks

    if any(chunk.request_id != request_id for chunk in sorted_chunks):
        raise ValueError("Bridge chunks request_id mismatch")
    if any(chunk.total_chunks != total_chunks for chunk in sorted_chunks):
        raise ValueError("Bridge chunks total_chunks mismatch")

    expected_indexes = list(range(1, total_chunks + 1))
    actual_indexes = [chunk.chunk_index for chunk in sorted_chunks]
    if actual_indexes != expected_indexes:
        raise ValueError("Bridge chunks are incomplete or out of sequence")

    content = "".join(chunk.content for chunk in sorted_chunks)
    try:
        payload = json.loads(content)
    except JSONDecodeError as exc:
        raise ValueError("Invalid bridge payload JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid bridge payload JSON")

    return AddonBridgeResponse(request_id=request_id, payload=payload)


def encode_ai_response_chunks(
    player_name: str,
    role: str,
    text: str,
    max_chunk_length: int | None = None,
) -> list[str]:
    """将 AI 响应编码为 scriptevent 分片命令列表。

    委托给 FlowControlMiddleware 统一处理，保持向后兼容。
    每个分片格式: scriptevent mcbeai:ai_resp {JSON}
    JSON 载荷: {"id":"...","i":1,"n":3,"p":"Steve","r":"assistant","c":"..."}

    max_chunk_length 为 None 或 ≤ 0 时使用 FlowControlMiddleware 的运行时默认。
    """
    from services.websocket.flow_control import FlowControlMiddleware

    return FlowControlMiddleware.chunk_ai_response(
        player_name, role, text, max_length=max_chunk_length
    )


def decode_ui_chat_chunk(chunk: str) -> UiChatChunk:
    """解析聊天中的 UI 聊天分片。"""
    parts = chunk.split("|", 4)
    if len(parts) != 5:
        raise ValueError("Invalid UI chat chunk format")

    namespace, prefix, msg_id, part, content = parts
    if namespace != "MCBEAI":
        raise ValueError("Invalid UI chat chunk namespace")
    if prefix != "UI_CHAT":
        raise ValueError("Invalid UI chat chunk prefix")

    if not msg_id:
        raise ValueError("Invalid UI chat chunk metadata")

    try:
        index_str, total_str = part.split("/", 1)
        chunk_index = int(index_str)
        total_chunks = int(total_str)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid UI chat chunk metadata") from exc

    if chunk_index <= 0 or total_chunks <= 0 or chunk_index > total_chunks:
        raise ValueError("Invalid UI chat chunk metadata")

    return UiChatChunk(
        msg_id=msg_id,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
        content=content,
    )


def reassemble_ui_chat_chunks(chunks: list[UiChatChunk]) -> UiChatMessage:
    """重组 UI 聊天分片并解析 JSON payload。"""
    if not chunks:
        raise ValueError("UI chat chunks must not be empty")

    sorted_chunks = sorted(chunks, key=lambda item: item.chunk_index)
    msg_id = sorted_chunks[0].msg_id
    total_chunks = sorted_chunks[0].total_chunks

    if any(chunk.msg_id != msg_id for chunk in sorted_chunks):
        raise ValueError("UI chat chunks msg_id mismatch")
    if any(chunk.total_chunks != total_chunks for chunk in sorted_chunks):
        raise ValueError("UI chat chunks total_chunks mismatch")

    expected_indexes = list(range(1, total_chunks + 1))
    actual_indexes = [chunk.chunk_index for chunk in sorted_chunks]
    if actual_indexes != expected_indexes:
        raise ValueError("UI chat chunks are incomplete or out of sequence")

    content = "".join(chunk.content for chunk in sorted_chunks)
    try:
        payload = json.loads(content)
    except JSONDecodeError as exc:
        raise ValueError("Invalid UI chat payload JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid UI chat payload JSON")

    player_name = payload.get("player", "")
    message = payload.get("message", "")
    if not message:
        raise ValueError("Invalid UI chat payload: missing message")

    return UiChatMessage(msg_id=msg_id, player_name=player_name, message=message)
