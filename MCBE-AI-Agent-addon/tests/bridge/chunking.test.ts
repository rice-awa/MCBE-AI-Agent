import { describe, expect, it } from "vitest";

import { chunkBridgePayload, formatResponseChunk } from "../../scripts/bridge/chunking";

describe("bridge chunking", () => {
  it("chunks payload into deterministic parts with sequential metadata", () => {
    const payload = JSON.stringify({ ok: true, value: 1 });
    const chunks = chunkBridgePayload("req-2", payload, 8);

    expect(chunks).toHaveLength(3);
    expect(chunks).toEqual([
      "MCBEAI|RESP|req-2|1/3|{\"ok\":tr",
      "MCBEAI|RESP|req-2|2/3|ue,\"valu",
      "MCBEAI|RESP|req-2|3/3|e\":1}",
    ]);
  });

  it("formats error responses consistently", () => {
    const chunk = formatResponseChunk("req-3", 1, 1, "{\"ok\":false}");
    expect(chunk).toBe("MCBEAI|RESP|req-3|1/1|{\"ok\":false}");
  });

  it("rejects non-positive max chunk content length", () => {
    expect(() => chunkBridgePayload("req-4", "{\"ok\":true}", 0)).toThrowError(
      "maxChunkContentLength must be greater than 0",
    );
  });
});
