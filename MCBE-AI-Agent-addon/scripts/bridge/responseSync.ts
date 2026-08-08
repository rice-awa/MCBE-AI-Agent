import { system, world } from "@minecraft/server";
import type { Player } from "@minecraft/server";

import { TEXT_RESP_MESSAGE_ID, TOOL_APPROVE_PREFIX, TOOL_DENY_PREFIX } from "./constants";
import { appendHistoryItem, createHistoryId } from "../ui/history";
import type { HistoryItem, HistoryRole } from "../ui/history";
import type { AgentUiStateV2, AgentUiState, ApprovalInfo } from "../ui/state";
import {
  PERSISTED_HISTORY_LIMIT,
  loadAgentUiState,
  saveAgentUiState,
} from "../ui/storage";

// ── Types ──

/** Extended chunk with optional session/usage fields. */
type AiRespChunk = {
  id: string;
  i: number;
  n: number;
  p: string;
  r: string;
  c: string;
  /** Optional conversation id — absent for backward-compatible frames. */
  cid?: string;
  /** Optional title — absent for backward-compatible frames. */
  t?: string;
  /** Optional usage stats — present on completion frame. Supports both compact (i/o) and verbose (input_tokens/output_tokens) formats. */
  u?: Record<string, number>;
};

/** Tracks streaming state for one conversation. */
type StreamingState = {
  conversationId: string;
  assembled: string;
  startedAt: number;
  done: boolean;
  inputTokens: number;
  outputTokens: number;
};

// ── State ──

// msg_id → Map<index, chunk payload>
const chunkBuffers = new Map<string, Map<number, AiRespChunk>>();

// Active UI states — accepts both v1 (AgentUiState) and v2 (AgentUiStateV2).
// Runtime check via the `version` field distinguishes them.
const activeUiStates = new Map<string, AgentUiStateV2 | AgentUiState>();

// Streaming state keyed by conversation_id
const streamingStates = new Map<string, StreamingState>();

let isRegistered = false;

// ── Public API ──

/**
 * Register the active UI state for a player (called by entry.ts on panel open).
 * Accepts both v1 and v2 state for backward compatibility.
 */
export function setActiveUiState(playerId: string, uiState: AgentUiStateV2 | AgentUiState): void {
  activeUiStates.set(playerId, uiState);
}

/**
 * Clear the active UI state (called by entry.ts on panel close).
 * If a stream is in progress, finalize accumulated text as a history item.
 */
export function clearActiveUiState(playerId: string): void {
  const uiState = activeUiStates.get(playerId);
  if (uiState) {
    // If streaming and not yet done, finalize what we have
    const streamState = uiState.isStreaming ? getStreamingState(uiState.streamingConversationId ?? null) : null;
    if (streamState && !streamState.done && streamState.assembled) {
      finalizeStreamItem(uiState, streamState);
    }
  }
  activeUiStates.delete(playerId);
}

export function resetResponseSyncForTests(): void {
  chunkBuffers.clear();
  activeUiStates.clear();
  streamingStates.clear();
  isRegistered = false;
}

/**
 * Register the scriptEventReceive subscription for mcbews:text_resp.
 * Handles streaming incremental rendering (typewriter effect).
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
      // Ignore parse errors
    }
  });
}

// ── Runtime type guards ──

function isV2State(uiState: AgentUiStateV2 | AgentUiState): uiState is AgentUiStateV2 {
  return (uiState as AgentUiStateV2).version === 2;
}

function getConversationHistory(uiState: AgentUiStateV2 | AgentUiState, conversationId: string): HistoryItem[] {
  if (isV2State(uiState)) {
    const bucket = uiState.conversations[conversationId] ?? uiState.conversations["default"];
    return bucket ? bucket.history : [];
  }
  // v1 flat history
  return uiState.history;
}

function setConversationHistory(uiState: AgentUiStateV2 | AgentUiState, conversationId: string, history: HistoryItem[]): void {
  if (isV2State(uiState)) {
    const bucket = uiState.conversations[conversationId] ?? uiState.conversations["default"];
    if (bucket) {
      bucket.history = history;
    }
  } else {
    // v1: assign directly to flat history (but limited by maxHistoryItems)
    // We can't access maxHistoryItems on the flat path since tests don't set it
    uiState.history = history;
  }
}

function getMaxHistoryItems(uiState: AgentUiStateV2 | AgentUiState): number {
  if (isV2State(uiState)) {
    return PERSISTED_HISTORY_LIMIT;
  }
  return uiState.settings.maxHistoryItems ?? 30;
}

// ── Streaming State Helpers ──

function getStreamingState(conversationId: string | null): StreamingState | undefined {
  if (!conversationId) return undefined;
  return streamingStates.get(conversationId);
}

function ensureStreamingState(
  conversationId: string,
  startedAt: number,
): StreamingState {
  let state = streamingStates.get(conversationId);
  if (!state) {
    state = {
      conversationId,
      assembled: "",
      startedAt,
      done: false,
      inputTokens: 0,
      outputTokens: 0,
    };
    streamingStates.set(conversationId, state);
  }
  return state;
}

function finalizeStreamItem(uiState: AgentUiStateV2 | AgentUiState, streamState: StreamingState): void {
  if (!streamState.assembled) return;

  const historyItem: HistoryItem = {
    id: createHistoryId("py", Date.now()),
    role: "assistant",
    content: streamState.assembled,
    createdAt: streamState.startedAt,
    source: "python",
  };

  // Append to history
  const history = getConversationHistory(uiState, streamState.conversationId);
  const limit = getMaxHistoryItems(uiState);
  const updated = appendHistoryItem(history, historyItem, limit);
  setConversationHistory(uiState, streamState.conversationId, updated);

  // Mark done
  streamState.done = true;

  // Clean up streaming state for this conversation
  streamingStates.delete(streamState.conversationId);
}

// ── Chunk Handling ──

function handleChunk(chunk: AiRespChunk): void {
  const { id, i, n, p: playerName, r: role, c: content } = chunk;

  if (!id || i <= 0 || n <= 0 || i > n) {
    return;
  }

  // Get or create buffer for this message id
  let buffer = chunkBuffers.get(id);
  if (!buffer) {
    buffer = new Map<number, AiRespChunk>();
    chunkBuffers.set(id, buffer);
  }

  // Check if we already have this index (dedup)
  if (buffer.has(i)) {
    return;
  }

  buffer.set(i, chunk);

  // Determine conversation id (from chunk or fallback)
  const conversationId = chunk.cid || "default";

  // Find the active UI state for this player
  const players = world.getAllPlayers();
  const targetPlayer = players.find((p) => p.name === playerName);
  const activeState = targetPlayer ? activeUiStates.get(targetPlayer.id) : undefined;

  // Check if this is a user echo that should be deduplicated (detect early)
  // This must happen before any refreshConversation call.
  const historyItem: HistoryItem = {
    id: createHistoryId("py", Date.now()),
    role: role as HistoryRole,
    content,
    createdAt: Date.now(),
    source: "python",
  };
  if (activeState && isDuplicateUiUserEcho(activeState, historyItem)) {
    // This is a duplicate echo — skip entirely after buffering
    // We still buffer in case other chunks share this msg id, but we won't refresh
    if (buffer.size >= n) {
      chunkBuffers.delete(id);
    }
    return;
  }

  // Compute sorted prefix: sort all known chunks by index, concatenate
  const sortedChunks = [...buffer.values()].sort((a, b) => a.i - b.i);
  const fullText = sortedChunks.map((c) => c.c).join("");

  // ── Handle approval frames ──
  if (role === "approval") {
    // Wait for all chunks
    if (buffer.size < n) {
      return;
    }
    chunkBuffers.delete(id);

    // Parse approval info from assembled content
    try {
      const approvalInfo = JSON.parse(fullText) as ApprovalInfo;
      if (activeState && isV2State(activeState)) {
        activeState.pendingApprovals.set(approvalInfo.approval_id, approvalInfo);
        activeState.refreshConversation?.();
      }
    } catch {
      // Ignore parse errors
    }
    return;
  }

  // ── Streaming incremental output ──

  // Ensure streaming state
  const streamState = ensureStreamingState(conversationId, Date.now());

  // Update assembled text
  streamState.assembled = fullText;

  // Extract usage from chunk if present (usually on last frame)
  if (chunk.u) {
    streamState.inputTokens = chunk.u.i || chunk.u.input_tokens || 0;
    streamState.outputTokens = chunk.u.o || chunk.u.output_tokens || 0;
  }

  // Update the active UI state's streaming fields
  if (activeState) {
    // Set streaming flags
    activeState.isStreaming = !streamState.done;
    activeState.streamingConversationId = conversationId;
    activeState.streamingChars = streamState.assembled.length;
    if (isV2State(activeState)) {
      activeState.streamingText = streamState.assembled;
    }

    // Update bridge status observable
    if (!streamState.done) {
      activeState.bridgeStatus.setData("connecting");
    }

    // Update lastResponsePreview observable with the current text + cursor
    const displayText = streamState.done
      ? streamState.assembled
      : streamState.assembled + "▌";
    activeState.lastResponsePreview.setData(
      displayText.length > 120
        ? `${displayText.slice(0, 120)}...`
        : displayText,
    );

    // Trigger DDUI refresh only if we're still waiting for more chunks
    // (completion path below also calls refreshConversation)
    if (buffer.size < n) {
      activeState.refreshConversation?.();
    }
  }

  // ── Check if all chunks received ──

  if (buffer.size < n) {
    return; // Wait for more chunks
  }

  // All chunks received — finalize

  // Update streaming state
  streamState.done = true;
  streamState.assembled = fullText;

  // Clean up buffer
  chunkBuffers.delete(id);

  // Process the completed message
  onMessageComplete(playerName, role as HistoryRole, fullText, conversationId, streamState, targetPlayer, activeState);
}

function onMessageComplete(
  playerName: string,
  role: HistoryRole,
  text: string,
  conversationId: string,
  streamState: StreamingState,
  targetPlayer: Player | undefined,
  activeState: AgentUiStateV2 | AgentUiState | undefined,
): void {
  if (!targetPlayer) {
    // No target player — just clean up streaming state
    streamingStates.delete(conversationId);
    return;
  }

  // Check for duplicate user echo
  const historyItem: HistoryItem = {
    id: createHistoryId("py", Date.now()),
    role,
    content: text,
    createdAt: streamState.startedAt,
    source: "python",
  };

  // ── Handle in-memory active state ──
  if (activeState) {
    // Skip if this is a duplicate user echo
    if (isDuplicateUiUserEcho(activeState, historyItem)) {
      cleanupStream(activeState, conversationId);
      return;
    }

    // Append to history
    const history = getConversationHistory(activeState, conversationId);
    const limit = getMaxHistoryItems(activeState);
    const updated = appendHistoryItem(history, historyItem, limit);
    setConversationHistory(activeState, conversationId, updated);

    if (role === "assistant") {
      // Update bridge status
      activeState.bridgeStatus.setData("ready");

      // Update preview (no cursor, show token stats)
      let preview = text;
      if (text.length > 120) {
        preview = `${text.slice(0, 120)}...`;
      }

      // Append token info if available
      if (streamState.inputTokens > 0 || streamState.outputTokens > 0) {
        preview += ` ✓ ${streamState.inputTokens}/${streamState.outputTokens}`;
      }

      activeState.lastResponsePreview.setData(preview);

      // Update token stats
      const totalTokens = streamState.inputTokens + streamState.outputTokens;
      activeState.stats = {
        ...activeState.stats,
        roundInputTokens: streamState.inputTokens,
        roundOutputTokens: streamState.outputTokens,
        roundTokensCombined: totalTokens,
        sessionInputTokens: activeState.stats.sessionInputTokens + streamState.inputTokens,
        sessionOutputTokens: activeState.stats.sessionOutputTokens + streamState.outputTokens,
        sessionTokensCombined: activeState.stats.sessionTokensCombined + totalTokens,
        totalInputTokens: activeState.stats.totalInputTokens + streamState.inputTokens,
        totalOutputTokens: activeState.stats.totalOutputTokens + streamState.outputTokens,
        totalTokensCombined: activeState.stats.totalTokensCombined + totalTokens,
      };
    }

    // Mark streaming complete
    activeState.isStreaming = false;
    activeState.streamingConversationId = null;
    activeState.streamingChars = 0;
    if (isV2State(activeState)) {
      activeState.streamingText = "";
    }

    // Refresh DDUI panel
    activeState.refreshConversation?.();
  }

  // ── Persist to DynamicProperty ──
  try {
    persistToDynamicProperty(targetPlayer, historyItem, role, text, conversationId);
  } catch {
    // DynamicProperty read/write may fail if world is not ready
  }

  // Cleanup streaming state
  streamingStates.delete(conversationId);
}

function cleanupStream(activeState: AgentUiStateV2 | AgentUiState, conversationId: string): void {
  activeState.isStreaming = false;
  activeState.streamingConversationId = null;
  activeState.streamingChars = 0;
  if (isV2State(activeState)) {
    activeState.streamingText = "";
  }
  streamingStates.delete(conversationId);
}

/**
 * Persist a history item to the player's DynamicProperty storage.
 */
function persistToDynamicProperty(
  targetPlayer: Player,
  historyItem: HistoryItem,
  role: HistoryRole,
  text: string,
  conversationId: string,
): void {
  const uiState = loadAgentUiState(targetPlayer);

  // Skip duplicate user echo
  if (isDuplicateUiUserEcho(uiState, historyItem)) {
    return;
  }

  // Append to history (per-conversation bucket)
  const history = getConversationHistory(uiState, conversationId);
  const limit = getMaxHistoryItems(uiState);
  const updated = appendHistoryItem(history, historyItem, limit);
  setConversationHistory(uiState, conversationId, updated);

  if (role === "assistant") {
    uiState.bridgeStatus.setData("ready");
  }
  const preview = text.length > 100 ? `${text.slice(0, 100)}...` : text;
  uiState.lastResponsePreview.setData(preview);

  const result = saveAgentUiState(targetPlayer, uiState);
  if (!result.ok) {
    // Silent fail
  }
}

/**
 * Check if a Python-sourced user message is a duplicate of a locally-prompted user message.
 */
function isDuplicateUiUserEcho(uiState: AgentUiStateV2 | AgentUiState, item: HistoryItem): boolean {
  if (item.role !== "user" || item.source !== "python") {
    return false;
  }

  const history = getConversationHistory(uiState, uiState.activeConversationId ?? "default");
  return history.some(
    (existing) =>
      existing.role === "user" && existing.source === "ui" && existing.content.trim() === item.content.trim(),
  );
}
