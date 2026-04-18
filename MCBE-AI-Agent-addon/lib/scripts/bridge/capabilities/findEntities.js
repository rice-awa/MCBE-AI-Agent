export function normalizeEntitySnapshot(entity) {
    var _a;
    return {
        id: entity.id,
        typeId: entity.typeId,
        position: {
            x: Math.round(entity.location.x),
            y: Math.round(entity.location.y),
            z: Math.round(entity.location.z),
        },
        nameTag: entity.nameTag,
        dimension: (_a = entity.dimension) === null || _a === void 0 ? void 0 : _a.id,
    };
}
export function handleFindEntities(event, payload) {
    var _a;
    const sourceEntity = event.sourceEntity;
    if (!sourceEntity) {
        return { ok: true, payload: { entities: [] } };
    }
    const entities = sourceEntity.dimension.getEntities({
        type: payload.entity_type,
        location: sourceEntity.location,
        maxDistance: (_a = payload.radius) !== null && _a !== void 0 ? _a : 32,
    });
    return {
        ok: true,
        payload: {
            entities: entities.map(normalizeEntitySnapshot),
        },
    };
}
//# sourceMappingURL=findEntities.js.map