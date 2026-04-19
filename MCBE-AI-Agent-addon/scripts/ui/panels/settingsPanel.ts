import type { Player } from "@minecraft/server";

import { createModalForm, showModalFormSafely } from "../forms/formAdapter";
import type { AgentUiDelivery, AgentUiState } from "../state";
import { saveAgentUiState } from "../storage";
import type { AgentPanelRoute } from "./routes";
import { CLOSE_ROUTE, MAIN_ROUTE } from "./routes";

const DELIVERY_OPTIONS: AgentUiDelivery[] = ["tellraw", "scriptevent"];

export async function showSettingsPanel(
  player: Player,
  uiState: AgentUiState,
): Promise<AgentPanelRoute> {
  const defaultDeliveryIndex = Math.max(
    0,
    DELIVERY_OPTIONS.indexOf(uiState.settings.defaultDelivery),
  );
  const form = createModalForm("MCBE AI Agent 设置")
    .toggle("自动保存历史", { defaultValue: uiState.settings.autoSaveHistory })
    .slider("历史保留条数", 10, 50, {
      defaultValue: uiState.settings.maxHistoryItems,
      valueStep: 5,
    })
    .toggle("显示工具事件", { defaultValue: uiState.settings.showToolEvents })
    .slider("响应预览长度", 60, 240, {
      defaultValue: uiState.settings.responsePreviewLength,
      valueStep: 20,
    })
    .dropdown("默认响应方式", DELIVERY_OPTIONS, { defaultValueIndex: defaultDeliveryIndex })
    .submitButton("保存");

  const response = await showModalFormSafely(player, form);
  if (response === undefined) {
    saveAgentUiState(player, uiState);
    return CLOSE_ROUTE;
  }

  if (response.canceled) {
    return MAIN_ROUTE;
  }

  const values = response.formValues ?? [];
  const maxHistoryItems = Number(values[1] ?? uiState.settings.maxHistoryItems);
  const responsePreviewLength = Number(values[3] ?? uiState.settings.responsePreviewLength);
  const deliveryIndex = Number(values[4] ?? defaultDeliveryIndex);

  uiState.settings = {
    autoSaveHistory: Boolean(values[0]),
    maxHistoryItems: clampToStep(maxHistoryItems, 10, 50),
    showToolEvents: Boolean(values[2]),
    responsePreviewLength: clampToStep(responsePreviewLength, 60, 240),
    defaultDelivery: DELIVERY_OPTIONS[deliveryIndex] ?? "tellraw",
  };
  uiState.history = uiState.history.slice(-uiState.settings.maxHistoryItems);
  uiState.stats.localHistoryCount = uiState.history.length;

  const saveResult = saveAgentUiState(player, uiState);
  player.sendMessage(
    saveResult.ok
      ? "MCBE AI Agent: 设置已保存。"
      : "MCBE AI Agent: 设置保存失败，本次仅内存生效。",
  );

  return MAIN_ROUTE;
}

function clampToStep(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(value)));
}
