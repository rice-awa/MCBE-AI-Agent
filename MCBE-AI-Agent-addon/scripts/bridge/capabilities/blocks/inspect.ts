import { world, BlockPermutation } from "@minecraft/server";

import {
  DEFAULT_MAX_POSITIONS,
  DEFAULT_MAX_FILL_VOLUME,
  DEFAULT_INSPECT_SUMMARY_THRESHOLD,
  DEFAULT_INSPECT_SAMPLE_LIMIT,
  HARD_MAX_DISCRETE,
  HARD_MAX_FILL_VOLUME,
  HARD_MAX_INSPECT_SUMMARY_THRESHOLD,
  HARD_MAX_INSPECT_SAMPLE_LIMIT,
  SCHEMA_VERSION,
  fail,
  ok,
  type AbsolutePosition,
  type BlockSnapshot,
  type BoxTarget,
  type BridgeResult,
  type CoordinateMode,
  type InspectSummary,
  type PositionInput,
  type RelativePosition,
  type RepairApplied,
  type UnknownSample,
  type UnifiedTarget,
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
  /** Unified target (issue 02+): positions XOR box. */
  target?: UnifiedTarget;
  player_name?: string;
  max_positions?: number;
  max_fill_volume?: number;
  /** Inspect auto-summary: above this count, return a bounded summary. */
  inspect_summary_threshold?: number;
  /** Maximum sample count in bounded summary. */
  inspect_sample_limit?: number;
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
  return p !== null && typeof p === "object" && "forward" in p && "right" in p && "up" in p;
}

function classifyError(error: unknown): {
  code: "UNLOADED_CHUNK" | "OUT_OF_BOUNDS" | "INTERNAL_ERROR";
  message: string;
} {
  const name = error instanceof Error ? error.name : "";
  const message = error instanceof Error ? error.message : String(error);
  if (name === "LocationInUnloadedChunkError" || /unloaded.?chunk/i.test(message)) {
    return { code: "UNLOADED_CHUNK", message };
  }
  if (name === "LocationOutOfWorldBoundariesError" || /out.?of.?world|boundar/i.test(message)) {
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

export function resolveAbsoluteDimension(
  dimensionRaw: string | undefined,
  playerName: string | undefined,
  repairs: RepairApplied[]
): BridgeResult<{ dimension: string; anchor?: PlayerAnchor }> {
  const dimension = repairDimension(dimensionRaw, repairs);
  if (dimension) {
    return ok({ dimension });
  }
  const anchorResult = resolvePlayerAnchor(playerName ?? "");
  if (!anchorResult.ok) return anchorResult;
  repairs.push({
    field: "dimension",
    from: dimensionRaw,
    to: anchorResult.payload.dimension,
    reason: "current_player_dimension_default",
  });
  return ok({
    dimension: anchorResult.payload.dimension,
    anchor: anchorResult.payload,
  });
}

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
  const yaw = typeof player.getRotation === "function" ? player.getRotation().y : 0;
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
  repairs: RepairApplied[]
): BridgeResult<{
  targets: ResolvedTarget[];
  dimension: string;
  facing?: CardinalFacing;
  player_origin?: AbsolutePosition;
  player_name?: string;
}> {
  if (coordinateMode === "absolute") {
    const dimensionResult = resolveAbsoluteDimension(dimensionRaw, playerName, repairs);
    if (!dimensionResult.ok) return dimensionResult;
    const { dimension, anchor } = dimensionResult.payload;
    const targets: ResolvedTarget[] = [];
    for (let i = 0; i < positions.length; i++) {
      const p = positions[i];
      if (!isAbsolutePos(p)) {
        return fail("INVALID_COORDINATE", `positions[${i}] must be absolute {x,y,z}`, { index: i });
      }
      if (![p.x, p.y, p.z].every((n) => typeof n === "number" && Number.isFinite(n))) {
        return fail("INVALID_COORDINATE", `positions[${i}] has non-finite coordinates`, {
          index: i,
        });
      }
      const floored = floorAbsolutePosition(p, `positions[${i}]`, repairs);
      targets.push({ dimension, ...floored });
    }
    return ok({
      targets,
      dimension,
      facing: anchor?.facing,
      player_origin: anchor?.origin,
      player_name: anchor?.player_name,
    });
  }

  if (coordinateMode === "player_relative") {
    const anchorResult = resolvePlayerAnchor(playerName ?? "");
    if (!anchorResult.ok) return anchorResult;
    const anchor = anchorResult.payload;
    const targets: ResolvedTarget[] = [];
    for (let i = 0; i < positions.length; i++) {
      const p = positions[i];
      if (!isRelativePos(p)) {
        return fail("INVALID_COORDINATE", `positions[${i}] must be relative {forward,right,up}`, { index: i });
      }
      if (![p.forward, p.right, p.up].every((n) => typeof n === "number" && Number.isFinite(n))) {
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

  return fail("INVALID_ARGUMENT", `coordinate_mode must be absolute or player_relative, got: ${coordinateMode}`);
}

/** Resolve a single corner (absolute or relative) to an absolute target. */
export function resolveBoxCorner(
  coordinateMode: CoordinateMode,
  dimension: string,
  corner: PositionInput | undefined,
  field: string,
  anchor: PlayerAnchor | undefined,
  repairs: RepairApplied[]
): BridgeResult<ResolvedTarget> {
  if (!corner) {
    return fail("INVALID_ARGUMENT", `${field} is required for box target`);
  }
  if (coordinateMode === "absolute") {
    if (!isAbsolutePos(corner)) {
      return fail("INVALID_COORDINATE", `${field} must be absolute {x,y,z}`);
    }
    if (![corner.x, corner.y, corner.z].every((n) => typeof n === "number" && Number.isFinite(n))) {
      return fail("INVALID_COORDINATE", `${field} has non-finite coordinates`);
    }
    const floored = floorAbsolutePosition(corner, field, repairs);
    return ok({ dimension, ...floored });
  }
  if (!anchor) {
    return fail("INVALID_ARGUMENT", "player anchor required for player_relative box");
  }
  if (!isRelativePos(corner)) {
    return fail("INVALID_COORDINATE", `${field} must be relative {forward,right,up}`);
  }
  if (![corner.forward, corner.right, corner.up].every((n) => typeof n === "number" && Number.isFinite(n))) {
    return fail("INVALID_COORDINATE", `${field} has non-finite offsets`);
  }
  const f = Math.floor(corner.forward);
  const r = Math.floor(corner.right);
  const u = Math.floor(corner.up);
  if (f !== corner.forward) {
    repairs.push({ field: `${field}.forward`, from: corner.forward, to: f, reason: "math_floor" });
  }
  if (r !== corner.right) {
    repairs.push({ field: `${field}.right`, from: corner.right, to: r, reason: "math_floor" });
  }
  if (u !== corner.up) {
    repairs.push({ field: `${field}.up`, from: corner.up, to: u, reason: "math_floor" });
  }
  const abs = resolveRelativePosition(anchor.origin, anchor.facing, { forward: f, right: r, up: u });
  return ok({ dimension: anchor.dimension, ...abs });
}

/** Enumerate all cells in a normalized AABB (min/max ordered). */
export function enumerateBoxCells(fromAbs: AbsolutePosition, toAbs: AbsolutePosition): AbsolutePosition[] {
  const minX = Math.min(fromAbs.x, toAbs.x);
  const maxX = Math.max(fromAbs.x, toAbs.x);
  const minY = Math.min(fromAbs.y, toAbs.y);
  const maxY = Math.max(fromAbs.y, toAbs.y);
  const minZ = Math.min(fromAbs.z, toAbs.z);
  const maxZ = Math.max(fromAbs.z, toAbs.z);
  const cells: AbsolutePosition[] = [];
  for (let x = minX; x <= maxX; x++) {
    for (let y = minY; y <= maxY; y++) {
      for (let z = minZ; z <= maxZ; z++) {
        cells.push({ x, y, z });
      }
    }
  }
  return cells;
}

export function boxVolume(fromAbs: AbsolutePosition, toAbs: AbsolutePosition): number {
  return (
    (Math.abs(toAbs.x - fromAbs.x) + 1) * (Math.abs(toAbs.y - fromAbs.y) + 1) * (Math.abs(toAbs.z - fromAbs.z) + 1)
  );
}

export function getBlockSafe(
  dimensionId: string,
  location: AbsolutePosition
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
    setPermutation: (p: BlockPermutation) => void;
    setType?: (t: string) => void;
    x?: number;
    y?: number;
    z?: number;
    location?: AbsolutePosition;
    dimension?: { id: string };
  };
}

export type WorldBlock = NonNullable<
  ReturnType<typeof getBlockSafe> extends BridgeResult<infer T> ? (T extends { block: infer B } ? B : never) : never
>;

export function resolvePermutation(
  typeId: string,
  states?: Record<string, string | number | boolean>
): BridgeResult<{ permutation: BlockPermutation; type_id: string }> {
  try {
    const permutation = states ? BlockPermutation.resolve(typeId, states as never) : BlockPermutation.resolve(typeId);
    return ok({ permutation, type_id: typeId });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (/state|permutation|invalid/i.test(message)) {
      const detail: Record<string, unknown> = { type_id: typeId, states };
      // Bounded suggestion: valid state keys for this block type, taken from
      // the default permutation. Lets the model correct the state names rather
      // than fall back to command tools (spec issue 05 §4.3).
      try {
        const defaults = BlockPermutation.resolve(typeId).getAllStates();
        const keys = Object.keys(defaults ?? {});
        if (keys.length) {
          detail.valid_state_keys = keys.slice(0, 8);
        }
      } catch {
        // Block type itself unresolvable: no key suggestions; type_id is carried.
      }
      return fail("STATE_INVALID", message, detail);
    }
    return fail("BLOCK_UNKNOWN", message, { type_id: typeId });
  }
}

/** Classify a getBlockSafe failure into an UnknownSample status. */
function unknownStatusFromCode(code: string): UnknownSample["status"] {
  if (code === "OUT_OF_BOUNDS") return "out_of_bounds";
  if (code === "UNLOADED_CHUNK") return "unloaded";
  return "unknown";
}

/** Build a bounded summary for multi-point or box targets. */
function buildInspectSummary(
  snapshots: BlockSnapshot[],
  unknowns: UnknownSample[],
  fromAbs: AbsolutePosition,
  toAbs: AbsolutePosition,
  sampleLimit: number
): InspectSummary {
  const type_counts: Record<string, number> = {};
  for (const s of snapshots) {
    type_counts[s.type_id] = (type_counts[s.type_id] ?? 0) + 1;
  }
  // Samples: prefer unknowns first (they need attention), then fill with snapshots.
  const samples: Array<BlockSnapshot | UnknownSample> = [];
  for (const u of unknowns.slice(0, sampleLimit)) {
    samples.push(u);
  }
  const remaining = sampleLimit - samples.length;
  if (remaining > 0) {
    for (const s of snapshots.slice(0, remaining)) {
      samples.push(s);
    }
  }
  return {
    bounds: {
      from: {
        x: Math.min(fromAbs.x, toAbs.x),
        y: Math.min(fromAbs.y, toAbs.y),
        z: Math.min(fromAbs.z, toAbs.z),
      },
      to: {
        x: Math.max(fromAbs.x, toAbs.x),
        y: Math.max(fromAbs.y, toAbs.y),
        z: Math.max(fromAbs.z, toAbs.z),
      },
    },
    count: snapshots.length + unknowns.length,
    type_counts,
    unknown_count: unknowns.length,
    samples,
  };
}

export async function handleInspectBlock(payload: InspectPayload): Promise<BridgeResult<Record<string, unknown>>> {
  const repairs: RepairApplied[] = [];
  const coordinateMode = (payload.coordinate_mode ?? "absolute") as CoordinateMode;
  const maxPositions = Math.min(payload.max_positions ?? DEFAULT_MAX_POSITIONS, HARD_MAX_DISCRETE);
  const maxFillVolume = Math.min(payload.max_fill_volume ?? DEFAULT_MAX_FILL_VOLUME, HARD_MAX_FILL_VOLUME);
  const summaryThreshold = Math.min(
    payload.inspect_summary_threshold ?? DEFAULT_INSPECT_SUMMARY_THRESHOLD,
    HARD_MAX_INSPECT_SUMMARY_THRESHOLD
  );
  const sampleLimit = Math.min(
    payload.inspect_sample_limit ?? DEFAULT_INSPECT_SAMPLE_LIMIT,
    HARD_MAX_INSPECT_SAMPLE_LIMIT
  );

  // Unified target shape: target.positions or target.box (mutually exclusive).
  // Backward-compatible: also accept legacy position / positions.
  let targetShape: "positions" | "box";
  let positions: PositionInput[] | undefined;
  let box: BoxTarget | undefined;

  if (payload.target !== undefined) {
    if (
      typeof payload.target !== "object" ||
      payload.target === null ||
      (payload.target.positions === undefined && payload.target.box === undefined)
    ) {
      return fail("INVALID_ARGUMENT", "target must provide positions or box");
    }
    if (payload.target.positions !== undefined && payload.target.box !== undefined) {
      return fail("INVALID_ARGUMENT", "target.positions and target.box are mutually exclusive");
    }
    if (payload.target.positions !== undefined) {
      if (!Array.isArray(payload.target.positions)) {
        return fail("INVALID_ARGUMENT", "target.positions must be an array");
      }
      positions = payload.target.positions;
      targetShape = "positions";
    } else {
      const b = payload.target.box!;
      if (typeof b !== "object" || b === null || b.from === undefined || b.to === undefined) {
        return fail("INVALID_ARGUMENT", "target.box must provide from and to");
      }
      box = b;
      targetShape = "box";
    }
  } else {
    // Legacy path: position / positions.
    const collected = collectPositions(payload);
    if (!Array.isArray(collected)) {
      return collected;
    }
    positions = collected;
    targetShape = "positions";
  }

  if (targetShape === "positions") {
    if (!positions || positions.length === 0) {
      return fail("INVALID_ARGUMENT", "at least one position is required");
    }
    if (positions.length > maxPositions) {
      return fail("LIMIT_EXCEEDED", `positions exceed limit ${maxPositions}`, {
        count: positions.length,
        max: maxPositions,
      });
    }

    const resolved = resolveTargets(coordinateMode, payload.dimension, positions, payload.player_name, repairs);
    if (!resolved.ok) return resolved;

    // Single or few points: full snapshots (never summarized below threshold).
    if (resolved.payload.targets.length <= summaryThreshold) {
      const blocks: BlockSnapshot[] = [];
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
          })
        );
      }
      return ok({
        schema_version: SCHEMA_VERSION,
        ok: true,
        status: "inspected",
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

    // Many points: bounded summary.
    const snapshots: BlockSnapshot[] = [];
    const unknowns: UnknownSample[] = [];
    let minT = resolved.payload.targets[0];
    let maxT = resolved.payload.targets[0];
    for (const target of resolved.payload.targets) {
      minT = {
        x: Math.min(minT.x, target.x),
        y: Math.min(minT.y, target.y),
        z: Math.min(minT.z, target.z),
        dimension: minT.dimension,
      };
      maxT = {
        x: Math.max(maxT.x, target.x),
        y: Math.max(maxT.y, target.y),
        z: Math.max(maxT.z, target.z),
        dimension: maxT.dimension,
      };
      const blockResult = getBlockSafe(target.dimension, {
        x: target.x,
        y: target.y,
        z: target.z,
      });
      if (!blockResult.ok) {
        unknowns.push({
          x: target.x,
          y: target.y,
          z: target.z,
          status: unknownStatusFromCode(blockResult.payload.code),
        });
        continue;
      }
      snapshots.push(
        buildBlockSnapshot(blockResult.payload.block, target.dimension, {
          x: target.x,
          y: target.y,
          z: target.z,
        })
      );
    }
    const summary = buildInspectSummary(
      snapshots,
      unknowns,
      { x: minT.x, y: minT.y, z: minT.z },
      { x: maxT.x, y: maxT.y, z: maxT.z },
      sampleLimit
    );
    return ok({
      schema_version: SCHEMA_VERSION,
      ok: true,
      status: "inspected",
      summary,
      repairs_applied: repairs,
      coordinate_mode: coordinateMode,
      dimension: resolved.payload.dimension,
    });
  }

  // Box target shape.
  let anchor: PlayerAnchor | undefined;
  let dimension: string;
  if (coordinateMode === "absolute") {
    const dimensionResult = resolveAbsoluteDimension(payload.dimension, payload.player_name, repairs);
    if (!dimensionResult.ok) return dimensionResult;
    dimension = dimensionResult.payload.dimension;
    anchor = dimensionResult.payload.anchor;
  } else {
    const anchorResult = resolvePlayerAnchor(payload.player_name ?? "");
    if (!anchorResult.ok) return anchorResult;
    anchor = anchorResult.payload;
    dimension = anchor.dimension;
  }

  const fromResult = resolveBoxCorner(coordinateMode, dimension, box!.from, "target.box.from", anchor, repairs);
  if (!fromResult.ok) return fromResult;
  const toResult = resolveBoxCorner(coordinateMode, dimension, box!.to, "target.box.to", anchor, repairs);
  if (!toResult.ok) return toResult;

  const fromAbs: AbsolutePosition = { x: fromResult.payload.x, y: fromResult.payload.y, z: fromResult.payload.z };
  const toAbs: AbsolutePosition = { x: toResult.payload.x, y: toResult.payload.y, z: toResult.payload.z };
  const volume = boxVolume(fromAbs, toAbs);
  if (volume > maxFillVolume) {
    return fail("LIMIT_EXCEEDED", `box volume ${volume} exceeds limit ${maxFillVolume}`, {
      volume,
      max: maxFillVolume,
    });
  }

  const cells = enumerateBoxCells(fromAbs, toAbs);

  // Below threshold: return full snapshots for every cell.
  if (cells.length <= summaryThreshold) {
    const blocks: BlockSnapshot[] = [];
    for (const cell of cells) {
      const blockResult = getBlockSafe(dimension, cell);
      if (!blockResult.ok) return blockResult;
      blocks.push(buildBlockSnapshot(blockResult.payload.block, dimension, cell));
    }
    return ok({
      schema_version: SCHEMA_VERSION,
      ok: true,
      status: "inspected",
      blocks,
      repairs_applied: repairs,
      coordinate_mode: coordinateMode,
      dimension,
      facing: anchor?.facing,
      player_origin: anchor?.origin,
      player_name: anchor?.player_name,
    });
  }

  // Above threshold: bounded summary with unknown tracking.
  const snapshots: BlockSnapshot[] = [];
  const unknowns: UnknownSample[] = [];
  for (const cell of cells) {
    const blockResult = getBlockSafe(dimension, cell);
    if (!blockResult.ok) {
      unknowns.push({
        x: cell.x,
        y: cell.y,
        z: cell.z,
        status: unknownStatusFromCode(blockResult.payload.code),
      });
      continue;
    }
    snapshots.push(buildBlockSnapshot(blockResult.payload.block, dimension, cell));
  }
  const summary = buildInspectSummary(snapshots, unknowns, fromAbs, toAbs, sampleLimit);
  return ok({
    schema_version: SCHEMA_VERSION,
    ok: true,
    status: "inspected",
    summary,
    repairs_applied: repairs,
    coordinate_mode: coordinateMode,
    dimension,
  });
}
