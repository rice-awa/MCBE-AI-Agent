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
    sourceEntity?: {
      dimension: { getEntities(options?: object): Entity[] };
      location: { x: number; y: number; z: number };
    };
  },
  payload: { entity_type: string; radius?: number; target?: string },
): { ok: true; payload: { entities: EntitySnapshot[] } } {
  const sourceEntity = event.sourceEntity;
  if (!sourceEntity) {
    return { ok: true, payload: { entities: [] } };
  }

  const entities = sourceEntity.dimension.getEntities({
    type: payload.entity_type,
    location: sourceEntity.location,
    maxDistance: payload.radius ?? 32,
  });

  return {
    ok: true,
    payload: {
      entities: entities.map(normalizeEntitySnapshot),
    },
  };
}
