import { beforeEach, describe, expect, it } from "vitest";

import { __resetMinecraftServerMock, __setMockPlayers } from "@minecraft/server";
import { __getLastCustomForm, __resetDduiMock, __setNextCustomFormInteraction } from "@minecraft/server-ui";

import { TOOL_PLAYER_NAME } from "../../scripts/bridge/constants";
import { showAgentConsole } from "../../scripts/ui/panels/agentConsole";
import { CLOSE_ROUTE } from "../../scripts/ui/panels/routes";
import { createAgentUiStateV2 } from "../../scripts/ui/state";
import type { AgentUiStateV2 } from "../../scripts/ui/state";
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
        消息内容: "  你好 AI  ",
      },
    });

    const player = createFakePlayer();
    const uiState = createAgentUiStateV2();

    const route = await showAgentConsole(player, uiState);

    expect(route).toEqual({ panel: "main" });
    expect(uiState.conversations.default.history).toHaveLength(1);
    expect(uiState.conversations.default.history[0]).toMatchObject({
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

  it("shows all expected buttons on the main panel", async () => {
    await showAgentConsole(createFakePlayer(), createAgentUiStateV2());

    const buttons = __getLastCustomForm()
      ?.getComponents()
      .filter((component) => component.startsWith("button:"));

    expect(buttons).toEqual([
      "button:切换",
      "button:发送",
      "button:新会话",
      "button:全部对话",
      "button:更多",
      "button:同意",
      "button:拒绝",
    ]);
  });

  it("routes to the conversation list from the 切换 button", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "切换",
      autoCloseAfterButtonClick: true,
    });

    const route = await showAgentConsole(createFakePlayer(), createAgentUiStateV2());

    expect(route).toEqual({ panel: "conversationList" });
  });

  it("routes to the more menu from the 更多 button", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "更多",
      autoCloseAfterButtonClick: true,
    });

    const route = await showAgentConsole(createFakePlayer(), createAgentUiStateV2());

    expect(route).toEqual({ panel: "more" });
  });

  it("routes to conversation preview from the 全部对话 button", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "全部对话",
      autoCloseAfterButtonClick: true,
    });

    const route = await showAgentConsole(createFakePlayer(), createAgentUiStateV2());

    expect(route).toEqual({ panel: "conversationPreview" });
  });

  it("keeps the main panel open when the message is empty", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "发送",
      fieldValues: {
        消息内容: "   ",
      },
    });

    const player = createFakePlayer();
    const uiState = createAgentUiStateV2();

    const route = await showAgentConsole(player, uiState);

    expect(route).toEqual({ panel: "main" });
    expect(uiState.conversations.default.history).toEqual([]);
    expect(player.messages).toContain("MCBE AI Agent: 消息不能为空。");
  });

  it("routes to close when the player dismisses the form without pressing a panel button", async () => {
    __setNextCustomFormInteraction({
      closeReason: "UserClose",
    });

    const player = createFakePlayer();
    const uiState = createAgentUiStateV2();
    uiState.lastPrompt.setData("hello");

    const route = await showAgentConsole(player, uiState);

    expect(route).toEqual(CLOSE_ROUTE);
    expect(player.getDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY)).toBeTypeOf("string");
  });

  it("adds spacing between dense main panel sections", async () => {
    await showAgentConsole(createFakePlayer(), createAgentUiStateV2());

    const form = __getLastCustomForm();

    expect(form?.getComponents().filter((component) => component === "spacer").length).toBeGreaterThanOrEqual(4);
  });

  it("closes cleanly when the DDUI observable api is unavailable", async () => {
    __setNextCustomFormInteraction({
      failOnObservableCreate: true,
    });

    const player = createFakePlayer();
    const uiState = createAgentUiStateV2();

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
