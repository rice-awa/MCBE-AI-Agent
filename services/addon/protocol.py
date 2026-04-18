"""Addon 桥接协议编解码函数。"""

import json

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
    prefix, request_id, part, content = chunk.split("|", 4)[1:]
    if prefix != "RESP":
        raise ValueError("Invalid bridge chunk prefix")
    index_str, total_str = part.split("/", 1)
    return AddonBridgeChunk(
        request_id=request_id,
        chunk_index=int(index_str),
        total_chunks=int(total_str),
        content=content,
    )


def reassemble_bridge_chunks(chunks: list[AddonBridgeChunk]) -> AddonBridgeResponse:
    """重组分片并解析 JSON payload。"""
    if not chunks:
        raise ValueError("Bridge chunks must not be empty")
    sorted_chunks = sorted(chunks, key=lambda item: item.chunk_index)
    request_id = sorted_chunks[0].request_id
    content = "".join(chunk.content for chunk in sorted_chunks)
    payload = json.loads(content)
    return AddonBridgeResponse(request_id=request_id, payload=payload)
