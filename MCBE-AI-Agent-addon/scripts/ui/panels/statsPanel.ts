import type { Player } from "@minecraft/server";

import { createCustomForm, createDduiObservable, showCustomFormSafely } from "../forms/formAdapter";
import type { AgentUiStateV2 } from "../state";
import { getActiveBucket } from "../state";
import { saveAgentUiState } from "../storage";
import { resetGlobalTokens, syncLocalHistoryCount } from "../stats";
import type { AgentPanelRoute } from "./routes";
import { CLOSE_ROUTE, MAIN_ROUTE } from "./routes";

export async function showStatsPanel(player: Player, uiState: AgentUiStateV2): Promise<AgentPanelRoute> {
  const activeBucket = getActiveBucket(uiState);
  uiState.stats = syncLocalHistoryCount(uiState.stats, activeBucket.history.length);

  try {
    const statsBody = createDduiObservable(createStatsBody(uiState));
    let nextRoute: AgentPanelRoute = CLOSE_ROUTE;

    const form = createCustomForm(player, "统计信息")
      .closeButton()
      .label(statsBody)
      .spacer()
      .divider()
      .spacer()
      .button("返回主面板", () => {
        nextRoute = MAIN_ROUTE;
        form.close();
      })
      .button("重置全局统计", () => {
        uiState.stats = syncLocalHistoryCount(resetGlobalTokens(uiState.stats), activeBucket.history.length);
        saveAgentUiState(player, uiState);
        player.sendMessage("MCBE AI Agent: 全局统计已重置。");
        nextRoute = MAIN_ROUTE;
        form.close();
      });

    const shown = await showCustomFormSafely(player, form);
    if (!shown.ok) {
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

function createStatsBody(uiState: AgentUiStateV2): string {
  const s = uiState.stats;
  const bucket = getActiveBucket(uiState);

  const lines: string[] = [
    `── 会话维度 ──`,
    `当前会话: ${bucket.title || "未命名"}`,
    `本轮 token: 输入 ${s.roundInputTokens} / 输出 ${s.roundOutputTokens} / 合计 ${s.roundTokensCombined}`,
    `会话累计 token: 输入 ${s.sessionInputTokens} / 输出 ${s.sessionOutputTokens} / 合计 ${s.sessionTokensCombined}`,
    ``,
    `── 全局维度 ──`,
    `总输入 token: ${s.totalInputTokens}`,
    `总输出 token: ${s.totalOutputTokens}`,
    `总 token 合计: ${s.totalTokensCombined}`,
    ``,
    `── 计数 ──`,
    `消息数: ${bucket.history.length}`,
    `打开面板次数: ${s.openCount}`,
    `发送消息次数: ${s.sentCount}`,
    `响应片段数: ${s.responseChunkCount}`,
    `最近打开: ${formatTimestamp(s.lastOpenedAt)}`,
    `最近发送: ${formatTimestamp(s.lastSentAt)}`,
  ];

  return lines.join("\n");
}

function formatTimestamp(timestamp: number): string {
  if (!timestamp) {
    return "无";
  }

  return new Date(timestamp).toLocaleString();
}
