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
