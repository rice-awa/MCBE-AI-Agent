/**
 * Block operations bridge capabilities.
 * Public surface: inspect_block + edit_blocks (mode: place|batch|fill)
 */

export { handleInspectBlock } from "./inspect";
export { handlePlace } from "./place";
export { handleBatch } from "./batch";
export { handleFill } from "./fill";
export type { EditBlocksPayload } from "./common";

import type { BridgeResult } from "./types";
import { fail } from "./types";
import { handlePlace } from "./place";
import { handleBatch } from "./batch";
import { handleFill } from "./fill";
import type { EditBlocksPayload } from "./common";

/**
 * Unified edit_blocks capability handler.
 */
export async function handleEditBlocks(
  payload: EditBlocksPayload,
): Promise<BridgeResult<Record<string, unknown>>> {
  const mode = payload.mode;
  if (mode === "place") {
    return handlePlace(payload);
  }
  if (mode === "batch") {
    return handleBatch(payload);
  }
  if (mode === "fill") {
    return handleFill(payload);
  }
  return fail(
    "INVALID_ARGUMENT",
    `edit_blocks mode must be place|batch|fill, got: ${String(mode)}`,
  );
}
