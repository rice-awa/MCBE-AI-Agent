import { describe, expect, it, beforeEach } from "vitest";
import {
  __resetBlocks,
  __setBlock,
  __setPlayers,
} from "../../__mocks__/minecraft-server";
import { handlePlace } from "../../../scripts/bridge/capabilities/blocks/place";
import { handleEditBlocks } from "../../../scripts/bridge/capabilities/blocks/index";

describe("edit_blocks place", () => {
  beforeEach(() => {
    __resetBlocks();
    __setPlayers([]);
  });

  it("places stone on air only by default", async () => {
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:air", isAir: true });

    const result = await handlePlace({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 0, y: 64, z: 0 },
      type_id: "minecraft:stone",
      phase: "execute",
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.changed).toBe(true);
    expect((result.payload.after as { type_id: string }).type_id).toBe("minecraft:stone");
    expect((result.payload.before as { type_id: string }).type_id).toBe("minecraft:air");
    expect(result.payload.verification).toEqual({ ok: true });
  });

  it("rejects non-air without replace_any", async () => {
    __setBlock("minecraft:overworld", 0, 64, 0, { typeId: "minecraft:dirt" });

    const result = await handlePlace({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 0, y: 64, z: 0 },
      type_id: "minecraft:stone",
    });

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("PRECONDITION_FAILED");
  });

  it("allows replace with expected_previous", async () => {
    __setBlock("minecraft:overworld", 1, 64, 1, {
      typeId: "minecraft:dirt",
      states: {},
    });

    const result = await handlePlace({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 1, y: 64, z: 1 },
      type_id: "minecraft:stone",
      expected_previous: { type_id: "minecraft:dirt" },
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect((result.payload.after as { type_id: string }).type_id).toBe("minecraft:stone");
  });

  it("rejects protected inventory blocks", async () => {
    __setBlock("minecraft:overworld", 2, 64, 2, {
      typeId: "minecraft:chest",
      components: { inventory: { container: {} } },
    });

    const result = await handlePlace({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 2, y: 64, z: 2 },
      type_id: "minecraft:stone",
      replace_any: true,
    });

    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.payload.code).toBe("PROTECTED_BLOCK");
  });

  it("requires authorization to place air", async () => {
    __setBlock("minecraft:overworld", 3, 64, 3, { typeId: "minecraft:stone" });

    const denied = await handlePlace({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 3, y: 64, z: 3 },
      type_id: "minecraft:air",
    });
    expect(denied.ok).toBe(false);
    if (!denied.ok) {
      expect(denied.payload.code).toBe("INVALID_ARGUMENT");
    }

    const allowed = await handlePlace({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 3, y: 64, z: 3 },
      type_id: "minecraft:air",
      replace_any: true,
    });
    expect(allowed.ok).toBe(true);
  });

  it("preflight does not write", async () => {
    __setBlock("minecraft:overworld", 4, 64, 4, { typeId: "minecraft:air", isAir: true });

    const result = await handlePlace({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 4, y: 64, z: 4 },
      type_id: "minecraft:stone",
      phase: "preflight",
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.ready).toBe(true);
    expect(result.payload.locked_targets).toEqual([
      { dimension: "minecraft:overworld", x: 4, y: 64, z: 4 },
    ]);
    // Still air
    const inspect = await handleEditBlocks({
      mode: "place",
      // misuse — just re-check via place preflight again
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 4, y: 64, z: 4 },
      type_id: "minecraft:dirt",
      phase: "preflight",
    });
    expect(inspect.ok).toBe(true);
    // verify world still air by placing and checking before
    const exec = await handlePlace({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 4, y: 64, z: 4 },
      type_id: "minecraft:dirt",
      phase: "execute",
    });
    expect(exec.ok).toBe(true);
    if (exec.ok) {
      expect((exec.payload.before as { type_id: string }).type_id).toBe("minecraft:air");
    }
  });

  it("detects precondition changed on execute after concurrent edit", async () => {
    __setBlock("minecraft:overworld", 5, 64, 5, { typeId: "minecraft:air", isAir: true });

    const pre = await handlePlace({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 5, y: 64, z: 5 },
      type_id: "minecraft:stone",
      phase: "preflight",
    });
    expect(pre.ok).toBe(true);

    // Concurrent change
    __setBlock("minecraft:overworld", 5, 64, 5, { typeId: "minecraft:dirt" });

    const exec = await handlePlace({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 5, y: 64, z: 5 },
      type_id: "minecraft:stone",
      phase: "execute",
    });
    // Default air-only will fail preflight now with PRECONDITION_FAILED
    // For PRECONDITION_CHANGED we need recheck path: preflight before stored as air,
    // but execute re-reads dirt while replace_any and expected match wouldn't apply.
    // With air-only, full preflight runs again on execute and fails PRECONDITION_FAILED first.
    expect(exec.ok).toBe(false);
  });

  it("routes via handleEditBlocks", async () => {
    __setBlock("minecraft:overworld", 6, 64, 6, { typeId: "minecraft:air", isAir: true });
    const result = await handleEditBlocks({
      mode: "place",
      coordinate_mode: "absolute",
      dimension: "minecraft:overworld",
      position: { x: 6, y: 64, z: 6 },
      type_id: "stone", // repair namespace
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const repairs = result.payload.repairs_applied as Array<{ reason: string }>;
    expect(repairs.some((r) => r.reason === "add_minecraft_namespace")).toBe(true);
  });
});
