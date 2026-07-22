import {
  SCHEMA_VERSION,
  fail,
  ok,
  type BridgeResult,
  type RepairApplied,
} from "./types";
import {
  prepareTypeAndPolicy,
  preflightDiscreteTargets,
  recheckPrecondition,
  resolveEditTargets,
  rollbackWritten,
  writeAndVerify,
  type EditBlocksPayload,
} from "./common";
import { buildBlockSnapshot } from "./snapshot";
import { getBlockSafe } from "./inspect";

const SAMPLE_LIMIT = 8;

/**
 * Capability: edit_blocks batch mode — full preflight, recheck, atomic-ish rollback.
 */
export async function handleBatch(
  payload: EditBlocksPayload,
): Promise<BridgeResult<Record<string, unknown>>> {
  const repairs: RepairApplied[] = [];
  const phase = payload.phase ?? "execute";

  const prepared = prepareTypeAndPolicy(payload, repairs);
  if (!prepared.ok) return prepared;

  const targetsResult = resolveEditTargets(payload, "batch", repairs);
  if (!targetsResult.ok) return targetsResult;

  const { targets, dimension, facing, player_origin, player_name } = targetsResult.payload;

  const preflight = preflightDiscreteTargets(
    targets,
    prepared.payload.replace_any,
    prepared.payload.expected_previous,
  );
  if (!preflight.ok) {
    return {
      ...preflight,
      payload: {
        ...preflight.payload,
        repairs_applied: repairs,
      },
    };
  }

  // Previous type counts for summary
  const previous_type_counts: Record<string, number> = {};
  for (const b of preflight.payload.befores) {
    previous_type_counts[b.type_id] = (previous_type_counts[b.type_id] ?? 0) + 1;
  }

  const baseMeta = {
    schema_version: SCHEMA_VERSION,
    mode: "batch" as const,
    phase,
    dimension,
    targets,
    count: targets.length,
    type_id: prepared.payload.type_id,
    states: prepared.payload.states ?? {},
    replace_any: prepared.payload.replace_any,
    expected_previous: prepared.payload.expected_previous,
    repairs_applied: repairs,
    facing,
    player_origin,
    player_name,
    previous_type_counts,
    before_samples: preflight.payload.befores.slice(0, SAMPLE_LIMIT),
  };

  if (phase === "preflight") {
    return ok({
      ...baseMeta,
      ok: true,
      locked_targets: targets,
      ready: true,
    });
  }

  // Execute: recheck all first
  for (let i = 0; i < targets.length; i++) {
    const target = targets[i];
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
      preflight.payload.befores[i],
    );
    if (!recheck.ok) return recheck;
  }

  const written: Array<{
    target: (typeof targets)[0];
    before: (typeof preflight.payload.befores)[0];
    after?: ReturnType<typeof buildBlockSnapshot>;
  }> = [];
  const afters: Array<ReturnType<typeof buildBlockSnapshot>> = [];

  for (let i = 0; i < targets.length; i++) {
    const target = targets[i];
    const reblock = getBlockSafe(target.dimension, {
      x: target.x,
      y: target.y,
      z: target.z,
    });
    if (!reblock.ok) {
      // Mid-fail: rollback already written
      const rollback = rollbackWritten(written);
      return fail("INTERNAL_ERROR", reblock.payload.message, {
        ...baseMeta,
        failed_index: i,
        written_count: written.length,
        rollback,
        verification_summary: { failed: true },
      });
    }

    const writeResult = writeAndVerify(
      reblock.payload.block,
      target,
      prepared.payload.permutation,
      prepared.payload.type_id,
      prepared.payload.states,
      preflight.payload.befores[i],
    );
    if (!writeResult.ok) {
      const rollback = rollbackWritten(written);
      return fail(writeResult.payload.code, writeResult.payload.message, {
        ...baseMeta,
        ...writeResult.payload,
        failed_index: i,
        written_count: written.length,
        rollback,
      });
    }

    written.push({
      target,
      before: preflight.payload.befores[i],
      after: writeResult.payload.after,
    });
    afters.push(writeResult.payload.after);
  }

  return ok({
    ...baseMeta,
    ok: true,
    changed_count: afters.length,
    after_samples: afters.slice(0, SAMPLE_LIMIT),
    verification: { ok: true, count: afters.length },
    rollback: { needed: false },
  });
}
