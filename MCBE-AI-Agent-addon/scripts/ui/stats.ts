export type AgentUiStats = {
  openCount: number;
  sentCount: number;
  localHistoryCount: number;
  responseChunkCount: number;
  lastOpenedAt: number;
  lastSentAt: number;
  /** 全局累计 input tokens */
  totalInputTokens: number;
  /** 全局累计 output tokens */
  totalOutputTokens: number;
  /** 全局累计 total tokens (input+output) */
  totalTokensCombined: number;
  /** 当前会话累计 input tokens（内存，不持久化） */
  sessionInputTokens: number;
  /** 当前会话累计 output tokens（内存，不持久化） */
  sessionOutputTokens: number;
  /** 当前会话累计 total tokens（内存，不持久化） */
  sessionTokensCombined: number;
  /** 本轮的 input tokens */
  roundInputTokens: number;
  /** 本轮的 output tokens */
  roundOutputTokens: number;
  /** 本轮的 total tokens */
  roundTokensCombined: number;
  /** Total messages across sessions */
  totalMessages: number;
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
}

/**
 * 记录本轮 token 用量，更新全局和会话累计值。
 */
export function recordTokenUsage(
  stats: AgentUiStats,
  inputTokens: number,
  outputTokens: number,
): AgentUiStats {
  const totalTokens = inputTokens + outputTokens;
  return {
    ...stats,
    roundInputTokens: inputTokens,
    roundOutputTokens: outputTokens,
    roundTokensCombined: totalTokens,
    sessionInputTokens: stats.sessionInputTokens + inputTokens,
    sessionOutputTokens: stats.sessionOutputTokens + outputTokens,
    sessionTokensCombined: stats.sessionTokensCombined + totalTokens,
    totalInputTokens: stats.totalInputTokens + inputTokens,
    totalOutputTokens: stats.totalOutputTokens + outputTokens,
    totalTokensCombined: stats.totalTokensCombined + totalTokens,
    totalMessages: stats.totalMessages + 1,
  };
}

/**
 * 重置本轮 token 统计（切换会话时调用）。
 */
export function resetRoundToken(stats: AgentUiStats): AgentUiStats {
  return {
    ...stats,
    roundInputTokens: 0,
    roundOutputTokens: 0,
    roundTokensCombined: 0,
  };
}

/**
 * 重置会话级别 token 统计（切换会话时调用）。
 */
export function resetSessionTokens(stats: AgentUiStats): AgentUiStats {
  return {
    ...stats,
    sessionInputTokens: 0,
    sessionOutputTokens: 0,
    sessionTokensCombined: 0,
    roundInputTokens: 0,
    roundOutputTokens: 0,
    roundTokensCombined: 0,
  };
}

/**
 * 重置全局 token 统计（+ resetSessionTokens）。
 */
export function resetGlobalTokens(stats: AgentUiStats): AgentUiStats {
  return {
    ...resetSessionTokens(stats),
    totalInputTokens: 0,
    totalOutputTokens: 0,
    totalTokensCombined: 0,
    totalMessages: 0,
  };
}
