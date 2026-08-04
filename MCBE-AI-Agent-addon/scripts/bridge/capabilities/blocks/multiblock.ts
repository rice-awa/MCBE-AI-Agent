/**
 * Multiblock block safety (spec issue 05 §6/§7).
 *
 * Some MCBE blocks occupy more than one cell or have a multi-part structure
 * (doors, beds, tall plants). Placing them via a single ``setPermutation``
 * writes only one half and silently produces a broken structure, and
 * verifying only that one cell would over-claim success.
 *
 * This check only constrains the **Script API single-cell write path**
 * (``setPermutation`` writes only one permutation). The
 * **command path** (``/setblock`` / ``/fill``) has placed the full
 * double-block structure correctly since Bedrock 1.26.10
 * (Microsoft Update1.26.10), so ``fallback_allowed`` is now computed by
 * the host using a unified rule and is no longer assumed ``false`` here.
 *
 * The list is intentionally a small, stable, suffix-based heuristic rather
 * than an exhaustive registry: door and bed variants all share the ``_door``
 * / ``_bed`` suffixes, and the tall-plant set is closed and well-known.
 */

const TALL_PLANT_IDS: ReadonlySet<string> = new Set([
  "minecraft:tall_grass",
  "minecraft:large_fern",
  "minecraft:sunflower",
  "minecraft:lilac",
  "minecraft:rose_bush",
  "minecraft:peony",
]);

/**
 * Return true when ``typeId`` is a block that cannot be correctly placed or
 * verified as a single cell. Only ``minecraft:`` namespaced ids are considered.
 */
export function isMultiblockBlock(typeId: string | undefined | null): boolean {
  if (typeof typeId !== "string" || !typeId) {
    return false;
  }
  if (TALL_PLANT_IDS.has(typeId)) {
    return true;
  }
  if (!typeId.startsWith("minecraft:")) {
    return false;
  }
  const name = typeId.slice("minecraft:".length);
  return name.endsWith("_door") || name.endsWith("_bed");
}
