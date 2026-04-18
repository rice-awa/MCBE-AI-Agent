import { EntityComponentTypes, type EntityInventoryComponent, type ItemStack, type Player, world } from "@minecraft/server";

type InventoryItemSnapshot = {
  slot: number;
  typeId: string;
  amount: number;
  nameTag?: string;
};

type InventorySnapshot = {
  player: string;
  size: number;
  items: InventoryItemSnapshot[];
};

function normalizeInventoryItem(slot: number, item: ItemStack): InventoryItemSnapshot {
  return {
    slot,
    typeId: item.typeId,
    amount: item.amount,
    nameTag: item.nameTag,
  };
}

function buildInventorySnapshot(player: Player): InventorySnapshot {
  const inventory = player.getComponent(EntityComponentTypes.Inventory) as
    | EntityInventoryComponent
    | undefined;
  const container = inventory?.container;
  const items: InventoryItemSnapshot[] = [];

  if (container) {
    for (let slot = 0; slot < container.size; slot += 1) {
      const item = container.getItem(slot);
      if (item) {
        items.push(normalizeInventoryItem(slot, item));
      }
    }
  }

  return {
    player: player.name,
    size: container?.size ?? 0,
    items,
  };
}

export function handleGetInventorySnapshot(payload: { target?: string }): {
  ok: true;
  payload: { inventories: InventorySnapshot[] };
} {
  const target = payload.target;
  const players =
    !target || target === "@a"
      ? world.getAllPlayers()
      : world.getPlayers({ name: target });

  return {
    ok: true,
    payload: {
      inventories: players.map(buildInventorySnapshot),
    },
  };
}
