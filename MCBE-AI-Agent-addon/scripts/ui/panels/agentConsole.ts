import type { Player } from "@minecraft/server";

import { sendUiChatMessage } from "../../bridge/toolPlayer";
import { buildAgentChatCommand } from "../commands";
import { appendHistoryItem, createHistoryId, summarizeHistoryItem } from "../history";
import {
  createCustomForm,
  createDduiObservable,
  showCustomFormSafely,
} from "../forms/formAdapter";
import type { AgentUiState } from "../state";
import { saveAgentUiState } from "../storage";
import { recordPromptSent, syncLocalHistoryCount } from "../stats";
import type { AgentPanelRoute } from "./routes";
import { CLOSE_ROUTE, MAIN_ROUTE } from "./routes";

const CONVERSATION_PREVIEW_LIMIT = 10;

export async function showAgentConsole(
  player: Player,
  uiState: AgentUiState,
): Promise<AgentPanelRoute> {
  try {
    const summary = createDduiObservable(buildSummary(uiState));
    const conversationBody = createDduiObservable(buildConversationBody(uiState));
    const messageValue = createDduiObservable("");
    let nextRoute: AgentPanelRoute = MAIN_ROUTE;

    const refreshConversation = () => {
      summary.setData(buildSummary(uiState));
      conversationBody.setData(buildConversationBody(uiState));
    };
    uiState.refreshConversation = refreshConversation;

    const form = createCustomForm(player, "MCBE AI Agent")
      .closeButton()
      .label(summary)
      .divider()
      .label(conversationBody)
      .divider()
      .textField("消息内容", messageValue, { description: "发送后面板会保持打开" })
      .button("发送", () => {
        const message = messageValue.getData().trim();
        if (!message) {
          player.sendMessage("MCBE AI Agent: 消息不能为空。");
          return;
        }

        const now = Date.now();
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
        uiState.lastPrompt.setData(message);
        uiState.bridgeStatus.setData("connecting");
        uiState.stats = syncLocalHistoryCount(
          recordPromptSent(uiState.stats, now),
          uiState.history.length,
        );
        messageValue.setData("");
        refreshConversation();

        const command = buildAgentChatCommand(message);
        try {
          sendUiChatMessage(player.name, message);
          uiState.bridgeStatus.setData("sent");
          player.sendMessage("MCBE AI Agent: 消息已发送至 AI 服务。");
        } catch {
          uiState.bridgeStatus.setData("error");
          player.sendMessage(`MCBE AI Agent: 自动发送失败，请在聊天框手动发送：${command}`);
        }

        refreshConversation();
        const saveResult = saveAgentUiState(player, uiState);
        if (!saveResult.ok) {
          player.sendMessage("MCBE AI Agent: 保存历史失败，本次仅内存生效。");
        }
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
        saveAgentUiState(player, uiState);
        form.close();
      });

    const shown = await showCustomFormSafely(player, form);
    if (!shown) {
      saveAgentUiState(player, uiState);
      return CLOSE_ROUTE;
    }

    if (nextRoute.panel !== "main") {
      uiState.refreshConversation = undefined;
    }
    return nextRoute;
  } catch {
    player.sendMessage("MCBE AI Agent: 表单暂时无法打开，请稍后再试。");
    saveAgentUiState(player, uiState);
    uiState.refreshConversation = undefined;
    return CLOSE_ROUTE;
  }
}

function buildConversationBody(uiState: AgentUiState): string {
  const items = uiState.history.slice(-CONVERSATION_PREVIEW_LIMIT);
  if (items.length === 0) {
    return "暂无对话记录。输入消息开始聊天。";
  }

  return items
    .map((item) => summarizeHistoryItem(item, uiState.settings.responsePreviewLength))
    .join("\n");
}

function buildSummary(uiState: AgentUiState): string {
  return [
    `桥接状态: ${uiState.bridgeStatus.getData()}`,
    `历史条数: ${uiState.history.length}`,
    `发送次数: ${uiState.stats.sentCount}`,
  ].join("\n");
}
