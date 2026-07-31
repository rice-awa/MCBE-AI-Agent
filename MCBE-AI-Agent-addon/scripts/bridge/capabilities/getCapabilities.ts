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
        /** "unsupported": multiblock blocks (doors, beds, tall plants) are
         * rejected as UNSUPPORTED_BLOCK_PLACEMENT until a full multi-cell
         * placement + verification path exists (spec issue 05 §6). */
        multiblock_placement: "unsupported";
      };
    };
  };
} {
  return {
    ok: true,
    payload: {
      schema_version: "1",
      capabilities: {
        block_ops: {
          version: 1,
          inspect: true,
          place: true,
          batch: true,
          fill: true,
          multiblock_placement: "unsupported",
        },
      },
    },
  };
}
