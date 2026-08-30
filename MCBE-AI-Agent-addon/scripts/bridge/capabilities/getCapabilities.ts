/**
 * Capability handshake for Add-on feature discovery.
 * Python host caches this per connection and hides unsupported tools.
 */
export function handleGetCapabilities(_payload: Record<string, unknown> = {}): {
  ok: true;
  payload: {
    schema_version: string;
    capabilities: {
      block_ops: {
        version: number;
        inspect: boolean;
        place: boolean;
        batch: boolean;
        fill: boolean;
        /** "command_fallback": multiblock blocks (doors, beds, tall plants) cannot be
         * written by the Script API single-cell path (setPermutation writes one half),
         * but the command path (/setblock /fill) places the full double-block
         * structure since Bedrock 1.26.10 (Microsoft Update1.26.10). */
        multiblock_placement: "command_fallback";
      };
    };
  };
} {
  const registered = new Set(projectCapabilityAdvertisements().map((item) => item.name));
  return {
    ok: true,
    payload: {
      schema_version: "1",
      capabilities: {
        block_ops: {
          version: 1,
          inspect: registered.has("inspect_block"),
          place: registered.has("edit_blocks"),
          batch: registered.has("edit_blocks"),
          fill: registered.has("edit_blocks"),
          multiblock_placement: "command_fallback",
        },
      },
    },
  };
}
import { projectCapabilityAdvertisements } from "./registry";
