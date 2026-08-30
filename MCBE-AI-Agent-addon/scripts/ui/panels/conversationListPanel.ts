import type { Player } from "@minecraft/server";

import { createCustomForm, createDduiObservable, showCustomFormSafely } from "../forms/formAdapter";
import type { DduiObservable } from "../forms/formAdapter";
import type { AgentUiStateV2 } from "../state";
import { saveAgentUiState } from "../storage";
import { formatResponseError, requestSession } from "../../bridge/sessionClient";
import type { SessionConversationInfo } from "../../bridge/sessionClient";
import type { AgentPanelRoute } from "./routes";
import { MAIN_ROUTE } from "./routes";

const PAGE_SIZE = 5;

/**
 * 会话列表面板。
 * 每页 5 个会话，点击切换，附"新会话"按钮。
 */
export async function showConversationListPanel(player: Player, uiState: AgentUiStateV2): Promise<AgentPanelRoute> {
  try {
    // Fetch session list from Python
    const resp = await requestSession("list", { player_name: player.name });
    const conversations: SessionConversationInfo[] = Array.isArray(resp.data?.conversations)
      ? (resp.data.conversations as SessionConversationInfo[])
      : [];

    // Fallback: use local conversation order if remote unavailable
    const localConversations = !resp.ok
      ? uiState.conversationOrder.map((id) => {
          const bucket = uiState.conversations[id];
          return {
            id: bucket?.id ?? id,
            short_id: bucket?.shortId ?? 0,
            title: bucket?.title ?? "",
            message_count: bucket?.history.length ?? 0,
            is_active: id === uiState.activeConversationId,
          };
        })
      : conversations;

    let currentPage = 0;
    const page = createDduiObservable(formatPage(localConversations, currentPage));
    const pageInfo = createDduiObservable(formatPageInfo(localConversations, currentPage));
    let nextRoute: AgentPanelRoute = MAIN_ROUTE;

    const refreshPage = () => {
      page.setData(formatPage(localConversations, currentPage));
      pageInfo.setData(formatPageInfo(localConversations, currentPage));
    };

    // 固定数量按钮槽：每页 PAGE_SIZE 个会话槽，翻页时原地更新 label/target
    const slots: { label: DduiObservable<string>; target: SessionConversationInfo | null }[] = [];
    for (let i = 0; i < PAGE_SIZE; i++) {
      slots.push({ label: createDduiObservable(""), target: null });
    }

    const renderPage = () => {
      const start = currentPage * PAGE_SIZE;
      const pageItems = localConversations.slice(start, start + PAGE_SIZE);
      for (let i = 0; i < PAGE_SIZE; i++) {
        const slot = slots[i];
        const conv = pageItems[i];
        if (conv) {
          slot.target = conv;
          slot.label.setData(
            `#${conv.short_id} · ${conv.title || "未命名"} · ${conv.message_count}轮${conv.is_active ? " ★" : ""}`
          );
        } else {
          slot.target = null;
          slot.label.setData("（无）");
        }
      }
    };

    // 初始化时绑定当前页
    renderPage();

    const form = createCustomForm(player, "会话列表")
      .closeButton()
      .label(pageInfo)
      .spacer()
      .divider()
      .spacer()
      .label(page)
      .spacer()
      .divider()
      .spacer();

    // 会话按钮槽（固定数量，可点击切换）
    for (const slot of slots) {
      form.button(slot.label, () => {
        const switchConv = async () => {
          if (!slot.target) {
            player.sendMessage("MCBE AI Agent: 该位置暂无会话。");
            return;
          }
          const switchResp = await requestSession("switch", {
            cid: slot.target.id,
            player_name: player.name,
          });
          if (switchResp.ok) {
            // Switch active conversation in local state
            uiState.activeConversationId = slot.target.id;
            saveAgentUiState(player, uiState);
          } else {
            player.sendMessage(`MCBE AI Agent: 切换会话失败: ${formatResponseError(switchResp.error)}`);
          }
          form.close();
        };
        void switchConv();
      });
    }

    form
      .spacer()
      .button("上一页", () => {
        if (currentPage > 0) {
          currentPage--;
          refreshPage();
          renderPage();
        } else {
          player.sendMessage("MCBE AI Agent: 已是第一页。");
        }
      })
      .button("下一页", () => {
        const totalPages = Math.max(1, Math.ceil(localConversations.length / PAGE_SIZE));
        if (currentPage < totalPages - 1) {
          currentPage++;
          refreshPage();
          renderPage();
        } else {
          player.sendMessage("MCBE AI Agent: 已是最后一页。");
        }
      })
      .spacer()
      .button("＋ 新会话", () => {
        const createNew = async () => {
          const newResp = await requestSession("new", { player_name: player.name });
          if (newResp.ok && newResp.data) {
            const data = newResp.data as { conversation_id?: string; short_id?: number; title?: string };
            if (data.conversation_id) {
              // Update local state
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
            }
          }
          saveAgentUiState(player, uiState);
          form.close();
        };
        void createNew();
      })
      .spacer()
      .button("返回", () => {
        saveAgentUiState(player, uiState);
        form.close();
      });

    const shown = await showCustomFormSafely(player, form);
    if (!shown.ok || shown.closedByUser) {
      saveAgentUiState(player, uiState);
      return MAIN_ROUTE;
    }

    return nextRoute;
  } catch {
    player.sendMessage("MCBE AI Agent: 表单暂时无法打开，请稍后再试。");
    saveAgentUiState(player, uiState);
    return MAIN_ROUTE;
  }
}

function formatPage(conversations: SessionConversationInfo[], pageIndex: number): string {
  const totalPages = Math.max(1, Math.ceil(conversations.length / PAGE_SIZE));
  const start = pageIndex * PAGE_SIZE;
  const pageItems = conversations.slice(start, start + PAGE_SIZE);

  if (pageItems.length === 0) {
    return "暂无会话。";
  }

  return pageItems
    .map(
      (conv) =>
        `#${conv.short_id} · ${conv.title || "未命名"} · ${conv.message_count}条${conv.is_active ? " [当前]" : ""}`
    )
    .join("\n\n---\n\n");
}

function formatPageInfo(conversations: SessionConversationInfo[], pageIndex: number): string {
  const totalPages = Math.max(1, Math.ceil(conversations.length / PAGE_SIZE));
  return `第 ${pageIndex + 1} 页 / 共 ${totalPages} 页 · ${conversations.length}个会话`;
}
