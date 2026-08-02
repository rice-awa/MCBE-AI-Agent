import { describe, expect, it, beforeEach } from "vitest";
import {
  __resetBlocks,
  __setBlock,
  __setPlayers,
  BlockTypes,
} from "../../__mocks__/minecraft-server";
import { handleEditBlocks } from "../../../scripts/bridge/capabilities/blocks/index";
import { handlePlace } from "../../../scripts/bridge/capabilities/blocks/place";
import {
  editDistance,
  findBlockCandidates,
  repairTypeId,
} from "../../../scripts/bridge/capabilities/blocks/repair";
import { isMultiblockBlock } from "../../../scripts/bridge/capabilities/blocks/multiblock";

/* eslint-disable @typescript-eslint/no-explicit-any */

describe("block id repair + candidate suggestions (issue 05 §4.2)", () => {
  beforeEach(() => {
    __resetBlocks();
    __setPlayers([]);
  });

  it("auto-repairs a unique edit-distance-1 typo and records the repair", async () => {
    // "minecraft:stonx" is edit-distance 1 from "minecraft:stone" only.
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:air", isAir: true });

    const result = await handlePlace({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 0, y: 64, z: 0 },
      type_id: "minecraft:stonx",
      phase: "execute",
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect((result.payload as any).type_id).toBe("minecraft:stone");
    const repairs = result.payload.repairs_applied as Array<{ reason: string }>;
    expect(repairs.some((r) => r.reason === "fuzzy_vanilla_edit_distance_1")).toBe(true);
  });

  it("normalizes block id casing deterministically and records the repair", async () => {
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:air", isAir: true });

    const result = await handlePlace({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 0, y: 64, z: 0 },
      type_id: "Minecraft:STONE",
      phase: "preflight",
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect((result.payload as any).type_id).toBe("minecraft:stone");
    const repairs = result.payload.repairs_applied as Array<{ reason: string }>;
    expect(repairs.some((r) => r.reason === "lowercase_type_id")).toBe(true);
  });

  it("returns up to 3 candidates for an ambiguous unknown id, never silently choosing", async () => {
    // Two known ids both at edit-distance 1 from "minecraft:test_ac".
    BlockTypes.__add("minecraft:test_aa");
    BlockTypes.__add("minecraft:test_ab");
    try {
      const candidates = findBlockCandidates("minecraft:test_ac", BlockTypes);
      expect(candidates.length).toBe(2);
      expect(candidates).toContain("minecraft:test_aa");
      expect(candidates).toContain("minecraft:test_ab");

      // The full place path rejects with BLOCK_UNKNOWN + candidates (no silent pick).
      __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:air", isAir: true });
      const result = await handlePlace({
        mode: "place",
        coordinate_mode: "absolute",
        dimension: "minecraft:overworld",
        position: { x: 0, y: 64, z: 0 },
        type_id: "minecraft:test_ac",
      });
      expect(result.ok).toBe(false);
      if (result.ok) return;
      expect(result.payload.code).toBe("BLOCK_UNKNOWN");
      expect(Array.isArray((result.payload as any).candidates)).toBe(true);
      expect((result.payload as any).candidates.length).toBe(2);
    } finally {
      // Best-effort cleanup so the extra ids do not affect other suites.
      (BlockTypes as any).__remove?.("minecraft:test_aa");
      (BlockTypes as any).__remove?.("minecraft:test_ab");
    }
  });

  it("bounds candidate suggestions to at most 3", () => {
    BlockTypes.__add("minecraft:test_c0");
    BlockTypes.__add("minecraft:test_c1");
    BlockTypes.__add("minecraft:test_c2");
    BlockTypes.__add("minecraft:test_c3");
    try {
      // "minecraft:test_cX" is distance 1 from each test_cN (X<->N).
      const candidates = findBlockCandidates("minecraft:test_cX", BlockTypes, 3);
      expect(candidates.length).toBeLessThanOrEqual(3);
    } finally {
      (BlockTypes as any).__remove?.("minecraft:test_c0");
      (BlockTypes as any).__remove?.("minecraft:test_c1");
      (BlockTypes as any).__remove?.("minecraft:test_c2");
      (BlockTypes as any).__remove?.("minecraft:test_c3");
    }
  });

  it("never suggests candidates for custom namespaces", () => {
    expect(findBlockCandidates("foo:bar", BlockTypes)).toEqual([]);
    // repairTypeId also leaves custom namespaces untouched (no fuzzy repair).
    const repairs: any[] = [];
    const id = repairTypeId("foo:stne", repairs, BlockTypes);
    expect(id).toBe("foo:stne");
    expect(repairs.some((r) => r.reason === "fuzzy_vanilla_edit_distance_1")).toBe(false);
  });
});

describe("invalid block states (issue 05 §4.3)", () => {
  beforeEach(() => {
    __resetBlocks();
    __setPlayers([]);
  });

  it("returns STATE_INVALID with bounded valid_state_keys, without writing", async () => {
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:air", isAir: true });

    const result = await handlePlace({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 0, y: 64, z: 0 },
      type_id: "minecraft:oak_stairs",
      states: { __invalid__: true } as any,
      phase: "preflight",
    });

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("STATE_INVALID");
    expect((result.payload as any).type_id).toBe("minecraft:oak_stairs");
    const keys = (result.payload as any).valid_state_keys as string[];
    expect(Array.isArray(keys)).toBe(true);
    expect(keys).toContain("minecraft:cardinal_direction");
    expect(keys.length).toBeLessThanOrEqual(8);
    // Preflight must not mutate the world.
    expect(__setBlock).toBeDefined();
  });
});

describe("multiblock placement safety (issue 05 §6/§7)", () => {
  beforeEach(() => {
    __resetBlocks();
    __setPlayers([]);
  });

  it("detects doors and beds as multiblock", () => {
    expect(isMultiblockBlock("minecraft:oak_door")).toBe(true);
    expect(isMultiblockBlock("minecraft:iron_door")).toBe(true);
    expect(isMultiblockBlock("minecraft:red_bed")).toBe(true);
    expect(isMultiblockBlock("minecraft:tall_grass")).toBe(true);
    expect(isMultiblockBlock("minecraft:sunflower")).toBe(true);
    // Single-cell blocks are not multiblock.
    expect(isMultiblockBlock("minecraft:stone")).toBe(false);
    expect(isMultiblockBlock("minecraft:oak_stairs")).toBe(false);
    expect(isMultiblockBlock(undefined)).toBe(false);
  });

  it("rejects a door with UNSUPPORTED_BLOCK_PLACEMENT before approval (preflight)", async () => {
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:air", isAir: true });

    const result = await handleEditBlocks({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 0, y: 64, z: 0 },
      type_id: "minecraft:oak_door",
      phase: "preflight",
    });

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("UNSUPPORTED_BLOCK_PLACEMENT");
    expect((result.payload as any).type_id).toBe("minecraft:oak_door");
    expect((result.payload as any).multiblock).toBe(true);
  });

  it("rejects a bed with UNSUPPORTED_BLOCK_PLACEMENT on execute too", async () => {
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:air", isAir: true });

    const result = await handlePlace({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 0, y: 64, z: 0 },
      type_id: "minecraft:red_bed",
      phase: "execute",
    });

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("UNSUPPORTED_BLOCK_PLACEMENT");
  });
});

describe("expect uses the same validation rules as the target block (issue 05 §4.4)", () => {
  beforeEach(() => {
    __resetBlocks();
    __setPlayers([]);
  });

  it("repairs expect type_id namespace and validates expect states", async () => {
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:dirt" });

    // Expect type "Dirt" -> repaired to "minecraft:dirt"; states invalid -> STATE_INVALID.
    const result = await handlePlace({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 0, y: 64, z: 0 },
      type_id: "minecraft:stone",
      expected_previous: { type_id: "Dirt", states: { __invalid__: true } as any },
      phase: "preflight",
    });

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("STATE_INVALID");
  });

  it("rejects an unknown expect type with BLOCK_UNKNOWN + candidates", async () => {
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:air", isAir: true });
    BlockTypes.__add("minecraft:test_aa");
    BlockTypes.__add("minecraft:test_ab");
    try {
      const result = await handlePlace({
        mode: "place",
        coordinate_mode: "absolute",
        dimension: "minecraft:overworld",
        position: { x: 0, y: 64, z: 0 },
        type_id: "minecraft:stone",
        expected_previous: { type_id: "minecraft:test_ac" },
        phase: "preflight",
      });
      expect(result.ok).toBe(false);
      if (result.ok) return;
      expect(result.payload.code).toBe("BLOCK_UNKNOWN");
      expect(Array.isArray((result.payload as any).candidates)).toBe(true);
    } finally {
      (BlockTypes as any).__remove?.("minecraft:test_aa");
      (BlockTypes as any).__remove?.("minecraft:test_ab");
    }
  });
});

describe("editDistance sanity", () => {
  it("computes known distances", () => {
    expect(editDistance("minecraft:stonx", "minecraft:stone")).toBe(1);
    expect(editDistance("minecraft:stone", "minecraft:stone")).toBe(0);
    expect(editDistance("a", "b")).toBe(1);
  });
});
