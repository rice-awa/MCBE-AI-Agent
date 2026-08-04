import type { BlockSnapshot } from "./types";

type BlockLike = {
  dimension?: { id?: string };
  x?: number;
  y?: number;
  z?: number;
  location?: { x: number; y: number; z: number };
  typeId?: string;
  permutation?: {
    getAllStates?: () => Record<string, string | number | boolean>;
  };
  isWaterlogged?: boolean;
  isAir?: boolean;
  isLiquid?: boolean;
};

/**
 * Build a compact construction snapshot for a block.
 */
export function buildBlockSnapshot(
  block: BlockLike,
  dimensionId?: string,
  coords?: { x: number; y: number; z: number },
): BlockSnapshot {
  const x = coords?.x ?? block.x ?? block.location?.x ?? 0;
  const y = coords?.y ?? block.y ?? block.location?.y ?? 0;
  const z = coords?.z ?? block.z ?? block.location?.z ?? 0;
  const states =
    typeof block.permutation?.getAllStates === "function"
      ? { ...block.permutation.getAllStates() }
      : {};

  return {
    dimension: dimensionId ?? block.dimension?.id ?? "minecraft:overworld",
    x,
    y,
    z,
    type_id: block.typeId ?? "minecraft:air",
    states,
    waterlogged: Boolean(block.isWaterlogged),
    is_air: Boolean(block.isAir),
    is_liquid: Boolean(block.isLiquid),
  };
}

/** Compare expected target type/states against actual after write. */
export function matchesTargetPermutation(
  snapshot: BlockSnapshot,
  typeId: string,
  states?: Record<string, string | number | boolean>,
): boolean {
  if (snapshot.type_id !== typeId) return false;
  if (!states) return true;
  for (const [key, value] of Object.entries(states)) {
    if (snapshot.states[key] !== value) return false;
  }
  return true;
}

/** Check expected_previous partial match (type + declared states). */
export function matchesExpectedPrevious(
  snapshot: BlockSnapshot,
  expected: { type_id: string; states?: Record<string, string | number | boolean> },
): boolean {
  if (snapshot.type_id !== expected.type_id) return false;
  const states = expected.states ?? {};
  for (const [key, value] of Object.entries(states)) {
    if (snapshot.states[key] !== value) return false;
  }
  return true;
}
