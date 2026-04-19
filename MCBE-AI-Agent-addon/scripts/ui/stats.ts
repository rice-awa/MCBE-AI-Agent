export type AgentUiStats = {
  openCount: number;
  sentCount: number;
  localHistoryCount: number;
  responseChunkCount: number;
  lastOpenedAt: number;
  lastSentAt: number;
};

export function recordUiOpened(stats: AgentUiStats, openedAt = Date.now()): AgentUiStats {
  return {
    ...stats,
    openCount: stats.openCount + 1,
    lastOpenedAt: openedAt,
  };
}

export function recordPromptSent(stats: AgentUiStats, sentAt = Date.now()): AgentUiStats {
  return {
    ...stats,
    sentCount: stats.sentCount + 1,
    lastSentAt: sentAt,
  };
}

export function recordResponseChunk(stats: AgentUiStats): AgentUiStats {
  return {
    ...stats,
    responseChunkCount: stats.responseChunkCount + 1,
  };
}

export function syncLocalHistoryCount(stats: AgentUiStats, localHistoryCount: number): AgentUiStats {
  return {
    ...stats,
    localHistoryCount,
  };
}

export function resetStats(resetAt = Date.now()): AgentUiStats {
  return {
    openCount: 0,
    sentCount: 0,
    localHistoryCount: 0,
    responseChunkCount: 0,
    lastOpenedAt: resetAt,
    lastSentAt: 0,
  };
}
