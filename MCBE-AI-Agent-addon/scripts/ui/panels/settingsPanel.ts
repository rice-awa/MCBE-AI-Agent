import type { Player } from "@minecraft/server";

import {
  createCustomForm,
  createDduiObservable,
  showCustomFormSafely,
} from "../forms/formAdapter";
import type { AgentUiDelivery, AgentUiState } from "../state";
import { saveAgentUiState } from "../storage";
import type { AgentPanelRoute } from "./routes";
import { CLOSE_ROUTE, MAIN_ROUTE } from "./routes";

const DELIVERY_OPTIONS: AgentUiDelivery[] = ["tellraw", "scriptevent"];

export async function showSettingsPanel(
  player: Player,
  uiState: AgentUiState,
): Promise<AgentPanelRoute> {
  try {
    const autoSaveHistory = createDduiObservable(uiState.settings.autoSaveHistory);
    const maxHistoryItems = createDduiObservable(uiState.settings.maxHistoryItems);
    const showToolEvents = createDduiObservable(uiState.settings.showToolEvents);
    const responsePreviewLength = createDduiObservable(uiState.settings.responsePreviewLength);
    const defaultDelivery = createDduiObservable<AgentUiDelivery>(uiState.settings.defaultDelivery);
    let didSave = false;

    const form = createCustomForm(player, "MCBE AI Agent 设置")
      .closeButton()
      .label("调整当前面板偏好，关闭即返回主菜单。")
      .divider()
      .toggle("自动保存历史", autoSaveHistory)
      .slider("历史保留条数", maxHistoryItems, 10, 50, { step: 5 })
      .toggle("显示工具事件", showToolEvents)
      .slider("响应预览长度", responsePreviewLength, 60, 240, { step: 20 })
      .dropdown(
        "默认响应方式",
        defaultDelivery,
        DELIVERY_OPTIONS.map((option) => ({ label: option, value: option })),
      )
      .spacer()
      .button("保存", () => {
        uiState.settings = {
          autoSaveHistory: autoSaveHistory.getData(),
          maxHistoryItems: clampToStep(maxHistoryItems.getData(), 10, 50, 5),
          showToolEvents: showToolEvents.getData(),
          responsePreviewLength: clampToStep(responsePreviewLength.getData(), 60, 240, 20),
          defaultDelivery: defaultDelivery.getData(),
        };
        uiState.history = uiState.history.slice(-uiState.settings.maxHistoryItems);
        uiState.stats.localHistoryCount = uiState.history.length;

        const saveResult = saveAgentUiState(player, uiState);
        didSave = true;
        player.sendMessage(
          saveResult.ok
            ? "MCBE AI Agent: 设置已保存。"
            : "MCBE AI Agent: 设置保存失败，本次仅内存生效。",
        );
      });

    const shown = await showCustomFormSafely(player, form);
    if (!shown) {
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

function clampToStep(value: number, min: number, max: number, step: number): number {
  const stepped = Math.round((value - min) / step) * step + min;
  return Math.min(max, Math.max(min, stepped));
}
