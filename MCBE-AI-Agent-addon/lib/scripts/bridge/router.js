export const BRIDGE_MESSAGE_ID = "mcbeai:bridge_request";
let isBridgeRouterRegistered = false;
function loadServerModule() {
    return (0, eval)('import("@minecraft/server")');
}
export function shouldHandleScriptEvent(messageId) {
    return messageId === BRIDGE_MESSAGE_ID;
}
export function handleBridgeScriptEvent(event) {
    return __awaiter(this, void 0, void 0, function* () {
        if (!shouldHandleScriptEvent(event.id)) {
            return;
        }
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