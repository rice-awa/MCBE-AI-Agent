import { world, type Entity } from "@minecraft/server";

export type EntitySnapshot = {
  id: string;
  typeId: string;
  position: {
    x: number;
    y: number;
    z: number;
  };
  nameTag?: string;
  dimension?: string;
};

type QueryAnchor = {
  dimension: { getEntities(options?: object): Entity[] };
  location: { x: number; y: number; z: number };
};

function isPlayerNameTarget(target: string | undefined): target is string {
  if (!target) {
    return false;
  }

  return !target.trim().startsWith("@");
}

function resolveQueryAnchor(
  event: {
    sourceEntity?: QueryAnchor;
    sourceBlock?: QueryAnchor;
  },
  targetPlayerName?: string,
): QueryAnchor | undefined {
  // 如果指定了目标玩家名，尝试找到该玩家并以其位置为查询中心
  if (targetPlayerName) {
    const players = world.getPlayers({ name: targetPlayerName });
    if (players.length > 0) {
      const player = players[0];
      return {
        dimension: player.dimension,
        location: player.location,
      };
    }
  }

  // 否则使用事件源作为查询中心
  if (event.sourceEntity) {
    return event.sourceEntity;
  }

  return event.sourceBlock;
}

export function normalizeEntitySnapshot(entity: Pick<Entity, "id" | "typeId" | "location"> & {
  nameTag?: string;
  dimension?: { id: string };
}): EntitySnapshot {
  return {
    id: entity.id,
    typeId: entity.typeId,
    position: {
      x: Math.round(entity.location.x),
      y: Math.round(entity.location.y),
      z: Math.round(entity.location.z),
    },
    nameTag: entity.nameTag,
    dimension: entity.dimension?.id,
  };
}

export function handleFindEntities(
  event: {
    sourceEntity?: QueryAnchor;
    sourceBlock?: QueryAnchor;
  },
  payload: { entity_type: string; radius?: number; target?: string },
): { ok: true; payload: { entities: EntitySnapshot[] } } {
  // target 参数用于指定查询中心玩家（如果提供且不是 @ 开头）
  const targetPlayerName = isPlayerNameTarget(payload.target) ? payload.target.trim() : undefined;
  const queryAnchor = resolveQueryAnchor(event, targetPlayerName);

  if (!queryAnchor) {
    return { ok: true, payload: { entities: [] } };
  }

  const options: {
    type: string;
    location: { x: number; y: number; z: number };
    maxDistance: number;
  } = {
    type: payload.entity_type,
    location: queryAnchor.location,
    maxDistance: payload.radius ?? 32,
  };

  const entities = queryAnchor.dimension.getEntities(options);

  return {
    ok: true,
    payload: {
      entities: entities.map(normalizeEntitySnapshot),
    },
  };
}
