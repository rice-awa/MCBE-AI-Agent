import type { AgentUiSettings, AgentUiState, BridgeStatus } from "./state";
import { createAgentUiState } from "./state";
import type { HistoryItem } from "./history";
import type { AgentUiStats } from "./stats";

export const AGENT_UI_STATE_PROPERTY_KEY = "mcbeai:ui_state";
export const AGENT_UI_STATE_VERSION = 1;
export const PERSISTED_HISTORY_LIMIT = 20;

type DynamicPropertyOwner = {
  getDynamicProperty(identifier: string): unknown;
  setDynamicProperty(identifier: string, value?: string): void;
};

type PersistedAgentUiState = {
  version: 1;
  bridgeStatus?: BridgeStatus;
  settings?: Partial<AgentUiSettings>;
  history?: HistoryItem[];
  stats?: Partial<AgentUiStats>;
};

export type SaveAgentUiStateResult = { ok: true } | { ok: false; error: unknown };

export function loadAgentUiState(owner: DynamicPropertyOwner): AgentUiState {
  try {
    const rawValue = owner.getDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY);
    if (typeof rawValue !== "string") {
      return createAgentUiState();
    }

    const persisted = JSON.parse(rawValue) as PersistedAgentUiState;
    if (persisted.version !== AGENT_UI_STATE_VERSION) {
      return createAgentUiState();
    }

    const history = normalizeHistory(persisted.history);

    return createAgentUiState({
      settings: normalizeSettings(persisted.settings),
      bridgeStatus: normalizeBridgeStatus(persisted.bridgeStatus),
      history,
      stats: normalizeStats(persisted.stats, history.length),
    });
  } catch {
    return createAgentUiState();
  }
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

function normalizeSettings(settings: Partial<AgentUiSettings> | undefined): Partial<AgentUiSettings> {
  if (settings === undefined || typeof settings !== "object") {
    return {};
  }

  return {
    autoSaveHistory:
      typeof settings.autoSaveHistory === "boolean" ? settings.autoSaveHistory : undefined,
    maxHistoryItems:
      typeof settings.maxHistoryItems === "number"
        ? clampNumber(settings.maxHistoryItems, 10, 50)
        : undefined,
    showToolEvents: typeof settings.showToolEvents === "boolean" ? settings.showToolEvents : undefined,
    responsePreviewLength:
      typeof settings.responsePreviewLength === "number"
        ? clampNumber(settings.responsePreviewLength, 60, 240)
        : undefined,
    defaultDelivery:
      settings.defaultDelivery === "tellraw" || settings.defaultDelivery === "scriptevent"
        ? settings.defaultDelivery
        : undefined,
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

function normalizeStats(
  stats: Partial<AgentUiStats> | undefined,
  rawHistoryCount: number,
): Partial<AgentUiStats> {
  if (stats === undefined || typeof stats !== "object") {
    return {};
  }

  return {
    openCount: normalizeNonNegativeNumber(stats.openCount),
    sentCount: normalizeNonNegativeNumber(stats.sentCount),
    localHistoryCount: Math.min(
      normalizeNonNegativeNumber(stats.localHistoryCount) ?? rawHistoryCount,
      rawHistoryCount,
    ),
    responseChunkCount: normalizeNonNegativeNumber(stats.responseChunkCount),
    lastOpenedAt: normalizeNonNegativeNumber(stats.lastOpenedAt),
    lastSentAt: normalizeNonNegativeNumber(stats.lastSentAt),
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

export function saveAgentUiState(
  owner: DynamicPropertyOwner,
  state: AgentUiState,
): SaveAgentUiStateResult {
  try {
    const persisted: PersistedAgentUiState = {
      version: AGENT_UI_STATE_VERSION,
      bridgeStatus: state.bridgeStatus.getData(),
      settings: state.settings,
      history: state.history.slice(-PERSISTED_HISTORY_LIMIT),
      stats: state.stats,
    };

    owner.setDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY, JSON.stringify(persisted));
    return { ok: true };
  } catch (error) {
    return { ok: false, error };
  }
}
