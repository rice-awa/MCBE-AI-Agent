import { BRIDGE_RESPONSE_PREFIX } from "./constants";
export function formatResponseChunk(requestId, index, total, content) {
    return `${BRIDGE_RESPONSE_PREFIX}|${requestId}|${index}/${total}|${content}`;
}
export function chunkBridgePayload(requestId, payload, maxChunkContentLength) {
    if (maxChunkContentLength <= 0) {
        throw new Error("maxChunkContentLength must be greater than 0");
    }
    const parts = [];
    for (let i = 0; i < payload.length; i += maxChunkContentLength) {
        parts.push(payload.slice(i, i + maxChunkContentLength));
    }
    const total = parts.length === 0 ? 1 : parts.length;
    const safeParts = parts.length === 0 ? [""] : parts;
    return safeParts.map((content, idx) => formatResponseChunk(requestId, idx + 1, total, content));
}
//# sourceMappingURL=chunking.js.map