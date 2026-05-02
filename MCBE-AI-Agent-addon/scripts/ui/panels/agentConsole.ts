import type { Player } from "@minecraft/server";

import {
  createCustomForm,
  createDduiObservable,
  showCustomFormSafely,
} from "../forms/formAdapter";
import type { AgentUiState } from "../state";
import { saveAgentUiState } from "../storage";
import type { AgentPanelRoute } from "./routes";
import { CLOSE_ROUTE } from "./routes";

export async function showAgentConsole(
  player: Player,
  uiState: AgentUiState,
): Promise<AgentPanelRoute> {
  try {
    const summary = createDduiObservable(buildSummary(uiState));
    let nextRoute: AgentPanelRoute = CLOSE_ROUTE;

    const form = createCustomForm(player, "MCBE AI Agent")
      .closeButton()
      .label(summary)
      .divider()
      .button("发送消息", () => {
        nextRoute = { panel: "chatInput" };
        form.close();
      })
      .button("聊天记录", () => {
        nextRoute = { panel: "history" };
        form.close();
      })
      .button("设置", () => {
        nextRoute = { panel: "settings" };
        form.close();
      })
      .button("统计信息", () => {
        nextRoute = { panel: "stats" };
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

function buildSummary(uiState: AgentUiState): string {
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
