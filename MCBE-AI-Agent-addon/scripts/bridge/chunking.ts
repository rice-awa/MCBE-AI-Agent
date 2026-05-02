import { BRIDGE_RESPONSE_PREFIX, BRIDGE_UI_CHAT_PREFIX } from "./constants";

export function formatChunk(
  prefix: string,
  id: string,
  index: number,
  total: number,
  content: string,
): string {
  return `${prefix}|${id}|${index}/${total}|${content}`;
}

export function formatResponseChunk(
  requestId: string,
  index: number,
  total: number,
  content: string,
): string {
  return formatChunk(BRIDGE_RESPONSE_PREFIX, requestId, index, total, content);
}

export function chunkPayload(
  prefix: string,
  id: string,
  payload: string,
  maxChunkContentLength: number,
): string[] {
  if (maxChunkContentLength <= 0) {
    throw new Error("maxChunkContentLength must be greater than 0");
  }

  const parts: string[] = [];
  for (let i = 0; i < payload.length; i += maxChunkContentLength) {
    parts.push(payload.slice(i, i + maxChunkContentLength));
  }

  const total = parts.length === 0 ? 1 : parts.length;
  const safeParts = parts.length === 0 ? [""] : parts;
  return safeParts.map((content, idx) => formatChunk(prefix, id, idx + 1, total, content));
}

export function chunkBridgePayload(
  requestId: string,
  payload: string,
  maxChunkContentLength: number,
): string[] {
  return chunkPayload(BRIDGE_RESPONSE_PREFIX, requestId, payload, maxChunkContentLength);
}

export function chunkUiChatPayload(
  id: string,
  payload: string,
  maxChunkContentLength: number,
): string[] {
  return chunkPayload(BRIDGE_UI_CHAT_PREFIX, id, payload, maxChunkContentLength);
}
