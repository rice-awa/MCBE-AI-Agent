// Mock for @minecraft/server - provides minimal stubs for vitest unit tests

export const system = {
  afterEvents: {
    scriptEventReceive: {
      subscribe: () => {},
      unsubscribe: () => {},
    },
  },
  runInterval: () => {},
  run: () => {},
};

export const world = {
  getAllPlayers: () => [],
  getPlayers: () => [],
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
