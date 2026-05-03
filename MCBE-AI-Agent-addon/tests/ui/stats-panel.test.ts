import { beforeEach, describe, expect, it } from "vitest";

import {
  __resetDduiMock,
  __setNextCustomFormInteraction,
} from "@minecraft/server-ui";

import type { HistoryItem } from "../../scripts/ui/history";
import { showStatsPanel } from "../../scripts/ui/panels/statsPanel";
import { CLOSE_ROUTE } from "../../scripts/ui/panels/routes";
import { createAgentUiState } from "../../scripts/ui/state";
import { AGENT_UI_STATE_PROPERTY_KEY } from "../../scripts/ui/storage";

describe("stats panel", () => {
  beforeEach(() => {
    __resetDduiMock();
  });

  it("routes to main and syncs local history count when 返回主面板 is clicked", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "返回主面板",
      autoCloseAfterButtonClick: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState({
      history: createHistoryItems(3),
      stats: { localHistoryCount: 0 },
    });

    const route = await showStatsPanel(player, uiState);

    expect(route).toEqual({ panel: "main" });
    expect(uiState.stats.localHistoryCount).toBe(3);
  });

  it("resets stats, preserves local history count, persists, notifies player, and returns main", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "重置统计",
      autoCloseAfterButtonClick: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState({
      history: createHistoryItems(4),
      stats: {
        openCount: 7,
        sentCount: 8,
        localHistoryCount: 1,
        responseChunkCount: 9,
        lastOpenedAt: 100,
        lastSentAt: 200,
      },
    });

    const route = await showStatsPanel(player, uiState);

    expect(route).toEqual({ panel: "main" });
    expect(uiState.stats.openCount).toBe(0);
    expect(uiState.stats.sentCount).toBe(0);
    expect(uiState.stats.responseChunkCount).toBe(0);
    expect(uiState.stats.lastSentAt).toBe(0);
    expect(uiState.stats.lastOpenedAt).toBeGreaterThan(0);
    expect(uiState.stats.localHistoryCount).toBe(4);
    expect(player.messages).toContain("MCBE AI Agent: 统计信息已重置。");

    const persisted = JSON.parse(String(player.getDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY)));
    expect(persisted.stats.openCount).toBe(0);
    expect(persisted.stats.sentCount).toBe(0);
    expect(persisted.stats.responseChunkCount).toBe(0);
    expect(persisted.stats.lastSentAt).toBe(0);
    expect(persisted.stats.localHistoryCount).toBe(4);
  });

  it("routes to close and persists state when 关闭 is clicked", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "关闭",
      autoCloseAfterButtonClick: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState({
      history: createHistoryItems(2),
      stats: { sentCount: 5, localHistoryCount: 0 },
    });

    const route = await showStatsPanel(player, uiState);

    expect(route).toEqual(CLOSE_ROUTE);
    const persistedRaw = player.getDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY);
    expect(typeof persistedRaw).toBe("string");
    const persisted = JSON.parse(String(persistedRaw));
    expect(persisted.stats.sentCount).toBe(5);
    expect(persisted.stats.localHistoryCount).toBe(2);
    expect(persisted.version).toBe(1);
  });

  it("closes cleanly when the DDUI observable api is unavailable", async () => {
    __setNextCustomFormInteraction({
      failOnObservableCreate: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState({ history: createHistoryItems(1) });

    const route = await showStatsPanel(player, uiState);

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
