import { system, world, BlockPermutation, BlockVolume } from "@minecraft/server";

import {
  DEFAULT_CELLS_PER_TICK,
  DEFAULT_MAX_FILL_VOLUME,
  HARD_MAX_FILL_VOLUME,
  SCHEMA_VERSION,
  fail,
  ok,
  type AbsolutePosition,
  type BridgeResult,
  type CoordinateMode,
  type PositionInput,
  type RepairApplied,
  type RelativePosition,
} from "./types";
import { repairDimension } from "./repair";
import {
  floorAbsolutePosition,
  resolveRelativePosition,
  type CardinalFacing,
} from "./coords";
import {
  checkPrecondition,
  prepareTypeAndPolicy,
  recheckPrecondition,
  type EditBlocksPayload,
} from "./common";
import {
  getBlockSafe,
  resolvePlayerAnchor,
  type ResolvedTarget,
} from "./inspect";
import { buildBlockSnapshot } from "./snapshot";

const SAMPLE_LIMIT = 8;

function isAbsolutePos(p: PositionInput): p is AbsolutePosition {
  return (
    p !== null &&
    typeof p === "object" &&
    "x" in p &&
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

function resolveCorner(
  coordinateMode: CoordinateMode,
  dimension: string,
  corner: PositionInput | undefined,
  field: string,
  anchor: { origin: AbsolutePosition; facing: CardinalFacing } | undefined,
  repairs: RepairApplied[],
): BridgeResult<AbsolutePosition & { dimension: string }> {
  if (!corner) {
    return fail("INVALID_ARGUMENT", `${field} is required for fill mode`);
  }
  if (coordinateMode === "absolute") {
    if (!isAbsolutePos(corner)) {
      return fail("INVALID_COORDINATE", `${field} must be absolute {x,y,z}`);
    }
    const floored = floorAbsolutePosition(corner, field, repairs);
    return ok({ dimension, ...floored });
  }
  if (!anchor) {
    return fail("INVALID_ARGUMENT", "player anchor required for player_relative fill");
  }
  if (!isRelativePos(corner)) {
    return fail("INVALID_COORDINATE", `${field} must be relative {forward,right,up}`);
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
  const abs = resolveRelativePosition(anchor.origin, anchor.facing, {
    forward: f,
    right: r,
    up: u,
  });
  return ok({ dimension: anchor.origin ? dimension : dimension, ...abs });
}

function enumerateVolume(
  from: AbsolutePosition,
  to: AbsolutePosition,
): AbsolutePosition[] {
  const minX = Math.min(from.x, to.x);
  const maxX = Math.max(from.x, to.x);
  const minY = Math.min(from.y, to.y);
  const maxY = Math.max(from.y, to.y);
  const minZ = Math.min(from.z, to.z);
  const maxZ = Math.max(from.z, to.z);
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

function volumeSize(from: AbsolutePosition, to: AbsolutePosition): number {
  return (
    (Math.abs(to.x - from.x) + 1) *
    (Math.abs(to.y - from.y) + 1) *
    (Math.abs(to.z - from.z) + 1)
  );
}

/**
 * Yield control to next tick via system.run when available, else Promise microtask.
 */
function nextTick(): Promise<void> {
  return new Promise((resolve) => {
    if (system && typeof system.run === "function") {
      system.run(() => resolve());
    } else if (system && typeof (system as { runJob?: (g: Generator) => void }).runJob === "function") {
      // fallback if only runJob
      (system as { runJob: (g: Generator) => void }).runJob(
        (function* () {
          yield;
          resolve();
        })(),
      );
    } else {
      // test environment
      queueMicrotask(() => resolve());
    }
  });
}

/**
 * Tick-sliced preflight scan of fill volume.
 */
export async function scanFillVolume(
  dimension: string,
  cells: AbsolutePosition[],
  replaceAny: boolean,
  expectedPrevious: EditBlocksPayload["expected_previous"],
  cellsPerTick: number,
): Promise<
  BridgeResult<{
    matched: ResolvedTarget[];
    skipped: number;
    previous_type_counts: Record<string, number>;
    befores: Array<ReturnType<typeof buildBlockSnapshot>>;
    protected_samples: Array<ReturnType<typeof buildBlockSnapshot>>;
  }>
> {
  const matched: ResolvedTarget[] = [];
  const befores: Array<ReturnType<typeof buildBlockSnapshot>> = [];
  const previous_type_counts: Record<string, number> = {};
  let skipped = 0;
  const protected_samples: Array<ReturnType<typeof buildBlockSnapshot>> = [];

  for (let i = 0; i < cells.length; i++) {
    if (i > 0 && i % cellsPerTick === 0) {
      await nextTick();
    }
    const cell = cells[i];
    const blockResult = getBlockSafe(dimension, cell);
    if (!blockResult.ok) return blockResult;
    const snapshot = buildBlockSnapshot(blockResult.payload.block, dimension, cell);
    previous_type_counts[snapshot.type_id] =
      (previous_type_counts[snapshot.type_id] ?? 0) + 1;

    const check = checkPrecondition(
      snapshot,
      blockResult.payload.block,
      replaceAny,
      expectedPrevious,
      true, // fill filter mode
    );
    if (!check.ok) {
      // PROTECTED_BLOCK rejects entire fill
      if (check.payload.code === "PROTECTED_BLOCK") {
        protected_samples.push(snapshot);
        return fail(check.payload.code, check.payload.message, {
          ...check.payload,
          protected_samples: protected_samples.slice(0, SAMPLE_LIMIT),
        });
      }
      return check;
    }
    if (check.payload.skip) {
      skipped += 1;
      continue;
    }
    matched.push({ dimension, ...cell });
    befores.push(snapshot);
  }

  return ok({
    matched,
    skipped,
    previous_type_counts,
    befores,
    protected_samples,
  });
}

/**
 * Capability: edit_blocks fill mode with tick-sliced preflight.
 */
export async function handleFill(
  payload: EditBlocksPayload,
): Promise<BridgeResult<Record<string, unknown>>> {
  const repairs: RepairApplied[] = [];
  const phase = payload.phase ?? "execute";
  const cellsPerTick = Math.max(1, payload.cells_per_tick ?? DEFAULT_CELLS_PER_TICK);
  const maxVolume = Math.min(
    payload.max_fill_volume ?? DEFAULT_MAX_FILL_VOLUME,
    HARD_MAX_FILL_VOLUME,
  );

  const prepared = prepareTypeAndPolicy(payload, repairs);
  if (!prepared.ok) return prepared;

  // locked_targets path after approval
  let dimension: string;
  let fromAbs: AbsolutePosition;
  let toAbs: AbsolutePosition;
  let facing: CardinalFacing | undefined;
  let player_origin: AbsolutePosition | undefined;
  let player_name: string | undefined;
  let cells: AbsolutePosition[];

  if (payload.locked_targets && payload.locked_targets.length > 0) {
    dimension = payload.locked_targets[0].dimension;
    cells = payload.locked_targets.map((t) => ({
      x: Math.floor(t.x),
      y: Math.floor(t.y),
      z: Math.floor(t.z),
    }));
    // reconstruct bounds for reporting
    fromAbs = cells[0];
    toAbs = cells[0];
    for (const c of cells) {
      fromAbs = {
        x: Math.min(fromAbs.x, c.x),
        y: Math.min(fromAbs.y, c.y),
        z: Math.min(fromAbs.z, c.z),
      };
      toAbs = {
        x: Math.max(toAbs.x, c.x),
        y: Math.max(toAbs.y, c.y),
        z: Math.max(toAbs.z, c.z),
      };
    }
  } else {
    const coordinateMode = (payload.coordinate_mode ?? "absolute") as CoordinateMode;
    let anchor:
      | { origin: AbsolutePosition; facing: CardinalFacing; dimension: string; player_name: string }
      | undefined;

    if (coordinateMode === "absolute") {
      const dim = repairDimension(payload.dimension, repairs);
      if (!dim) {
        return fail("INVALID_ARGUMENT", "dimension is required for absolute fill");
      }
      dimension = dim;
    } else {
      const anchorResult = resolvePlayerAnchor(payload.player_name ?? "");
      if (!anchorResult.ok) return anchorResult;
      anchor = {
        origin: anchorResult.payload.origin,
        facing: anchorResult.payload.facing,
        dimension: anchorResult.payload.dimension,
        player_name: anchorResult.payload.player_name,
      };
      dimension = anchor.dimension;
      facing = anchor.facing;
      player_origin = anchor.origin;
      player_name = anchor.player_name;
    }

    const fromResult = resolveCorner(
      coordinateMode,
      dimension,
      payload.from,
      "from",
      anchor,
      repairs,
    );
    if (!fromResult.ok) return fromResult;
    const toResult = resolveCorner(
      coordinateMode,
      dimension,
      payload.to,
      "to",
      anchor,
      repairs,
    );
    if (!toResult.ok) return toResult;

    fromAbs = { x: fromResult.payload.x, y: fromResult.payload.y, z: fromResult.payload.z };
    toAbs = { x: toResult.payload.x, y: toResult.payload.y, z: toResult.payload.z };

    const size = volumeSize(fromAbs, toAbs);
    if (size > maxVolume) {
      return fail("LIMIT_EXCEEDED", `fill volume ${size} exceeds limit ${maxVolume}`, {
        volume: size,
        max: maxVolume,
        from: fromAbs,
        to: toAbs,
      });
    }
    cells = enumerateVolume(fromAbs, toAbs);
  }

  // Preflight scan (tick-sliced)
  const scan = await scanFillVolume(
    dimension,
    cells,
    prepared.payload.replace_any,
    prepared.payload.expected_previous,
    cellsPerTick,
  );
  if (!scan.ok) {
    return {
      ...scan,
      payload: {
        ...scan.payload,
        repairs_applied: repairs,
      },
    };
  }

  const baseMeta = {
    schema_version: SCHEMA_VERSION,
    mode: "fill" as const,
    phase,
    dimension,
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
    volume: cells.length,
    matched_count: scan.payload.matched.length,
    skipped: scan.payload.skipped,
    previous_type_counts: scan.payload.previous_type_counts,
    type_id: prepared.payload.type_id,
    states: prepared.payload.states ?? {},
    replace_any: prepared.payload.replace_any,
    expected_previous: prepared.payload.expected_previous,
    repairs_applied: repairs,
    facing,
    player_origin,
    player_name,
    before_samples: scan.payload.befores.slice(0, SAMPLE_LIMIT),
  };

  if (phase === "preflight") {
    return ok({
      ...baseMeta,
      ok: true,
      locked_targets: scan.payload.matched,
      ready: true,
    });
  }

  // Execute: recheck matched cells (tick-sliced)
  const matched = scan.payload.matched;
  for (let i = 0; i < matched.length; i++) {
    if (i > 0 && i % cellsPerTick === 0) {
      await nextTick();
    }
    const target = matched[i];
    const reblock = getBlockSafe(target.dimension, {
      x: target.x,
      y: target.y,
      z: target.z,
    });
    if (!reblock.ok) return reblock;
    const current = buildBlockSnapshot(reblock.payload.block, target.dimension, {
      x: target.x,
      y: target.y,
      z: target.z,
    });
    const recheck = recheckPrecondition(
      current,
      reblock.payload.block,
      prepared.payload.replace_any,
      prepared.payload.expected_previous,
      scan.payload.befores[i],
    );
    if (!recheck.ok) return recheck;
  }

  // Prefer fillBlocks API when entire volume matches and replace_any/air-only with no skips
  // Spec: no full rollback promise; on exception return STATE_UNKNOWN
  try {
    if (
      scan.payload.skipped === 0 &&
      matched.length === cells.length &&
      typeof BlockVolume === "function"
    ) {
      try {
        const dim = world.getDimension(dimension);
        const volume = new BlockVolume(
          { x: baseMeta.from.x, y: baseMeta.from.y, z: baseMeta.from.z },
          { x: baseMeta.to.x, y: baseMeta.to.y, z: baseMeta.to.z },
        );
        if (typeof dim.fillBlocks === "function") {
          dim.fillBlocks(volume, prepared.payload.permutation as never);
          return ok({
            ...baseMeta,
            ok: true,
            changed_count: matched.length,
            verification: { ok: true, method: "fillBlocks" },
            rollback: { promised: false },
          });
        }
      } catch {
        // fall through to cell-by-cell
      }
    }

    // Cell-by-cell write for filtered fills
    let changed = 0;
    for (let i = 0; i < matched.length; i++) {
      if (i > 0 && i % cellsPerTick === 0) {
        await nextTick();
      }
      const target = matched[i];
      const reblock = getBlockSafe(target.dimension, {
        x: target.x,
        y: target.y,
        z: target.z,
      });
      if (!reblock.ok) {
        return fail("STATE_UNKNOWN", reblock.payload.message, {
          ...baseMeta,
          changed_count: changed,
          failed_index: i,
          retryable: false,
        });
      }
      try {
        reblock.payload.block.setPermutation(prepared.payload.permutation);
        changed += 1;
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        return fail("STATE_UNKNOWN", message, {
          ...baseMeta,
          changed_count: changed,
          failed_index: i,
          retryable: false,
        });
      }
    }

    return ok({
      ...baseMeta,
      ok: true,
      changed_count: changed,
      verification: { ok: true, method: "cell_by_cell" },
      rollback: { promised: false },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return fail("STATE_UNKNOWN", message, {
      ...baseMeta,
      retryable: false,
      rollback: { promised: false },
    });
  }
}

// silence unused
void BlockPermutation;
