import type { Player } from "@minecraft/server";

import { createCustomForm, createDduiObservable, showCustomFormSafely } from "../forms/formAdapter";
import type { AgentUiStateV2 } from "../state";
import { saveAgentUiState } from "../storage";
import { requestSession } from "../../bridge/sessionClient";
import type { SessionSavedInfo } from "../../bridge/sessionClient";
import type { AgentPanelRoute } from "./routes";
import { CLOSE_ROUTE, MAIN_ROUTE } from "./routes";

const PAGE_SIZE = 5;

/**
 * 会话文件管理面板。
 * 保存当前会话 + saved 列表 + 恢复/删除（二次确认）。
 */
export async function showSessionFilesPanel(
  player: Player,
  uiState: AgentUiStateV2,
): Promise<AgentPanelRoute> {
  try {
    let nextRoute: AgentPanelRoute = CLOSE_ROUTE;
    const saveStatus = createDduiObservable("");
    const listBody = createDduiObservable("");

    // Delete confirm state: session_id of entry pending deletion
    let pendingDeleteId: string | null = null;

    // Fetch saved sessions list
    const fetchSaved = async () => {
      const resp = await requestSession("saved", { player_name: player.name });
      if (resp.ok && Array.isArray(resp.data?.saved)) {
        const saved = resp.data.saved as SessionSavedInfo[];
        listBody.setData(formatSavedList(saved, 0, PAGE_SIZE, pendingDeleteId));
      } else {
        listBody.setData(resp.ok ? "暂无保存的会话。" : "会话同步不可用。");
      }
    };

    // Initial fetch
    await fetchSaved();

    const form = createCustomForm(player, "会话文件管理")
      .closeButton()
      .label(saveStatus)
      .spacer()
      .divider()
      .spacer()
      .label(listBody)
      .spacer()
      .divider()
      .spacer()
      .button("保存当前会话", async () => {
        const resp = await requestSession("save", { player_name: player.name });
        if (resp.ok && resp.data?.session_id) {
          const sid = resp.data.session_id as string;
          saveStatus.setData(`✅ 已保存: ${sid}`);
          await fetchSaved();
        } else {
          saveStatus.setData(`❌ 保存失败: ${resp.error || "未知错误"}`);
        }
      })
      .spacer()
      .button("返回", () => {
        saveAgentUiState(player, uiState);
        form.close();
      });

    // Dynamically add saved session entries if available
    const savedResp = await requestSession("saved", { player_name: player.name });
    const savedList: SessionSavedInfo[] = (savedResp.ok && Array.isArray(savedResp.data?.saved))
      ? (savedResp.data.saved as SessionSavedInfo[])
      : [];

    // Add sorted saved session entries
    // Sort by updated_at descending
    savedList.sort((a, b) => b.updated_at.localeCompare(a.updated_at));

    // Paginated: only first PAGE_SIZE shown (simple approach)
    const pageItems = savedList.slice(0, PAGE_SIZE);

    if (pageItems.length === 0) {
      form.label("暂无已保存的会话记录。");
    }

    for (const item of pageItems) {
      const displayLabel = `${item.title || "未命名"} · ${item.message_count}条 · ${item.updated_at.slice(0, 16)}`;

      if (pendingDeleteId === item.session_id) {
        // Show confirm delete prompt
        form
          .label(`⚠ 确认删除? [${item.title || "未命名"}]`)
          .button("确认删除", () => {
            const doDelete = async () => {
              const delResp = await requestSession("delete", {
                sid: item.session_id,
                player_name: player.name,
              });
              if (delResp.ok) {
                player.sendMessage("MCBE AI Agent: 已删除会话。");
                await fetchSaved();
              } else {
                player.sendMessage(`MCBE AI Agent: 删除失败: ${delResp.error || "未知错误"}`);
              }
              pendingDeleteId = null;
              nextRoute = MAIN_ROUTE;
              form.close();
            };
            void doDelete();
          })
          .button("取消", () => {
            pendingDeleteId = null;
            // Re-fetch to refresh display
            void fetchSaved();
            form.close();
          });
      } else {
        form
          .label(displayLabel)
          .button("恢复", () => {
            const doRestore = async () => {
              const restoreResp = await requestSession("restore", {
                sid: item.session_id,
                player_name: player.name,
              });
              if (restoreResp.ok) {
                player.sendMessage("MCBE AI Agent: 会话已恢复。");
                saveAgentUiState(player, uiState);
              } else {
                player.sendMessage(`MCBE AI Agent: 恢复失败: ${restoreResp.error || "未知错误"}`);
              }
              nextRoute = MAIN_ROUTE;
              form.close();
            };
            void doRestore();
          })
          .button("删除", () => {
            pendingDeleteId = item.session_id;
            void fetchSaved();
            form.close();
          });
      }
    }

    const shown = await showCustomFormSafely(player, form);
    if (!shown.ok && nextRoute.panel === "close") {
      saveAgentUiState(player, uiState);
      return CLOSE_ROUTE;
    }

    return nextRoute;
  } catch {
    player.sendMessage("MCBE AI Agent: 表单暂时无法打开，请稍后再试。");
    saveAgentUiState(player, uiState);
    return CLOSE_ROUTE;
  }
}

function formatSavedList(
  saved: SessionSavedInfo[],
  pageIndex: number,
  _pageSize: number,
  _pendingDeleteId: string | null,
): string {
  if (saved.length === 0) {
    return "暂无保存的会话。";
  }

  return saved
    .slice(pageIndex * _pageSize, (pageIndex + 1) * _pageSize)
    .map((item) => {
      const title = item.title || "未命名";
      return `${title} · ${item.message_count}条 · ${item.updated_at.slice(0, 16)}`;
    })
    .join("\n\n---\n\n");
}
