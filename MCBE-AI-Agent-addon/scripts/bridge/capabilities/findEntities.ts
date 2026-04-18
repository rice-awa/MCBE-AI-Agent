import type { Entity } from "@minecraft/server";

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

function resolveQueryAnchor(event: {
  sourceEntity?: QueryAnchor;
  sourceBlock?: QueryAnchor;
}): QueryAnchor | undefined {
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
  const queryAnchor = resolveQueryAnchor(event);
  if (!queryAnchor) {
    return { ok: true, payload: { entities: [] } };
  }

  const options: {
    type: string;
    location: { x: number; y: number; z: number };
    maxDistance: number;
    name?: string;
  } = {
    type: payload.entity_type,
    location: queryAnchor.location,
    maxDistance: payload.radius ?? 32,
  };

  if (isPlayerNameTarget(payload.target)) {
    options.name = payload.target.trim();
  }

  const entities = queryAnchor.dimension.getEntities(options);

  return {
    ok: true,
    payload: {
      entities: entities.map(normalizeEntitySnapshot),
    },
  };
}
