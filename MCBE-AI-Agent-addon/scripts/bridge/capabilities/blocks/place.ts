import {
  SCHEMA_VERSION,
  fail,
  ok,
  type BridgeResult,
  type RepairApplied,
} from "./types";
import {
  checkPrecondition,
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

/**
 * Capability: edit_blocks place mode
 */
export async function handlePlace(
  payload: EditBlocksPayload,
): Promise<BridgeResult<Record<string, unknown>>> {
  const repairs: RepairApplied[] = [];
  const phase = payload.phase ?? "execute";

  const prepared = prepareTypeAndPolicy(payload, repairs);
  if (!prepared.ok) return prepared;

  const targetsResult = resolveEditTargets(payload, "place", repairs);
  if (!targetsResult.ok) return targetsResult;

  const { targets, dimension, facing, player_origin, player_name } = targetsResult.payload;
  if (targets.length !== 1) {
    return fail("INVALID_ARGUMENT", "place mode requires exactly one target");
  }

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

  const baseMeta = {
    schema_version: SCHEMA_VERSION,
    mode: "place" as const,
    phase,
    dimension,
    targets,
    type_id: prepared.payload.type_id,
    states: prepared.payload.states ?? {},
    replace_any: prepared.payload.replace_any,
    expected_previous: prepared.payload.expected_previous,
    repairs_applied: repairs,
    facing,
    player_origin,
    player_name,
    before: preflight.payload.befores[0],
  };

  if (phase === "preflight") {
    return ok({
      ...baseMeta,
      ok: true,
      locked_targets: targets,
      ready: true,
    });
  }

  // Execute: recheck then write
  const target = targets[0];
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
    preflight.payload.befores[0],
  );
  if (!recheck.ok) return recheck;

  const writeResult = writeAndVerify(
    reblock.payload.block,
    target,
    prepared.payload.permutation,
    prepared.payload.type_id,
    prepared.payload.states,
    preflight.payload.befores[0],
  );
  if (!writeResult.ok) return writeResult;

  return ok({
    ...baseMeta,
    ok: true,
    after: writeResult.payload.after,
    changed: writeResult.payload.changed,
    verification: writeResult.payload.verification,
  });
}

// ensure checkPrecondition is available for tests
void checkPrecondition;
void rollbackWritten;
