import type { Player } from "@minecraft/server";

import { clearHistory, getHistoryPage, summarizeHistoryItem } from "../history";
import {
  createCustomForm,
  createDduiObservable,
  showCustomFormSafely,
} from "../forms/formAdapter";
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
  try {
    const page = getHistoryPage(uiState.history, pageIndex, PAGE_SIZE);
    const body =
      page.items.length === 0
        ? "暂无聊天记录。"
        : [
            `第 ${page.pageIndex + 1}/${page.totalPages} 页，共 ${page.totalItems} 条`,
            "",
            ...page.items.map((item) => summarizeHistoryItem(item, uiState.settings.responsePreviewLength)),
          ].join("\n");
    const historyBody = createDduiObservable(body);
    let nextRoute: AgentPanelRoute = CLOSE_ROUTE;

    const form = createCustomForm(player, "聊天记录")
      .closeButton()
      .label(historyBody)
      .divider()
      .button("上一页", () => {
        nextRoute = { panel: "history", pageIndex: Math.max(0, page.pageIndex - 1) };
        form.close();
      })
      .button("下一页", () => {
        nextRoute = { panel: "history", pageIndex: Math.min(page.totalPages - 1, page.pageIndex + 1) };
        form.close();
      })
      .button("清空历史", () => {
        uiState.history = clearHistory(uiState.history);
        uiState.stats = syncLocalHistoryCount(uiState.stats, 0);
        saveAgentUiState(player, uiState);
        player.sendMessage("MCBE AI Agent: 本地聊天记录已清空。");
        nextRoute = MAIN_ROUTE;
        form.close();
      })
      .button("返回主面板", () => {
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
