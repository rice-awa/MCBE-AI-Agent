import { canonicalizeDimension } from "./dimensions";
import { floorAbsolutePosition, mathFloor } from "./coords";
import type { RepairApplied } from "./types";

/**
 * Levenshtein distance (edit distance) between two strings.
 */
export function editDistance(a: string, b: string): number {
  if (a === b) return 0;
  if (a.length === 0) return b.length;
  if (b.length === 0) return a.length;
  const prev = new Array<number>(b.length + 1);
  const curr = new Array<number>(b.length + 1);
  for (let j = 0; j <= b.length; j++) prev[j] = j;
  for (let i = 1; i <= a.length; i++) {
    curr[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      curr[j] = Math.min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost);
    }
    for (let j = 0; j <= b.length; j++) prev[j] = curr[j];
  }
  return prev[b.length];
}

export type BlockTypesLike = {
  getAll?: () => Array<{ id: string } | string>;
  get?: (id: string) => unknown;
};

/**
 * Fuzzy-correct vanilla block IDs only when exactly one candidate has edit distance 1.
 * Custom namespaces are never fuzzily corrected.
 */
export function fuzzyVanillaBlockId(
  typeId: string,
  registry: BlockTypesLike | undefined,
): string | undefined {
  if (!typeId.startsWith("minecraft:")) {
    return undefined;
  }
  if (!registry || typeof registry.getAll !== "function") {
    return undefined;
  }
  let all: string[];
  try {
    all = registry.getAll().map((item) => (typeof item === "string" ? item : item.id));
  } catch {
    return undefined;
  }
  const candidates = all.filter((id) => editDistance(typeId, id) === 1);
  if (candidates.length === 1) {
    return candidates[0];
  }
  return undefined;
}

export function repairTypeId(
  raw: unknown,
  repairs: RepairApplied[],
  registry?: BlockTypesLike,
): string | undefined {
  if (raw === undefined || raw === null) {
    return undefined;
  }
  let value = String(raw);
  const original = value;
  const trimmed = value.trim();
  if (trimmed !== value) {
    repairs.push({
      field: "type_id",
      from: value,
      to: trimmed,
      reason: "trim_whitespace",
    });
    value = trimmed;
  }
  if (value && !value.includes(":")) {
    const namespaced = `minecraft:${value}`;
    repairs.push({
      field: "type_id",
      from: value,
      to: namespaced,
      reason: "add_minecraft_namespace",
    });
    value = namespaced;
  }
  if (registry && typeof registry.get === "function") {
    try {
      const known = registry.get(value);
      if (known) {
        return value;
      }
    } catch {
      // fall through to fuzzy
    }
  }
  const fuzzy = fuzzyVanillaBlockId(value, registry);
  if (fuzzy && fuzzy !== value) {
    repairs.push({
      field: "type_id",
      from: value,
      to: fuzzy,
      reason: "fuzzy_vanilla_edit_distance_1",
    });
    return fuzzy;
  }
  // If original was empty after trim
  if (!value && original) {
    return value;
  }
  return value || undefined;
}

export function repairDimension(
  raw: unknown,
  repairs: RepairApplied[],
): string | undefined {
  if (raw === undefined || raw === null) {
    return undefined;
  }
  const result = canonicalizeDimension(String(raw));
  if (!result.dimension) {
    return undefined;
  }
  if (result.repaired) {
    repairs.push({
      field: "dimension",
      from: result.from ?? raw,
      to: result.dimension,
      reason: "canonicalize_dimension_alias",
    });
  }
  return result.dimension;
}

export function repairAbsoluteCoords(
  pos: { x: number; y: number; z: number },
  fieldPrefix: string,
  repairs: RepairApplied[],
): { x: number; y: number; z: number } {
  return floorAbsolutePosition(pos, fieldPrefix, repairs);
}

export { mathFloor };
