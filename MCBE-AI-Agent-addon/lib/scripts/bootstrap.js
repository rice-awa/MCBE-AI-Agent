import { registerBridgeRouter } from "./bridge/router";
import { initializeToolPlayer } from "./bridge/toolPlayer";
import { registerUiEntry } from "./ui/entry";
export function initializeAddon() {
    initializeToolPlayer();
    registerBridgeRouter();
    registerUiEntry();
}
//# sourceMappingURL=bootstrap.js.map