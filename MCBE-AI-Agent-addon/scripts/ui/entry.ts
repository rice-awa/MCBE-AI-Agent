import { system, world } from "@minecraft/server";
import type { Player } from "@minecraft/server";

import { isUiTriggerItem } from "./commands";
import { showAgentConsole } from "./panels/agentConsole";
import { showChatInputPanel } from "./panels/chatInput";
import { showHistoryPanel } from "./panels/historyPanel";
import type { AgentPanelRoute } from "./panels/routes";
import { showSettingsPanel } from "./panels/settingsPanel";
import { showStatsPanel } from "./panels/statsPanel";
import { loadAgentUiState, saveAgentUiState } from "./storage";
import { recordUiOpened } from "./stats";

const OPEN_COOLDOWN_TICKS = 20;
const lastOpenedTicks = new Map<string, number>();
const openPanels = new Set<string>();

export function registerUiEntry(): void {
  world.afterEvents.itemUse.subscribe((event) => {
    if (!isUiTriggerItem(event.itemStack.typeId)) {
      return;
    }

    const player = event.source;
    const currentTick = system.currentTick;
    const lastOpenedTick = lastOpenedTicks.get(player.id) ?? -OPEN_COOLDOWN_TICKS;
    if (currentTick - lastOpenedTick < OPEN_COOLDOWN_TICKS) {
      return;
    }

    lastOpenedTicks.set(player.id, currentTick);
    void openAgentUi(player).catch(() => {
      player.sendMessage("MCBE AI Agent: 面板打开失败，请稍后再试。");
    });
  });
}

export async function openAgentUi(player: Player): Promise<void> {
  if (openPanels.has(player.id)) {
    player.sendMessage("MCBE AI Agent: 面板已打开。");
    return;
  }

  openPanels.add(player.id);
  const uiState = loadAgentUiState(player);
  uiState.stats = recordUiOpened(uiState.stats);
  saveAgentUiState(player, uiState);

  try {
    let route: AgentPanelRoute = { panel: "main" };
    while (route.panel !== "close") {
      switch (route.panel) {
        case "main":
          route = await showAgentConsole(player, uiState);
          break;
        case "chatInput":
          route = await showChatInputPanel(player, uiState);
          break;
        case "history":
          route = await showHistoryPanel(player, uiState, route.pageIndex ?? 0);
          break;
        case "settings":
          route = await showSettingsPanel(player, uiState);
          break;
        case "stats":
          route = await showStatsPanel(player, uiState);
          break;
      }
    }

    saveAgentUiState(player, uiState);
  } finally {
    openPanels.delete(player.id);
  }
}
