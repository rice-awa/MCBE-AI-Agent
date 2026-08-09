import {
  RESPONSE_BUFFER_TTL_MS,
  RESPONSE_MAX_BUFFERS,
  RESPONSE_MAX_CHUNKS_PER_MESSAGE,
  RESPONSE_MAX_MESSAGE_BYTES,
  RESPONSE_MAX_TOTAL_BUFFER_BYTES,
  TEXT_RESPONSE_ALLOWED_ROLES,
} from "./protocol";
import { utf8ByteLength } from "./chunking";

export type TextResponseRole = (typeof TEXT_RESPONSE_ALLOWED_ROLES)[number];

export type TokenUsage = {
  i: number;
  o: number;
};

export type TextResponseChunk = {
  id: string;
  i: number;
  n: number;
  p: string;
  r: string;
  c: string;
  cid?: string;
  t?: string;
  u?: TokenUsage;
};

export type TextResponseMessage = {
  responseId: string;
  playerName: string;
  role: TextResponseRole;
  content: string;
  conversationId: string;
  title?: string;
  usage?: TokenUsage;
};

export type TextResponsePreview = {
  responseId: string;
  playerName: string;
  role: TextResponseRole;
  conversationId: string;
  title?: string;
  content: string;
};

export type ResponseAssemblerLimits = {
  ttlMs: number;
  maxBuffers: number;
  maxChunksPerMessage: number;
  maxMessageBytes: number;
  maxTotalBufferBytes: number;
};

export const DEFAULT_RESPONSE_ASSEMBLER_LIMITS: ResponseAssemblerLimits = {
  ttlMs: RESPONSE_BUFFER_TTL_MS,
  maxBuffers: RESPONSE_MAX_BUFFERS,
  maxChunksPerMessage: RESPONSE_MAX_CHUNKS_PER_MESSAGE,
  maxMessageBytes: RESPONSE_MAX_MESSAGE_BYTES,
  maxTotalBufferBytes: RESPONSE_MAX_TOTAL_BUFFER_BYTES,
};

type BufferState = {
  lastUpdatedAt: number;
  total: number;
  playerName: string;
  role: TextResponseRole;
  conversationId: string;
  title?: string;
  usage?: TokenUsage;
  byteLength: number;
  chunks: Map<number, StoredChunk>;
};

type StoredChunk = {
  content: string;
  usage?: TokenUsage;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function normalizeConversationId(value: string | undefined): string {
  return value?.trim() || "default";
}

function parseUsage(value: unknown): TokenUsage | undefined | null {
  if (value === undefined) return undefined;
  if (!isRecord(value)) return null;
  const input = value.i ?? value.input_tokens;
  const output = value.o ?? value.output_tokens;
  if (
    typeof input !== "number" ||
    typeof output !== "number" ||
    !Number.isInteger(input) ||
    !Number.isInteger(output) ||
    input < 0 ||
    output < 0
  ) {
    return null;
  }
  return { i: input, o: output };
}

/** Validate a raw JSON value at the text response boundary. */
export function parseTextResponseChunk(value: unknown): TextResponseChunk | null {
  if (!isRecord(value)) return null;
  if (
    typeof value.id !== "string" ||
    typeof value.i !== "number" ||
    typeof value.n !== "number" ||
    typeof value.p !== "string" ||
    typeof value.r !== "string" ||
    typeof value.c !== "string" ||
    !value.id.trim() ||
    !value.p.trim() ||
    !Number.isInteger(value.i) ||
    !Number.isInteger(value.n) ||
    value.i < 1 ||
    value.i > value.n ||
    value.n > RESPONSE_MAX_CHUNKS_PER_MESSAGE ||
    !TEXT_RESPONSE_ALLOWED_ROLES.includes(value.r as TextResponseRole)
  ) {
    return null;
  }
  if (value.cid !== undefined && (typeof value.cid !== "string" || !value.cid.trim())) return null;
  if (value.t !== undefined && typeof value.t !== "string") return null;
  const usage = parseUsage(value.u);
  if (usage === null || (usage !== undefined && value.i !== value.n)) return null;
  return {
    id: value.id,
    i: value.i,
    n: value.n,
    p: value.p,
    r: value.r,
    c: value.c,
    ...(value.cid === undefined ? {} : { cid: value.cid }),
    ...(value.t === undefined ? {} : { t: value.t }),
    ...(usage === undefined ? {} : { u: usage }),
  };
}

function sameUsage(left: TokenUsage | undefined, right: TokenUsage | undefined): boolean {
  return left?.i === right?.i && left?.o === right?.o;
}

function isValidUsage(value: unknown): value is TokenUsage {
  return (
    isRecord(value) &&
    Number.isInteger(value.i) &&
    Number.isInteger(value.o) &&
    (value.i as number) >= 0 &&
    (value.o as number) >= 0
  );
}

export function textResponseStreamKey(
  playerName: string,
  conversationId: string | undefined,
  responseId: string
): string {
  return `${playerName}\u0000${normalizeConversationId(conversationId)}\u0000${responseId}`;
}

/** Pure bounded raw text response reassembly. */
export class BoundedTextResponseAssembler {
  private readonly buffers = new Map<string, BufferState>();
  private totalBufferedBytes = 0;

  constructor(
    private readonly limits: ResponseAssemblerLimits = DEFAULT_RESPONSE_ASSEMBLER_LIMITS,
    private readonly now: () => number = Date.now
  ) {}

  get bufferCount(): number {
    return this.buffers.size;
  }

  get bufferedBytes(): number {
    return this.totalBufferedBytes;
  }

  clear(): void {
    this.buffers.clear();
    this.totalBufferedBytes = 0;
  }

  clearForPlayer(playerName: string): void {
    for (const [key, state] of this.buffers) {
      if (state.playerName === playerName) this.drop(key);
    }
  }

  clearForStream(playerName: string, conversationId: string | undefined, responseId: string): void {
    this.drop(textResponseStreamKey(playerName, conversationId, responseId));
  }

  pruneExpired(): void {
    const cutoff = this.now();
    for (const [key, state] of this.buffers) {
      if (cutoff - state.lastUpdatedAt >= this.limits.ttlMs) this.drop(key);
    }
  }

  /** Push one validated frame; null means pending or rejected. */
  push(chunk: TextResponseChunk): TextResponseMessage | null {
    this.pruneExpired();
    if (
      typeof chunk.id !== "string" ||
      typeof chunk.p !== "string" ||
      typeof chunk.r !== "string" ||
      typeof chunk.c !== "string" ||
      !chunk.id.trim() ||
      !chunk.p.trim() ||
      !TEXT_RESPONSE_ALLOWED_ROLES.includes(chunk.r as TextResponseRole) ||
      !Number.isInteger(chunk.i) ||
      !Number.isInteger(chunk.n) ||
      chunk.i < 1 ||
      chunk.i > chunk.n ||
      chunk.n > this.limits.maxChunksPerMessage ||
      (chunk.u !== undefined && (!isValidUsage(chunk.u) || chunk.i !== chunk.n)) ||
      (chunk.cid !== undefined && (typeof chunk.cid !== "string" || !chunk.cid.trim())) ||
      (chunk.t !== undefined && typeof chunk.t !== "string")
    ) {
      return null;
    }

    const conversationId = normalizeConversationId(chunk.cid);
    const key = textResponseStreamKey(chunk.p, conversationId, chunk.id);
    let state = this.buffers.get(key);
    if (!state) {
      if (this.buffers.size >= this.limits.maxBuffers) return null;
      state = {
        lastUpdatedAt: this.now(),
        total: chunk.n,
        playerName: chunk.p,
        role: chunk.r as TextResponseRole,
        conversationId,
        title: chunk.t,
        usage: chunk.u,
        byteLength: 0,
        chunks: new Map<number, StoredChunk>(),
      };
      this.buffers.set(key, state);
    } else if (
      state.total !== chunk.n ||
      state.playerName !== chunk.p ||
      state.role !== chunk.r ||
      state.conversationId !== conversationId ||
      state.title !== chunk.t ||
      (chunk.i === chunk.n && state.usage !== undefined && !sameUsage(state.usage, chunk.u))
    ) {
      this.drop(key);
      return null;
    }

    const existing = state.chunks.get(chunk.i);
    if (existing !== undefined) {
      if (existing.content !== chunk.c || !sameUsage(existing.usage, chunk.u)) this.drop(key);
      return null;
    }

    const chunkBytes = utf8ByteLength(chunk.c);
    const nextBytes = state.byteLength + chunkBytes;
    if (
      nextBytes > this.limits.maxMessageBytes ||
      this.totalBufferedBytes + chunkBytes > this.limits.maxTotalBufferBytes
    ) {
      this.drop(key);
      return null;
    }
    state.chunks.set(chunk.i, {
      content: chunk.c,
      ...(chunk.u === undefined ? {} : { usage: chunk.u }),
    });
    state.byteLength = nextBytes;
    if (chunk.u !== undefined) state.usage = chunk.u;
    state.lastUpdatedAt = this.now();
    this.totalBufferedBytes += chunkBytes;

    if (state.chunks.size !== state.total) return null;
    const ordered: string[] = [];
    for (let index = 1; index <= state.total; index += 1) {
      const content = state.chunks.get(index);
      if (content === undefined) return null;
      ordered.push(content.content);
    }
    const result: TextResponseMessage = {
      responseId: chunk.id,
      playerName: state.playerName,
      role: state.role,
      content: ordered.join(""),
      conversationId: state.conversationId,
      ...(state.title === undefined ? {} : { title: state.title }),
      ...(state.usage === undefined ? {} : { usage: state.usage }),
    };
    this.drop(key);
    return result;
  }

  /** Safe contiguous prefix for UI streaming previews; this owns no raw state rules. */
  getPreview(chunk: TextResponseChunk): TextResponsePreview | null {
    const conversationId = normalizeConversationId(chunk.cid);
    const state = this.buffers.get(textResponseStreamKey(chunk.p, conversationId, chunk.id));
    if (!state) return null;
    const parts: string[] = [];
    for (let index = 1; index <= state.total; index += 1) {
      const content = state.chunks.get(index);
      if (content === undefined) break;
      parts.push(content.content);
    }
    return {
      responseId: chunk.id,
      playerName: state.playerName,
      role: state.role,
      conversationId: state.conversationId,
      ...(state.title === undefined ? {} : { title: state.title }),
      content: parts.join(""),
    };
  }

  private drop(key: string): void {
    const state = this.buffers.get(key);
    if (state) this.totalBufferedBytes -= state.byteLength;
    this.buffers.delete(key);
  }
}
