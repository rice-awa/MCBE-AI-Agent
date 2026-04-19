import { beforeEach, describe, expect, it } from "vitest";

import {
  __resetDduiMock,
  __setNextCustomFormInteraction,
} from "@minecraft/server-ui";

import { appendHistoryItem } from "../../scripts/ui/history";
import { showSettingsPanel } from "../../scripts/ui/panels/settingsPanel";
import { createAgentUiState } from "../../scripts/ui/state";
import { AGENT_UI_STATE_PROPERTY_KEY } from "../../scripts/ui/storage";

describe("settings panel", () => {
  beforeEach(() => {
    __resetDduiMock();
  });

  it("saves updated settings through the DDUI save action", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "保存",
      autoCloseAfterButtonClick: true,
      fieldValues: {
        "自动保存历史": false,
        "历史保留条数": 10,
        "显示工具事件": false,
        "响应预览长度": 60,
        "默认响应方式": 1,
      },
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState();
    uiState.history = Array.from({ length: 12 }, (_, index) => ({
      id: `history-${index}`,
      role: "user" as const,
      content: `message-${index}`,
      createdAt: index,
      source: "ui" as const,
    })).reduce(
      (history, item) => appendHistoryItem(history, item, 50),
      uiState.history,
    );
    uiState.stats.localHistoryCount = uiState.history.length;

    const route = await showSettingsPanel(player, uiState);

    expect(route).toEqual({ panel: "main" });
    expect(uiState.settings).toMatchObject({
      autoSaveHistory: false,
      maxHistoryItems: 10,
      showToolEvents: false,
      responsePreviewLength: 60,
      defaultDelivery: "scriptevent",
    });
    expect(uiState.history).toHaveLength(10);
    expect(uiState.stats.localHistoryCount).toBe(10);
    expect(player.messages).toContain("MCBE AI Agent: 设置已保存。");

    const persisted = JSON.parse(String(player.getDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY)));
    expect(persisted.settings.defaultDelivery).toBe("scriptevent");
    expect(persisted.history).toHaveLength(10);
  });

  it("closes cleanly when the DDUI beta api is unavailable", async () => {
    __setNextCustomFormInteraction({
      failOnObservableCreate: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState();

    await expect(showSettingsPanel(player, uiState)).resolves.toEqual({ panel: "close" });
    expect(player.messages).toContain("MCBE AI Agent: 表单暂时无法打开，请稍后再试。");
  });
});

function createFakePlayer(initialProperties: Record<string, unknown> = {}) {
  const properties = new Map(Object.entries(initialProperties));
  const messages: string[] = [];

  return {
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
