import { system, world } from "@minecraft/server";
import type { Player } from "@minecraft/server";

import { TEXT_RESP_MESSAGE_ID } from "./constants";
import { appendHistoryItem, createHistoryId } from "../ui/history";
import type { HistoryItem, HistoryRole } from "../ui/history";
import type { AgentUiState } from "../ui/state";
import { loadAgentUiState, saveAgentUiState, AGENT_UI_STATE_PROPERTY_KEY } from "../ui/storage";

// ── 分片缓冲区 ──
// msg_id → Map<index, chunk payload>
const chunkBuffers = new Map<string, Map<number, AiRespChunk>>();

// ── 活跃 UI 状态（由 entry.ts 管理） ──
const activeUiStates = new Map<string, AgentUiState>();

type AiRespChunk = {
  id: string;
  i: number;
  n: number;
  p: string;
  r: string;
  c: string;
};

let isRegistered = false;

/**
 * 由 entry.ts 调用：当玩家打开 UI 面板时注册其内存中的状态。
 */
export function setActiveUiState(playerId: string, uiState: AgentUiState): void {
  activeUiStates.set(playerId, uiState);
}

/**
 * 由 entry.ts 调用：当玩家关闭 UI 面板时清除。
 */
export function clearActiveUiState(playerId: string): void {
  activeUiStates.delete(playerId);
}

export function resetResponseSyncForTests(): void {
  chunkBuffers.clear();
  activeUiStates.clear();
  isRegistered = false;
}

/**
 * 注册 scriptEventReceive 订阅，监听 mcbews:text_resp 分片。
 */
export function registerResponseSyncHandler(): void {
  if (isRegistered) {
    return;
  }
  isRegistered = true;

  system.afterEvents.scriptEventReceive.subscribe((event) => {
    if (event.id !== TEXT_RESP_MESSAGE_ID) {
      return;
    }

    try {
      const chunk = JSON.parse(event.message) as AiRespChunk;
      handleChunk(chunk);
    } catch {
      // 忽略解析错误
    }
  });
}

function handleChunk(chunk: AiRespChunk): void {
  const { id, i, n, p: playerName, r: role, c: content } = chunk;

  if (!id || i <= 0 || n <= 0 || i > n) {
    return;
  }

  // 获取或创建该消息 ID 的缓冲区
  let buffer = chunkBuffers.get(id);
  if (!buffer) {
    buffer = new Map<number, AiRespChunk>();
    chunkBuffers.set(id, buffer);
  }

  buffer.set(i, chunk);

  // 检查是否所有分片都已到达
  if (buffer.size < n) {
    return;
  }

  // 重组完整内容
  const sortedChunks = [...buffer.values()].sort((a, b) => a.i - b.i);
  const fullText = sortedChunks.map((c) => c.c).join("");

  // 清理缓冲区
  chunkBuffers.delete(id);

  // 处理重组完成的消息
  onMessageComplete(playerName, role as HistoryRole, fullText);
}

function onMessageComplete(playerName: string, role: HistoryRole, text: string): void {
  // 查找目标玩家
  const players = world.getAllPlayers();
  const targetPlayer = players.find((p) => p.name === playerName);
  if (!targetPlayer) {
    return;
  }

  // 创建历史条目
  const historyItem: HistoryItem = {
    id: createHistoryId("py", Date.now()),
    role,
    content: text,
    createdAt: Date.now(),
    source: "python",
  };

  // 尝试从内存中的活跃 UI 状态更新
  const activeState = activeUiStates.get(targetPlayer.id);
  if (activeState) {
    // 用户 UI 回显去重：如果此条目与用户 UI 发送的回显内容相同则跳过
    if (isDuplicateUiUserEcho(activeState.history, historyItem)) {
      return;
    }

    activeState.history = appendHistoryItem(activeState.history, historyItem, activeState.settings.maxHistoryItems);

    // 仅 assistant 角色更新响应预览
    if (role === "assistant") {
      activeState.bridgeStatus.setData("ready");
      const previewLength = activeState.settings.responsePreviewLength;
      activeState.lastResponsePreview.setData(
        text.length > previewLength ? `${text.slice(0, previewLength)}...` : text
      );
    }

    // 实时刷新 DDUI 面板
    activeState.refreshConversation?.();
  }

  // 持久化到 DynamicProperty
  try {
    const uiState = loadAgentUiState(targetPlayer);
    if (isDuplicateUiUserEcho(uiState.history, historyItem)) {
      return;
    }

    uiState.history = appendHistoryItem(uiState.history, historyItem, uiState.settings.maxHistoryItems);

    if (role === "assistant") {
      uiState.bridgeStatus.setData("ready");
      const previewLength = uiState.settings.responsePreviewLength;
      uiState.lastResponsePreview.setData(text.length > previewLength ? `${text.slice(0, previewLength)}...` : text);
    }

    const result = saveAgentUiState(targetPlayer, uiState);
    if (!result.ok) {
      // 保存失败时静默处理，不影响游戏体验
    }
  } catch {
    // DynamicProperty 读写可能因世界未就绪而失败
  }
}

/**
 * 判断历史条目是否为用户 UI 发送回显的重复（去重逻辑）。
 * 当用户通过 DDUI 面板发送消息时，source 为 "ui" 的条目已写入本地。
 * Python 回写历史时 source 为 "python"，内容相同则跳过以免重复。
 */
function isDuplicateUiUserEcho(history: HistoryItem[], item: HistoryItem): boolean {
  if (item.role !== "user" || item.source !== "python") {
    return false;
  }

  return history.some(
    (existing) =>
      existing.role === "user" && existing.source === "ui" && existing.content.trim() === item.content.trim()
  );
}
