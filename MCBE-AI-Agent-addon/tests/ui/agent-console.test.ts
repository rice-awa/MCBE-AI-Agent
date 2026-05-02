import { beforeEach, describe, expect, it } from "vitest";

import {
  __resetDduiMock,
  __setNextCustomFormInteraction,
} from "@minecraft/server-ui";

import { showAgentConsole } from "../../scripts/ui/panels/agentConsole";
import { CLOSE_ROUTE } from "../../scripts/ui/panels/routes";
import { createAgentUiState } from "../../scripts/ui/state";
import { AGENT_UI_STATE_PROPERTY_KEY } from "../../scripts/ui/storage";

describe("agent console panel", () => {
  beforeEach(() => {
    __resetDduiMock();
  });

  it("routes to chat input when 发送消息 is clicked", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "发送消息",
      autoCloseAfterButtonClick: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState();

    const route = await showAgentConsole(player, uiState);

    expect(route).toEqual({ panel: "chatInput" });
  });

  it("routes to history when 聊天记录 is clicked", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "聊天记录",
      autoCloseAfterButtonClick: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState();

    const route = await showAgentConsole(player, uiState);

    expect(route).toEqual({ panel: "history" });
  });

  it("routes to close and persists state when 关闭 is clicked", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "关闭",
      autoCloseAfterButtonClick: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState();
    uiState.lastPrompt.setData("hello");
    uiState.lastResponsePreview.setData("world");

    const route = await showAgentConsole(player, uiState);

    expect(route).toEqual(CLOSE_ROUTE);
    const persistedRaw = player.getDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY);
    expect(typeof persistedRaw).toBe("string");
    const persisted = JSON.parse(String(persistedRaw));
    expect(persisted.version).toBe(1);
    expect(persisted.settings).toBeDefined();
  });

  it("closes cleanly when the DDUI observable api is unavailable", async () => {
    __setNextCustomFormInteraction({
      failOnObservableCreate: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState();

    await expect(showAgentConsole(player, uiState)).resolves.toEqual(CLOSE_ROUTE);
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
