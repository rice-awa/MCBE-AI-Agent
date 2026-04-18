import { world } from "@minecraft/server";

import { chunkBridgePayload } from "./chunking";
import {
  BRIDGE_MAX_CHUNK_CONTENT_LENGTH,
  TOOL_PLAYER_NAME,
} from "./constants";

const TOOL_PLAYER_DIMENSION = "overworld";
const TOOL_PLAYER_LOCATION = { x: 300000, y: 100, z: 300000 };
const TOOL_PLAYER_CHECK_INTERVAL_TICKS = 20 * 30;

let isToolPlayerInitialized = false;

function loadServerModule(): Promise<typeof import("@minecraft/server")> {
  return (0, eval)('import("@minecraft/server")') as Promise<typeof import("@minecraft/server")>;
}

type GameTestModule = {
  spawnSimulatedPlayer: (
    location: {
      dimension: import("@minecraft/server").Dimension;
      x: number;
      y: number;
      z: number;
    },
    name: string,
    gameMode: import("@minecraft/server").GameMode,
  ) => unknown;
};

function loadGameTestModule(): Promise<GameTestModule> {
  return (0, eval)('import("@minecraft/server-gametest")') as Promise<GameTestModule>;
}

export function ensureToolPlayer(): void {
  void Promise.all([loadServerModule(), loadGameTestModule()]).then(
    ([serverModule, gameTestModule]) => {
      const { GameMode, world } = serverModule;
      const existing = world
        .getAllPlayers()
        .find((player) => player.name === TOOL_PLAYER_NAME);

      if (existing) {
        return;
      }

      gameTestModule.spawnSimulatedPlayer(
        {
          dimension: world.getDimension(TOOL_PLAYER_DIMENSION),
          ...TOOL_PLAYER_LOCATION,
        },
        TOOL_PLAYER_NAME,
        GameMode.Creative,
      );
    },
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

export function initializeToolPlayer(): void {
  if (isToolPlayerInitialized) {
    return;
  }

  isToolPlayerInitialized = true;

  void loadServerModule().then(({ system }) => {
    ensureToolPlayer();
    system.runInterval(() => {
      ensureToolPlayer();
    }, TOOL_PLAYER_CHECK_INTERVAL_TICKS);
  });
}
