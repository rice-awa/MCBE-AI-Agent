import type { Player } from "@minecraft/server";

import { createCustomForm, createDduiObservable, showCustomFormSafely } from "../forms/formAdapter";
import type { AgentUiStateV2 } from "../state";
import { getActiveBucket } from "../state";
import type { ChatHistoryItem } from "../history";
import { getHistoryPage, formatHistoryItem } from "../history";
import { saveAgentUiState } from "../storage";
import type { AgentPanelRoute } from "./routes";
import { MAIN_ROUTE } from "./routes";

const PAGE_SIZE = 5;

/**
 * 全量对话预览面板。
 * 每页 5 条，倒序显示，只读翻页。
 * showToolEvents 设置控制是否显示 tool 条目。
 */
export async function showConversationPreviewPanel(
  player: Player,
  uiState: AgentUiStateV2,
): Promise<AgentPanelRoute> {
  try {
    const bucket = getActiveBucket(uiState);
    const allHistory = bucket.history;
    const showToolEvents = uiState.settings.showToolEvents;

    // Filter history based on showToolEvents setting
    const displayHistory = showToolEvents
      ? allHistory
      : allHistory.filter((item: ChatHistoryItem) => item.role !== "tool");

    let currentPage = 0;
    const page = createDduiObservable(formatPage(displayHistory, currentPage));
    const pageInfo = createDduiObservable(formatPageInfo(displayHistory, currentPage));
    let nextRoute: AgentPanelRoute = MAIN_ROUTE;

    const refreshPage = () => {
      page.setData(formatPage(displayHistory, currentPage));
      pageInfo.setData(formatPageInfo(displayHistory, currentPage));
    };

    const form = createCustomForm(player, "全量对话记录")
      .closeButton()
      .label(pageInfo)
      .spacer()
      .divider()
      .spacer()
      .label(page)
      .spacer()
      .divider()
      .spacer()
      .button("上一页", () => {
        if (currentPage > 0) {
          currentPage--;
          refreshPage();
        } else {
          player.sendMessage("MCBE AI Agent: 已是第一页。");
        }
      })
      .button("下一页", () => {
        const totalPages = Math.max(1, Math.ceil(displayHistory.length / PAGE_SIZE));
        if (currentPage < totalPages - 1) {
          currentPage++;
          refreshPage();
        } else {
          player.sendMessage("MCBE AI Agent: 已是最后一页。");
        }
      })
      .spacer()
      .button("返回", () => {
        saveAgentUiState(player, uiState);
        form.close();
      });

    const shown = await showCustomFormSafely(player, form);
    if (!shown.ok) {
      saveAgentUiState(player, uiState);
      return MAIN_ROUTE;
    }

    return nextRoute;
  } catch {
    player.sendMessage("MCBE AI Agent: 表单暂时无法打开，请稍后再试。");
    saveAgentUiState(player, uiState);
    return MAIN_ROUTE;
  }
}

function formatPage(history: ChatHistoryItem[], pageIndex: number): string {
  const pageData = getHistoryPage(history, pageIndex, PAGE_SIZE);
  if (pageData.items.length === 0) {
    return "暂无对话记录。";
  }

  return pageData.items.map((item) => formatHistoryItem(item)).join("\n\n---\n\n");
}

function formatPageInfo(history: ChatHistoryItem[], pageIndex: number): string {
  const totalPages = Math.max(1, Math.ceil(history.length / PAGE_SIZE));
  return `第 ${pageIndex + 1} 页 / 共 ${totalPages} 页 · 共 ${history.length} 条`;
}
