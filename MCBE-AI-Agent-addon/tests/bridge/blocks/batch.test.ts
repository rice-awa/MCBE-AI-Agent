import { describe, expect, it, beforeEach } from "vitest";
import {
  __resetBlocks,
  __setBlock,
  __setPlayers,
} from "../../__mocks__/minecraft-server";
import { handleBatch } from "../../../scripts/bridge/capabilities/blocks/batch";

describe("edit_blocks batch", () => {
  beforeEach(() => {
    __resetBlocks();
    __setPlayers([]);
  });

  it("places same type at multiple positions", async () => {
    for (const [x, z] of [
      [0, 0],
      [1, 0],
      [2, 0],
    ]) {
      __setBlock("minecraft:overworld", x, 64, z, { typeId: "minecraft:air", isAir: true });
    }

    const result = await handleBatch({
      mode: "batch",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      positions: [
        { x: 0, y: 64, z: 0 },
        { x: 1, y: 64, z: 0 },
        { x: 2, y: 64, z: 0 },
      ],
      type_id: "minecraft:stone",
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.changed_count).toBe(3);
    expect(result.payload.verification).toMatchObject({ ok: true });
  });

  it("fails entire batch on preflight without writes", async () => {
    __setBlock("minecraft:overworld", 0, 65, 0, { typeId: "minecraft:air", isAir: true });
    __setBlock("minecraft:overworld", 1, 65, 0, { typeId: "minecraft:dirt" });

    const result = await handleBatch({
      mode: "batch",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      positions: [
        { x: 0, y: 65, z: 0 },
        { x: 1, y: 65, z: 0 },
      ],
      type_id: "minecraft:stone",
    });

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("PRECONDITION_FAILED");
    // first cell still air
    // re-run place check by inspecting via batch preflight on air only
    const airOnly = await handleBatch({
      mode: "batch",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      positions: [{ x: 0, y: 65, z: 0 }],
      type_id: "minecraft:cobblestone",
      phase: "preflight",
    });
    expect(airOnly.ok).toBe(true);
  });

  it("rolls back on mid-execution failure", async () => {
    __setBlock("minecraft:overworld", 10, 64, 10, { typeId: "minecraft:air", isAir: true });
    __setBlock("minecraft:overworld", 11, 64, 10, { typeId: "minecraft:air", isAir: true });

    // Make second write fail by overriding setPermutation after first success is hard;
    // instead use invalid permutation via __invalid__ state on second only — but shared states.
    // Use a custom approach: poison the second block's setPermutation.
    const block2 = __setBlock("minecraft:overworld", 11, 64, 10, {
      typeId: "minecraft:air",
      isAir: true,
    });
    let callCount = 0;
    const original = block2.setPermutation.bind(block2);
    block2.setPermutation = (perm) => {
      callCount += 1;
      if (callCount >= 1) {
        // first write is on block1; this is block2
        throw new Error("simulated write failure");
      }
      original(perm);
    };

    // Actually first block is 10,10 - second is 11,10. Only poison second.
    // reset callCount so first call on block2 throws
    callCount = 0;
    block2.setPermutation = () => {
      throw new Error("simulated write failure");
    };

    const result = await handleBatch({
      mode: "batch",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      positions: [
        { x: 10, y: 64, z: 10 },
        { x: 11, y: 64, z: 10 },
      ],
      type_id: "minecraft:stone",
    });

    expect(result.ok).toBe(false);
    if (result.ok) return;
    // First block should be rolled back to air
    const rollback = result.payload.rollback as Array<{ ok: boolean }>;
    expect(Array.isArray(rollback)).toBe(true);
    // Verify block1 restored
    const block1 = __setBlock; // keep lint happy
    void block1;
    // Re-read via world mock: use getBlockSafe path by running place preflight
    const { getBlockSafe } = await import(
      "../../../scripts/bridge/capabilities/blocks/inspect"
    );
    const b1 = getBlockSafe("minecraft:overworld", { x: 10, y: 64, z: 10 });
    expect(b1.ok).toBe(true);
    if (b1.ok) {
      expect(b1.payload.block.typeId).toBe("minecraft:air");
    }
  });

  it("enforces discrete limits", async () => {
    const positions = Array.from({ length: 300 }, (_, i) => ({
      x: i,
      y: 70,
      z: 0,
    }));
    const result = await handleBatch({
      mode: "batch",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      positions,
      type_id: "minecraft:stone",
      max_discrete: 256,
    });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("LIMIT_EXCEEDED");
  });
});
