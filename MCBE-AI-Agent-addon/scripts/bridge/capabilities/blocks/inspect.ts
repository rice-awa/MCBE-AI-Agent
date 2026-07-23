import { world, BlockPermutation } from "@minecraft/server";

import {
  DEFAULT_MAX_POSITIONS,
  HARD_MAX_DISCRETE,
  SCHEMA_VERSION,
  fail,
  ok,
  type AbsolutePosition,
  type BridgeResult,
  type CoordinateMode,
  type PositionInput,
  type RelativePosition,
  type RepairApplied,
} from "./types";
import { repairDimension } from "./repair";
import {
  floorAbsolutePosition,
  footBlockOrigin,
  resolveRelativePosition,
  snapYawToCardinal,
  type CardinalFacing,
} from "./coords";
import { buildBlockSnapshot } from "./snapshot";

type InspectPayload = {
  coordinate_mode?: CoordinateMode;
  dimension?: string;
  position?: PositionInput;
  positions?: PositionInput[];
  player_name?: string;
  max_positions?: number;
};

function isAbsolutePos(p: PositionInput): p is AbsolutePosition {
  return (
    p !== null &&
    typeof p === "object" &&
    "x" in p &&
    "y" in p &&
    "z" in p &&
    typeof (p as AbsolutePosition).x === "number"
  );
}

function isRelativePos(p: PositionInput): p is RelativePosition {
  return (
    p !== null &&
    typeof p === "object" &&
    "forward" in p &&
    "right" in p &&
    "up" in p
  );
}

function classifyError(error: unknown): { code: "UNLOADED_CHUNK" | "OUT_OF_BOUNDS" | "INTERNAL_ERROR"; message: string } {
  const name = error instanceof Error ? error.name : "";
  const message = error instanceof Error ? error.message : String(error);
  if (
    name === "LocationInUnloadedChunkError" ||
    /unloaded.?chunk/i.test(message)
  ) {
    return { code: "UNLOADED_CHUNK", message };
  }
  if (
    name === "LocationOutOfWorldBoundariesError" ||
    /out.?of.?world|boundar/i.test(message)
  ) {
    return { code: "OUT_OF_BOUNDS", message };
  }
  return { code: "INTERNAL_ERROR", message };
}

export type ResolvedTarget = {
  dimension: string;
  x: number;
  y: number;
  z: number;
};

export type PlayerAnchor = {
  dimension: string;
  origin: AbsolutePosition;
  facing: CardinalFacing;
  player_name: string;
};

export function resolvePlayerAnchor(playerName: string): BridgeResult<PlayerAnchor> {
  const name = playerName?.trim();
  if (!name) {
    return fail("INVALID_ARGUMENT", "player_name is required for player_relative mode");
  }
  const players = world.getPlayers({ name });
  if (!players.length) {
    return fail("INVALID_ARGUMENT", `player not found: ${name}`);
  }
  const player = players[0] as {
    location: { x: number; y: number; z: number };
    dimension: { id: string };
    getRotation?: () => { x: number; y: number };
  };
  const yaw =
    typeof player.getRotation === "function" ? player.getRotation().y : 0;
  const facing = snapYawToCardinal(yaw);
  return ok({
    dimension: player.dimension.id,
    origin: footBlockOrigin(player.location),
    facing,
    player_name: name,
  });
}

export function collectPositions(payload: {
  position?: PositionInput;
  positions?: PositionInput[];
}): PositionInput[] | BridgeResult<never> {
  if (payload.positions !== undefined) {
    if (!Array.isArray(payload.positions)) {
      return fail("INVALID_ARGUMENT", "positions must be an array");
    }
    return payload.positions;
  }
  if (payload.position !== undefined) {
    return [payload.position];
  }
  return fail("INVALID_ARGUMENT", "position or positions is required");
}

export function resolveTargets(
  coordinateMode: CoordinateMode,
  dimensionRaw: string | undefined,
  positions: PositionInput[],
  playerName: string | undefined,
  repairs: RepairApplied[],
): BridgeResult<{
  targets: ResolvedTarget[];
  dimension: string;
  facing?: CardinalFacing;
  player_origin?: AbsolutePosition;
  player_name?: string;
}> {
  if (coordinateMode === "absolute") {
    const dimension = repairDimension(dimensionRaw, repairs);
    if (!dimension) {
      return fail("INVALID_ARGUMENT", "dimension is required for absolute coordinate_mode");
    }
    const targets: ResolvedTarget[] = [];
    for (let i = 0; i < positions.length; i++) {
      const p = positions[i];
      if (!isAbsolutePos(p)) {
        return fail(
          "INVALID_COORDINATE",
          `positions[${i}] must be absolute {x,y,z}`,
          { index: i },
        );
      }
      if (![p.x, p.y, p.z].every((n) => typeof n === "number" && Number.isFinite(n))) {
        return fail("INVALID_COORDINATE", `positions[${i}] has non-finite coordinates`, {
          index: i,
        });
      }
      const floored = floorAbsolutePosition(p, `positions[${i}]`, repairs);
      targets.push({ dimension, ...floored });
    }
    return ok({ targets, dimension });
  }

  if (coordinateMode === "player_relative") {
    const anchorResult = resolvePlayerAnchor(playerName ?? "");
    if (!anchorResult.ok) return anchorResult;
    const anchor = anchorResult.payload;
    const targets: ResolvedTarget[] = [];
    for (let i = 0; i < positions.length; i++) {
      const p = positions[i];
      if (!isRelativePos(p)) {
        return fail(
          "INVALID_COORDINATE",
          `positions[${i}] must be relative {forward,right,up}`,
          { index: i },
        );
      }
      if (
        ![p.forward, p.right, p.up].every(
          (n) => typeof n === "number" && Number.isFinite(n),
        )
      ) {
        return fail("INVALID_COORDINATE", `positions[${i}] has non-finite offsets`, {
          index: i,
        });
      }
      // Report floor on relative offsets
      const f = Math.floor(p.forward);
      const r = Math.floor(p.right);
      const u = Math.floor(p.up);
      if (f !== p.forward) {
        repairs.push({
          field: `positions[${i}].forward`,
          from: p.forward,
          to: f,
          reason: "math_floor",
        });
      }
      if (r !== p.right) {
        repairs.push({
          field: `positions[${i}].right`,
          from: p.right,
          to: r,
          reason: "math_floor",
        });
      }
      if (u !== p.up) {
        repairs.push({
          field: `positions[${i}].up`,
          from: p.up,
          to: u,
          reason: "math_floor",
        });
      }
      const abs = resolveRelativePosition(anchor.origin, anchor.facing, {
        forward: f,
        right: r,
        up: u,
      });
      targets.push({ dimension: anchor.dimension, ...abs });
    }
    return ok({
      targets,
      dimension: anchor.dimension,
      facing: anchor.facing,
      player_origin: anchor.origin,
      player_name: anchor.player_name,
    });
  }

  return fail(
    "INVALID_ARGUMENT",
    `coordinate_mode must be absolute or player_relative, got: ${coordinateMode}`,
  );
}

export function getBlockSafe(
  dimensionId: string,
  location: AbsolutePosition,
): BridgeResult<{ block: ReturnType<typeof getBlockFromDim>; dimensionId: string }> {
  try {
    const dim = world.getDimension(dimensionId);
    const block = dim.getBlock(location);
    if (!block) {
      return fail("UNLOADED_CHUNK", "block unavailable (unloaded chunk or invalid location)", {
        dimension: dimensionId,
        ...location,
      });
    }
    if ("isValid" in block && block.isValid === false) {
      return fail("UNLOADED_CHUNK", "block reference is invalid", {
        dimension: dimensionId,
        ...location,
      });
    }
    return ok({ block, dimensionId });
  } catch (error) {
    const classified = classifyError(error);
    return fail(classified.code, classified.message, {
      dimension: dimensionId,
      ...location,
    });
  }
}

function getBlockFromDim(_location: AbsolutePosition) {
  // type helper only
  return null as unknown as {
    typeId: string;
    isAir: boolean;
    isLiquid: boolean;
    isWaterlogged: boolean;
    isValid?: boolean;
    permutation: {
      getAllStates: () => Record<string, string | number | boolean>;
    };
    getComponent: (id: string) => unknown;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    setPermutation: (p: any) => void;
    setType?: (t: string) => void;
    x?: number;
    y?: number;
    z?: number;
    location?: AbsolutePosition;
    dimension?: { id: string };
  };
}

export type WorldBlock = NonNullable<
  ReturnType<typeof getBlockSafe> extends BridgeResult<infer T>
    ? T extends { block: infer B }
      ? B
      : never
    : never
>;

export function resolvePermutation(
  typeId: string,
  states?: Record<string, string | number | boolean>,
): BridgeResult<{ permutation: unknown; type_id: string }> {
  try {
    const permutation = states
      ? BlockPermutation.resolve(typeId, states as never)
      : BlockPermutation.resolve(typeId);
    return ok({ permutation, type_id: typeId });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (/state|permutation|invalid/i.test(message)) {
      return fail("STATE_INVALID", message, { type_id: typeId, states });
    }
    return fail("BLOCK_UNKNOWN", message, { type_id: typeId });
  }
}

export async function handleInspectBlock(
  payload: InspectPayload,
): Promise<BridgeResult<Record<string, unknown>>> {
  const repairs: RepairApplied[] = [];
  const coordinateMode = (payload.coordinate_mode ?? "absolute") as CoordinateMode;
  const collected = collectPositions(payload);
  if (!Array.isArray(collected)) {
    return collected;
  }
  const maxPositions = Math.min(
    payload.max_positions ?? DEFAULT_MAX_POSITIONS,
    HARD_MAX_DISCRETE,
  );
  if (collected.length === 0) {
    return fail("INVALID_ARGUMENT", "at least one position is required");
  }
  if (collected.length > maxPositions) {
    return fail("LIMIT_EXCEEDED", `positions exceed limit ${maxPositions}`, {
      count: collected.length,
      max: maxPositions,
    });
  }

  const resolved = resolveTargets(
    coordinateMode,
    payload.dimension,
    collected,
    payload.player_name,
    repairs,
  );
  if (!resolved.ok) return resolved;

  const blocks = [];
  for (const target of resolved.payload.targets) {
    const blockResult = getBlockSafe(target.dimension, {
      x: target.x,
      y: target.y,
      z: target.z,
    });
    if (!blockResult.ok) return blockResult;
    blocks.push(
      buildBlockSnapshot(blockResult.payload.block, target.dimension, {
        x: target.x,
        y: target.y,
        z: target.z,
      }),
    );
  }

  return ok({
    schema_version: SCHEMA_VERSION,
    ok: true,
    blocks,
    repairs_applied: repairs,
    coordinate_mode: coordinateMode,
    dimension: resolved.payload.dimension,
    facing: resolved.payload.facing,
    player_origin: resolved.payload.player_origin,
    player_name: resolved.payload.player_name,
    targets: resolved.payload.targets,
  });
}
