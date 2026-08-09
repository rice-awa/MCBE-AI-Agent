import { describe, expect, it } from "vitest";

import {
  chunkBridgePayload,
  chunkUiChatPayload,
  formatResponseChunk,
  utf8ByteLength,
} from "../../scripts/bridge/chunking";

describe("bridge chunking", () => {
  it("chunks payload into deterministic parts with sequential metadata", () => {
    const payload = JSON.stringify({ ok: true, value: 1 });
    const chunks = chunkBridgePayload("req-2", payload, 8);

    expect(chunks).toHaveLength(3);
    expect(chunks).toEqual([
      "MCBEWS|BRIDGE|req-2|1/3|{\"ok\":tr",
      "MCBEWS|BRIDGE|req-2|2/3|ue,\"valu",
      "MCBEWS|BRIDGE|req-2|3/3|e\":1}",
    ]);
  });

  it("formats error responses consistently", () => {
    const chunk = formatResponseChunk("req-3", 1, 1, "{\"ok\":false}");
    expect(chunk).toBe("MCBEWS|BRIDGE|req-3|1/1|{\"ok\":false}");
  });

  it("rejects non-positive max chunk content length", () => {
    expect(() => chunkBridgePayload("req-4", "{\"ok\":true}", 0)).toThrowError(
      "maxChunkContentLength must be greater than 0",
    );
  });

  it("splits Unicode code points and keeps tell command lines within 461 UTF-8 bytes", () => {
    const payload = JSON.stringify({ player: "玩家", message: `${"中".repeat(256)}😀` });
    const chunks = chunkUiChatPayload("ui-unicode", payload);

    expect(chunks.map((chunk) => chunk.split("|").slice(4).join("|")).join("")).toBe(payload);
    for (const chunk of chunks) {
      expect(utf8ByteLength(`tell @s ${chunk}`)).toBeLessThanOrEqual(461);
      expect(Array.from(chunk.split("|").slice(4).join("|")).length).toBeLessThanOrEqual(256);
    }
  });

  it("emits an empty 1/1 frame and rejects wrappers with no room", () => {
    expect(chunkBridgePayload("empty", "")).toEqual(["MCBEWS|BRIDGE|empty|1/1|"]);
    expect(() =>
      chunkBridgePayload("too-large", "中", {
        commandLineByteBudget: 5,
      }),
    ).toThrowError("chunk framing leaves no room for one Unicode code point");
  });
});
