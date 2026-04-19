import type { HistoryItem } from "./history";
import type { AgentUiStats } from "./stats";

export type BridgeStatus = "disconnected" | "connecting" | "ready";

export type ObservableLike<T> = {
  getData(): T;
  setData(value: T): void;
};

export type AgentUiDelivery = "tellraw" | "scriptevent";

export type AgentUiSettings = {
  autoSaveHistory: boolean;
  maxHistoryItems: number;
  showToolEvents: boolean;
  responsePreviewLength: number;
  defaultDelivery: AgentUiDelivery;
};

export type AgentUiState = {
  bridgeStatus: ObservableLike<BridgeStatus>;
  lastPrompt: ObservableLike<string>;
  lastResponsePreview: ObservableLike<string>;
  history: HistoryItem[];
  settings: AgentUiSettings;
  stats: AgentUiStats;
};

export type AgentUiStateInput = Partial<{
  bridgeStatus: BridgeStatus;
  lastPrompt: string;
  lastResponsePreview: string;
  history: HistoryItem[];
  settings: Partial<AgentUiSettings>;
  stats: Partial<AgentUiStats>;
}>;

export const DEFAULT_AGENT_UI_SETTINGS: AgentUiSettings = {
  autoSaveHistory: true,
  maxHistoryItems: 30,
  showToolEvents: true,
  responsePreviewLength: 120,
  defaultDelivery: "tellraw",
};

export const DEFAULT_AGENT_UI_STATS: AgentUiStats = {
  openCount: 0,
  sentCount: 0,
  localHistoryCount: 0,
  responseChunkCount: 0,
  lastOpenedAt: 0,
  lastSentAt: 0,
};

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
