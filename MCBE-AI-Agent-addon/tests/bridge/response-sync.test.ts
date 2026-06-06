import { beforeEach, describe, expect, it } from "vitest";
import {
  __emitScriptEvent,
  __resetMinecraftServerMock,
  __setMockPlayers,
} from "@minecraft/server";

import { AI_RESP_MESSAGE_ID } from "../../scripts/bridge/constants";
import {
  clearActiveUiState,
  registerResponseSyncHandler,
  resetResponseSyncForTests,
  setActiveUiState,
} from "../../scripts/bridge/responseSync";
import { AGENT_UI_STATE_PROPERTY_KEY, saveAgentUiState } from "../../scripts/ui/storage";
import { createAgentUiState } from "../../scripts/ui/state";

const PLAYER_ID = "player-1";
const PLAYER_NAME = "TestPlayer";

describe("response sync", () => {
  beforeEach(() => {
    __resetMinecraftServerMock();
    resetResponseSyncForTests();
  });

  it("refreshes the active conversation when an assistant response completes", () => {
    const player = createFakePlayer();
    const uiState = createAgentUiState();
    let refreshCount = 0;
    uiState.refreshConversation = () => {
      refreshCount += 1;
    };

    __setMockPlayers([player]);
    setActiveUiState(PLAYER_ID, uiState);
    registerResponseSyncHandler();

    __emitScriptEvent({
      id: AI_RESP_MESSAGE_ID,
      message: JSON.stringify({
        id: "resp-1",
        i: 1,
        n: 1,
        p: PLAYER_NAME,
        r: "assistant",
        c: "你好，玩家",
      }),
    });

    expect(uiState.history).toHaveLength(1);
    expect(uiState.history[0]).toMatchObject({
      role: "assistant",
      content: "你好，玩家",
      source: "python",
    });
    expect(uiState.lastResponsePreview.getData()).toBe("你好，玩家");
    expect(refreshCount).toBe(1);

    clearActiveUiState(PLAYER_ID);
  });

  it("ignores python user echoes that already exist as UI-submitted prompts", () => {
    const player = createFakePlayer();
    const uiState = createAgentUiState({
      history: [
        {
          id: "ui-1",
          role: "user",
          content: "你有什么工具",
          createdAt: 1,
          source: "ui",
        },
      ],
    });
    let refreshCount = 0;
    uiState.refreshConversation = () => {
      refreshCount += 1;
    };
    saveAgentUiState(player, uiState);

    __setMockPlayers([player]);
    setActiveUiState(PLAYER_ID, uiState);
    registerResponseSyncHandler();

    __emitScriptEvent({
      id: AI_RESP_MESSAGE_ID,
      message: JSON.stringify({
        id: "echo-1",
        i: 1,
        n: 1,
        p: PLAYER_NAME,
        r: "user",
        c: "你有什么工具",
      }),
    });

    expect(uiState.history).toHaveLength(1);
    expect(uiState.history[0]).toMatchObject({
      role: "user",
      content: "你有什么工具",
      source: "ui",
    });
    expect(refreshCount).toBe(0);

    const persisted = JSON.parse(String(player.getDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY)));
    expect(persisted.history).toHaveLength(1);
    expect(persisted.history[0]).toMatchObject({
      role: "user",
      content: "你有什么工具",
      source: "ui",
    });

    clearActiveUiState(PLAYER_ID);
  });
});

function createFakePlayer() {
  const properties = new Map<string, unknown>();

  return {
    id: PLAYER_ID,
    name: PLAYER_NAME,
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
