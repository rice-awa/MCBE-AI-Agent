import { PROTECTED_COMPONENT_IDS } from "./types";

type BlockWithComponent = {
  getComponent?: (id: string) => unknown;
  typeId?: string;
};

/**
 * Reject blocks that expose data-bearing components.
 * Returns the first matching component id, or undefined if safe.
 */
export function findProtectedComponent(block: BlockWithComponent): string | undefined {
  if (typeof block.getComponent !== "function") {
    return undefined;
  }
  for (const id of PROTECTED_COMPONENT_IDS) {
    try {
      const comp = block.getComponent(id);
      if (comp !== undefined && comp !== null) {
        return id;
      }
    } catch {
      // Invalid component access — treat as not present
    }
  }
  return undefined;
}

export function isProtectedBlock(block: BlockWithComponent): boolean {
  return findProtectedComponent(block) !== undefined;
}
