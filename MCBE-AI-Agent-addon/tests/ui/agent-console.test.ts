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

  it("keeps only send and more actions on the main panel", async () => {
    await showAgentConsole(createFakePlayer(), createAgentUiState());

    const buttons = __getLastCustomForm()
      ?.getComponents()
      .filter((component) => component.startsWith("button:"));

    expect(buttons).toEqual(["button:发送", "button:其他"]);
  });

  it("routes to the more menu from the main panel", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "其他",
      autoCloseAfterButtonClick: true,
    });

    const route = await showAgentConsole(createFakePlayer(), createAgentUiState());

    expect(route).toEqual({ panel: "more" });
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

  it("routes to close when the player dismisses the form without pressing a panel button", async () => {
    __setNextCustomFormInteraction({
      closeReason: "UserClose",
    });

    const player = createFakePlayer();
    const uiState = createAgentUiState();
    uiState.lastPrompt.setData("hello");

    const route = await showAgentConsole(player, uiState);

    expect(route).toEqual(CLOSE_ROUTE);
    expect(player.getDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY)).toBeTypeOf("string");
  });

  it("adds spacing between dense main panel sections", async () => {
    await showAgentConsole(createFakePlayer(), createAgentUiState());

    const form = __getLastCustomForm();

    expect(form?.getComponents().filter((component) => component === "spacer").length).toBeGreaterThanOrEqual(3);
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
