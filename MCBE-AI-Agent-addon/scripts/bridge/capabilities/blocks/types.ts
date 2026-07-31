/** Stable error codes shared with Python harness. */
export type BlockErrorCode =
  | "INVALID_ARGUMENT"
  | "INVALID_COORDINATE"
  | "BLOCK_UNKNOWN"
  | "STATE_INVALID"
  | "PROTECTED_BLOCK"
  | "PRECONDITION_FAILED"
  | "PRECONDITION_CHANGED"
  | "UNLOADED_CHUNK"
  | "OUT_OF_BOUNDS"
  | "LIMIT_EXCEEDED"
  | "ADDON_UNAVAILABLE"
  | "STATE_UNKNOWN"
  | "INTERNAL_ERROR";

export type CoordinateMode = "absolute" | "player_relative";

export type AbsolutePosition = { x: number; y: number; z: number };
export type RelativePosition = { forward: number; right: number; up: number };
export type PositionInput = AbsolutePosition | RelativePosition;

/** Box (cuboid) target shape for inspect / edit. */
export type BoxTarget = {
  from: PositionInput;
  to: PositionInput;
};

/** Unified target: non-empty positions XOR box. */
export type UnifiedTarget = {
  positions?: PositionInput[];
  box?: BoxTarget;
};

export type ExpectedPrevious = {
  type_id: string;
  states?: Record<string, string | number | boolean>;
};

export type LockedTarget = {
  dimension: string;
  x: number;
  y: number;
  z: number;
};

export type BlockSnapshot = {
  dimension: string;
  x: number;
  y: number;
  z: number;
  type_id: string;
  states: Record<string, string | number | boolean>;
  waterlogged: boolean;
  is_air: boolean;
  is_liquid: boolean;
};

/** Sample entry for an unloaded / unreadable cell in a bounded summary. */
export type UnknownSample = {
  x: number;
  y: number;
  z: number;
  status: "unknown" | "unloaded" | "out_of_bounds";
};

/** Bounded inspect summary for multi-point or box targets. */
export type InspectSummary = {
  bounds: { from: AbsolutePosition; to: AbsolutePosition };
  count: number;
  type_counts: Record<string, number>;
  unknown_count: number;
  samples: Array<BlockSnapshot | UnknownSample>;
};

export type RepairApplied = {
  field: string;
  from: unknown;
  to: unknown;
  reason: string;
};

export type BridgeSuccess<T> = { ok: true; payload: T };
export type BridgeFailure = {
  ok: false;
  payload: {
    code: BlockErrorCode;
    message: string;
    [key: string]: unknown;
  };
};
export type BridgeResult<T> = BridgeSuccess<T> | BridgeFailure;

export const DEFAULT_MAX_POSITIONS = 256;
export const HARD_MAX_DISCRETE = 1024;
export const DEFAULT_MAX_FILL_VOLUME = 4096;
export const HARD_MAX_FILL_VOLUME = 16384;
export const DEFAULT_CELLS_PER_TICK = 128;
export const SCHEMA_VERSION = "1";

// Inspect auto-summary thresholds (issue 02).
// When the resolved target count exceeds the threshold, inspect returns a
// bounded summary (bounds / count / type_counts / unknown_count / samples)
// instead of enumerating every snapshot.
export const DEFAULT_INSPECT_SUMMARY_THRESHOLD = 8;
export const HARD_MAX_INSPECT_SUMMARY_THRESHOLD = 64;
export const DEFAULT_INSPECT_SAMPLE_LIMIT = 8;
export const HARD_MAX_INSPECT_SAMPLE_LIMIT = 32;

export const PROTECTED_COMPONENT_IDS = [
  "minecraft:inventory",
  "inventory",
  "minecraft:sign",
  "sign",
  "minecraft:record_player",
  "record_player",
  "minecraft:fluid_container",
  "fluid_container",
  "minecraft:dynamic_properties",
  "dynamic_properties",
] as const;

export function fail(
  code: BlockErrorCode,
  message: string,
  details: Record<string, unknown> = {},
): BridgeFailure {
  return {
    ok: false,
    payload: {
      code,
      message,
      ...details,
    },
  };
}

export function ok<T>(payload: T): BridgeSuccess<T> {
  return { ok: true, payload };
}
