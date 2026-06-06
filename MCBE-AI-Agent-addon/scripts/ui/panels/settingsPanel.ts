import type { Player } from "@minecraft/server";

import {
  createCustomForm,
  createDduiObservable,
  showCustomFormSafely,
} from "../forms/formAdapter";
import { clearHistory } from "../history";
import type { AgentUiDelivery, AgentUiState } from "../state";
import { saveAgentUiState } from "../storage";
import { syncLocalHistoryCount } from "../stats";
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
      .slider("历史保留条数", maxHistoryItems, 10, 50, { step: 5 })
      .spacer()
      .toggle("显示工具事件", showToolEvents)
      .spacer()
      .slider("响应预览长度", responsePreviewLength, 60, 240, { step: 20 })
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
          maxHistoryItems: clampToStep(maxHistoryItems.getData(), 10, 50, 5),
          showToolEvents: showToolEvents.getData(),
          responsePreviewLength: clampToStep(responsePreviewLength.getData(), 60, 240, 20),
          defaultDelivery: DELIVERY_OPTIONS[defaultDelivery.getData()] ?? "tellraw",
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
        form.close();
      })
      .button("清空历史", () => {
        uiState.history = clearHistory(uiState.history);
        uiState.stats = syncLocalHistoryCount(uiState.stats, 0);
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

function clampToStep(value: number, min: number, max: number, step: number): number {
  const stepped = Math.round((value - min) / step) * step + min;
  return Math.min(max, Math.max(min, stepped));
}
