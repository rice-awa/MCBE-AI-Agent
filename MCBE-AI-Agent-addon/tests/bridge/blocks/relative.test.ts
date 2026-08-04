import { describe, expect, it, beforeEach } from "vitest";
import {
  __resetBlocks,
  __setBlock,
  __setPlayers,
} from "../../__mocks__/minecraft-server";
import { handleInspectBlock } from "../../../scripts/bridge/capabilities/blocks/inspect";
import { handlePlace } from "../../../scripts/bridge/capabilities/blocks/place";
import {
  snapYawToCardinal,
  resolveRelativePosition,
  footBlockOrigin,
} from "../../../scripts/bridge/capabilities/blocks/coords";

describe("player-relative coordinates", () => {
  beforeEach(() => {
    __resetBlocks();
    __setPlayers([]);
  });

  it("foot block origin floors continuous location", () => {
    expect(footBlockOrigin({ x: -1.1, y: 64.9, z: 2.2 })).toEqual({
      x: -2,
      y: 64,
      z: 2,
    });
  });

  it("cardinal snap covers quadrants", () => {
    expect(snapYawToCardinal(10)).toBe("south");
    expect(snapYawToCardinal(100)).toBe("west");
    expect(snapYawToCardinal(190)).toBe("north");
    expect(snapYawToCardinal(280)).toBe("east");
  });

  it("relative place locks absolute target from player facing north", async () => {
    __setPlayers([
      {
        name: "Alex",
        location: { x: 100.4, y: 70.2, z: 200.6 },
        yaw: 180, // north
        dimensionId: "minecraft:overworld",
      },
    ]);
    // origin floor: 100,70,200; north forward 1 => z-1 = 199, right 0
    __setBlock("minecraft:overworld", 100, 70, 199, {
      typeId: "minecraft:air",
      isAir: true,
    });

    const pre = await handlePlace({
      mode: "place",
      coordinate_mode: "player_relative",
      player_name: "Alex",
      position: { forward: 1, right: 0, up: 0 },
      type_id: "minecraft:stone",
      phase: "preflight",
    });

    expect(pre.ok).toBe(true);
    if (!pre.ok) return;
    expect(pre.payload.facing).toBe("north");
    expect(pre.payload.locked_targets).toEqual([
      { dimension: "minecraft:overworld", x: 100, y: 70, z: 199 },
    ]);

    // Player moves — execute with locked_targets must use locked coords
    __setPlayers([
      {
        name: "Alex",
        location: { x: 0, y: 64, z: 0 },
        yaw: 0,
        dimensionId: "minecraft:overworld",
      },
    ]);

    const exec = await handlePlace({
      mode: "place",
      coordinate_mode: "player_relative",
      player_name: "Alex",
      position: { forward: 1, right: 0, up: 0 },
      type_id: "minecraft:stone",
      phase: "execute",
      locked_targets: [
        { dimension: "minecraft:overworld", x: 100, y: 70, z: 199 },
      ],
    });

    expect(exec.ok).toBe(true);
    if (!exec.ok) return;
    expect((exec.payload.after as { x: number; z: number }).x).toBe(100);
    expect((exec.payload.after as { x: number; z: number }).z).toBe(199);
  });

  it("relative inspect uses player dimension and facing", async () => {
    __setPlayers([
      {
        name: "Bob",
        location: { x: 0, y: 64, z: 0 },
        yaw: 90, // west
        dimensionId: "minecraft:nether",
      },
    ]);
    // west: forward 2 => x-2, right 1 => z-1
    __setBlock("minecraft:nether", -2, 64, -1, { typeId: "minecraft:netherrack" });
    // add netherrack to known types for any repair path - not needed for inspect

    const result = await handleInspectBlock({
      coordinate_mode: "player_relative",
      player_name: "Bob",
      position: { forward: 2, right: 1, up: 0 },
    });

    // type may be air if netherrack not set properly - we set it
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.facing).toBe("west");
    expect(result.payload.dimension).toBe("minecraft:nether");
    const blocks = result.payload.blocks as Array<{ x: number; z: number; type_id: string }>;
    expect(blocks[0].x).toBe(-2);
    expect(blocks[0].z).toBe(-1);
  });

  it("resolveRelativePosition maps all four facings", () => {
    const o = { x: 0, y: 0, z: 0 };
    const off = { forward: 1, right: 1, up: 0 };
    expect(resolveRelativePosition(o, "north", off)).toEqual({ x: 1, y: 0, z: -1 });
    expect(resolveRelativePosition(o, "south", off)).toEqual({ x: -1, y: 0, z: 1 });
    expect(resolveRelativePosition(o, "east", off)).toEqual({ x: 1, y: 0, z: 1 });
    expect(resolveRelativePosition(o, "west", off)).toEqual({ x: -1, y: 0, z: -1 });
  });
});
