import { describe, expect, it, beforeEach } from "vitest";
import { __resetBlocks, __setBlock, __setPlayers, BlockTypes } from "../../__mocks__/minecraft-server";
import { handleFill } from "../../../scripts/bridge/capabilities/blocks/fill";

describe("edit_blocks fill", () => {
  beforeEach(() => {
    __resetBlocks();
    __setPlayers([]);
  });

  it("defaults an absolute fill to the current player's dimension", async () => {
    __setPlayers([{ name: "Alex", location: { x: 0, y: 64, z: 0 }, dimensionId: "minecraft:nether" }]);
    __setBlock("minecraft:nether", 0, 64, 0, { typeId: "minecraft:air", isAir: true });

    const result = await handleFill({
      mode: "fill",
      coordinate_mode: "absolute",
      from: { x: 0, y: 64, z: 0 },
      to: { x: 0, y: 64, z: 0 },
      type_id: "minecraft:stone",
      player_name: "Alex",
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.dimension).toBe("minecraft:nether");
    expect(result.payload.repairs_applied).toContainEqual(
      expect.objectContaining({ reason: "current_player_dimension_default" })
    );
  });

  it("fills air only by default and skips non-air", async () => {
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:air", isAir: true });
    __setBlock("minecraft:overworld", 1, 64, 0, { typeId: "minecraft:dirt" });
    __setBlock("minecraft:overworld", 0, 64, 1, { typeId: "minecraft:air", isAir: true });
    __setBlock("minecraft:overworld", 1, 64, 1, { typeId: "minecraft:air", isAir: true });

    const result = await handleFill({
      mode: "fill",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      from: { x: 0, y: 64, z: 0 },
      to: { x: 1, y: 64, z: 1 },
      type_id: "minecraft:stone",
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.skipped).toBe(1);
    expect(result.payload.changed_count).toBe(3);
    expect(result.payload.volume).toBe(4);
  });

  it("uses expected_previous as include filter", async () => {
    __setBlock("minecraft:overworld", 5, 64, 5, { typeId: "minecraft:dirt" });
    __setBlock("minecraft:overworld", 6, 64, 5, { typeId: "minecraft:stone" });
    __setBlock("minecraft:overworld", 5, 64, 6, { typeId: "minecraft:dirt" });
    __setBlock("minecraft:overworld", 6, 64, 6, { typeId: "minecraft:dirt" });

    const result = await handleFill({
      mode: "fill",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      from: { x: 5, y: 64, z: 5 },
      to: { x: 6, y: 64, z: 6 },
      type_id: "minecraft:sand",
      expected_previous: { type_id: "minecraft:dirt" },
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.skipped).toBe(1);
    expect(result.payload.changed_count).toBe(3);
  });

  it("rejects protected blocks in fill set", async () => {
    __setBlock("minecraft:overworld", 0, 70, 0, { typeId: "minecraft:air", isAir: true });
    __setBlock("minecraft:overworld", 1, 70, 0, {
      typeId: "minecraft:chest",
      components: { inventory: {} },
    });

    const result = await handleFill({
      mode: "fill",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      from: { x: 0, y: 70, z: 0 },
      to: { x: 1, y: 70, z: 0 },
      type_id: "minecraft:stone",
      replace_any: true,
    });

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("PROTECTED_BLOCK");
    expect(result.payload.hint ?? "").not.toContain("any");
  });

  it("enforces fill volume limits", async () => {
    const result = await handleFill({
      mode: "fill",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      from: { x: 0, y: 0, z: 0 },
      to: { x: 50, y: 50, z: 50 },
      type_id: "minecraft:stone",
      max_fill_volume: 4096,
    });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("LIMIT_EXCEEDED");
  });

  it("preflight returns locked matched targets without writing", async () => {
    __setBlock("minecraft:overworld", 0, 80, 0, { typeId: "minecraft:air", isAir: true });
    __setBlock("minecraft:overworld", 1, 80, 0, { typeId: "minecraft:air", isAir: true });

    const result = await handleFill({
      mode: "fill",
      coordinate_mode: "absolute",
      dimension: "overworld",
      from: { x: 0, y: 80, z: 0 },
      to: { x: 1, y: 80, z: 0 },
      type_id: "minecraft:stone",
      phase: "preflight",
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.ready).toBe(true);
    expect((result.payload.locked_targets as unknown[]).length).toBe(2);
  });

  it("preflight zero-match on homogeneous blocks suggests the observed expect", async () => {
    // Glass-replaces-grass scenario: default air-only policy, all cells are grass.
    BlockTypes.__add("minecraft:glass");
    BlockTypes.__add("minecraft:grass_block");
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:grass_block" });
    __setBlock("minecraft:overworld", 1, 64, 0, { typeId: "minecraft:grass_block" });

    const result = await handleFill({
      mode: "fill",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      from: { x: 0, y: 64, z: 0 },
      to: { x: 1, y: 64, z: 0 },
      type_id: "minecraft:glass",
      phase: "preflight",
    });

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("PRECONDITION_FAILED");
    expect(result.payload.matched_count).toBe(0);
    expect(result.payload.actual_type_counts).toMatchObject({
      "minecraft:grass_block": 2,
    });
    expect(result.payload.hint).toContain("minecraft:grass_block");
    expect(result.payload.hint).toContain("any");
    expect(result.payload.hint).not.toContain("设为 air");
    for (const hidden of ["replace_any", "expected_previous", "locked_targets", "phase"]) {
      expect(result.payload.hint).not.toContain(hidden);
    }
  });

  it("preflight zero-match on mixed blocks lists exact and any choices", async () => {
    BlockTypes.__add("minecraft:glass");
    BlockTypes.__add("minecraft:stone");
    BlockTypes.__add("minecraft:dirt");
    __setBlock("minecraft:overworld", 0, 65, 0, { typeId: "minecraft:stone" });
    __setBlock("minecraft:overworld", 1, 65, 0, { typeId: "minecraft:dirt" });

    const result = await handleFill({
      mode: "fill",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      from: { x: 0, y: 65, z: 0 },
      to: { x: 1, y: 65, z: 0 },
      type_id: "minecraft:glass",
      phase: "preflight",
    });

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("PRECONDITION_FAILED");
    expect(result.payload.actual_type_counts).toMatchObject({
      "minecraft:stone": 1,
      "minecraft:dirt": 1,
    });
    expect(result.payload.hint).toContain("minecraft:stone");
    expect(result.payload.hint).toContain("minecraft:dirt");
    expect(result.payload.hint).toContain("any");
  });

  it("preflight all-already-target returns noop status with matched_count 0", async () => {
    BlockTypes.__add("minecraft:glass");
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:glass" });
    __setBlock("minecraft:overworld", 1, 64, 0, { typeId: "minecraft:glass" });

    const result = await handleFill({
      mode: "fill",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      from: { x: 0, y: 64, z: 0 },
      to: { x: 1, y: 64, z: 0 },
      type_id: "minecraft:glass",
      phase: "preflight",
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.status).toBe("noop");
    expect(result.payload.matched_count).toBe(0);
    expect((result.payload.locked_targets as unknown[]).length).toBe(0);
  });

  it("execute all-already-target returns noop without writing", async () => {
    BlockTypes.__add("minecraft:glass");
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:glass" });
    __setBlock("minecraft:overworld", 1, 64, 0, { typeId: "minecraft:glass" });

    const result = await handleFill({
      mode: "fill",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      from: { x: 0, y: 64, z: 0 },
      to: { x: 1, y: 64, z: 0 },
      type_id: "minecraft:glass",
      phase: "execute",
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.status).toBe("noop");
    expect(result.payload.changed_count).toBe(0);
    expect(result.payload.verification).toMatchObject({ ok: true, method: "noop" });
  });

  it("tick-slices preflight with small cells_per_tick", async () => {
    for (let x = 0; x < 3; x++) {
      __setBlock("minecraft:overworld", x, 90, 0, { typeId: "minecraft:air", isAir: true });
    }
    const result = await handleFill({
      mode: "fill",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      from: { x: 0, y: 90, z: 0 },
      to: { x: 2, y: 90, z: 0 },
      type_id: "minecraft:cobblestone",
      cells_per_tick: 1,
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.changed_count).toBe(3);
  });
});
