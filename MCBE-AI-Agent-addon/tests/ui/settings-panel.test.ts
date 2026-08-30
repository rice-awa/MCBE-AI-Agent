import { beforeEach, describe, expect, it } from "vitest";

import { __getLastCustomForm, __resetDduiMock, __setNextCustomFormInteraction } from "@minecraft/server-ui";

import { showSettingsPanel } from "../../scripts/ui/panels/settingsPanel";
import { createAgentUiStateV2 } from "../../scripts/ui/state";
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
        自动保存历史: false,
        显示工具事件: false,
        默认响应方式: 1,
      },
    });

    const player = createFakePlayer();
    const uiState = createAgentUiStateV2();
    uiState.conversations.default.history = Array.from({ length: 5 }, (_, index) => ({
      id: `history-${index}`,
      role: "user" as const,
      content: `message-${index}`,
      createdAt: index,
      source: "ui" as const,
    }));
    uiState.stats.localHistoryCount = uiState.conversations.default.history.length;

    const route = await showSettingsPanel(player, uiState);

    expect(route).toEqual({ panel: "main" });
    expect(uiState.settings).toMatchObject({
      autoSaveHistory: false,
      showToolEvents: false,
      defaultDelivery: "scriptevent",
    });
    expect(player.messages).toContain("MCBE AI Agent: 设置已保存。");

    const persisted = JSON.parse(String(player.getDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY)));
    expect(persisted.settings.defaultDelivery).toBe("scriptevent");
  });

  it("clears local history from settings", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "清空历史",
      autoCloseAfterButtonClick: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiStateV2();
    uiState.conversations.default.history = [
      {
        id: "history-1",
        role: "user",
        content: "hello",
        createdAt: 1,
        source: "ui",
      },
    ];
    uiState.stats.localHistoryCount = 1;
    uiState.lastPrompt.setData("hello");
    uiState.lastResponsePreview.setData("world");

    const route = await showSettingsPanel(player, uiState);

    expect(route).toEqual({ panel: "main" });
    expect(uiState.conversations.default.history).toEqual([]);
    expect(uiState.stats.localHistoryCount).toBe(0);
    expect(uiState.lastResponsePreview.getData()).toBe("");
    expect(player.messages).toContain("MCBE AI Agent: 本地聊天记录已清空。");

    const persisted = JSON.parse(String(player.getDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY)));
    expect(persisted.conversations[0].history).toEqual([]);
    expect(persisted.stats.localHistoryCount).toBe(0);
  });

  it("returns to the main panel when the player dismisses settings", async () => {
    __setNextCustomFormInteraction({
      closeReason: "UserClose",
    });

    const route = await showSettingsPanel(createFakePlayer(), createAgentUiStateV2());

    expect(route).toEqual({ panel: "main" });
  });

  it("adds spacing between setting groups", async () => {
    await showSettingsPanel(createFakePlayer(), createAgentUiStateV2());

    const form = __getLastCustomForm();

    expect(form?.getComponents().filter((component) => component === "spacer").length).toBeGreaterThanOrEqual(4);
  });

  it("closes cleanly when the DDUI beta api is unavailable", async () => {
    __setNextCustomFormInteraction({
      failOnObservableCreate: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiStateV2();

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
