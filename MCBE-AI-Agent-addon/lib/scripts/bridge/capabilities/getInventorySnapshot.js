import { EntityComponentTypes, world } from "@minecraft/server";
function normalizeInventoryItem(slot, item) {
    return {
        slot,
        typeId: item.typeId,
        amount: item.amount,
        nameTag: item.nameTag,
    };
}
function buildInventorySnapshot(player) {
    var _a;
    const inventory = player.getComponent(EntityComponentTypes.Inventory);
    const container = inventory === null || inventory === void 0 ? void 0 : inventory.container;
    const items = [];
    if (container) {
        for (let slot = 0; slot < container.size; slot += 1) {
            const item = container.getItem(slot);
            if (item) {
                items.push(normalizeInventoryItem(slot, item));
            }
        }
    }
    return {
        player: player.name,
        size: (_a = container === null || container === void 0 ? void 0 : container.size) !== null && _a !== void 0 ? _a : 0,
        items,
    };
}
export function handleGetInventorySnapshot(payload) {
    const target = payload.target;
    const players = !target || target === "@a"
        ? world.getAllPlayers()
        : world.getPlayers({ name: target });
    return {
        ok: true,
        payload: {
            inventories: players.map(buildInventorySnapshot),
        },
    };
}
//# sourceMappingURL=getInventorySnapshot.js.map