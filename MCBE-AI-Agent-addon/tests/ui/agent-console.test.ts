import { beforeEach, describe, expect, it } from "vitest";

import {
  __resetMinecraftServerMock,
  __setMockPlayers,
} from "@minecraft/server";
import {
  __getLastCustomForm,
  __resetDduiMock,
  __setNextCustomFormInteraction,
} from "@minecraft/server-ui";

import { TOOL_PLAYER_NAME } from "../../scripts/bridge/constants";
import { showAgentConsole } from "../../scripts/ui/panels/agentConsole";
import { CLOSE_ROUTE } from "../../scripts/ui/panels/routes";
import { createAgentUiState } from "../../scripts/ui/state";
import { AGENT_UI_STATE_PROPERTY_KEY } from "../../scripts/ui/storage";

describe("agent console panel", () => {
  beforeEach(() => {
    __resetDduiMock();
    __resetMinecraftServerMock();
    __setMockPlayers([createToolPlayer()]);
  });

  it("sends a message from the main panel without closing it", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "发送",
      fieldValues: {
        "消息内容": "  你好 AI  ",
      },
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState();

    const route = await showAgentConsole(player, uiState);

    expect(route).toEqual({ panel: "main" });
    expect(uiState.history).toHaveLength(1);
    expect(uiState.history[0]).toMatchObject({
      role: "user",
      content: "你好 AI",
      source: "ui",
    });
    expect(uiState.lastPrompt.getData()).toBe("你好 AI");
    expect(uiState.bridgeStatus.getData()).toBe("sent");
    expect(uiState.stats.sentCount).toBe(1);
    expect(__getLastCustomForm()?.getFieldData("消息内容")).toBe("");
    expect(player.messages).toContain("MCBE AI Agent: 消息已发送至 AI 服务。");
  });

  it("keeps the main panel open when the message is empty", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "发送",
      fieldValues: {
        "消息内容": "   ",
      },
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState();

    const route = await showAgentConsole(player, uiState);

    expect(route).toEqual({ panel: "main" });
    expect(uiState.history).toEqual([]);
    expect(player.messages).toContain("MCBE AI Agent: 消息不能为空。");
  });

  it("routes to settings from the main panel", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "设置",
      autoCloseAfterButtonClick: true,
    });

    const route = await showAgentConsole(createFakePlayer(), createAgentUiState());

    expect(route).toEqual({ panel: "settings" });
  });

  it("routes to stats from the main panel", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "统计信息",
      autoCloseAfterButtonClick: true,
    });

    const route = await showAgentConsole(createFakePlayer(), createAgentUiState());

    expect(route).toEqual({ panel: "stats" });
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
    id: "player-1",
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

function createToolPlayer() {
  return {
    name: TOOL_PLAYER_NAME,
    runCommand: () => ({ successCount: 1 }),
  };
}
