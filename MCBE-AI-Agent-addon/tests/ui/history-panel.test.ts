import { beforeEach, describe, expect, it } from "vitest";

import {
  __resetDduiMock,
  __setNextCustomFormInteraction,
} from "@minecraft/server-ui";

import { showHistoryPanel } from "../../scripts/ui/panels/historyPanel";
import { CLOSE_ROUTE } from "../../scripts/ui/panels/routes";
import { createAgentUiState } from "../../scripts/ui/state";
import { AGENT_UI_STATE_PROPERTY_KEY } from "../../scripts/ui/storage";

import type { HistoryItem } from "../../scripts/ui/history";

describe("history panel", () => {
  beforeEach(() => {
    __resetDduiMock();
  });

  it("routes to the previous page from page index 1", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "上一页",
      autoCloseAfterButtonClick: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState({ history: createHistoryItems(6) });

    const route = await showHistoryPanel(player, uiState, 1);

    expect(route).toEqual({ panel: "history", pageIndex: 0 });
  });

  it("routes to the next page from page index 0", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "下一页",
      autoCloseAfterButtonClick: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState({ history: createHistoryItems(6) });

    const route = await showHistoryPanel(player, uiState, 0);

    expect(route).toEqual({ panel: "history", pageIndex: 1 });
  });

  it("clears history, syncs stats, persists, notifies player, and returns main", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "清空历史",
      autoCloseAfterButtonClick: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState({
      history: createHistoryItems(6),
      stats: { localHistoryCount: 6 },
    });

    const route = await showHistoryPanel(player, uiState, 0);

    expect(route).toEqual({ panel: "main" });
    expect(uiState.history).toEqual([]);
    expect(uiState.stats.localHistoryCount).toBe(0);
    expect(player.messages).toContain("MCBE AI Agent: 本地聊天记录已清空。");

    const persisted = JSON.parse(String(player.getDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY)));
    expect(persisted.history).toEqual([]);
    expect(persisted.stats.localHistoryCount).toBe(0);
  });

  it("routes to close and persists state when close is clicked", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "关闭",
      autoCloseAfterButtonClick: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState({ history: createHistoryItems(2) });
    uiState.lastPrompt.setData("persist me");

    const route = await showHistoryPanel(player, uiState, 0);

    expect(route).toEqual(CLOSE_ROUTE);
    const persistedRaw = player.getDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY);
    expect(typeof persistedRaw).toBe("string");
    const persisted = JSON.parse(String(persistedRaw));
    expect(persisted.history).toHaveLength(2);
    expect(persisted.version).toBe(1);
  });

  it("closes cleanly when the DDUI observable api is unavailable", async () => {
    __setNextCustomFormInteraction({
      failOnObservableCreate: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState({ history: createHistoryItems(1) });

    const route = await showHistoryPanel(player, uiState, 0);

    expect(route).toEqual(CLOSE_ROUTE);
    expect(player.messages).toContain("MCBE AI Agent: 表单暂时无法打开，请稍后再试。");
  });
});

function createHistoryItems(count: number): HistoryItem[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `history-${index}`,
    role: "user" as const,
    content: `message-${index}`,
    createdAt: index,
    source: "ui" as const,
  }));
}

function createFakePlayer(initialProperties: Record<string, unknown> = {}) {
  const properties = new Map(Object.entries(initialProperties));
  const messages: string[] = [];

  return {
    name: "TestPlayer",
    messages,
    getDynamicProperty(identifier: string) {
      return properties.get(identifier);
    },
    setDynamicProperty(identifier: string, value: unknown) {
      if (value === undefined) {
        properties.delete(identifier);
        return;
      }
      properties.set(identifier, value);
    },
    sendMessage(message: string) {
      messages.push(message);
    },
  };
}
