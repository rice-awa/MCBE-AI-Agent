import { world, type Player } from "@minecraft/server";

export type LookBlockHit = {
  player: string;
  hit: true;
  block: {
    typeId: string;
    location: { x: number; y: number; z: number };
    dimension: string;
  };
  face: string;
  faceLocation: { x: number; y: number; z: number };
};

export type LookBlockMiss = {
  player: string;
  hit: false;
};

export type LookBlockPayload = LookBlockHit | LookBlockMiss;

export type GetLookBlockRequest = {
  target?: string;
  max_distance?: number;
  include_liquid_blocks?: boolean;
  include_passable_blocks?: boolean;
};

function roundCoord(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function resolvePlayer(target: string | undefined): Player | undefined {
  const name = typeof target === "string" ? target.trim() : "";
  if (!name || name.startsWith("@")) {
    return undefined;
  }

  const players = world.getPlayers({ name });
  return players[0];
}

export function handleGetLookBlock(payload: GetLookBlockRequest): {
  ok: boolean;
  payload: LookBlockPayload | { error: string };
} {
  const target = payload.target;
  const player = resolvePlayer(target);

  if (!player) {
    return {
      ok: false,
      payload: {
        error: `玩家未找到: ${typeof target === "string" && target.trim() ? target.trim() : "(未指定)"}`,
      },
    };
  }

  const maxDistance =
    typeof payload.max_distance === "number" && Number.isFinite(payload.max_distance)
      ? Math.max(1, Math.min(payload.max_distance, 64))
      : 8;

  const hit = player.getBlockFromViewDirection({
    maxDistance,
    includeLiquidBlocks: Boolean(payload.include_liquid_blocks),
    includePassableBlocks: Boolean(payload.include_passable_blocks),
  });

  if (!hit) {
    return {
      ok: true,
      payload: {
        player: player.name,
        hit: false,
      },
    };
  }

  const block = hit.block;
  return {
    ok: true,
    payload: {
      player: player.name,
      hit: true,
      block: {
        typeId: block.typeId,
        location: {
          x: Math.round(block.location.x),
          y: Math.round(block.location.y),
          z: Math.round(block.location.z),
        },
        dimension: block.dimension.id,
      },
      face: String(hit.face),
      faceLocation: {
        x: roundCoord(hit.faceLocation.x),
        y: roundCoord(hit.faceLocation.y),
        z: roundCoord(hit.faceLocation.z),
      },
    },
  };
}
