import { EntityComponentTypes, type EntityHealthComponent, type Player, world } from "@minecraft/server";

type PlayerSnapshot = {
  name: string;
  health: number | null;
  tags: string[];
  location: { x: number; y: number; z: number };
  dimension: string;
  gameMode: string;
};

export function buildPlayerSnapshot(player: Player): PlayerSnapshot {
  const healthComponent = player.getComponent(EntityComponentTypes.Health) as
    | EntityHealthComponent
    | undefined;

  return {
    name: player.name,
    health: healthComponent ? Math.round(healthComponent.currentValue) : null,
    tags: player.getTags(),
    location: {
      x: Math.round(player.location.x),
      y: Math.round(player.location.y),
      z: Math.round(player.location.z),
    },
    dimension: player.dimension.id,
    gameMode: String(player.getGameMode()),
  };
}

export function handleGetPlayerSnapshot(payload: { target?: string }): {
  ok: true;
  payload: { players: PlayerSnapshot[] };
} {
  const target = payload.target;
  const players =
    !target || target === "@a"
      ? world.getAllPlayers()
      : world.getPlayers({ name: target });

  return {
    ok: true,
    payload: {
      players: players.map(buildPlayerSnapshot),
    },
  };
}
