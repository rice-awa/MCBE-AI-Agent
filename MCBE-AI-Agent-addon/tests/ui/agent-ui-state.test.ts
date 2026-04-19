import { describe, expect, it } from "vitest";

import { buildAgentChatCommand, isUiTriggerItem } from "../../scripts/ui/commands";
import {
  appendHistoryItem,
  clearHistory,
  getHistoryPage,
  summarizeHistoryItem,
} from "../../scripts/ui/history";
import {
  createAgentUiState,
  DEFAULT_AGENT_UI_SETTINGS,
  DEFAULT_AGENT_UI_STATS,
} from "../../scripts/ui/state";
import { loadAgentUiState, saveAgentUiState } from "../../scripts/ui/storage";

describe("agent ui state", () => {
  it("starts with bridge disconnected state", () => {
    const state = createAgentUiState();

    expect(state.bridgeStatus.getData()).toBe("disconnected");
    expect(state.lastPrompt.getData()).toBe("");
    expect(state.lastResponsePreview.getData()).toBe("");
    expect(state.history).toEqual([]);
    expect(state.settings).toEqual(DEFAULT_AGENT_UI_SETTINGS);
    expect(state.stats).toEqual(DEFAULT_AGENT_UI_STATS);
  });

  it("creates a player-scoped state from persisted dynamic properties", () => {
    const player = createFakePlayer({
      "mcbeai:ui_state": JSON.stringify({
        version: 1,
        settings: {
          autoSaveHistory: false,
          maxHistoryItems: 20,
          showToolEvents: false,
          responsePreviewLength: 80,
          defaultDelivery: "scriptevent",
        },
        history: [
          {
            id: "old-1",
            role: "user",
            content: "你好",
            createdAt: 100,
            source: "ui",
          },
        ],
        stats: {
          openCount: 3,
          sentCount: 2,
          localHistoryCount: 1,
          responseChunkCount: 0,
          lastOpenedAt: 100,
          lastSentAt: 90,
        },
      }),
    });

    const state = loadAgentUiState(player);

    expect(state.settings.maxHistoryItems).toBe(20);
    expect(state.settings.defaultDelivery).toBe("scriptevent");
    expect(state.history).toHaveLength(1);
    expect(state.stats.openCount).toBe(3);
  });

  it("filters invalid persisted history and keeps stats aligned with loaded history", () => {
    const player = createFakePlayer({
      "mcbeai:ui_state": JSON.stringify({
        version: 1,
        settings: {
          maxHistoryItems: 50,
          responsePreviewLength: 999,
        },
        history: [
          {
            id: "valid-1",
            role: "user",
            content: "保留",
            createdAt: 100,
            source: "ui",
          },
          {
            id: "invalid-1",
            role: "user",
            content: 123,
            createdAt: 100,
            source: "ui",
          },
        ],
        stats: {
          openCount: 3,
          sentCount: 2,
          localHistoryCount: 99,
          responseChunkCount: 0,
          lastOpenedAt: 100,
          lastSentAt: 90,
        },
      }),
    });

    const state = loadAgentUiState(player);

    expect(state.history.map((item) => item.id)).toEqual(["valid-1"]);
    expect(state.settings.responsePreviewLength).toBe(240);
    expect(state.stats.localHistoryCount).toBe(1);
  });

  it("persists only the compact versioned UI state", () => {
    const player = createFakePlayer();
    const state = createAgentUiState({
      history: Array.from({ length: 12 }, (_, index) => ({
        id: `item-${index}`,
        role: "user",
        content: `message-${index}`,
        createdAt: index,
        source: "ui",
      })),
      stats: {
        ...DEFAULT_AGENT_UI_STATS,
        localHistoryCount: 12,
      },
    });

    const result = saveAgentUiState(player, state);

    expect(result.ok).toBe(true);
    const rawValue = player.getDynamicProperty("mcbeai:ui_state");
    const persisted = JSON.parse(String(rawValue));
    expect(persisted.version).toBe(1);
    expect(persisted.history).toHaveLength(12);
    expect(persisted.history[0].id).toBe("item-0");
  });
});

describe("agent UI commands", () => {
  it("only opens the UI for command blocks", () => {
    expect(isUiTriggerItem("minecraft:command_block")).toBe(true);
    expect(isUiTriggerItem("minecraft:compass")).toBe(false);
  });

  it("builds the existing chat command text from user input", () => {
    expect(buildAgentChatCommand("  你好 AI  ")).toBe("AGENT 聊天 你好 AI");
  });
});

describe("agent UI history", () => {
  it("appends, truncates, and pages newest history first", () => {
    const history = Array.from({ length: 6 }, (_, index) => ({
      id: `item-${index}`,
      role: "user" as const,
      content: `message-${index}`,
      createdAt: index,
      source: "ui" as const,
    }));

    const nextHistory = appendHistoryItem(
      history,
      {
        id: "item-6",
        role: "assistant",
        content: "message-6",
        createdAt: 6,
        source: "python",
      },
      5,
    );

    expect(nextHistory.map((item) => item.id)).toEqual([
      "item-2",
      "item-3",
      "item-4",
      "item-5",
      "item-6",
    ]);
    expect(getHistoryPage(nextHistory, 0, 3).items.map((item) => item.id)).toEqual([
      "item-6",
      "item-5",
      "item-4",
    ]);
    expect(getHistoryPage(nextHistory, 1, 3).items.map((item) => item.id)).toEqual([
      "item-3",
      "item-2",
    ]);
  });

  it("summarizes long history items for form display", () => {
    const summary = summarizeHistoryItem(
      {
        id: "long",
        role: "user",
        content: "a".repeat(130),
        createdAt: 0,
        source: "ui",
      },
      10,
    );

    expect(summary).toContain("[user/ui]");
    expect(summary).toContain("aaaaaaaaaa...");
  });

  it("clears history", () => {
    expect(
      clearHistory([
        {
          id: "item-1",
          role: "user",
          content: "message",
          createdAt: 1,
          source: "ui",
        },
      ]),
    ).toEqual([]);
  });
});

function createFakePlayer(initialProperties: Record<string, unknown> = {}) {
  const properties = new Map(Object.entries(initialProperties));

  return {
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
  };
}
