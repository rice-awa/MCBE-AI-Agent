import { system, world } from "@minecraft/server";
import type { Player } from "@minecraft/server";

import { TEXT_RESPONSE_SCRIPT_EVENT_ID } from "./protocol";
import {
  BoundedTextResponseAssembler,
  DEFAULT_RESPONSE_ASSEMBLER_LIMITS,
  parseTextResponseChunk,
  textResponseStreamKey,
  type TextResponseChunk,
  type TextResponseMessage,
  type TextResponseRole,
} from "./textResponseAssembler";
import { appendHistoryItem, createHistoryId } from "../ui/history";
import type { HistoryItem } from "../ui/history";
import type { AgentUiStateV2, AgentUiState, ApprovalInfo } from "../ui/state";
import { PERSISTED_HISTORY_LIMIT, loadAgentUiState, saveAgentUiState } from "../ui/storage";

export {
  BoundedTextResponseAssembler,
  DEFAULT_RESPONSE_ASSEMBLER_LIMITS as DEFAULT_RESPONSE_SYNC_LIMITS,
  parseTextResponseChunk,
};
export type { TextResponseChunk, TextResponseMessage, TextResponseRole };
/** Compatibility name for code that imported the old facade-owned assembler. */
export { BoundedTextResponseAssembler as ResponseAssembler };

export type ReassembledResponse = TextResponseMessage;
export type TextRespHandler = (playerName: string, role: string, text: string) => void;
export type TextResponseMessageHandler = (message: TextResponseMessage) => void;

type StreamingState = {
  streamKey: string;
  playerId: string | undefined;
  playerName: string;
  responseId: string;
  conversationId: string;
  role: "user" | "assistant";
  assembled: string;
  startedAt: number;
  done: boolean;
  inputTokens: number;
  outputTokens: number;
};

const assembler = new BoundedTextResponseAssembler();
const activeUiStates = new Map<string, AgentUiStateV2 | AgentUiState>();
const activePlayerNames = new Map<string, string>();
const streamingStates = new Map<string, StreamingState>();
let legacyHandler: TextRespHandler | null = null;
let typedHandler: TextResponseMessageHandler | null = null;
let isRegistered = false;

export function setTextRespHandler(handler: TextRespHandler): void {
  legacyHandler = handler;
}

export function setTextResponseMessageHandler(handler: TextResponseMessageHandler): void {
  typedHandler = handler;
}

/** Register an active UI facade for one owning player. */
export function setActiveUiState(playerId: string, uiState: AgentUiStateV2 | AgentUiState, playerName?: string): void {
  activeUiStates.set(playerId, uiState);
  if (playerName) activePlayerNames.set(playerId, playerName);
}

/**
 * Close only one player's UI ownership. Partial response buffers and preview
 * state belonging to another player are deliberately left untouched.
 */
export function clearActiveUiState(playerId: string): void {
  const uiState = activeUiStates.get(playerId);
  const playerName =
    activePlayerNames.get(playerId) ?? world.getAllPlayers().find((player) => player.id === playerId)?.name;
  if (playerName) assembler.clearForPlayer(playerName);

  for (const [key, streamState] of streamingStates) {
    if (streamState.playerId !== playerId && streamState.playerName !== playerName) continue;
    if (uiState && !streamState.done && streamState.assembled) {
      finalizeStreamItem(uiState, streamState);
    } else {
      streamingStates.delete(key);
    }
  }
  activeUiStates.delete(playerId);
  activePlayerNames.delete(playerId);
}

export function resetResponseSyncForTests(): void {
  assembler.clear();
  activeUiStates.clear();
  activePlayerNames.clear();
  streamingStates.clear();
  legacyHandler = null;
  typedHandler = null;
  isRegistered = false;
}

/** SDK reference compatibility alias. */
export const _testingReset = resetResponseSyncForTests;

export function registerResponseSyncHandler(): void {
  if (isRegistered) return;
  isRegistered = true;
  system.afterEvents.scriptEventReceive.subscribe((event) => {
    if (event.id !== TEXT_RESPONSE_SCRIPT_EVENT_ID) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(event.message);
    } catch {
      return;
    }
    const chunk = parseTextResponseChunk(parsed);
    if (!chunk) return;
    handleChunk(chunk);
  });
}

function isV2State(uiState: AgentUiStateV2 | AgentUiState): uiState is AgentUiStateV2 {
  return (uiState as AgentUiStateV2).version === 2;
}

function ensureConversationBucket(uiState: AgentUiStateV2, conversationId: string, title?: string) {
  const existing = uiState.conversations[conversationId];
  if (existing) {
    if (title && !existing.title) existing.title = title;
    return existing;
  }
  const bucket = {
    id: conversationId,
    shortId: 0,
    title: title ?? "",
    history: [] as HistoryItem[],
    lastActiveAt: Date.now(),
  };
  uiState.conversations[conversationId] = bucket;
  if (!uiState.conversationOrder.includes(conversationId)) uiState.conversationOrder.push(conversationId);
  return bucket;
}

function getConversationHistory(uiState: AgentUiStateV2 | AgentUiState, conversationId: string): HistoryItem[] {
  if (isV2State(uiState)) return ensureConversationBucket(uiState, conversationId).history;
  return uiState.history;
}

function setConversationHistory(
  uiState: AgentUiStateV2 | AgentUiState,
  conversationId: string,
  history: HistoryItem[]
): void {
  if (isV2State(uiState)) ensureConversationBucket(uiState, conversationId).history = history;
  else uiState.history = history;
}

function getMaxHistoryItems(uiState: AgentUiStateV2 | AgentUiState): number {
  return isV2State(uiState) ? PERSISTED_HISTORY_LIMIT : (uiState.settings.maxHistoryItems ?? 30);
}

function getActiveState(playerName: string): {
  player: Player | undefined;
  uiState: AgentUiStateV2 | AgentUiState | undefined;
} {
  const player = world.getAllPlayers().find((candidate) => candidate.name === playerName);
  return { player, uiState: player ? activeUiStates.get(player.id) : undefined };
}

function historyItem(role: "user" | "assistant", content: string, createdAt: number): HistoryItem {
  return {
    id: createHistoryId("py", createdAt),
    role,
    content,
    createdAt,
    source: "python",
  };
}

function isDuplicateUiUserEcho(
  uiState: AgentUiStateV2 | AgentUiState,
  item: HistoryItem,
  conversationId: string
): boolean {
  if (item.role !== "user" || item.source !== "python") return false;
  return getConversationHistory(uiState, conversationId).some(
    (existing) =>
      existing.role === "user" && existing.source === "ui" && existing.content.trim() === item.content.trim()
  );
}

function updateStreamingPreview(
  state: StreamingState,
  activeState: AgentUiStateV2 | AgentUiState | undefined,
  content: string
): void {
  state.assembled = content;
  if (!activeState || state.conversationId !== (activeState.activeConversationId ?? "default")) return;
  activeState.isStreaming = true;
  activeState.streamingConversationId = state.conversationId;
  activeState.streamingChars = Array.from(content).length;
  if (isV2State(activeState)) activeState.streamingText = content;
  activeState.bridgeStatus.setData("connecting");
  const displayText = `${content}▌`;
  activeState.lastResponsePreview.setData(displayText.length > 120 ? `${displayText.slice(0, 120)}...` : displayText);
  activeState.refreshConversation?.();
}

function finalizeStreamItem(uiState: AgentUiStateV2 | AgentUiState, streamState: StreamingState): void {
  if (!streamState.assembled) {
    streamingStates.delete(streamState.streamKey);
    return;
  }
  const item = historyItem(streamState.role, streamState.assembled, streamState.startedAt);
  const history = getConversationHistory(uiState, streamState.conversationId);
  setConversationHistory(
    uiState,
    streamState.conversationId,
    appendHistoryItem(history, item, getMaxHistoryItems(uiState))
  );
  streamState.done = true;
  streamingStates.delete(streamState.streamKey);
  if (streamState.conversationId === (uiState.activeConversationId ?? "default")) {
    uiState.isStreaming = false;
    uiState.streamingConversationId = null;
    uiState.streamingChars = 0;
    if (isV2State(uiState)) uiState.streamingText = "";
    uiState.refreshConversation?.();
  }
}

function normalizeApprovalInfo(value: unknown, message: TextResponseMessage): ApprovalInfo | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  if (typeof raw.approval_id !== "string" || !raw.approval_id.trim()) return null;
  const claimedPlayer = raw.player_name ?? raw.player;
  const claimedCid = raw.cid ?? raw.conversation_id;
  if (claimedPlayer !== undefined && (typeof claimedPlayer !== "string" || claimedPlayer !== message.playerName))
    return null;
  if (claimedCid !== undefined && (typeof claimedCid !== "string" || claimedCid !== message.conversationId))
    return null;
  const numberOrNull = (candidate: unknown): number | null =>
    candidate === null || candidate === undefined
      ? null
      : typeof candidate === "number" && Number.isInteger(candidate) && candidate >= 0
        ? candidate
        : null;
  return {
    approval_id: raw.approval_id,
    tool_name: typeof raw.tool_name === "string" ? raw.tool_name : "unknown",
    args_summary: typeof raw.args_summary === "string" ? raw.args_summary : "",
    reason: typeof raw.reason === "string" ? raw.reason : "",
    batch_id: typeof raw.batch_id === "string" ? raw.batch_id : null,
    batch_size: numberOrNull(raw.batch_size),
    batch_index: numberOrNull(raw.batch_index),
    player_name: message.playerName,
    cid: message.conversationId,
    conversation_id: message.conversationId,
  };
}

function handleApproval(message: TextResponseMessage, activeState: AgentUiStateV2 | AgentUiState | undefined): void {
  let parsed: unknown;
  try {
    parsed = JSON.parse(message.content);
  } catch {
    return;
  }
  const approvalInfo = normalizeApprovalInfo(parsed, message);
  if (!approvalInfo || !activeState || !isV2State(activeState)) return;
  activeState.pendingApprovals.set(approvalInfo.approval_id, approvalInfo);
  activeState.refreshConversation?.();
}

function notifyResponseHandlers(message: TextResponseMessage): void {
  typedHandler?.(message);
  legacyHandler?.(message.playerName, message.role, message.content);
}

function handleChunk(chunk: TextResponseChunk): void {
  const owner = getActiveState(chunk.p);
  const result = assembler.push(chunk);
  if (chunk.r === "approval") {
    if (result) {
      notifyResponseHandlers(result);
      handleApproval(result, owner.uiState);
    }
    return;
  }
  if (chunk.r !== "user" && chunk.r !== "assistant") return;

  const streamKey = textResponseStreamKey(chunk.p, chunk.cid, chunk.id);
  let streamState = streamingStates.get(streamKey);
  if (!streamState) {
    streamState = {
      streamKey,
      playerId: owner.player?.id,
      playerName: chunk.p,
      responseId: chunk.id,
      conversationId: chunk.cid?.trim() || "default",
      role: chunk.r,
      assembled: "",
      startedAt: Date.now(),
      done: false,
      inputTokens: 0,
      outputTokens: 0,
    };
    streamingStates.set(streamKey, streamState);
  }

  if (result) {
    notifyResponseHandlers(result);
    streamState.inputTokens = result.usage?.i ?? 0;
    streamState.outputTokens = result.usage?.o ?? 0;
    streamState.done = true;
    onMessageComplete(result, owner.player, owner.uiState, streamState);
    return;
  }

  const preview = assembler.getPreview(chunk);
  if (preview) updateStreamingPreview(streamState, owner.uiState, preview.content);
}

function onMessageComplete(
  message: TextResponseMessage,
  targetPlayer: Player | undefined,
  activeState: AgentUiStateV2 | AgentUiState | undefined,
  streamState: StreamingState
): void {
  streamState.assembled = message.content;
  const item = historyItem(message.role === "user" ? "user" : "assistant", message.content, streamState.startedAt);

  if (targetPlayer && activeState && !isDuplicateUiUserEcho(activeState, item, message.conversationId)) {
    const history = getConversationHistory(activeState, message.conversationId);
    setConversationHistory(
      activeState,
      message.conversationId,
      appendHistoryItem(history, item, getMaxHistoryItems(activeState))
    );
    if (message.role === "assistant" && message.conversationId === (activeState.activeConversationId ?? "default")) {
      activeState.bridgeStatus.setData("ready");
      let preview = message.content;
      if (preview.length > 120) preview = `${preview.slice(0, 120)}...`;
      if (message.usage && (message.usage.i > 0 || message.usage.o > 0)) {
        preview += ` ✓ ${message.usage.i}/${message.usage.o}`;
      }
      activeState.lastResponsePreview.setData(preview);
      const total = (message.usage?.i ?? 0) + (message.usage?.o ?? 0);
      activeState.stats = {
        ...activeState.stats,
        roundInputTokens: message.usage?.i ?? 0,
        roundOutputTokens: message.usage?.o ?? 0,
        roundTokensCombined: total,
        sessionInputTokens: activeState.stats.sessionInputTokens + (message.usage?.i ?? 0),
        sessionOutputTokens: activeState.stats.sessionOutputTokens + (message.usage?.o ?? 0),
        sessionTokensCombined: activeState.stats.sessionTokensCombined + total,
        totalInputTokens: activeState.stats.totalInputTokens + (message.usage?.i ?? 0),
        totalOutputTokens: activeState.stats.totalOutputTokens + (message.usage?.o ?? 0),
        totalTokensCombined: activeState.stats.totalTokensCombined + total,
      };
      activeState.isStreaming = false;
      activeState.streamingConversationId = null;
      activeState.streamingChars = 0;
      if (isV2State(activeState)) activeState.streamingText = "";
      activeState.refreshConversation?.();
    }
  }

  if (targetPlayer) persistToDynamicProperty(targetPlayer, item, message.conversationId, message.title);
  streamingStates.delete(streamState.streamKey);
}

function persistToDynamicProperty(
  targetPlayer: Player,
  item: HistoryItem,
  conversationId: string,
  title?: string
): void {
  try {
    const uiState = loadAgentUiState(targetPlayer);
    if (isDuplicateUiUserEcho(uiState, item, conversationId)) return;
    const history = getConversationHistory(uiState, conversationId);
    if (isV2State(uiState) && title) ensureConversationBucket(uiState, conversationId, title).title = title;
    setConversationHistory(uiState, conversationId, appendHistoryItem(history, item, getMaxHistoryItems(uiState)));
    if (item.role === "assistant") uiState.bridgeStatus.setData("ready");
    const preview = item.content.length > 100 ? `${item.content.slice(0, 100)}...` : item.content;
    uiState.lastResponsePreview.setData(preview);
    saveAgentUiState(targetPlayer, uiState);
  } catch {
    // DynamicProperty is an external boundary; the in-memory response is valid.
  }
}
