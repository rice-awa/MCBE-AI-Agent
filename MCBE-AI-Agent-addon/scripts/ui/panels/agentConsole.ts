import type { Player } from "@minecraft/server";

import { createActionForm, showActionFormSafely } from "../forms/formAdapter";
import type { AgentUiState } from "../state";
import { saveAgentUiState } from "../storage";
import type { AgentPanelRoute } from "./routes";
import { CLOSE_ROUTE } from "./routes";

const MAIN_MENU_BUTTONS = {
  sendMessage: 0,
  history: 1,
  settings: 2,
  stats: 3,
  refresh: 4,
  close: 5,
} as const;

export async function showAgentConsole(
  player: Player,
  uiState: AgentUiState,
): Promise<AgentPanelRoute> {
  const form = createActionForm("MCBE AI Agent", createSummary(uiState))
    .button("发送消息")
    .button("聊天记录")
    .button("设置")
    .button("统计信息")
    .button("刷新")
    .button("关闭");

  const response = await showActionFormSafely(player, form);
  if (response === undefined) {
    saveAgentUiState(player, uiState);
    return CLOSE_ROUTE;
  }

  if (response.canceled || response.selection === undefined) {
    saveAgentUiState(player, uiState);
    return CLOSE_ROUTE;
  }

  switch (response.selection) {
    case MAIN_MENU_BUTTONS.sendMessage:
      return { panel: "chatInput" };
    case MAIN_MENU_BUTTONS.history:
      return { panel: "history" };
    case MAIN_MENU_BUTTONS.settings:
      return { panel: "settings" };
    case MAIN_MENU_BUTTONS.stats:
      return { panel: "stats" };
    case MAIN_MENU_BUTTONS.refresh:
      return { panel: "main" };
    case MAIN_MENU_BUTTONS.close:
    default:
      saveAgentUiState(player, uiState);
      return CLOSE_ROUTE;
  }
}

function createSummary(uiState: AgentUiState): string {
  const lastPrompt = uiState.lastPrompt.getData() || "无";
  const lastResponse = uiState.lastResponsePreview.getData() || "无";

  return [
    `桥接状态: ${uiState.bridgeStatus.getData()}`,
    `最近问题: ${lastPrompt}`,
    `最近响应: ${lastResponse}`,
    `历史条数: ${uiState.history.length}`,
    `发送次数: ${uiState.stats.sentCount}`,
  ].join("\n");
}
