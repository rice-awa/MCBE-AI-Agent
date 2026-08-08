export type HistoryRole = "user" | "assistant" | "system" | "tool";

export type HistorySource = "ui" | "python" | "system" | "local";

export type ChatHistoryItem = {
  id: string;
  role: HistoryRole;
  content: string;
  createdAt: number;
  source: HistorySource;
};

export type HistoryItem = ChatHistoryItem;

export type HistoryPage = {
  items: ChatHistoryItem[];
  pageIndex: number;
  pageSize: number;
  totalItems: number;
  totalPages: number;
};

export function appendHistoryItem(
  history: ChatHistoryItem[],
  item: ChatHistoryItem,
  maxHistoryItems: number,
): ChatHistoryItem[] {
  const limit = Math.max(0, Math.floor(maxHistoryItems));
  if (limit === 0) {
    return [];
  }

  return [...history, item].slice(-limit);
}

export function getHistoryPage(
  history: ChatHistoryItem[],
  pageIndex: number,
  pageSize: number,
): HistoryPage {
  const normalizedPageSize = Math.max(1, Math.floor(pageSize));
  const normalizedPageIndex = Math.max(0, Math.floor(pageIndex));
  const newestFirst = [...history].reverse();
  const start = normalizedPageIndex * normalizedPageSize;

  return {
    items: newestFirst.slice(start, start + normalizedPageSize),
    pageIndex: normalizedPageIndex,
    pageSize: normalizedPageSize,
    totalItems: history.length,
    totalPages: Math.max(1, Math.ceil(history.length / normalizedPageSize)),
  };
}

export function formatHistoryItem(item: ChatHistoryItem): string {
  return `[${item.role}/${item.source}] ${item.content}`;
}

export function summarizeHistoryItem(item: ChatHistoryItem, previewLength: number): string {
  const normalizedLength = Math.max(0, Math.floor(previewLength));
  const content =
    item.content.length > normalizedLength
      ? `${item.content.slice(0, normalizedLength)}...`
      : item.content;

  return `[${item.role}/${item.source}] ${content}`;
}

export function clearHistory(_history: ChatHistoryItem[]): ChatHistoryItem[] {
  return [];
}

export function createHistoryId(prefix = "ui", createdAt = Date.now()): string {
  return `${prefix}-${createdAt}-${Math.floor(Math.random() * 100000)}`;
}

/**
 * Conversation bucket with history (for v2 data structure).
 */
export type ConversationBucketV2 = {
  id: string;
  shortId: number;
  title: string;
  history: HistoryItem[];
  lastActiveAt: number;
};

/**
 * Append an item to a conversation bucket and slice to the given limit.
 */
export function appendToBucket(
  bucket: ConversationBucketV2,
  item: HistoryItem,
  limit: number,
): ConversationBucketV2 {
  const safeLimit = Math.max(0, Math.floor(limit));
  const newHistory = safeLimit === 0 ? [] : [...bucket.history, item].slice(-safeLimit);
  return {
    ...bucket,
    history: newHistory,
    lastActiveAt: Date.now(),
  };
}

/**
 * 返回最近 N 个 user/assistant 轮次的完整对话内容。
 * 过滤 tool 条目，按角色分组。
 */
export function getRecentTurns(history: ChatHistoryItem[], count: number): ChatHistoryItem[] {
  const filtered = history.filter((item) => item.role === "user" || item.role === "assistant");
  return filtered.slice(-count);
}

/**
 * 格式化最近若干轮对话（不含 tool 条目）。
 */
export function formatRecentConversation(history: ChatHistoryItem[], count: number): string {
  const turns = getRecentTurns(history, count);
  if (turns.length === 0) return "";
  return turns
    .map((item) => {
      const roleLabel = item.role === "user" ? "你" : "AI";
      return `${roleLabel}: ${item.content}`;
    })
    .join("\n\n---\n\n");
}
