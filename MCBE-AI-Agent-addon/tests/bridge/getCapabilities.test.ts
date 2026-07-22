import { describe, expect, it } from "vitest";
import { handleGetCapabilities } from "../../scripts/bridge/capabilities/getCapabilities";

describe("get_capabilities", () => {
  it("returns versioned block_ops capability flags", () => {
    const result = handleGetCapabilities({});
    expect(result.ok).toBe(true);
    expect(result.payload.schema_version).toBe("1");
    expect(result.payload.capabilities.block_ops).toEqual({
      version: 1,
      inspect: true,
      place: true,
      batch: true,
      fill: true,
    });
  });
});
