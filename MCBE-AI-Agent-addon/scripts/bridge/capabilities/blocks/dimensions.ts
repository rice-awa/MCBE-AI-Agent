/** Canonical vanilla dimension IDs. */
const VANILLA_ALIASES: Record<string, string> = {
  overworld: "minecraft:overworld",
  "minecraft:overworld": "minecraft:overworld",
  nether: "minecraft:nether",
  "the_nether": "minecraft:nether",
  "minecraft:nether": "minecraft:nether",
  "the nether": "minecraft:nether",
  end: "minecraft:the_end",
  the_end: "minecraft:the_end",
  "the end": "minecraft:the_end",
  "minecraft:the_end": "minecraft:the_end",
  "minecraft:end": "minecraft:the_end",
};

/**
 * Canonicalize vanilla dimension aliases.
 * Custom namespaced dimensions (contain `:`) pass through after trim.
 * Bare unknown ids without namespace are rejected by callers via getDimension.
 */
export function canonicalizeDimension(
  raw: string | undefined | null,
): { dimension?: string; repaired: boolean; from?: string } {
  if (raw === undefined || raw === null) {
    return { repaired: false };
  }
  const trimmed = String(raw).trim();
  if (!trimmed) {
    return { repaired: false };
  }
  const lower = trimmed.toLowerCase();
  const mapped = VANILLA_ALIASES[lower];
  if (mapped) {
    return {
      dimension: mapped,
      repaired: mapped !== trimmed,
      from: repairedFrom(trimmed, mapped),
    };
  }
  // Allow custom namespaced dimensions as-is (after trim)
  if (trimmed.includes(":")) {
    const repaired = trimmed !== raw;
    return { dimension: trimmed, repaired, from: repaired ? String(raw) : undefined };
  }
  // Bare non-vanilla aliases: treat as custom bare id (engine may reject)
  const repaired = trimmed !== raw;
  return { dimension: trimmed, repaired, from: repaired ? String(raw) : undefined };
}

function repairedFrom(from: string, to: string): string | undefined {
  return from !== to ? from : undefined;
}
