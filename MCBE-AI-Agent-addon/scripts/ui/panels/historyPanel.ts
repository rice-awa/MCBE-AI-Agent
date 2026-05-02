import type { Player } from "@minecraft/server";

import { clearHistory, formatHistoryItem, getHistoryPage } from "../history";
import { createActionForm, showActionFormSafely } from "../forms/formAdapter";
import type { AgentUiState } from "../state";
import { saveAgentUiState } from "../storage";
import { syncLocalHistoryCount } from "../stats";
import type { AgentPanelRoute } from "./routes";
import { CLOSE_ROUTE, MAIN_ROUTE } from "./routes";

const PAGE_SIZE = 5;

export async function showHistoryPanel(
  player: Player,
  uiState: AgentUiState,
  pageIndex = 0,
): Promise<AgentPanelRoute> {
  const page = getHistoryPage(uiState.history, pageIndex, PAGE_SIZE);
  const body =
    page.items.length === 0
      ? "暂无聊天记录。"
      : [
          `第 ${page.pageIndex + 1}/${page.totalPages} 页，共 ${page.totalItems} 条`,
          "",
          ...page.items.map(formatHistoryItem),
        ].join("\n");

  const form = createActionForm("聊天记录", body)
    .button("上一页")
    .button("下一页")
    .button("清空历史")
    .button("返回主面板")
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
      return { panel: "history", pageIndex: Math.max(0, page.pageIndex - 1) };
    case 1:
      return { panel: "history", pageIndex: Math.min(page.totalPages - 1, page.pageIndex + 1) };
    case 2:
      uiState.history = clearHistory(uiState.history);
      uiState.stats = syncLocalHistoryCount(uiState.stats, 0);
      saveAgentUiState(player, uiState);
      player.sendMessage("MCBE AI Agent: 本地聊天记录已清空。");
      return MAIN_ROUTE;
    case 3:
      return MAIN_ROUTE;
    case 4:
    default:
      saveAgentUiState(player, uiState);
      return CLOSE_ROUTE;
  }
}
