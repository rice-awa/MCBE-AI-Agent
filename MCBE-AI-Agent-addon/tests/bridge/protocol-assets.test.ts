import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { MCBEWS_V1_MANIFEST, MCBEWS_V1_WIRE_VECTORS } from "../../scripts/bridge/protocol";
import { chunkPayload, utf8ByteLength } from "../../scripts/bridge/chunking";
import { BoundedTextResponseAssembler, parseTextResponseChunk } from "../../scripts/bridge/textResponseAssembler";

const readJson = (name: string): unknown =>
  JSON.parse(readFileSync(fileURLToPath(new URL(`../../protocol/${name}`, import.meta.url)), "utf8"));

const toCamel = (value: unknown): unknown => {
  if (Array.isArray(value)) return value.map(toCamel);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase()), toCamel(item)]),
    );
  }
  return value;
};

describe("MCBEWS/1 generated protocol assets", () => {
  it("matches the checked-in manifest and vectors projection", () => {
    expect(MCBEWS_V1_MANIFEST).toEqual(toCamel(readJson("manifest.json")));
    expect(MCBEWS_V1_WIRE_VECTORS).toEqual(toCamel(readJson("vectors.json")));
  });

  it("keeps the semantic version axes and no mcbeai wire IDs", () => {
    expect(MCBEWS_V1_MANIFEST.versions).toEqual({
      capabilityRequestSchema: 2,
      sessionSchema: 1,
      textResponseFraming: 1,
      dduiPersistence: 2,
    });
    expect(JSON.stringify(MCBEWS_V1_MANIFEST)).not.toContain("mcbeai:");
  });

  it("executes shared chunking and text-response behavior vectors", () => {
    for (const vector of MCBEWS_V1_WIRE_VECTORS.behavior.chunking) {
      const options = {
        commandLineByteBudget: vector.budget,
        maxContentCodePoints: vector.maxContentCodePoints,
        wrapCommandLine: (chunk: string) => `${vector.wrapperPrefix}${chunk}`,
      };
      if (vector.name === "empty-wrapper-no-room") {
        expect(() => chunkPayload(vector.prefix, vector.id, vector.payload, options)).toThrow();
        continue;
      }
      const chunks = chunkPayload(vector.prefix, vector.id, vector.payload, options);
      expect(chunks.map((item) => item.split("|").slice(4).join("|")).join("")).toBe(vector.payload);
      for (const item of chunks) {
        expect(utf8ByteLength(`${vector.wrapperPrefix}${item}`)).toBeLessThanOrEqual(vector.budget);
      }
    }

    for (const vector of MCBEWS_V1_WIRE_VECTORS.behavior.textResponse) {
      const assembler = new BoundedTextResponseAssembler();
      let result;
      for (const frame of vector.chunks) {
        const parsed = parseTextResponseChunk(frame);
        expect(parsed).not.toBeNull();
        if (parsed) result = assembler.push(parsed) ?? result;
      }
      if ("expected" in vector) {
        expect(result).toMatchObject({
          playerName: vector.expected.playerName,
          role: vector.expected.role,
          content: vector.expected.text,
          responseId: vector.expected.responseId,
          conversationId: vector.expected.conversationId,
          usage: vector.expected.usage,
        });
      } else {
        expect(assembler.bufferCount).toBe(0);
      }
    }
  });
});
