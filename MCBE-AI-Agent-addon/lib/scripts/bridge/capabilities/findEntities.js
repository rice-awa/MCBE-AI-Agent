function isPlayerNameTarget(target) {
    if (!target) {
        return false;
    }
    return !target.trim().startsWith("@");
}
function resolveQueryAnchor(event) {
    if (event.sourceEntity) {
        return event.sourceEntity;
    }
    return event.sourceBlock;
}
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
    const queryAnchor = resolveQueryAnchor(event);
    if (!queryAnchor) {
        return { ok: true, payload: { entities: [] } };
    }
    const options = {
        type: payload.entity_type,
        location: queryAnchor.location,
        maxDistance: (_a = payload.radius) !== null && _a !== void 0 ? _a : 32,
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
//# sourceMappingURL=findEntities.js.map