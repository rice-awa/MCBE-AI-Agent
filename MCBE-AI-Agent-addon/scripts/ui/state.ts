import type { HistoryItem } from "./history";
import type { AgentUiStats } from "./stats";
import { DDUI_PERSISTENCE_VERSION } from "../bridge/protocol";

export type BridgeStatus = "disconnected" | "connecting" | "ready" | "sent" | "error";

export type ObservableLike<T> = {
  getData(): T;
  setData(value: T): void;
};

/** 待审批工具调用信息（从 addon 侧审批帧解析） */
export type ApprovalInfo = {
  approval_id: string;
  tool_name: string;
  args_summary: string;
  reason: string;
  batch_id: string | null;
  batch_size: number | null;
  batch_index: number | null;
  /** Trusted owner copied from the outer text response frame. */
  player_name: string;
  /** Compact wire conversation id; always normalized to a non-empty value. */
  cid: string;
  /** Semantic alias retained for UI/domain adapters. */
  conversation_id: string;
};

export type AgentUiDelivery = "tellraw" | "scriptevent";

// ── v1 Settings (legacy) ──

export type AgentUiSettings = {
  autoSaveHistory: boolean;
  maxHistoryItems: number;
  showToolEvents: boolean;
  responsePreviewLength: number;
  defaultDelivery: AgentUiDelivery;
};

// ── v2 Settings (removed maxHistoryItems and responsePreviewLength) ──

export type AgentUiSettingsV2 = {
  autoSaveHistory: boolean;
  showToolEvents: boolean;
  defaultDelivery: AgentUiDelivery;
};

// ── Conversation Bucket (v2) ──

export type ConversationBucket = {
  id: string;
  shortId: number;
  title: string;
  history: HistoryItem[];
  lastActiveAt: number;
};

// ── v1 State (legacy) ──

export type AgentUiState = {
  bridgeStatus: ObservableLike<BridgeStatus>;
  lastPrompt: ObservableLike<string>;
  lastResponsePreview: ObservableLike<string>;
  history: HistoryItem[];
  settings: AgentUiSettings;
  stats: AgentUiStats;
  refreshConversation?: () => void;

  // Streaming fields (added in v2, optional presence in v1 for type compatibility)
  isStreaming?: boolean;
  streamingConversationId?: string | null;
  streamingChars?: number;
  activeConversationId?: string;
};

// ── v2 State (new) ──

export type AgentUiStateV2 = {
  version: 2;
  activeConversationId: string;
  conversations: Record<string, ConversationBucket>;
  conversationOrder: string[];
  settings: AgentUiSettingsV2;
  stats: AgentUiStats;

  // v1-compat observables (still needed for DDUI panel refresh)
  bridgeStatus: ObservableLike<BridgeStatus>;
  lastPrompt: ObservableLike<string>;
  lastResponsePreview: ObservableLike<string>;

  // Streaming state
  isStreaming: boolean;
  streamingConversationId: string | null;
  streamingChars: number;
  /** 实时流式文本内容（用于打字机效果展示） */
  streamingText: string;
  /** 待审批工具调用（<approval_id, ApprovalInfo>） */
  pendingApprovals: Map<string, ApprovalInfo>;
  refreshConversation?: () => void;
};

export type AgentUiStateInput = Partial<{
  bridgeStatus: BridgeStatus;
  lastPrompt: string;
  lastResponsePreview: string;
  history: HistoryItem[];
  settings: Partial<AgentUiSettings>;
  stats: Partial<AgentUiStats>;
}>;

// ── Defaults ──

export const DEFAULT_AGENT_UI_SETTINGS: AgentUiSettings = {
  autoSaveHistory: true,
  maxHistoryItems: 30,
  showToolEvents: true,
  responsePreviewLength: 120,
  defaultDelivery: "tellraw",
};

export const DEFAULT_AGENT_UI_SETTINGS_V2: AgentUiSettingsV2 = {
  autoSaveHistory: true,
  showToolEvents: false,
  defaultDelivery: "tellraw",
};

export const DEFAULT_AGENT_UI_STATS: AgentUiStats = {
  openCount: 0,
  sentCount: 0,
  localHistoryCount: 0,
  responseChunkCount: 0,
  lastOpenedAt: 0,
  lastSentAt: 0,
  totalInputTokens: 0,
  totalOutputTokens: 0,
  totalTokensCombined: 0,
  sessionInputTokens: 0,
  sessionOutputTokens: 0,
  sessionTokensCombined: 0,
  roundInputTokens: 0,
  roundOutputTokens: 0,
  roundTokensCombined: 0,
  totalMessages: 0,
};

// ── Utility ──

function createObservable<T>(initialValue: T): ObservableLike<T> {
  let value = initialValue;
  return {
    getData(): T {
      return value;
    },
    setData(nextValue: T): void {
      value = nextValue;
    },
  };
}

/**
 * Create a v1 AgentUiState (legacy).
 */
export function createAgentUiState(initialState: AgentUiStateInput = {}): AgentUiState {
  const settings = {
    ...DEFAULT_AGENT_UI_SETTINGS,
    ...initialState.settings,
  };
  const history = [...(initialState.history ?? [])].slice(-settings.maxHistoryItems);
  const initialStats = {
    ...DEFAULT_AGENT_UI_STATS,
    ...initialState.stats,
  };

  return {
    bridgeStatus: createObservable<BridgeStatus>(initialState.bridgeStatus ?? "disconnected"),
    lastPrompt: createObservable(initialState.lastPrompt ?? ""),
    lastResponsePreview: createObservable(initialState.lastResponsePreview ?? ""),
    history,
    settings,
    stats: {
      ...initialStats,
      localHistoryCount: Math.min(initialStats.localHistoryCount, history.length),
    },
  };
}

/**
 * Create a v2 AgentUiStateV2 with per-conversation buckets.
 */
export function createAgentUiStateV2(): AgentUiStateV2 {
  const defaultBucket: ConversationBucket = {
    id: "default",
    shortId: 0,
    title: "默认会话",
    history: [],
    lastActiveAt: Date.now(),
  };

  return {
    version: DDUI_PERSISTENCE_VERSION,
    activeConversationId: "default",
    conversations: {
      default: defaultBucket,
    },
    conversationOrder: ["default"],
    settings: { ...DEFAULT_AGENT_UI_SETTINGS_V2 },
    stats: { ...DEFAULT_AGENT_UI_STATS },

    bridgeStatus: createObservable<BridgeStatus>("disconnected"),
    lastPrompt: createObservable(""),
    lastResponsePreview: createObservable(""),

    isStreaming: false,
    streamingConversationId: null,
    streamingChars: 0,
    streamingText: "",
    pendingApprovals: new Map(),
  };
}

/**
 * Get the active conversation bucket from state.
 * Falls back to "default" bucket if active conversation not found.
 */
export function getActiveBucket(state: AgentUiStateV2): ConversationBucket {
  const bucket = state.conversations[state.activeConversationId];
  if (!bucket) {
    return (
      state.conversations["default"] ?? {
        id: "default",
        shortId: 0,
        title: "",
        history: [],
        lastActiveAt: Date.now(),
      }
    );
  }
  return bucket;
}
