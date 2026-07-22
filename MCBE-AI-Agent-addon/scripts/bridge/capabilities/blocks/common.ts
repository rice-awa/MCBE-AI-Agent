import { BlockTypes } from "@minecraft/server";

import {
  DEFAULT_MAX_POSITIONS,
  HARD_MAX_DISCRETE,
  fail,
  ok,
  type AbsolutePosition,
  type BridgeResult,
  type CoordinateMode,
  type ExpectedPrevious,
  type LockedTarget,
  type PositionInput,
  type RepairApplied,
} from "./types";
import { repairTypeId } from "./repair";
import {
  collectPositions,
  getBlockSafe,
  resolvePermutation,
  resolveTargets,
  type ResolvedTarget,
} from "./inspect";
import { buildBlockSnapshot, matchesExpectedPrevious, matchesTargetPermutation } from "./snapshot";
import { findProtectedComponent } from "./protect";

// Re-export DEFAULT for limits that place/batch use
const DEFAULT_DISCRETE = DEFAULT_MAX_POSITIONS;

export type EditPhase = "preflight" | "execute";

export type EditBlocksPayload = {
  mode?: "place" | "batch" | "fill";
  coordinate_mode?: CoordinateMode;
  dimension?: string;
  position?: PositionInput;
  positions?: PositionInput[];
  from?: PositionInput;
  to?: PositionInput;
  type_id?: string;
  states?: Record<string, string | number | boolean>;
  replace_any?: boolean;
  expected_previous?: ExpectedPrevious;
  player_name?: string;
  phase?: EditPhase;
  locked_targets?: LockedTarget[];
  max_discrete?: number;
  max_fill_volume?: number;
  cells_per_tick?: number;
};

export type PreparedWrite = {
  targets: ResolvedTarget[];
  dimension: string;
  type_id: string;
  states?: Record<string, string | number | boolean>;
  replace_any: boolean;
  expected_previous?: ExpectedPrevious;
  repairs_applied: RepairApplied[];
  facing?: string;
  player_origin?: AbsolutePosition;
  player_name?: string;
  permutation: unknown;
  mode: "place" | "batch" | "fill";
  befores?: Array<ReturnType<typeof buildBlockSnapshot>>;
  // fill-only
  from?: AbsolutePosition;
  to?: AbsolutePosition;
  matched_targets?: ResolvedTarget[];
  skipped?: number;
  previous_type_counts?: Record<string, number>;
};

function isAirType(typeId: string): boolean {
  return typeId === "minecraft:air" || typeId === "air";
}

export function validateWritePolicy(
  typeId: string,
  replaceAny: boolean | undefined,
  expectedPrevious: ExpectedPrevious | undefined,
): BridgeResult<void> {
  if (replaceAny && expectedPrevious) {
    return fail(
      "INVALID_ARGUMENT",
      "replace_any and expected_previous are mutually exclusive",
    );
  }
  if (isAirType(typeId) && !replaceAny && !expectedPrevious) {
    return fail(
      "INVALID_ARGUMENT",
      "placing air requires replace_any or expected_previous",
    );
  }
  return ok(undefined);
}

export function checkPrecondition(
  snapshot: ReturnType<typeof buildBlockSnapshot>,
  block: { getComponent?: (id: string) => unknown },
  replaceAny: boolean,
  expectedPrevious: ExpectedPrevious | undefined,
  forFillFilter: boolean,
): BridgeResult<{ skip?: boolean }> {
  // Protected always rejects when we would write over it
  const wouldWrite =
    forFillFilter
      ? // fill: only check protected if this cell matches include filter
        true
      : true;

  if (expectedPrevious) {
    if (!matchesExpectedPrevious(snapshot, expectedPrevious)) {
      if (forFillFilter) {
        return ok({ skip: true });
      }
      return fail("PRECONDITION_FAILED", "expected_previous does not match current block", {
        expected: expectedPrevious,
        actual: { type_id: snapshot.type_id, states: snapshot.states },
        target: {
          dimension: snapshot.dimension,
          x: snapshot.x,
          y: snapshot.y,
          z: snapshot.z,
        },
      });
    }
  } else if (!replaceAny) {
    // air only
    if (!snapshot.is_air && snapshot.type_id !== "minecraft:air") {
      if (forFillFilter) {
        return ok({ skip: true });
      }
      return fail("PRECONDITION_FAILED", "target is not air (default air-only policy)", {
        actual: { type_id: snapshot.type_id, states: snapshot.states },
        target: {
          dimension: snapshot.dimension,
          x: snapshot.x,
          y: snapshot.y,
          z: snapshot.z,
        },
      });
    }
  }

  if (wouldWrite && !snapshot.is_air && snapshot.type_id !== "minecraft:air") {
    const protectedId = findProtectedComponent(block);
    if (protectedId) {
      return fail("PROTECTED_BLOCK", `block has protected component: ${protectedId}`, {
        component: protectedId,
        type_id: snapshot.type_id,
        target: {
          dimension: snapshot.dimension,
          x: snapshot.x,
          y: snapshot.y,
          z: snapshot.z,
        },
      });
    }
  }

  // Also protect air cells? no
  // When writing air over non-air, still check protected
  if ((replaceAny || expectedPrevious) && !snapshot.is_air) {
    const protectedId = findProtectedComponent(block);
    if (protectedId) {
      return fail("PROTECTED_BLOCK", `block has protected component: ${protectedId}`, {
        component: protectedId,
        type_id: snapshot.type_id,
        target: {
          dimension: snapshot.dimension,
          x: snapshot.x,
          y: snapshot.y,
          z: snapshot.z,
        },
      });
    }
  }

  return ok({});
}

export function recheckPrecondition(
  snapshot: ReturnType<typeof buildBlockSnapshot>,
  block: { getComponent?: (id: string) => unknown },
  replaceAny: boolean,
  expectedPrevious: ExpectedPrevious | undefined,
  previousSnapshot: ReturnType<typeof buildBlockSnapshot>,
): BridgeResult<void> {
  // Concurrent change: type or states differ from preflight before
  if (
    snapshot.type_id !== previousSnapshot.type_id ||
    JSON.stringify(snapshot.states) !== JSON.stringify(previousSnapshot.states)
  ) {
    return fail("PRECONDITION_CHANGED", "block changed since preflight", {
      previous: previousSnapshot,
      actual: snapshot,
    });
  }
  const check = checkPrecondition(snapshot, block, replaceAny, expectedPrevious, false);
  if (!check.ok) {
    if (check.payload.code === "PRECONDITION_FAILED") {
      return fail("PRECONDITION_CHANGED", check.payload.message, check.payload);
    }
    return check as BridgeResult<void>;
  }
  return ok(undefined);
}

function getBlockTypesRegistry(): { getAll?: () => Array<{ id: string } | string>; get?: (id: string) => unknown } | undefined {
  try {
    if (BlockTypes && typeof BlockTypes === "object") {
      return BlockTypes as {
        getAll?: () => Array<{ id: string } | string>;
        get?: (id: string) => unknown;
      };
    }
  } catch {
    // optional
  }
  return undefined;
}

export function prepareTypeAndPolicy(
  payload: EditBlocksPayload,
  repairs: RepairApplied[],
): BridgeResult<{
  type_id: string;
  states?: Record<string, string | number | boolean>;
  replace_any: boolean;
  expected_previous?: ExpectedPrevious;
  permutation: unknown;
}> {
  const typeId = repairTypeId(payload.type_id, repairs, getBlockTypesRegistry());
  if (!typeId) {
    return fail("INVALID_ARGUMENT", "type_id is required");
  }
  // Validate known block if registry available
  const registry = getBlockTypesRegistry();
  if (registry?.get) {
    try {
      const known = registry.get(typeId);
      if (known === undefined || known === null) {
        // try without fail if registry incomplete in tests
        if (typeof registry.getAll === "function") {
          // only fail if getAll exists and doesn't include
          const all = registry.getAll().map((i) => (typeof i === "string" ? i : i.id));
          if (all.length > 0 && !all.includes(typeId)) {
            return fail("BLOCK_UNKNOWN", `unknown block type: ${typeId}`, { type_id: typeId });
          }
        }
      }
    } catch {
      // ignore registry errors
    }
  }

  const replaceAny = Boolean(payload.replace_any);
  const expectedPrevious = payload.expected_previous;
  if (expectedPrevious) {
    // repair expected type id namespace
    if (expectedPrevious.type_id) {
      const repairedExpected: RepairApplied[] = [];
      const et = repairTypeId(expectedPrevious.type_id, repairedExpected, getBlockTypesRegistry());
      if (et && et !== expectedPrevious.type_id) {
        expectedPrevious.type_id = et;
        repairs.push(...repairedExpected.map((r) => ({ ...r, field: `expected_previous.${r.field}` })));
      }
    }
  }

  const policy = validateWritePolicy(typeId, replaceAny, expectedPrevious);
  if (!policy.ok) return policy;

  const permResult = resolvePermutation(typeId, payload.states);
  if (!permResult.ok) return permResult;

  return ok({
    type_id: typeId,
    states: payload.states,
    replace_any: replaceAny,
    expected_previous: expectedPrevious,
    permutation: permResult.payload.permutation,
  });
}

export function resolveEditTargets(
  payload: EditBlocksPayload,
  mode: "place" | "batch",
  repairs: RepairApplied[],
): BridgeResult<{
  targets: ResolvedTarget[];
  dimension: string;
  facing?: string;
  player_origin?: AbsolutePosition;
  player_name?: string;
}> {
  // After approval, locked_targets may be provided
  if (payload.locked_targets && payload.locked_targets.length > 0) {
    const targets = payload.locked_targets.map((t) => ({
      dimension: t.dimension,
      x: Math.floor(t.x),
      y: Math.floor(t.y),
      z: Math.floor(t.z),
    }));
    return ok({
      targets,
      dimension: targets[0].dimension,
    });
  }

  const coordinateMode = (payload.coordinate_mode ?? "absolute") as CoordinateMode;
  let positions: PositionInput[];
  if (mode === "place") {
    if (payload.position) {
      positions = [payload.position];
    } else if (payload.positions && payload.positions.length === 1) {
      positions = payload.positions;
    } else if (payload.positions && payload.positions.length > 1) {
      return fail("INVALID_ARGUMENT", "place mode accepts exactly one position");
    } else {
      return fail("INVALID_ARGUMENT", "place mode requires position");
    }
  } else {
    const collected = collectPositions(payload);
    if (!Array.isArray(collected)) return collected;
    positions = collected;
  }

  const maxDiscrete = Math.min(
    payload.max_discrete ?? DEFAULT_DISCRETE,
    HARD_MAX_DISCRETE,
  );
  if (positions.length > maxDiscrete) {
    return fail("LIMIT_EXCEEDED", `positions exceed limit ${maxDiscrete}`, {
      count: positions.length,
      max: maxDiscrete,
    });
  }
  if (positions.length === 0) {
    return fail("INVALID_ARGUMENT", "at least one position is required");
  }

  return resolveTargets(
    coordinateMode,
    payload.dimension,
    positions,
    payload.player_name,
    repairs,
  );
}

export function preflightDiscreteTargets(
  targets: ResolvedTarget[],
  replaceAny: boolean,
  expectedPrevious: ExpectedPrevious | undefined,
): BridgeResult<{
  befores: Array<ReturnType<typeof buildBlockSnapshot>>;
  blocks: Array<{ block: NonNullable<ReturnType<typeof getBlockSafe> extends BridgeResult<infer T> ? T extends { block: infer B } ? B : never : never>; target: ResolvedTarget }>;
}> {
  const befores = [];
  const blocks = [];
  for (const target of targets) {
    const blockResult = getBlockSafe(target.dimension, {
      x: target.x,
      y: target.y,
      z: target.z,
    });
    if (!blockResult.ok) return blockResult;
    const snapshot = buildBlockSnapshot(blockResult.payload.block, target.dimension, {
      x: target.x,
      y: target.y,
      z: target.z,
    });
    const check = checkPrecondition(
      snapshot,
      blockResult.payload.block,
      replaceAny,
      expectedPrevious,
      false,
    );
    if (!check.ok) return check;
    befores.push(snapshot);
    blocks.push({ block: blockResult.payload.block, target });
  }
  return ok({ befores, blocks });
}

export function writeAndVerify(
  block: {
    setPermutation: (p: unknown) => void;
    permutation?: {
      getAllStates?: () => Record<string, string | number | boolean>;
    };
    typeId?: string;
    isAir?: boolean;
    isLiquid?: boolean;
    isWaterlogged?: boolean;
    getComponent?: (id: string) => unknown;
  },
  target: ResolvedTarget,
  permutation: unknown,
  typeId: string,
  states: Record<string, string | number | boolean> | undefined,
  beforeSnapshot: ReturnType<typeof buildBlockSnapshot>,
): BridgeResult<{
  after: ReturnType<typeof buildBlockSnapshot>;
  changed: boolean;
  verification: { ok: boolean };
  rollback?: { ok: boolean; message?: string };
}> {
  try {
    block.setPermutation(permutation);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return fail("INTERNAL_ERROR", `setPermutation failed: ${message}`, {
      target,
    });
  }

  // Re-read
  const reread = getBlockSafe(target.dimension, {
    x: target.x,
    y: target.y,
    z: target.z,
  });
  if (!reread.ok) {
    // Try rollback with retained before permutation if possible
    return fail("STATE_UNKNOWN", "could not re-read block after write", {
      target,
      before: beforeSnapshot,
    });
  }
  const after = buildBlockSnapshot(reread.payload.block, target.dimension, {
    x: target.x,
    y: target.y,
    z: target.z,
  });
  const verificationOk = matchesTargetPermutation(after, typeId, states);
  if (!verificationOk) {
    // Attempt rollback
    let rollbackOk = false;
    let rollbackMessage: string | undefined;
    try {
      // Best-effort: resolve before type as default permutation
      const beforePerm = resolvePermutation(
        beforeSnapshot.type_id,
        Object.keys(beforeSnapshot.states).length ? beforeSnapshot.states : undefined,
      );
      if (beforePerm.ok) {
        reread.payload.block.setPermutation(beforePerm.payload.permutation);
        const afterRollback = getBlockSafe(target.dimension, {
          x: target.x,
          y: target.y,
          z: target.z,
        });
        if (afterRollback.ok) {
          const rbSnap = buildBlockSnapshot(afterRollback.payload.block, target.dimension, {
            x: target.x,
            y: target.y,
            z: target.z,
          });
          rollbackOk = rbSnap.type_id === beforeSnapshot.type_id;
        }
      } else {
        rollbackMessage = "could not resolve before permutation";
      }
    } catch (e) {
      rollbackMessage = e instanceof Error ? e.message : String(e);
    }
    return fail("INTERNAL_ERROR", "post-write verification failed", {
      before: beforeSnapshot,
      after,
      verification: { ok: false },
      rollback: { ok: rollbackOk, message: rollbackMessage },
      target,
    });
  }

  return ok({
    after,
    changed: after.type_id !== beforeSnapshot.type_id ||
      JSON.stringify(after.states) !== JSON.stringify(beforeSnapshot.states),
    verification: { ok: true },
  });
}

export function rollbackWritten(
  written: Array<{
    target: ResolvedTarget;
    before: ReturnType<typeof buildBlockSnapshot>;
  }>,
): Array<{ target: ResolvedTarget; ok: boolean; message?: string }> {
  const results = [];
  for (const item of written) {
    try {
      const blockResult = getBlockSafe(item.target.dimension, {
        x: item.target.x,
        y: item.target.y,
        z: item.target.z,
      });
      if (!blockResult.ok) {
        results.push({ target: item.target, ok: false, message: blockResult.payload.message });
        continue;
      }
      const beforePerm = resolvePermutation(
        item.before.type_id,
        Object.keys(item.before.states).length ? item.before.states : undefined,
      );
      if (!beforePerm.ok) {
        results.push({ target: item.target, ok: false, message: "could not resolve before permutation" });
        continue;
      }
      blockResult.payload.block.setPermutation(beforePerm.payload.permutation);
      const verify = getBlockSafe(item.target.dimension, {
        x: item.target.x,
        y: item.target.y,
        z: item.target.z,
      });
      if (!verify.ok) {
        results.push({ target: item.target, ok: false, message: "could not verify rollback" });
        continue;
      }
      const snap = buildBlockSnapshot(verify.payload.block, item.target.dimension, {
        x: item.target.x,
        y: item.target.y,
        z: item.target.z,
      });
      results.push({
        target: item.target,
        ok: snap.type_id === item.before.type_id,
      });
    } catch (e) {
      results.push({
        target: item.target,
        ok: false,
        message: e instanceof Error ? e.message : String(e),
      });
    }
  }
  return results;
}

