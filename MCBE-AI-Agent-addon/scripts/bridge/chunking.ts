import {
  CAPABILITY_RESPONSE_CHAT_PREFIX,
  COMMAND_LINE_BYTE_BUDGET,
  UI_CHAT_CHUNK_PREFIX,
  UPSTREAM_MAX_CONTENT_CODE_POINTS,
} from "./protocol";

export type ChunkOptions = {
  commandLineByteBudget?: number;
  maxContentCodePoints?: number;
  wrapCommandLine?: (chunk: string) => string;
};

/** Count UTF-8 bytes without relying on TextEncoder (unavailable in Bedrock). */
export function utf8ByteLength(value: string): number {
  let byteLength = 0;
  for (const symbol of value) {
    const codePoint = symbol.codePointAt(0) ?? 0;
    if (codePoint <= 0x7f) byteLength += 1;
    else if (codePoint <= 0x7ff) byteLength += 2;
    else if (codePoint <= 0xffff) byteLength += 3;
    else byteLength += 4;
  }
  return byteLength;
}

export function formatChunk(prefix: string, id: string, index: number, total: number, content: string): string {
  return `${prefix}|${id}|${index}/${total}|${content}`;
}

export function formatResponseChunk(requestId: string, index: number, total: number, content: string): string {
  return formatChunk(CAPABILITY_RESPONSE_CHAT_PREFIX, requestId, index, total, content);
}

function normalizeOptions(
  optionsOrLegacy: ChunkOptions | number | undefined
): Required<Pick<ChunkOptions, "commandLineByteBudget" | "maxContentCodePoints" | "wrapCommandLine">> {
  if (typeof optionsOrLegacy === "number") {
    if (!Number.isFinite(optionsOrLegacy) || optionsOrLegacy <= 0) {
      throw new Error("maxChunkContentLength must be greater than 0");
    }
    return {
      commandLineByteBudget: COMMAND_LINE_BYTE_BUDGET,
      maxContentCodePoints: optionsOrLegacy,
      wrapCommandLine: (chunk: string) => `tell @s ${chunk}`,
    };
  }
  const commandLineByteBudget = optionsOrLegacy?.commandLineByteBudget ?? COMMAND_LINE_BYTE_BUDGET;
  const maxContentCodePoints = optionsOrLegacy?.maxContentCodePoints ?? UPSTREAM_MAX_CONTENT_CODE_POINTS;
  if (!Number.isFinite(commandLineByteBudget) || commandLineByteBudget <= 0) {
    throw new Error("commandLineByteBudget must be greater than 0");
  }
  if (!Number.isFinite(maxContentCodePoints) || maxContentCodePoints <= 0) {
    throw new Error("maxContentCodePoints must be greater than 0");
  }
  const normalizedMaxContentCodePoints = Math.floor(maxContentCodePoints);
  if (normalizedMaxContentCodePoints < 1) {
    throw new Error("maxContentCodePoints must be at least 1");
  }
  return {
    commandLineByteBudget,
    maxContentCodePoints: normalizedMaxContentCodePoints,
    wrapCommandLine: optionsOrLegacy?.wrapCommandLine ?? ((chunk: string) => `tell @s ${chunk}`),
  };
}

/**
 * Split by Unicode code point and complete command-line byte budget.  The
 * index/total metadata is included in the probe and the fixpoint loop repeats
 * until the total digit width no longer changes the split.
 */
export function chunkPayload(
  prefix: string,
  id: string,
  payload: string,
  optionsOrLegacy?: ChunkOptions | number
): string[] {
  const options = normalizeOptions(optionsOrLegacy);
  const symbols = Array.from(payload);

  const split = (totalHint: number): string[] => {
    const parts: string[] = [""];
    if (symbols.length === 0) {
      const emptyFrame = formatChunk(prefix, id, 1, totalHint, "");
      if (utf8ByteLength(options.wrapCommandLine(emptyFrame)) > options.commandLineByteBudget) {
        throw new Error("chunk framing leaves no room for one Unicode code point");
      }
      return parts;
    }
    for (const symbol of symbols) {
      const index = parts.length;
      const candidate = parts[index - 1] + symbol;
      const candidateFrame = formatChunk(prefix, id, index, totalHint, candidate);
      if (
        Array.from(candidate).length <= options.maxContentCodePoints &&
        utf8ByteLength(options.wrapCommandLine(candidateFrame)) <= options.commandLineByteBudget
      ) {
        parts[index - 1] = candidate;
        continue;
      }

      const nextFrame = formatChunk(prefix, id, index + 1, totalHint, symbol);
      if (utf8ByteLength(options.wrapCommandLine(nextFrame)) > options.commandLineByteBudget) {
        throw new Error("chunk framing leaves no room for one Unicode code point");
      }
      parts.push(symbol);
    }
    return parts;
  };

  let totalHint = 1;
  for (let iteration = 0; iteration < 32; iteration += 1) {
    const parts = split(totalHint);
    if (parts.length === totalHint) {
      return parts.map((content, index) => formatChunk(prefix, id, index + 1, parts.length, content));
    }
    totalHint = parts.length;
  }
  throw new Error("chunk framing metadata failed to converge");
}

export function chunkBridgePayload(
  requestId: string,
  payload: string,
  optionsOrLegacy?: ChunkOptions | number
): string[] {
  return chunkPayload(CAPABILITY_RESPONSE_CHAT_PREFIX, requestId, payload, optionsOrLegacy);
}

export function chunkUiChatPayload(id: string, payload: string, optionsOrLegacy?: ChunkOptions | number): string[] {
  return chunkPayload(UI_CHAT_CHUNK_PREFIX, id, payload, optionsOrLegacy);
}
