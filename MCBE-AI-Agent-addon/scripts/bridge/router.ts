import type { ScriptEventCommandMessageAfterEvent } from "@minecraft/server";

export const BRIDGE_MESSAGE_ID = "mcbeai:bridge_request";

let isBridgeRouterRegistered = false;

function loadServerModule(): Promise<typeof import("@minecraft/server")> {
  return (0, eval)('import("@minecraft/server")') as Promise<typeof import("@minecraft/server")>;
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
}

export function registerBridgeRouter(): void {
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
