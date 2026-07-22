import type { AbsolutePosition, RelativePosition, RepairApplied } from "./types";

/** Mathematical floor that works for negatives (Math.floor). */
export function mathFloor(n: number): number {
  return Math.floor(n);
}

export function floorCoordValue(
  value: number,
  field: string,
  repairs: RepairApplied[],
): number {
  if (!Number.isFinite(value)) {
    return value;
  }
  const floored = mathFloor(value);
  if (floored !== value) {
    repairs.push({
      field,
      from: value,
      to: floored,
      reason: "math_floor",
    });
  }
  return floored;
}

export function floorAbsolutePosition(
  pos: { x: number; y: number; z: number },
  fieldPrefix: string,
  repairs: RepairApplied[],
): AbsolutePosition {
  return {
    x: floorCoordValue(pos.x, `${fieldPrefix}.x`, repairs),
    y: floorCoordValue(pos.y, `${fieldPrefix}.y`, repairs),
    z: floorCoordValue(pos.z, `${fieldPrefix}.z`, repairs),
  };
}

export type CardinalFacing = "north" | "south" | "east" | "west";

/**
 * Snap yaw (Minecraft degrees, 0 = south, increasing clockwise) to four cardinals.
 * Common MC mapping: south=0, west=90, north=180, east=270 (or -90).
 */
export function snapYawToCardinal(yaw: number): CardinalFacing {
  // Normalize to [0, 360)
  let y = yaw % 360;
  if (y < 0) y += 360;
  // Buckets centered on 0/90/180/270 with ±45
  if (y >= 315 || y < 45) return "south";
  if (y >= 45 && y < 135) return "west";
  if (y >= 135 && y < 225) return "north";
  return "east";
}

/**
 * Resolve player-relative offsets using foot-block origin and cardinal facing.
 * forward: positive in facing direction
 * right: positive to the player's right
 * up: positive Y
 */
export function resolveRelativePosition(
  origin: AbsolutePosition,
  facing: CardinalFacing,
  offset: RelativePosition,
): AbsolutePosition {
  const f = mathFloor(offset.forward);
  const r = mathFloor(offset.right);
  const u = mathFloor(offset.up);

  let dx = 0;
  let dz = 0;
  switch (facing) {
    case "north": // -Z
      dx = r;
      dz = -f;
      break;
    case "south": // +Z
      dx = -r;
      dz = f;
      break;
    case "east": // +X
      dx = f;
      dz = r;
      break;
    case "west": // -X
      dx = -f;
      dz = -r;
      break;
  }
  return {
    x: origin.x + dx,
    y: origin.y + u,
    z: origin.z + dz,
  };
}

/** Foot block origin from continuous player location. */
export function footBlockOrigin(location: { x: number; y: number; z: number }): AbsolutePosition {
  return {
    x: mathFloor(location.x),
    y: mathFloor(location.y),
    z: mathFloor(location.z),
  };
}
