// Mock for @minecraft/server - provides minimal stubs for vitest unit tests

export const system = {
  afterEvents: {
    scriptEventReceive: {
      subscribe: () => {},
      unsubscribe: () => {},
    },
  },
  currentTick: 0,
  runInterval: () => {},
  run: (callback: () => void) => {
    // Execute immediately in tests (simulate next tick)
    queueMicrotask(callback);
    return 0;
  },
  runJob: (generator: Generator) => {
    const run = () => {
      const result = generator.next();
      if (!result.done) {
        queueMicrotask(run);
      }
    };
    queueMicrotask(run);
    return 0;
  },
};

type MockBlock = {
  typeId: string;
  isAir: boolean;
  isLiquid: boolean;
  isWaterlogged: boolean;
  isValid: boolean;
  x: number;
  y: number;
  z: number;
  location: { x: number; y: number; z: number };
  dimension: { id: string };
  permutation: {
    getAllStates: () => Record<string, string | number | boolean>;
  };
  components: Record<string, unknown>;
  getComponent: (id: string) => unknown;
  setPermutation: (perm: { typeId: string; states?: Record<string, string | number | boolean> }) => void;
  setType: (typeId: string) => void;
};

const blockStore = new Map<string, MockBlock>();

function blockKey(dim: string, x: number, y: number, z: number): string {
  return `${dim}|${x}|${y}|${z}`;
}

export function __resetBlocks(): void {
  blockStore.clear();
}

export function __setBlock(
  dim: string,
  x: number,
  y: number,
  z: number,
  opts: {
    typeId?: string;
    states?: Record<string, string | number | boolean>;
    isAir?: boolean;
    isLiquid?: boolean;
    isWaterlogged?: boolean;
    components?: Record<string, unknown>;
    isValid?: boolean;
  } = {},
): MockBlock {
  const typeId = opts.typeId ?? "minecraft:air";
  const isAir = opts.isAir ?? typeId === "minecraft:air";
  const states = { ...(opts.states ?? {}) };
  const components = { ...(opts.components ?? {}) };
  const block: MockBlock = {
    typeId,
    isAir,
    isLiquid: opts.isLiquid ?? false,
    isWaterlogged: opts.isWaterlogged ?? false,
    isValid: opts.isValid ?? true,
    x,
    y,
    z,
    location: { x, y, z },
    dimension: { id: dim },
    permutation: {
      getAllStates: () => ({ ...states }),
    },
    components,
    getComponent(id: string) {
      return components[id] ?? components[id.replace(/^minecraft:/, "")] ?? undefined;
    },
    setPermutation(perm) {
      this.typeId = perm.typeId;
      this.isAir = perm.typeId === "minecraft:air";
      const nextStates = { ...(perm.states ?? {}) };
      this.permutation = {
        getAllStates: () => ({ ...nextStates }),
      };
      // clear components when type changes unless air
      if (this.isAir) {
        this.components = {};
      }
    },
    setType(typeIdValue: string) {
      this.setPermutation({ typeId: typeIdValue });
    },
  };
  blockStore.set(blockKey(dim, x, y, z), block);
  return block;
}

export class BlockPermutation {
  typeId: string;
  states: Record<string, string | number | boolean>;

  constructor(typeId: string, states: Record<string, string | number | boolean> = {}) {
    this.typeId = typeId;
    this.states = { ...states };
  }

  static resolve(
    typeId: string,
    states?: Record<string, string | number | boolean>,
  ): BlockPermutation {
    if (!typeId || typeof typeId !== "string") {
      throw new Error("Invalid block type");
    }
    // Simulate invalid state
    if (states) {
      for (const [key, value] of Object.entries(states)) {
        if (key === "__invalid__") {
          throw new Error(`Invalid state: ${key}=${value}`);
        }
      }
    }
    return new BlockPermutation(typeId, states);
  }

  getAllStates(): Record<string, string | number | boolean> {
    return { ...this.states };
  }

  withState(name: string, value: string | number | boolean): BlockPermutation {
    return new BlockPermutation(this.typeId, { ...this.states, [name]: value });
  }
}

export class BlockVolume {
  from: { x: number; y: number; z: number };
  to: { x: number; y: number; z: number };
  constructor(
    from: { x: number; y: number; z: number },
    to: { x: number; y: number; z: number },
  ) {
    this.from = from;
    this.to = to;
  }
}

const knownBlockTypes = new Set([
  "minecraft:air",
  "minecraft:stone",
  "minecraft:dirt",
  "minecraft:grass_block",
  "minecraft:oak_stairs",
  "minecraft:chest",
  "minecraft:oak_sign",
  "minecraft:water",
  "minecraft:sand",
  "minecraft:cobblestone",
]);

export const BlockTypes = {
  get(id: string) {
    return knownBlockTypes.has(id) ? { id } : undefined;
  },
  getAll() {
    return Array.from(knownBlockTypes).map((id) => ({ id }));
  },
  __add(id: string) {
    knownBlockTypes.add(id);
  },
  __reset() {
    // keep defaults
  },
};

const players: Array<{
  name: string;
  location: { x: number; y: number; z: number };
  dimension: { id: string; getBlock: (loc: { x: number; y: number; z: number }) => MockBlock | undefined; fillBlocks?: Function; runCommand?: Function; getEntities?: Function };
  getRotation: () => { x: number; y: number };
  getTags?: () => string[];
  getGameMode?: () => string;
  getComponent?: () => undefined;
}> = [];

export function __setPlayers(
  list: Array<{
    name: string;
    location: { x: number; y: number; z: number };
    dimensionId?: string;
    yaw?: number;
  }>,
): void {
  players.length = 0;
  for (const p of list) {
    const dimId = p.dimensionId ?? "minecraft:overworld";
    players.push({
      name: p.name,
      location: p.location,
      dimension: createDimension(dimId),
      getRotation: () => ({ x: 0, y: p.yaw ?? 0 }),
      getTags: () => [],
      getGameMode: () => "Survival",
      getComponent: () => undefined,
    });
  }
}

function createDimension(id: string) {
  return {
    id,
    getBlock(location: { x: number; y: number; z: number }) {
      const key = blockKey(id, location.x, location.y, location.z);
      if (blockStore.has(key)) {
        return blockStore.get(key);
      }
      // Default air if not set
      return __setBlock(id, location.x, location.y, location.z, {
        typeId: "minecraft:air",
        isAir: true,
      });
    },
    fillBlocks(
      volume: BlockVolume,
      permutation: BlockPermutation,
    ) {
      const minX = Math.min(volume.from.x, volume.to.x);
      const maxX = Math.max(volume.from.x, volume.to.x);
      const minY = Math.min(volume.from.y, volume.to.y);
      const maxY = Math.max(volume.from.y, volume.to.y);
      const minZ = Math.min(volume.from.z, volume.to.z);
      const maxZ = Math.max(volume.from.z, volume.to.z);
      for (let x = minX; x <= maxX; x++) {
        for (let y = minY; y <= maxY; y++) {
          for (let z = minZ; z <= maxZ; z++) {
            const block = this.getBlock({ x, y, z });
            block?.setPermutation(permutation);
          }
        }
      }
      return { getBlockLocationIterator: () => [] };
    },
    runCommand: () => ({ successCount: 0 }),
    getEntities: () => [],
  };
}

export const world = {
  afterEvents: {
    itemUse: {
      subscribe: () => {},
      unsubscribe: () => {},
    },
  },
  getAllPlayers: () => players,
  getPlayers: (opts?: { name?: string }) => {
    if (!opts?.name) return [...players];
    return players.filter((p) => p.name === opts.name);
  },
  getDimension: (id: string) => createDimension(id),
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
