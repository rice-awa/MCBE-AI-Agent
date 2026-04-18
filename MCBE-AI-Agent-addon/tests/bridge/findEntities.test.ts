import { describe, expect, it } from "vitest";

import { normalizeEntitySnapshot } from "../../scripts/bridge/capabilities/findEntities";

describe("findEntities", () => {
  it("rounds coordinates to integers", () => {
    const result = normalizeEntitySnapshot({
      id: "1",
      typeId: "minecraft:cow",
      location: { x: 1.2, y: 64.8, z: -3.4 },
    });

    expect(result.position).toEqual({ x: 1, y: 65, z: -3 });
  });
});
