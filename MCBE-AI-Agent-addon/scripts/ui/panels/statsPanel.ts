import type { Player } from "@minecraft/server";

import {
  createCustomForm,
  createDduiObservable,
  showCustomFormSafely,
} from "../forms/formAdapter";
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

  try {
    const statsBody = createDduiObservable(createStatsBody(uiState));
    let nextRoute: AgentPanelRoute = CLOSE_ROUTE;

    const form = createCustomForm(player, "统计信息")
      .closeButton()
      .label(statsBody)
      .divider()
      .button("返回主面板", () => {
        nextRoute = MAIN_ROUTE;
        form.close();
      })
      .button("重置统计", () => {
        uiState.stats = syncLocalHistoryCount(resetStats(), uiState.history.length);
        saveAgentUiState(player, uiState);
        player.sendMessage("MCBE AI Agent: 统计信息已重置。");
        nextRoute = MAIN_ROUTE;
        form.close();
      })
      .button("关闭", () => {
        nextRoute = CLOSE_ROUTE;
        form.close();
      });

    const shown = await showCustomFormSafely(player, form);
    if (!shown) {
      saveAgentUiState(player, uiState);
      return CLOSE_ROUTE;
    }

    if (nextRoute.panel === "close") {
      saveAgentUiState(player, uiState);
    }
    return nextRoute;
  } catch {
    player.sendMessage("MCBE AI Agent: 表单暂时无法打开，请稍后再试。");
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
