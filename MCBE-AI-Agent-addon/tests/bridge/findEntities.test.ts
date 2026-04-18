import { describe, expect, it, vi } from "vitest";

import { handleFindEntities, normalizeEntitySnapshot } from "../../scripts/bridge/capabilities/findEntities";

describe("findEntities", () => {
  it("rounds coordinates to integers", () => {
    const result = normalizeEntitySnapshot({
      id: "1",
      typeId: "minecraft:cow",
      location: { x: 1.2, y: 64.8, z: -3.4 },
    });

    expect(result.position).toEqual({ x: 1, y: 65, z: -3 });
  });

  it("uses sourceBlock as query anchor when sourceEntity is absent", () => {
    const getEntities = vi.fn(() => [
      {
        id: "1",
        typeId: "minecraft:villager",
        location: { x: 10, y: 65, z: -2 },
      },
    ]);

    const response = handleFindEntities(
      {
        sourceBlock: {
          dimension: { getEntities },
          location: { x: 8, y: 64, z: -5 },
        },
      },
      {
        entity_type: "minecraft:villager",
        radius: 16,
        target: "Steve",
      },
    );

    expect(getEntities).toHaveBeenCalledWith({
      type: "minecraft:villager",
      location: { x: 8, y: 64, z: -5 },
      maxDistance: 16,
      name: "Steve",
    });
    expect(response.payload.entities).toHaveLength(1);
  });
});
