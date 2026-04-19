import type { ScriptEventCommandMessageAfterEvent } from "@minecraft/server";
import { system } from "@minecraft/server";

import { BRIDGE_MESSAGE_ID } from "./constants";

let isBridgeRouterRegistered = false;
export { BRIDGE_MESSAGE_ID };

type BridgeCapabilityHandler = (
  event: ScriptEventCommandMessageAfterEvent,
  payload: Record<string, unknown>,
) => Record<string, unknown>;

async function loadCapabilityHandlers(): Promise<Record<string, BridgeCapabilityHandler>> {
  const [
    playerSnapshotModule,
    inventorySnapshotModule,
    findEntitiesModule,
    worldCommandModule,
  ] = await Promise.all([
    import("./capabilities/getPlayerSnapshot"),
    import("./capabilities/getInventorySnapshot"),
    import("./capabilities/findEntities"),
    import("./capabilities/runWorldCommand"),
  ]);

  return {
    get_player_snapshot: (_event, payload) =>
      playerSnapshotModule.handleGetPlayerSnapshot(payload as { target?: string }),
    get_inventory_snapshot: (_event, payload) =>
      inventorySnapshotModule.handleGetInventorySnapshot(payload as { target?: string }),
    find_entities: (event, payload) =>
      findEntitiesModule.handleFindEntities(
        event,
        payload as { entity_type: string; radius?: number; target?: string },
      ),
    run_world_command: (_event, payload) =>
      worldCommandModule.handleRunWorldCommand(payload as { command?: string }),
  };
}

export function shouldHandleScriptEvent(messageId: string): boolean {
  return messageId === BRIDGE_MESSAGE_ID;
}

export async function handleBridgeScriptEvent(
  event: ScriptEventCommandMessageAfterEvent,
): Promise<void> {
  if (!shouldHandleScriptEvent(event.id)) {
    return;
  }

  const request = JSON.parse(event.message) as {
    request_id: string;
    capability: string;
    payload?: Record<string, unknown>;
  };
  const capabilityHandlers = await loadCapabilityHandlers();
  const handler = capabilityHandlers[request.capability];
  const response = handler
    ? handler(event, request.payload ?? {})
    : {
        ok: false,
        payload: {
          error: `未知桥接能力: ${request.capability}`,
        },
      };

  const { sendBridgeResponseChunks } = await import("./toolPlayer");
  sendBridgeResponseChunks(request.request_id, JSON.stringify(response));
}

export function registerBridgeRouter(): void {
  if (isBridgeRouterRegistered) {
    return;
  }

  isBridgeRouterRegistered = true;

  system.afterEvents.scriptEventReceive.subscribe((event) => {
    if (!shouldHandleScriptEvent(event.id)) {
      return;
    }

    void handleBridgeScriptEvent(event);
  });
}
