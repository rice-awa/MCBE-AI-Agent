import { describe, expect, it } from "vitest";

import { chunkBridgePayload, formatResponseChunk } from "../../scripts/bridge/chunking";

describe("bridge chunking", () => {
  it("formats chunks with request id and part metadata", () => {
    const chunks = chunkBridgePayload("req-2", JSON.stringify({ ok: true, value: 1 }), 12);
    expect(chunks[0]).toMatch(/^MCBEAI\|RESP\|req-2\|1\/\d+\|/);
  });

  it("formats error responses consistently", () => {
    const chunk = formatResponseChunk("req-3", 1, 1, "{\"ok\":false}");
    expect(chunk).toBe("MCBEAI|RESP|req-3|1/1|{\"ok\":false}");
  });
});
