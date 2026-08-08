import type { Player } from "@minecraft/server";

import { createCustomForm, createDduiObservable, showCustomFormSafely } from "../forms/formAdapter";
import { clearHistory } from "../history";
import type { AgentUiDelivery, AgentUiStateV2 } from "../state";
import { saveAgentUiState } from "../storage";
import { syncLocalHistoryCount } from "../stats";
import type { AgentPanelRoute } from "./routes";
import { CLOSE_ROUTE, MAIN_ROUTE } from "./routes";

const DELIVERY_OPTIONS: AgentUiDelivery[] = ["tellraw", "scriptevent"];

/**
 * 设置面板 v2：移除 maxHistoryItems 和 responsePreviewLength。
 * 保留 autoSaveHistory, showToolEvents, defaultDelivery。
 */
export async function showSettingsPanel(player: Player, uiState: AgentUiStateV2): Promise<AgentPanelRoute> {
  try {
    const autoSaveHistory = createDduiObservable(uiState.settings.autoSaveHistory);
    const showToolEvents = createDduiObservable(uiState.settings.showToolEvents);
    const defaultDeliveryIndex = Math.max(0, DELIVERY_OPTIONS.indexOf(uiState.settings.defaultDelivery));
    const defaultDelivery = createDduiObservable(defaultDeliveryIndex);
    let didSave = false;

    const form = createCustomForm(player, "MCBE AI Agent 设置")
      .closeButton()
      .label("调整当前面板偏好，关闭即返回主菜单。")
      .spacer()
      .divider()
      .spacer()
      .toggle("自动保存历史", autoSaveHistory)
      .spacer()
      .toggle("显示工具事件", showToolEvents, { description: "控制全量对话预览中是否显示工具事件" })
      .spacer()
      .dropdown(
        "默认响应方式",
        defaultDelivery,
        DELIVERY_OPTIONS.map((option, index) => ({ label: option, value: index })),
      )
      .spacer()
      .button("保存", () => {
        uiState.settings = {
          autoSaveHistory: autoSaveHistory.getData(),
          showToolEvents: showToolEvents.getData(),
          defaultDelivery: DELIVERY_OPTIONS[defaultDelivery.getData()] ?? "tellraw",
        };

        const saveResult = saveAgentUiState(player, uiState);
        didSave = true;
        player.sendMessage(
          saveResult.ok ? "MCBE AI Agent: 设置已保存。" : "MCBE AI Agent: 设置保存失败，本次仅内存生效。"
        );
        form.close();
      })
      .button("清空历史", () => {
        // Clear history of active conversation only
        const activeBucket = uiState.conversations[uiState.activeConversationId];
        if (activeBucket) {
          activeBucket.history = clearHistory(activeBucket.history);
        }
        uiState.stats = syncLocalHistoryCount(uiState.stats, activeBucket?.history.length ?? 0);
        uiState.lastResponsePreview.setData("");
        saveAgentUiState(player, uiState);
        didSave = true;
        player.sendMessage("MCBE AI Agent: 本地聊天记录已清空。");
        form.close();
      });

    const shown = await showCustomFormSafely(player, form);
    if (!shown.ok) {
      saveAgentUiState(player, uiState);
      return CLOSE_ROUTE;
    }

    if (!didSave) {
      return MAIN_ROUTE;
    }
    return MAIN_ROUTE;
  } catch {
    player.sendMessage("MCBE AI Agent: 表单暂时无法打开，请稍后再试。");
    saveAgentUiState(player, uiState);
    return CLOSE_ROUTE;
  }
}
