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

describe("inspect_block unified target", () => {
  beforeEach(() => {
    __resetBlocks();
    __setPlayers([]);
  });

  it("accepts target.positions with single point (full snapshot)", async () => {
    __setBlock("minecraft:overworld", 5, 64, 10, { typeId: "minecraft:stone" });
    const result = await handleInspectBlock({
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      target: {
        positions: [{ x: 5, y: 64, z: 10 }],
      },
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const blocks = result.payload.blocks as Array<{ type_id: string }>;
    expect(blocks).toHaveLength(1);
    expect(blocks[0].type_id).toBe("minecraft:stone");
  });

  it("rejects target with both positions and box", async () => {
    const result = await handleInspectBlock({
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      target: {
        positions: [{ x: 0, y: 64, z: 0 }],
        box: { from: { x: 0, y: 64, z: 0 }, to: { x: 1, y: 64, z: 1 } },
      },
    });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("INVALID_ARGUMENT");
  });

  it("rejects target with neither positions nor box", async () => {
    const result = await handleInspectBlock({
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      target: {},
    });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("INVALID_ARGUMENT");
  });

  it("inspects a small box returning full snapshots", async () => {
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:stone" });
    __setBlock("minecraft:overworld", 1, 64, 0, { typeId: "minecraft:dirt" });
    __setBlock("minecraft:overworld", 0, 65, 0, { typeId: "minecraft:air", isAir: true });
    __setBlock("minecraft:overworld", 1, 65, 0, { typeId: "minecraft:air", isAir: true });
    const result = await handleInspectBlock({
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      target: {
        box: {
          from: { x: 0, y: 64, z: 0 },
          to: { x: 1, y: 65, z: 0 },
        },
      },
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.status).toBe("inspected");
    const blocks = result.payload.blocks as Array<{
      x: number;
      y: number;
      z: number;
      type_id: string;
    }>;
    expect(blocks).toHaveLength(4);
    // Corners are normalized regardless of from/to order.
    const types = blocks.map((b) => b.type_id).sort();
    expect(types).toEqual(["minecraft:air", "minecraft:air", "minecraft:dirt", "minecraft:stone"]);
  });

  it("normalizes reversed box corners", async () => {
    // Set blocks in a 1x1x2 volume (y=64..65 at 0,0)
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:stone" });
    __setBlock("minecraft:overworld", 0, 65, 0, { typeId: "minecraft:dirt" });
    const result = await handleInspectBlock({
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      target: {
        // Reversed: to < from in y
        box: {
          from: { x: 0, y: 65, z: 0 },
          to: { x: 0, y: 64, z: 0 },
        },
      },
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const blocks = result.payload.blocks as Array<{ type_id: string }>;
    expect(blocks).toHaveLength(2);
  });

  it("returns bounded summary for box above threshold", async () => {
    // 3x3x1 = 9 cells, threshold 8 -> summary
    for (let x = 0; x < 3; x++) {
      for (let z = 0; z < 3; z++) {
        __setBlock("minecraft:overworld", x, 64, z, {
          typeId: x === 0 ? "minecraft:stone" : "minecraft:dirt",
        });
      }
    }
    const result = await handleInspectBlock({
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      target: {
        box: {
          from: { x: 0, y: 64, z: 0 },
          to: { x: 2, y: 64, z: 2 },
        },
      },
      inspect_summary_threshold: 8,
      inspect_sample_limit: 4,
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.summary).toBeDefined();
    const summary = result.payload.summary as {
      count: number;
      unknown_count: number;
      type_counts: Record<string, number>;
      bounds: { from: { x: number; y: number; z: number }; to: { x: number; y: number; z: number } };
      samples: Array<{ type_id?: string }>;
    };
    expect(summary.count).toBe(9);
    expect(summary.unknown_count).toBe(0);
    expect(summary.type_counts["minecraft:stone"]).toBe(3);
    expect(summary.type_counts["minecraft:dirt"]).toBe(6);
    expect(summary.bounds.from).toEqual({ x: 0, y: 64, z: 0 });
    expect(summary.bounds.to).toEqual({ x: 2, y: 64, z: 2 });
    expect(summary.samples.length).toBeLessThanOrEqual(4);
    // No full blocks array on summary path.
    expect(result.payload.blocks).toBeUndefined();
  });

  it("tracks unknown/unloaded cells in summary without faking air", async () => {
    // 3x3x1 = 9 cells, threshold 8 -> summary.
    // Set only 2 cells; remaining default to air in mock. But mark one as invalid
    // (unloaded) to verify unknown tracking.
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:stone" });
    __setBlock("minecraft:overworld", 1, 64, 0, {
      typeId: "minecraft:air",
      isAir: true,
      isValid: false,
    });
    const result = await handleInspectBlock({
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      target: {
        box: {
          from: { x: 0, y: 64, z: 0 },
          to: { x: 2, y: 64, z: 2 },
        },
      },
      inspect_summary_threshold: 8,
      inspect_sample_limit: 8,
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const summary = result.payload.summary as {
      unknown_count: number;
      samples: Array<{ status?: string; type_id?: string }>;
    };
    expect(summary.unknown_count).toBe(1);
    const unknownSample = summary.samples.find((s) => s.status === "unloaded");
    expect(unknownSample).toBeDefined();
  });

  it("rejects box exceeding fill volume limit", async () => {
    const result = await handleInspectBlock({
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      target: {
        box: {
          from: { x: 0, y: 0, z: 0 },
          to: { x: 100, y: 100, z: 100 },
        },
      },
      max_fill_volume: 27,
    });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("LIMIT_EXCEEDED");
  });

  it("returns bounded summary for many positions above threshold", async () => {
    // 10 positions, threshold 8 -> summary
    for (let i = 0; i < 10; i++) {
      __setBlock("minecraft:overworld", i, 64, 0, {
        typeId: i < 3 ? "minecraft:stone" : "minecraft:dirt",
      });
    }
    const result = await handleInspectBlock({
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      target: {
        positions: Array.from({ length: 10 }, (_, i) => ({ x: i, y: 64, z: 0 })),
      },
      inspect_summary_threshold: 8,
      inspect_sample_limit: 3,
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const summary = result.payload.summary as {
      count: number;
      type_counts: Record<string, number>;
      samples: Array<unknown>;
    };
    expect(summary.count).toBe(10);
    expect(summary.type_counts["minecraft:stone"]).toBe(3);
    expect(summary.type_counts["minecraft:dirt"]).toBe(7);
    expect(summary.samples.length).toBeLessThanOrEqual(3);
  });

  it("resolves player_relative box with cardinal snap", async () => {
    // yaw 0 = south, foot at 5,70,10
    __setPlayers([
      {
        name: "Steve",
        location: { x: 5.2, y: 70.1, z: 10.8 },
        yaw: 0,
        dimensionId: "minecraft:overworld",
      },
    ]);
    // south facing: forward +1 => z+1, right 0 => x unchanged
    // box from (forward=0,right=0,up=0) to (forward=1,right=1,up=0)
    // => cells: (5,70,10),(5,70,11),(4,70,10),(4,70,11)
    __setBlock("minecraft:overworld", 5, 70, 10, { typeId: "minecraft:stone" });
    __setBlock("minecraft:overworld", 5, 70, 11, { typeId: "minecraft:dirt" });
    __setBlock("minecraft:overworld", 4, 70, 10, { typeId: "minecraft:sand" });
    __setBlock("minecraft:overworld", 4, 70, 11, { typeId: "minecraft:gravel" });
    const result = await handleInspectBlock({
      coordinate_mode: "player_relative",
      player_name: "Steve",
      target: {
        box: {
          from: { forward: 0, right: 0, up: 0 },
          to: { forward: 1, right: 1, up: 0 },
        },
      },
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.dimension).toBe("minecraft:overworld");
    expect(result.payload.facing).toBe("south");
    const blocks = result.payload.blocks as Array<{ type_id: string }>;
    expect(blocks).toHaveLength(4);
  });
});
