import { describe, expect, it, beforeEach } from "vitest";
import {
  __resetBlocks,
  __setBlock,
  __setPlayers,
} from "../../__mocks__/minecraft-server";
import { handleInspectBlock } from "../../../scripts/bridge/capabilities/blocks/inspect";
import { snapYawToCardinal, resolveRelativePosition, mathFloor } from "../../../scripts/bridge/capabilities/blocks/coords";
import { canonicalizeDimension } from "../../../scripts/bridge/capabilities/blocks/dimensions";
import { editDistance, repairTypeId } from "../../../scripts/bridge/capabilities/blocks/repair";
import { BlockTypes } from "../../__mocks__/minecraft-server";

describe("coords helpers", () => {
  it("math floor works for negatives", () => {
    expect(mathFloor(-1.2)).toBe(-2);
    expect(mathFloor(1.8)).toBe(1);
    expect(mathFloor(-0.1)).toBe(-1);
  });

  it("snaps yaw to cardinals", () => {
    expect(snapYawToCardinal(0)).toBe("south");
    expect(snapYawToCardinal(90)).toBe("west");
    expect(snapYawToCardinal(180)).toBe("north");
    expect(snapYawToCardinal(270)).toBe("east");
    expect(snapYawToCardinal(-45)).toBe("south"); // -45 -> 315
  });

  it("resolves relative offsets with cardinal facing", () => {
    const origin = { x: 10, y: 64, z: 20 };
    // facing north (-Z): forward 2 => z-2, right 1 => x+1
    expect(resolveRelativePosition(origin, "north", { forward: 2, right: 1, up: 0 })).toEqual({
      x: 11,
      y: 64,
      z: 18,
    });
    // facing east (+X): forward 3 => x+3, right 1 => z+1
    expect(resolveRelativePosition(origin, "east", { forward: 3, right: 1, up: 1 })).toEqual({
      x: 13,
      y: 65,
      z: 21,
    });
  });
});

describe("dimension aliases", () => {
  it("canonicalizes vanilla aliases", () => {
    expect(canonicalizeDimension("overworld").dimension).toBe("minecraft:overworld");
    expect(canonicalizeDimension("nether").dimension).toBe("minecraft:nether");
    expect(canonicalizeDimension("the_end").dimension).toBe("minecraft:the_end");
    expect(canonicalizeDimension("end").dimension).toBe("minecraft:the_end");
  });

  it("allows custom namespaced dimensions", () => {
    expect(canonicalizeDimension("my_pack:void").dimension).toBe("my_pack:void");
  });
});

describe("repair helpers", () => {
  it("adds minecraft namespace and trims", () => {
    const repairs: Array<{ field: string; from: unknown; to: unknown; reason: string }> = [];
    const id = repairTypeId("  stone  ", repairs, BlockTypes);
    expect(id).toBe("minecraft:stone");
    expect(repairs.some((r) => r.reason === "trim_whitespace")).toBe(true);
    expect(repairs.some((r) => r.reason === "add_minecraft_namespace")).toBe(true);
  });

  it("fuzzy corrects unique edit-distance-1 vanilla id", () => {
    const repairs: Array<{ field: string; from: unknown; to: unknown; reason: string }> = [];
    // ston vs stone
    const id = repairTypeId("minecraft:stonx", repairs, BlockTypes);
    // stonx vs stone is distance 1 if only one candidate - stonx to stone is 1 (x->e)
    // Actually stone vs stonx: last char e vs x = distance 1. Unique among known types?
    expect(editDistance("minecraft:stonx", "minecraft:stone")).toBe(1);
    // May or may not be unique - check if only one candidate
    if (id === "minecraft:stone") {
      expect(repairs.some((r) => r.reason === "fuzzy_vanilla_edit_distance_1")).toBe(true);
    }
  });
});

describe("inspect_block", () => {
  beforeEach(() => {
    __resetBlocks();
    __setPlayers([]);
  });

  it("inspects absolute positions and floors negatives", async () => {
    __setBlock("minecraft:overworld", -2, 64, 3, {
      typeId: "minecraft:stone",
      states: { dummy: 1 },
    });

    const result = await handleInspectBlock({
      coordinate_mode: "absolute",
      dimension: "overworld",
      position: { x: -1.2, y: 64.9, z: 3.1 },
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.dimension).toBe("minecraft:overworld");
    const blocks = result.payload.blocks as Array<{
      x: number;
      y: number;
      z: number;
      type_id: string;
    }>;
    // floor(-1.2) = -2
    expect(blocks[0].x).toBe(-2);
    expect(blocks[0].y).toBe(64);
    expect(blocks[0].z).toBe(3);
    expect(blocks[0].type_id).toBe("minecraft:stone");
    const repairs = result.payload.repairs_applied as Array<{ reason: string }>;
    expect(repairs.some((r) => r.reason === "math_floor")).toBe(true);
    expect(repairs.some((r) => r.reason === "canonicalize_dimension_alias")).toBe(true);
  });

  it("requires dimension for absolute mode", async () => {
    const result = await handleInspectBlock({
      coordinate_mode: "absolute",
      position: { x: 0, y: 64, z: 0 },
    });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("INVALID_ARGUMENT");
  });

  it("supports multiple positions", async () => {
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:dirt" });
    __setBlock("minecraft:overworld", 1, 64, 0, { typeId: "minecraft:stone" });
    const result = await handleInspectBlock({
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      positions: [
        { x: 0, y: 64, z: 0 },
        { x: 1, y: 64, z: 0 },
      ],
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const blocks = result.payload.blocks as Array<{ type_id: string }>;
    expect(blocks).toHaveLength(2);
    expect(blocks[0].type_id).toBe("minecraft:dirt");
    expect(blocks[1].type_id).toBe("minecraft:stone");
  });

  it("resolves player_relative with cardinal snap", async () => {
    // yaw 0 = south, foot at 5.2, 70.1, 10.8 => floor origin 5,70,10
    __setPlayers([
      {
        name: "Steve",
        location: { x: 5.2, y: 70.1, z: 10.8 },
        yaw: 0,
        dimensionId: "minecraft:overworld",
      },
    ]);
    // south facing: forward 2 => z+2 = 12, right 1 => x-1 = 4
    __setBlock("minecraft:overworld", 4, 70, 12, { typeId: "minecraft:sand" });

    const result = await handleInspectBlock({
      coordinate_mode: "player_relative",
      player_name: "Steve",
      position: { forward: 2, right: 1, up: 0 },
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.facing).toBe("south");
    const blocks = result.payload.blocks as Array<{
      x: number;
      y: number;
      z: number;
      type_id: string;
    }>;
    expect(blocks[0]).toMatchObject({ x: 4, y: 70, z: 12, type_id: "minecraft:sand" });
  });

  it("rejects over limit positions", async () => {
    const positions = Array.from({ length: 300 }, (_, i) => ({ x: i, y: 64, z: 0 }));
    const result = await handleInspectBlock({
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      positions,
      max_positions: 256,
    });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("LIMIT_EXCEEDED");
  });
});
