import { registerBridgeRouter } from "./bridge/router";
import { initializeToolPlayer } from "./bridge/toolPlayer";

function registerUiEntry(): void {
  // UI entry will be added in a later task.
}

export function initializeAddon(): void {
  initializeToolPlayer();
  registerBridgeRouter();
  registerUiEntry();
}
