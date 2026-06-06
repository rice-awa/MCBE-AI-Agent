// Mock for @minecraft/server - provides minimal stubs for vitest unit tests

type ScriptEventCallback = (event: { id: string; message: string }) => void;

const scriptEventSubscribers = new Set<ScriptEventCallback>();
let mockPlayers: unknown[] = [];

export function __resetMinecraftServerMock(): void {
  scriptEventSubscribers.clear();
  mockPlayers = [];
}

export function __setMockPlayers(players: unknown[]): void {
  mockPlayers = players;
}

export function __emitScriptEvent(event: { id: string; message: string }): void {
  for (const subscriber of scriptEventSubscribers) {
    subscriber(event);
  }
}

export const system = {
  afterEvents: {
    scriptEventReceive: {
      subscribe: (callback: ScriptEventCallback) => {
        scriptEventSubscribers.add(callback);
        return callback;
      },
      unsubscribe: (callback: ScriptEventCallback) => {
        scriptEventSubscribers.delete(callback);
      },
    },
  },
  currentTick: 0,
  runInterval: () => {},
  run: () => {},
};

export const world = {
  afterEvents: {
    itemUse: {
      subscribe: () => {},
      unsubscribe: () => {},
    },
  },
  getAllPlayers: () => mockPlayers,
  getPlayers: () => mockPlayers,
  getDimension: () => ({
    getEntities: () => [],
    runCommand: () => ({ successCount: 0 }),
  }),
};

export const GameMode = {
  Survival: "Survival",
  Creative: "Creative",
  Adventure: "Adventure",
  Spectator: "Spectator",
};

export const EntityComponentTypes = {
  Health: "minecraft:health",
  Inventory: "minecraft:inventory",
};
