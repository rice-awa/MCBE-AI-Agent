import { describe, expect, it, vi } from "vitest";

import { handleGetLookBlock } from "../../scripts/bridge/capabilities/getLookBlock";

vi.mock("@minecraft/server", () => ({
  world: {
    getPlayers: vi.fn(),
  },
}));

import { world } from "@minecraft/server";

describe("getLookBlock", () => {
  it("returns error when target is missing or a selector", () => {
    (world.getPlayers as ReturnType<typeof vi.fn>).mockReturnValue([]);

    expect(handleGetLookBlock({}).ok).toBe(false);
    expect(handleGetLookBlock({ target: "@s" }).ok).toBe(false);
    expect(handleGetLookBlock({ target: "  " }).ok).toBe(false);
  });

  it("returns hit=false when raycast misses", () => {
    const player = {
      name: "Steve",
      getBlockFromViewDirection: vi.fn(() => undefined),
    };
    (world.getPlayers as ReturnType<typeof vi.fn>).mockReturnValue([player]);

    const response = handleGetLookBlock({ target: "Steve", max_distance: 8 });

    expect(player.getBlockFromViewDirection).toHaveBeenCalledWith({
      maxDistance: 8,
      includeLiquidBlocks: false,
      includePassableBlocks: false,
    });
    expect(response).toEqual({
      ok: true,
      payload: { player: "Steve", hit: false },
    });
  });

  it("returns block snapshot when raycast hits", () => {
    const player = {
      name: "Steve",
      getBlockFromViewDirection: vi.fn(() => ({
        block: {
          typeId: "minecraft:oak_log",
          location: { x: 10.2, y: 64.7, z: -3.4 },
          dimension: { id: "minecraft:overworld" },
        },
        face: "North",
        faceLocation: { x: 0.5123, y: 0.3, z: 0.0 },
      })),
    };
    (world.getPlayers as ReturnType<typeof vi.fn>).mockReturnValue([player]);

    const response = handleGetLookBlock({
      target: "Steve",
      max_distance: 16,
      include_liquid_blocks: true,
    });

    expect(player.getBlockFromViewDirection).toHaveBeenCalledWith({
      maxDistance: 16,
      includeLiquidBlocks: true,
      includePassableBlocks: false,
    });
    expect(response).toEqual({
      ok: true,
      payload: {
        player: "Steve",
        hit: true,
        block: {
          typeId: "minecraft:oak_log",
          location: { x: 10, y: 65, z: -3 },
          dimension: "minecraft:overworld",
        },
        face: "North",
        faceLocation: { x: 0.512, y: 0.3, z: 0 },
      },
    });
  });

  it("clamps max_distance into [1, 64]", () => {
    const player = {
      name: "Alex",
      getBlockFromViewDirection: vi.fn(() => undefined),
    };
    (world.getPlayers as ReturnType<typeof vi.fn>).mockReturnValue([player]);

    handleGetLookBlock({ target: "Alex", max_distance: 0 });
    expect(player.getBlockFromViewDirection).toHaveBeenLastCalledWith(
      expect.objectContaining({ maxDistance: 1 }),
    );

    handleGetLookBlock({ target: "Alex", max_distance: 999 });
    expect(player.getBlockFromViewDirection).toHaveBeenLastCalledWith(
      expect.objectContaining({ maxDistance: 64 }),
    );
  });
});
