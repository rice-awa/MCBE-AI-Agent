"""Addon 桥接协议编解码函数。"""

import json
from json import JSONDecodeError

from models.addon_bridge import AddonBridgeChunk, AddonBridgeResponse

BRIDGE_MESSAGE_ID = "mcbeai:bridge_request"
BRIDGE_PREFIX = "MCBEAI|RESP"


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

    return AddonBridgeResponse(request_id=request_id, payload=payload)
