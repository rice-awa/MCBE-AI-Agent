import { describe, expect, it } from "vitest";

import {
  BoundedTextResponseAssembler,
  DEFAULT_RESPONSE_ASSEMBLER_LIMITS,
  parseTextResponseChunk,
  type TextResponseChunk,
} from "../../scripts/bridge/textResponseAssembler";

const chunk = (overrides: Partial<TextResponseChunk> = {}): TextResponseChunk => ({
  id: "response-1",
  i: 1,
  n: 2,
  p: "Alice",
  r: "assistant",
  c: "a",
  cid: "chat-a",
  t: "Chat",
  ...overrides,
});

describe("BoundedTextResponseAssembler", () => {
  it("reassembles out of order and isolates player/cid/response keys", () => {
    const assembler = new BoundedTextResponseAssembler();
    expect(assembler.push(chunk({ p: "Alice", cid: "a", i: 2, c: "A" }))).toBeNull();
    expect(assembler.push(chunk({ p: "Bob", cid: "b", i: 1, c: "b" }))).toBeNull();
    expect(assembler.push(chunk({ p: "Alice", cid: "a", i: 1, c: "a" }))).toMatchObject({
      playerName: "Alice",
      conversationId: "a",
      responseId: "response-1",
      content: "aA",
    });
    expect(assembler.push(chunk({ p: "Bob", cid: "b", i: 2, c: "B" }))).toMatchObject({
      playerName: "Bob",
      conversationId: "b",
      content: "bB",
    });
  });

  it("accepts identical duplicates but drops conflicting duplicates and metadata", () => {
    const assembler = new BoundedTextResponseAssembler();
    expect(assembler.push(chunk())).toBeNull();
    expect(assembler.push(chunk())).toBeNull();
    expect(assembler.push(chunk({ c: "conflict" }))).toBeNull();
    expect(assembler.bufferCount).toBe(0);

    expect(assembler.push(chunk())).toBeNull();
    expect(assembler.push(chunk({ i: 2, c: "b", t: "Other" }))).toBeNull();
    expect(assembler.bufferCount).toBe(0);
  });

  it("keeps identical non-final duplicates idempotent after a final frame arrives first", () => {
    const assembler = new BoundedTextResponseAssembler();
    expect(assembler.push(chunk({ i: 3, n: 3, c: "done", u: { i: 1, o: 2 } }))).toBeNull();
    expect(assembler.push(chunk({ i: 1, n: 3, c: "answer " }))).toBeNull();
    expect(assembler.push(chunk({ i: 1, n: 3, c: "answer " }))).toBeNull();
    expect(assembler.push(chunk({ i: 2, n: 3, c: "middle " }))).toMatchObject({ content: "answer middle done" });
    expect(assembler.bufferCount).toBe(0);
  });

  it("enforces TTL, message/chunk/buffer/total byte limits and owner cleanup", () => {
    let now = 0;
    const assembler = new BoundedTextResponseAssembler(
      {
        ...DEFAULT_RESPONSE_ASSEMBLER_LIMITS,
        ttlMs: 10,
        maxBuffers: 1,
        maxChunksPerMessage: 2,
        maxMessageBytes: 3,
        maxTotalBufferBytes: 3,
      },
      () => now
    );
    expect(assembler.push(chunk({ p: "Alice", c: "中" }))).toBeNull();
    expect(assembler.push(chunk({ p: "Bob", i: 1, c: "b" }))).toBeNull();
    expect(assembler.bufferCount).toBe(1);
    assembler.clearForPlayer("Alice");
    expect(assembler.bufferCount).toBe(0);
    now = 22;
    expect(assembler.push(chunk({ i: 1, c: "a" }))).toBeNull();
    now = 33;
    assembler.pruneExpired();
    expect(assembler.bufferCount).toBe(0);
  });

  it("requires completion-only usage and rejects unknown roles", () => {
    expect(parseTextResponseChunk({ ...chunk({ n: 2 }), u: { i: 1, o: 2 } })).toBeNull();
    expect(parseTextResponseChunk({ ...chunk({ r: "unknown" }) })).toBeNull();
    const assembler = new BoundedTextResponseAssembler();
    expect(assembler.push(chunk({ i: 2, u: { i: 3, o: 5 }, c: "b" }))).toBeNull();
    const complete = assembler.push(chunk({ u: { i: 3, o: 5 }, c: "a" }));
    expect(complete).toBeNull();
  });
});
