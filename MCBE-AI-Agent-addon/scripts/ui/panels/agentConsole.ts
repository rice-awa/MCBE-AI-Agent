import type { Player } from "@minecraft/server";

import { sendApprovalDecision, sendUiChatMessage } from "../../bridge/toolPlayer";
import { formatResponseError, requestSession } from "../../bridge/sessionClient";
import { buildAgentChatCommand } from "../commands";
import { createHistoryId, formatRecentConversation, getRecentTurns } from "../history";
import type { AgentUiStateV2 } from "../state";
import { getActiveBucket } from "../state";
import { createCustomForm, createDduiObservable, showCustomFormSafely } from "../forms/formAdapter";
import { saveAgentUiState } from "../storage";
import { recordPromptSent, syncLocalHistoryCount } from "../stats";
import type { AgentPanelRoute } from "./routes";
import { CLOSE_ROUTE, MAIN_ROUTE } from "./routes";

const RECENT_TURNS_COUNT = 5;

/**
 * 主面板 - 重构版。
 *
 * 布局：
 * 1. 标题行: ✦ #short_id · 标题 [切换]
 * 2. 状态行: 桥接状态 / 生成中 / 完成+token
 * 3. 最近 5 轮完整对话（无 tool）
 * 4. 输入框 + [发送] [新会话] [全部对话] [更多]
 */
export async function showAgentConsole(player: Player, uiState: AgentUiStateV2): Promise<AgentPanelRoute> {
  try {
    const bucket = getActiveBucket(uiState);

    // ── Observables ──
    const titleLine = createDduiObservable(buildTitleLine(uiState));
    const statusLine = createDduiObservable(buildStatusLine(uiState));
    const conversationBody = createDduiObservable(buildConversationBody(uiState));
    const messageValue = createDduiObservable("");
    let nextRoute: AgentPanelRoute = MAIN_ROUTE;

    // ── Refresh function (injected into uiState for external calls) ──
    const refreshConversation = () => {
      titleLine.setData(buildTitleLine(uiState));
      statusLine.setData(buildStatusLine(uiState));
      conversationBody.setData(buildConversationBody(uiState));
    };
    uiState.refreshConversation = refreshConversation;

    // ── Build form ──
    const form = createCustomForm(player, "MCBE AI Agent")
      .closeButton()
      // Title row
      .label(titleLine)
      .button("切换", () => {
        nextRoute = { panel: "conversationList" };
        saveAgentUiState(player, uiState);
        form.close();
      })
      .spacer()
      // Status row
      .label(statusLine)
      .spacer()
      .divider()
      .spacer()
      // Conversation body
      .label(conversationBody)
      .spacer()
      .divider()
      .spacer()
      // Input field
      .textField("消息内容", messageValue, { description: "发送后面板会保持打开" })
      .spacer()
      // Four buttons row
      .button("发送", () => {
        // Disable send when streaming
        if (uiState.isStreaming) {
          player.sendMessage("MCBE AI Agent: 正在生成回复，请稍后。");
          return;
        }

        const message = messageValue.getData().trim();
        if (!message) {
          player.sendMessage("MCBE AI Agent: 消息不能为空。");
          return;
        }

        const now = Date.now();
        const activeBucket = getActiveBucket(uiState);

        // Write local history entry (source: "ui")
        activeBucket.history = [
          ...activeBucket.history,
          {
            id: createHistoryId("ui", now),
            role: "user",
            content: message,
            createdAt: now,
            source: "ui",
          },
        ];

        uiState.lastPrompt.setData(message);
        uiState.bridgeStatus.setData("connecting");
        uiState.stats = syncLocalHistoryCount(recordPromptSent(uiState.stats, now), activeBucket.history.length);
        messageValue.setData("");
        refreshConversation();

        // Send via tool player with conversation_id
        const conversationId = uiState.activeConversationId;
        try {
          sendUiChatMessage(player.name, message, conversationId);
          uiState.bridgeStatus.setData("sent");
          player.sendMessage("MCBE AI Agent: 消息已发送至 AI 服务。");
        } catch {
          uiState.bridgeStatus.setData("error");
          const command = buildAgentChatCommand(message);
          player.sendMessage(`MCBE AI Agent: 自动发送失败，请在聊天框手动发送：${command}`);
        }

        refreshConversation();
        const saveResult = saveAgentUiState(player, uiState);
        if (!saveResult.ok) {
          player.sendMessage("MCBE AI Agent: 保存历史失败，本次仅内存生效。");
        }
      })
      .button("新会话", () => {
        const createNew = async () => {
          const resp = await requestSession("new", { player_name: player.name });
          if (resp.ok && resp.data) {
            const data = resp.data as { conversation_id?: string; short_id?: number; title?: string };
            if (data.conversation_id) {
              // Switch local state to new conversation
              uiState.activeConversationId = data.conversation_id;
              if (!uiState.conversations[data.conversation_id]) {
                uiState.conversations[data.conversation_id] = {
                  id: data.conversation_id,
                  shortId: data.short_id ?? 0,
                  title: data.title ?? "",
                  history: [],
                  lastActiveAt: Date.now(),
                };
              }
              if (!uiState.conversationOrder.includes(data.conversation_id)) {
                uiState.conversationOrder.unshift(data.conversation_id);
              }
              // Reset streaming state
              uiState.isStreaming = false;
              uiState.streamingConversationId = null;
              uiState.streamingChars = 0;
              uiState.streamingText = "";
              // Reset token stats for session
              uiState.stats = {
                ...uiState.stats,
                sessionInputTokens: 0,
                sessionOutputTokens: 0,
                sessionTokensCombined: 0,
                roundInputTokens: 0,
                roundOutputTokens: 0,
                roundTokensCombined: 0,
              };
              refreshConversation();
              saveAgentUiState(player, uiState);
            }
          } else {
            player.sendMessage(`MCBE AI Agent: 新建会话失败: ${formatResponseError(resp.error)}`);
          }
        };
        void createNew();
      })
      .button("全部对话", () => {
        nextRoute = { panel: "conversationPreview" };
        saveAgentUiState(player, uiState);
        form.close();
      })
      .button("更多", () => {
        nextRoute = { panel: "more" };
        saveAgentUiState(player, uiState);
        form.close();
      })
      .spacer()
      .divider();

    // Approval controls are scoped to this player's pending records.
    if (uiState.pendingApprovals.size > 0) {
      form
        .spacer()
        .label(createDduiObservable(buildApprovalStatusLine(uiState)))
        .button("同意", () => {
          const first = uiState.pendingApprovals.values().next().value;
          if (!first) {
            player.sendMessage("MCBE AI Agent: 当前没有待审批的工具调用。");
            return;
          }
          void sendApprovalDecision(first.player_name, first.cid, first.approval_id, "approve")
            .then(() => {
              uiState.pendingApprovals.delete(first.approval_id);
              refreshConversation();
            })
            .catch(() => player.sendMessage("MCBE AI Agent: 审批发送失败，请稍后重试。"));
        })
        .button("拒绝", () => {
          const first = uiState.pendingApprovals.values().next().value;
          if (!first) {
            player.sendMessage("MCBE AI Agent: 当前没有待审批的工具调用。");
            return;
          }
          void sendApprovalDecision(first.player_name, first.cid, first.approval_id, "deny")
            .then(() => {
              uiState.pendingApprovals.delete(first.approval_id);
              refreshConversation();
            })
            .catch(() => player.sendMessage("MCBE AI Agent: 审批发送失败，请稍后重试。"));
        })
        .spacer()
        .divider();
    }

    // ── Show form ──
    const shown = await showCustomFormSafely(player, form);
    if (!shown.ok || (shown.closedByUser && nextRoute.panel === "main")) {
      saveAgentUiState(player, uiState);
      uiState.refreshConversation = undefined;
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

// ── Build functions ──

function buildTitleLine(uiState: AgentUiStateV2): string {
  const bucket = getActiveBucket(uiState);

  // Show pending approval count in title when applicable
  const approvalCount = uiState.pendingApprovals.size;
  const approvalPrefix = approvalCount > 0 ? `⏳ [审批${approvalCount}] ` : "";

  if (uiState.isStreaming) {
    const chars = uiState.streamingChars ?? 0;
    return `${approvalPrefix}✦ 生成中 ${chars} chars`;
  }

  const shortId = bucket.shortId > 0 ? `#${bucket.shortId}` : "";
  const title = bucket.title || "未命名";
  return `${approvalPrefix}✦ ${shortId} · ${title}`;
}

function buildStatusLine(uiState: AgentUiStateV2): string {
  if (uiState.isStreaming) {
    const chars = uiState.streamingChars ?? 0;
    return `⏳ 生成中… (${chars} chars)`;
  }

  const stats = uiState.stats;
  if (stats.roundTokensCombined > 0) {
    return `✓ 完成 · 本轮 ${stats.roundInputTokens}/${stats.roundOutputTokens} tokens (合计${stats.roundTokensCombined})`;
  }

  return `桥接状态: ${uiState.bridgeStatus.getData()} · 消息数: ${getActiveBucket(uiState).history.length}`;
}

function buildConversationBody(uiState: AgentUiStateV2): string {
  const bucket = getActiveBucket(uiState);
  const items = getRecentTurns(bucket.history, RECENT_TURNS_COUNT * 2);

  if (items.length === 0 && !uiState.isStreaming) {
    return "暂无对话记录。输入消息开始聊天。";
  }

  const lines = items.map((item) => {
    const roleLabel = item.role === "user" ? "你" : "AI";
    return `${roleLabel}: ${item.content}`;
  });

  // Append streaming text with cursor (typewriter effect)
  if (uiState.isStreaming && uiState.streamingText) {
    lines.push(`AI: ${uiState.streamingText}▌`);
  }

  return lines.join("\n\n---\n\n");
}

/** Build a status line showing pending approvals (empty string if none). */
function buildApprovalStatusLine(uiState: AgentUiStateV2): string {
  const count = uiState.pendingApprovals.size;
  if (count === 0) {
    return "✔ 无待审批";
  }

  const parts: string[] = [];
  let idx = 0;
  for (const info of uiState.pendingApprovals.values()) {
    idx++;
    const batchInfo = info.batch_size && info.batch_size > 1 ? ` [批次 ${info.batch_index}/${info.batch_size}]` : "";
    parts.push(`  ${idx}. ${info.tool_name}${batchInfo}: ${info.args_summary}`);
  }
  return `⏳ 待审批 ${count} 项:\n${parts.join("\n")}`;
}
