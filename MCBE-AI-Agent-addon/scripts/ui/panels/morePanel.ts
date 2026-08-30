import type { Player } from "@minecraft/server";

import { createCustomForm, showCustomFormSafely } from "../forms/formAdapter";
import type { AgentUiStateV2 } from "../state";
import { saveAgentUiState } from "../storage";
import type { AgentPanelRoute } from "./routes";
import { CLOSE_ROUTE } from "./routes";

export async function showMorePanel(player: Player, uiState: AgentUiStateV2): Promise<AgentPanelRoute> {
  try {
    let nextRoute: AgentPanelRoute = CLOSE_ROUTE;

    const form = createCustomForm(player, "更多操作")
      .closeButton()
      .button("设置", () => {
        nextRoute = { panel: "settings" };
        form.close();
      })
      .button("统计信息", () => {
        nextRoute = { panel: "stats" };
        form.close();
      })
      .button("会话文件管理", () => {
        nextRoute = { panel: "sessionFiles" };
        form.close();
      })
      .button("关闭", () => {
        saveAgentUiState(player, uiState);
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