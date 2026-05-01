import type { Player } from "@minecraft/server";

import { createActionForm, showActionFormSafely } from "../forms/formAdapter";
import type { AgentUiState } from "../state";
import { saveAgentUiState } from "../storage";
import { resetStats, syncLocalHistoryCount } from "../stats";
import type { AgentPanelRoute } from "./routes";
import { CLOSE_ROUTE, MAIN_ROUTE } from "./routes";

export async function showStatsPanel(
  player: Player,
  uiState: AgentUiState,
): Promise<AgentPanelRoute> {
  uiState.stats = syncLocalHistoryCount(uiState.stats, uiState.history.length);

  const form = createActionForm("统计信息", createStatsBody(uiState))
    .button("返回主面板")
    .button("重置统计")
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
    case 0:
      return MAIN_ROUTE;
    case 1:
      uiState.stats = syncLocalHistoryCount(resetStats(), uiState.history.length);
      saveAgentUiState(player, uiState);
      player.sendMessage("MCBE AI Agent: 统计信息已重置。");
      return MAIN_ROUTE;
    case 2:
    default:
      saveAgentUiState(player, uiState);
      return CLOSE_ROUTE;
  }
}

function createStatsBody(uiState: AgentUiState): string {
  return [
    `打开面板次数: ${uiState.stats.openCount}`,
    `发送消息次数: ${uiState.stats.sentCount}`,
    `本地历史条数: ${uiState.history.length}`,
    `响应片段数: ${uiState.stats.responseChunkCount}（响应同步已启用）`,
    `最近打开: ${formatTimestamp(uiState.stats.lastOpenedAt)}`,
    `最近发送: ${formatTimestamp(uiState.stats.lastSentAt)}`,
  ].join("\n");
}

function formatTimestamp(timestamp: number): string {
  if (!timestamp) {
    return "无";
  }

  return new Date(timestamp).toLocaleString();
}
