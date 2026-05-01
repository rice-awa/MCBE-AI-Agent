import type { Player } from "@minecraft/server";

import { sendUiChatMessage } from "../../bridge/toolPlayer";
import { buildAgentChatCommand } from "../commands";
import { appendHistoryItem, createHistoryId } from "../history";
import { createModalForm, showModalFormSafely } from "../forms/formAdapter";
import type { AgentUiState } from "../state";
import { saveAgentUiState } from "../storage";
import { recordPromptSent, syncLocalHistoryCount } from "../stats";
import type { AgentPanelRoute } from "./routes";
import { CLOSE_ROUTE, MAIN_ROUTE } from "./routes";

export async function showChatInputPanel(
  player: Player,
  uiState: AgentUiState,
): Promise<AgentPanelRoute> {
  const form = createModalForm("发送 AI 聊天")
    .textField("消息内容", "输入要发送给 AI 的内容", {
      defaultValue: uiState.lastPrompt.getData(),
    })
    .toggle("发送后返回主面板", { defaultValue: true })
    .submitButton("发送");

  const response = await showModalFormSafely(player, form);
  if (response === undefined) {
    saveAgentUiState(player, uiState);
    return CLOSE_ROUTE;
  }

  if (response.canceled) {
    return MAIN_ROUTE;
  }

  const message = String(response.formValues?.[0] ?? "").trim();
  const shouldReturnToMain = Boolean(response.formValues?.[1] ?? true);

  if (!message) {
    player.sendMessage("MCBE AI Agent: 消息不能为空。");
    return { panel: "chatInput" };
  }

  const now = Date.now();
  if (uiState.settings.autoSaveHistory) {
    uiState.history = appendHistoryItem(
      uiState.history,
      {
        id: createHistoryId("ui", now),
        role: "user",
        content: message,
        createdAt: now,
        source: "ui",
      },
      uiState.settings.maxHistoryItems,
    );
  }
  uiState.lastPrompt.setData(message);
  uiState.lastResponsePreview.setData(
    uiState.settings.autoSaveHistory
      ? "已记录到本地历史；第一阶段暂未启用响应同步。"
      : "已发送本地提示；自动保存历史已关闭。",
  );
  uiState.bridgeStatus.setData("connecting");
  uiState.stats = syncLocalHistoryCount(recordPromptSent(uiState.stats, now), uiState.history.length);

  const command = buildAgentChatCommand(message);

  try {
    sendUiChatMessage(player.name, message);
    uiState.bridgeStatus.setData("sent");
    player.sendMessage("MCBE AI Agent: 消息已发送至 AI 服务。");
  } catch {
    uiState.bridgeStatus.setData("error");
    player.sendMessage(
      `MCBE AI Agent: 自动发送失败，请在聊天框手动发送：${command}`,
    );
  }

  const saveResult = saveAgentUiState(player, uiState);
  if (!saveResult.ok) {
    player.sendMessage("MCBE AI Agent: 保存历史失败，本次仅内存生效。");
  }

  if (shouldReturnToMain) {
    return MAIN_ROUTE;
  }

  return CLOSE_ROUTE;
}
