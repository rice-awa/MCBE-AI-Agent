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
