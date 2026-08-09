import type { ScriptEventCommandMessageAfterEvent } from "@minecraft/server";

import { handleFindEntities } from "./findEntities";
import { handleGetCapabilities } from "./getCapabilities";
import { handleGetInventorySnapshot } from "./getInventorySnapshot";
import { handleGetLookBlock } from "./getLookBlock";
import { handleGetPlayerSnapshot } from "./getPlayerSnapshot";
import { handleRunWorldCommand } from "./runWorldCommand";
import { handleEditBlocks, handleInspectBlock } from "./blocks/index";

export type CapabilityContext = {
  caller: { kind: "server" };
  requestVersion: 1 | 2;
  event: Pick<ScriptEventCommandMessageAfterEvent, "id" | "message" | "sourceType">;
};

export type CapabilityHandler = (
  capability: string,
  payload: Record<string, unknown>,
  context: CapabilityContext
) => Record<string, unknown> | Promise<Record<string, unknown>>;

export type CapabilityAdvertisement = {
  name: string;
  enabled: boolean;
  version: number;
};

export type CapabilityRegistration = {
  handler: CapabilityHandler;
  advertisement?: CapabilityAdvertisement;
};

const withEvent =
  (handler: (event: CapabilityContext["event"], payload: Record<string, unknown>) => unknown): CapabilityHandler =>
  (_capability, payload, context) =>
    handler(context.event, payload) as Record<string, unknown>;

const withPayload =
  (handler: (payload: Record<string, unknown>) => unknown): CapabilityHandler =>
  (_capability, payload) =>
    handler(payload) as Record<string, unknown>;

/** Single source for executable handlers and advertised capability metadata. */
export const capabilityRegistry: Record<string, CapabilityRegistration> = {
  get_player_snapshot: {
    handler: withPayload((payload) => handleGetPlayerSnapshot(payload as { target?: string })),
    advertisement: { name: "get_player_snapshot", enabled: true, version: 1 },
  },
  get_inventory_snapshot: {
    handler: withPayload((payload) => handleGetInventorySnapshot(payload as { target?: string })),
    advertisement: { name: "get_inventory_snapshot", enabled: true, version: 1 },
  },
  find_entities: {
    handler: withEvent((event, payload) =>
      handleFindEntities(
        event as Parameters<typeof handleFindEntities>[0],
        payload as { entity_type: string; radius?: number; target?: string }
      )
    ),
    advertisement: { name: "find_entities", enabled: true, version: 1 },
  },
  run_world_command: {
    handler: withPayload((payload) => handleRunWorldCommand(payload as { command?: string })),
    advertisement: { name: "run_world_command", enabled: true, version: 1 },
  },
  get_look_block: {
    handler: withPayload((payload) => handleGetLookBlock(payload)),
    advertisement: { name: "get_look_block", enabled: true, version: 1 },
  },
  inspect_block: {
    handler: withPayload((payload) => handleInspectBlock(payload as Parameters<typeof handleInspectBlock>[0])),
    advertisement: { name: "inspect_block", enabled: true, version: 1 },
  },
  edit_blocks: {
    handler: withPayload((payload) => handleEditBlocks(payload as Parameters<typeof handleEditBlocks>[0])),
    advertisement: { name: "edit_blocks", enabled: true, version: 1 },
  },
  get_capabilities: {
    handler: withPayload((payload) => handleGetCapabilities(payload)),
  },
};

export const defaultCapabilityRegistry: Record<string, CapabilityHandler> = Object.fromEntries(
  Object.entries(capabilityRegistry).map(([name, registration]) => [name, registration.handler])
) as Record<string, CapabilityHandler>;

export function projectCapabilityAdvertisements(): CapabilityAdvertisement[] {
  return Object.values(capabilityRegistry)
    .map((registration) => registration.advertisement)
    .filter(
      (advertisement): advertisement is CapabilityAdvertisement =>
        advertisement !== undefined && advertisement.enabled
    );
}
