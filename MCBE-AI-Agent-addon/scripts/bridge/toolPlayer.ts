import { GameMode, world, system } from "@minecraft/server";
import { spawnSimulatedPlayer } from "@minecraft/server-gametest";

import { chunkBridgePayload, chunkUiChatPayload } from "./chunking";
import {
  BRIDGE_MAX_CHUNK_CONTENT_LENGTH,
  TOOL_PLAYER_NAME,
} from "./constants";

const TOOL_PLAYER_DIMENSION = "overworld";
const TOOL_PLAYER_LOCATION = { x: 300000, y: 100, z: 300000 };
const TOOL_PLAYER_CHECK_INTERVAL_TICKS = 20 * 30;

let isToolPlayerInitialized = false;

export function ensureToolPlayer(): void {
  const existing = world
    .getAllPlayers()
    .find((player) => player.name === TOOL_PLAYER_NAME);

  if (existing) {
    return;
  }

  spawnSimulatedPlayer(
    {
      dimension: world.getDimension(TOOL_PLAYER_DIMENSION),
      ...TOOL_PLAYER_LOCATION,
    },
    TOOL_PLAYER_NAME,
    GameMode.Creative,
  );
}

export function sendBridgeResponseChunks(requestId: string, payload: string): void {
  const toolPlayer = world
    .getAllPlayers()
    .find((player) => player.name === TOOL_PLAYER_NAME);

  if (!toolPlayer) {
    throw new Error("Tool player is not available");
  }

  const chunks = chunkBridgePayload(requestId, payload, BRIDGE_MAX_CHUNK_CONTENT_LENGTH);
  for (const chunk of chunks) {
    toolPlayer.runCommand(`tell @s ${chunk}`);
  }
}

let uiChatSeq = 0;

export function sendUiChatMessage(playerName: string, message: string): void {
  const toolPlayer = world
    .getAllPlayers()
    .find((player) => player.name === TOOL_PLAYER_NAME);

  if (!toolPlayer) {
    throw new Error("Tool player is not available");
  }

  const id = `ui-${Date.now()}-${++uiChatSeq}`;
  const payload = JSON.stringify({ player: playerName, message });
  const chunks = chunkUiChatPayload(id, payload, BRIDGE_MAX_CHUNK_CONTENT_LENGTH);
  for (const chunk of chunks) {
    toolPlayer.runCommand(`tell @s ${chunk}`);
  }
}

export function initializeToolPlayer(): void {
  if (isToolPlayerInitialized) {
    return;
  }

  ensureToolPlayer();
  isToolPlayerInitialized = true;

  system.runInterval(() => {
    ensureToolPlayer();
  }, TOOL_PLAYER_CHECK_INTERVAL_TICKS);
}
