import { registerBridgeRouter } from "./bridge/router";
import { initializeToolPlayer } from "./bridge/toolPlayer";
function registerUiEntry() {
    // UI entry will be added in a later task.
}
export function initializeAddon() {
    initializeToolPlayer();
    registerBridgeRouter();
    registerUiEntry();
}
//# sourceMappingURL=bootstrap.js.map