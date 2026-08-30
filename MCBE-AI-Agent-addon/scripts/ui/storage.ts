import type { AgentUiSettingsV2, AgentUiStateV2, ApprovalInfo, BridgeStatus, ConversationBucket } from "./state";
import { createAgentUiStateV2 } from "./state";
import { DDUI_PERSISTENCE_VERSION } from "../bridge/protocol";
import type { HistoryItem } from "./history";
import type { AgentUiStats } from "./stats";

export const AGENT_UI_STATE_PROPERTY_KEY = "mcbeai:ui_state";
/** DynamicProperty key is retained for old worlds; it is not a wire ID. */
export const DDUI_PERSISTENCE_STATE_VERSION = DDUI_PERSISTENCE_VERSION;
/** @deprecated Use DDUI_PERSISTENCE_STATE_VERSION. */
export const AGENT_UI_STATE_VERSION = DDUI_PERSISTENCE_STATE_VERSION;
export const PERSISTED_HISTORY_LIMIT = 100;
export const MAX_CONVERSATIONS = 20;

type DynamicPropertyOwner = {
  getDynamicProperty(identifier: string): unknown;
  setDynamicProperty(identifier: string, value?: string): void;
};

/** v1 persisted format (legacy) — used its own full settings type */
type PersistedAgentUiSettingsV1 = {
  autoSaveHistory?: boolean;
  maxHistoryItems?: number;
  showToolEvents?: boolean;
  responsePreviewLength?: number;
  defaultDelivery?: string;
};

type PersistedAgentUiStateV1 = {
  version: 1;
  bridgeStatus?: BridgeStatus;
  settings?: Partial<PersistedAgentUiSettingsV1>;
  history?: HistoryItem[];
  stats?: Partial<AgentUiStats>;
};

/** v2 persisted format — per-conversation buckets */
type PersistedConversationBucketV2 = {
  id: string;
  shortId: number;
  title: string;
  history: HistoryItem[];
  lastActiveAt: number;
};

type PersistedAgentUiStateV2 = {
  version: 2;
  activeConversationId: string;
  conversations: PersistedConversationBucketV2[];
  conversationOrder: string[];
  settings: Partial<AgentUiSettingsV2>;
  stats?: Partial<AgentUiStats>;
  bridgeStatus?: BridgeStatus;
  pendingApprovals?: ApprovalInfo[];
};

export type SaveAgentUiStateResult = { ok: true } | { ok: false; error: unknown };

export function loadAgentUiState(owner: DynamicPropertyOwner): AgentUiStateV2 {
  try {
    const rawValue = owner.getDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY);
    if (typeof rawValue !== "string") {
      return createAgentUiStateV2();
    }

    const persisted = JSON.parse(rawValue);

    // v1 → v2 migration
    if (persisted.version === 1 || persisted.version === undefined) {
      return migrateV1ToV2(persisted as PersistedAgentUiStateV1);
    }

    if (persisted.version !== DDUI_PERSISTENCE_STATE_VERSION) {
      return createAgentUiStateV2();
    }

    // v2 load
    const v2 = persisted as PersistedAgentUiStateV2;
    const conversations: Record<string, ConversationBucket> = {};
    for (const bucket of v2.conversations ?? []) {
      conversations[bucket.id] = {
        id: bucket.id,
        shortId: bucket.shortId ?? 0,
        title: bucket.title ?? "",
        history: normalizeHistory(bucket.history),
        lastActiveAt: bucket.lastActiveAt ?? Date.now(),
      };
    }
    if (!conversations.default) {
      conversations.default = { id: "default", shortId: 0, title: "", history: [], lastActiveAt: Date.now() };
    }

    const order = v2.conversationOrder?.length ? v2.conversationOrder : ["default"];

    const state = createAgentUiStateV2();
    state.activeConversationId = v2.activeConversationId ?? "default";
    state.conversations = conversations;
    state.conversationOrder = order;
    state.settings = normalizeSettingsV2(v2.settings);
    state.bridgeStatus.setData(normalizeBridgeStatus(v2.bridgeStatus) ?? "disconnected");

    const activeBucket = conversations[state.activeConversationId] ?? conversations.default;
    const activeHistoryLen = activeBucket ? activeBucket.history.length : 0;
    const rawStats = normalizeStatsV2(v2.stats);
    state.stats = {
      ...state.stats,
      ...rawStats,
      localHistoryCount: Math.min(
        normalizeNonNegativeNumber(rawStats.localHistoryCount) ?? activeHistoryLen,
        activeHistoryLen
      ),
    };
    state.pendingApprovals = normalizeApprovals(v2.pendingApprovals);

    return state;
  } catch {
    return createAgentUiStateV2();
  }
}

/**
 * Migrate v1 persisted state to v2 by moving the flat history into the "default" conversation bucket.
 */
function migrateV1ToV2(v1: PersistedAgentUiStateV1): AgentUiStateV2 {
  const state = createAgentUiStateV2();
  const history = normalizeHistory(v1.history);
  state.conversations.default = {
    id: "default",
    shortId: 0,
    title: "",
    history,
    lastActiveAt: Date.now(),
  };
  state.activeConversationId = "default";
  state.conversationOrder = ["default"];
  state.settings = normalizeSettingsV2(v1.settings as Partial<AgentUiSettingsV2>);
  const rawStats = normalizeStatsV2(v1.stats);
  state.stats = {
    ...state.stats,
    ...rawStats,
    localHistoryCount: Math.min(
      normalizeNonNegativeNumber(rawStats.localHistoryCount) ?? history.length,
      history.length
    ),
  };
  if (v1.bridgeStatus) {
    state.bridgeStatus.setData(normalizeBridgeStatus(v1.bridgeStatus) ?? "disconnected");
  }
  return state;
}

function normalizeBridgeStatus(status: unknown): BridgeStatus | undefined {
  return status === "disconnected" ||
    status === "connecting" ||
    status === "ready" ||
    status === "sent" ||
    status === "error"
    ? status
    : undefined;
}

/**
 * Normalize persisted pending approvals (array) into a Map keyed by approval_id.
 */
function normalizeApprovals(value: unknown): Map<string, ApprovalInfo> {
  const map = new Map<string, ApprovalInfo>();
  if (!Array.isArray(value)) return map;
  for (const entry of value) {
    if (entry === null || typeof entry !== "object") continue;
    const candidate = entry as Partial<ApprovalInfo>;
    if (typeof candidate.approval_id !== "string" || !candidate.approval_id.trim()) continue;
    map.set(candidate.approval_id, candidate as ApprovalInfo);
  }
  return map;
}

function normalizeSettingsV2(settings: Partial<AgentUiSettingsV2> | undefined): AgentUiSettingsV2 {
  if (settings === undefined || typeof settings !== "object") {
    return {
      autoSaveHistory: true,
      showToolEvents: false,
      defaultDelivery: "tellraw",
    };
  }

  return {
    autoSaveHistory: typeof settings.autoSaveHistory === "boolean" ? settings.autoSaveHistory : true,
    showToolEvents: typeof settings.showToolEvents === "boolean" ? settings.showToolEvents : false,
    defaultDelivery:
      settings.defaultDelivery === "tellraw" || settings.defaultDelivery === "scriptevent"
        ? settings.defaultDelivery
        : "tellraw",
  };
}

function normalizeHistory(history: unknown): HistoryItem[] {
  if (!Array.isArray(history)) {
    return [];
  }

  return history.filter((item): item is HistoryItem => {
    if (item === null || typeof item !== "object") {
      return false;
    }

    const candidate = item as Partial<HistoryItem>;
    return (
      typeof candidate.id === "string" &&
      typeof candidate.content === "string" &&
      typeof candidate.createdAt === "number" &&
      isHistoryRole(candidate.role) &&
      isHistorySource(candidate.source)
    );
  });
}

/**
 * Normalize v2 stats — includes token fields.
 */
function normalizeStatsV2(stats: Partial<AgentUiStats> | undefined): Partial<AgentUiStats> {
  if (stats === undefined || typeof stats !== "object") {
    return {};
  }

  return {
    openCount: normalizeNonNegativeNumber(stats.openCount),
    sentCount: normalizeNonNegativeNumber(stats.sentCount),
    localHistoryCount: normalizeNonNegativeNumber(stats.localHistoryCount),
    responseChunkCount: normalizeNonNegativeNumber(stats.responseChunkCount),
    lastOpenedAt: normalizeNonNegativeNumber(stats.lastOpenedAt),
    lastSentAt: normalizeNonNegativeNumber(stats.lastSentAt),
    totalInputTokens: normalizeNonNegativeNumber(stats.totalInputTokens),
    totalOutputTokens: normalizeNonNegativeNumber(stats.totalOutputTokens),
    totalTokensCombined: normalizeNonNegativeNumber(stats.totalTokensCombined),
    sessionInputTokens: normalizeNonNegativeNumber(stats.sessionInputTokens),
    sessionOutputTokens: normalizeNonNegativeNumber(stats.sessionOutputTokens),
    sessionTokensCombined: normalizeNonNegativeNumber(stats.sessionTokensCombined),
    roundInputTokens: normalizeNonNegativeNumber(stats.roundInputTokens),
    roundOutputTokens: normalizeNonNegativeNumber(stats.roundOutputTokens),
    roundTokensCombined: normalizeNonNegativeNumber(stats.roundTokensCombined),
  };
}

function isHistoryRole(value: unknown): value is HistoryItem["role"] {
  return value === "user" || value === "assistant" || value === "system" || value === "tool";
}

function isHistorySource(value: unknown): value is HistoryItem["source"] {
  return value === "ui" || value === "python" || value === "system" || value === "local";
}

function normalizeNonNegativeNumber(value: unknown): number | undefined {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return undefined;
  }

  return Math.max(0, Math.floor(value));
}

function clampNumber(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.floor(value)));
}

export function saveAgentUiState(owner: DynamicPropertyOwner, state: AgentUiStateV2): SaveAgentUiStateResult {
  try {
    // Build bucket array (persist only recent history per bucket)
    const buckets: PersistedConversationBucketV2[] = [];
    for (const id of state.conversationOrder) {
      const bucket = state.conversations[id];
      if (!bucket) continue;
      buckets.push({
        id: bucket.id,
        shortId: bucket.shortId,
        title: bucket.title,
        history: bucket.history.slice(-PERSISTED_HISTORY_LIMIT),
        lastActiveAt: bucket.lastActiveAt,
      });
      if (buckets.length >= MAX_CONVERSATIONS) break;
    }

    const persistedIds = buckets.map((bucket) => bucket.id);
    const order = state.conversationOrder.filter((id) => persistedIds.includes(id)).slice(0, MAX_CONVERSATIONS);
    const persisted: PersistedAgentUiStateV2 = {
      version: DDUI_PERSISTENCE_STATE_VERSION,
      activeConversationId: state.activeConversationId,
      conversations: buckets,
      conversationOrder: order.length > 0 ? order : ["default"],
      settings: state.settings,
      stats: state.stats,
      bridgeStatus: state.bridgeStatus.getData(),
      pendingApprovals: Array.from(state.pendingApprovals.values()),
    };

    owner.setDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY, JSON.stringify(persisted));
    return { ok: true };
  } catch (error) {
    return { ok: false, error };
  }
}

/**
 * Load global token stats from a separate DynamicProperty.
 */
export function loadGlobalTokenStats(owner: DynamicPropertyOwner): { input: number; output: number } {
  try {
    const key = `${AGENT_UI_STATE_PROPERTY_KEY}_global_tokens`;
    const raw = owner.getDynamicProperty(key);
    if (typeof raw !== "string") {
      return { input: 0, output: 0 };
    }
    return JSON.parse(raw) as { input: number; output: number };
  } catch {
    return { input: 0, output: 0 };
  }
}

/**
 * Reset global token stats.
 */
export function resetGlobalTokenStats(owner: DynamicPropertyOwner): void {
  try {
    const key = `${AGENT_UI_STATE_PROPERTY_KEY}_global_tokens`;
    owner.setDynamicProperty(key, JSON.stringify({ input: 0, output: 0 }));
  } catch {
    // Best-effort
  }
}
