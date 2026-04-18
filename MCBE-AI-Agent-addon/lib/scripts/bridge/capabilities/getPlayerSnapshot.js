import { EntityComponentTypes, world } from "@minecraft/server";
export function buildPlayerSnapshot(player) {
    const healthComponent = player.getComponent(EntityComponentTypes.Health);
    return {
        name: player.name,
        health: healthComponent ? Math.round(healthComponent.currentValue) : null,
        tags: player.getTags(),
        location: {
            x: Math.round(player.location.x),
            y: Math.round(player.location.y),
            z: Math.round(player.location.z),
        },
        dimension: player.dimension.id,
        gameMode: String(player.getGameMode()),
    };
}
export function handleGetPlayerSnapshot(payload) {
    const target = payload.target;
    const players = !target || target === "@a"
        ? world.getAllPlayers()
        : world.getPlayers({ name: target });
    return {
        ok: true,
        payload: {
            players: players.map(buildPlayerSnapshot),
        },
    };
}
//# sourceMappingURL=getPlayerSnapshot.js.map