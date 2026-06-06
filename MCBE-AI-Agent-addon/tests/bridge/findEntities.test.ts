import { describe, expect, it, vi } from "vitest";

import { handleFindEntities, normalizeEntitySnapshot } from "../../scripts/bridge/capabilities/findEntities";

// Mock @minecraft/server
vi.mock("@minecraft/server", () => ({
  world: {
    getPlayers: vi.fn(),
  },
}));

import { world } from "@minecraft/server";

describe("findEntities", () => {
  it("rounds coordinates to integers", () => {
    const result = normalizeEntitySnapshot({
      id: "1",
      typeId: "minecraft:cow",
      location: { x: 1.2, y: 64.8, z: -3.4 },
    });

    expect(result.position).toEqual({ x: 1, y: 65, z: -3 });
  });

  it("uses sourceBlock as query anchor when sourceEntity is absent and no target player", () => {
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
      },
    );

    expect(getEntities).toHaveBeenCalledWith({
      type: "minecraft:villager",
      location: { x: 8, y: 64, z: -5 },
      maxDistance: 16,
    });
    expect(response.payload.entities).toHaveLength(1);
  });

  it("uses target player location as query anchor when target is a player name", () => {
    const mockPlayer = {
      dimension: {
        getEntities: vi.fn(() => [
          {
            id: "1",
            typeId: "minecraft:cow",
            location: { x: 100, y: 64, z: 200 },
          },
        ]),
      },
      location: { x: 100, y: 64, z: 200 },
    };

    (world.getPlayers as ReturnType<typeof vi.fn>).mockReturnValue([mockPlayer]);

    const response = handleFindEntities(
      {
        sourceEntity: undefined,
        sourceBlock: undefined,
      },
      {
        entity_type: "minecraft:cow",
        radius: 32,
        target: "Steve",
      },
    );

    expect(world.getPlayers).toHaveBeenCalledWith({ name: "Steve" });
    expect(mockPlayer.dimension.getEntities).toHaveBeenCalledWith({
      type: "minecraft:cow",
      location: { x: 100, y: 64, z: 200 },
      maxDistance: 32,
    });
    expect(response.payload.entities).toHaveLength(1);
  });
});
