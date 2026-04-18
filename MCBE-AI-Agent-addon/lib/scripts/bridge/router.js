import { BRIDGE_MESSAGE_ID } from "./constants";
let isBridgeRouterRegistered = false;
export { BRIDGE_MESSAGE_ID };
function loadServerModule() {
    return (0, eval)('import("@minecraft/server")');
}
function loadCapabilityHandlers() {
    return __awaiter(this, void 0, void 0, function* () {
        const [playerSnapshotModule, inventorySnapshotModule, findEntitiesModule, worldCommandModule,] = yield Promise.all([
            import("./capabilities/getPlayerSnapshot"),
            import("./capabilities/getInventorySnapshot"),
            import("./capabilities/findEntities"),
            import("./capabilities/runWorldCommand"),
        ]);
        return {
            get_player_snapshot: (_event, payload) => playerSnapshotModule.handleGetPlayerSnapshot(payload),
            get_inventory_snapshot: (_event, payload) => inventorySnapshotModule.handleGetInventorySnapshot(payload),
            find_entities: (event, payload) => findEntitiesModule.handleFindEntities(event, payload),
            run_world_command: (_event, payload) => worldCommandModule.handleRunWorldCommand(payload),
        };
    });
}
export function shouldHandleScriptEvent(messageId) {
    return messageId === BRIDGE_MESSAGE_ID;
}
export function handleBridgeScriptEvent(event) {
    return __awaiter(this, void 0, void 0, function* () {
        var _a;
        if (!shouldHandleScriptEvent(event.id)) {
            return;
        }
        const request = JSON.parse(event.message);
        const capabilityHandlers = yield loadCapabilityHandlers();
        const handler = capabilityHandlers[request.capability];
        const response = handler
            ? handler(event, (_a = request.payload) !== null && _a !== void 0 ? _a : {})
            : {
                ok: false,
                payload: {
                    error: `未知桥接能力: ${request.capability}`,
                },
            };
        const { sendBridgeResponseChunks } = yield import("./toolPlayer");
        sendBridgeResponseChunks(request.request_id, JSON.stringify(response));
    });
}
export function registerBridgeRouter() {
    if (isBridgeRouterRegistered) {
        return;
    }
    isBridgeRouterRegistered = true;
    void loadServerModule().then(({ system }) => {
        system.afterEvents.scriptEventReceive.subscribe((event) => {
            if (!shouldHandleScriptEvent(event.id)) {
                return;
            }
            void handleBridgeScriptEvent(event);
        });
    });
}
//# sourceMappingURL=router.js.map