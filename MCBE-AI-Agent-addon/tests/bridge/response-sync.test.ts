import { beforeEach, describe, expect, it, vi } from "vitest";
import { __emitScriptEvent, __resetMinecraftServerMock, __setMockPlayers } from "@minecraft/server";

import { TEXT_RESP_MESSAGE_ID } from "../../scripts/bridge/constants";
import {
  clearActiveUiState,
  registerResponseSyncHandler,
  resetResponseSyncForTests,
  setActiveUiState,
  setTextRespHandler,
  setTextResponseMessageHandler,
} from "../../scripts/bridge/responseSync";
import { AGENT_UI_STATE_PROPERTY_KEY, saveAgentUiState } from "../../scripts/ui/storage";
import { createAgentUiStateV2 } from "../../scripts/ui/state";

const PLAYER_ID = "player-1";
const PLAYER_NAME = "TestPlayer";

describe("response sync", () => {
  beforeEach(() => {
    __resetMinecraftServerMock();
    resetResponseSyncForTests();
  });

  it("refreshes the active conversation when an assistant response completes", () => {
    const player = createFakePlayer();
    const uiState = createAgentUiStateV2();
    let refreshCount = 0;
    uiState.refreshConversation = () => {
      refreshCount += 1;
    };

    __setMockPlayers([player]);
    setActiveUiState(PLAYER_ID, uiState);
    registerResponseSyncHandler();

    __emitScriptEvent({
      id: TEXT_RESP_MESSAGE_ID,
      message: JSON.stringify({
        id: "resp-1",
        i: 1,
        n: 1,
        p: PLAYER_NAME,
        r: "assistant",
        c: "你好，玩家",
      }),
    });

    const defaultBucket = uiState.conversations["default"];
    expect(defaultBucket.history).toHaveLength(1);
    expect(defaultBucket.history[0]).toMatchObject({
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
    const uiState = createAgentUiStateV2();
    // Pre-populate with a user message from the UI
    uiState.conversations.default.history = [
      {
        id: "ui-1",
        role: "user",
        content: "你有什么工具",
        createdAt: 1,
        source: "ui",
      },
    ];
    let refreshCount = 0;
    uiState.refreshConversation = () => {
      refreshCount += 1;
    };
    saveAgentUiState(player, uiState);

    __setMockPlayers([player]);
    setActiveUiState(PLAYER_ID, uiState);
    registerResponseSyncHandler();

    __emitScriptEvent({
      id: TEXT_RESP_MESSAGE_ID,
      message: JSON.stringify({
        id: "echo-1",
        i: 1,
        n: 1,
        p: PLAYER_NAME,
        r: "user",
        c: "你有什么工具",
      }),
    });

    expect(uiState.conversations.default.history).toHaveLength(1);
    expect(uiState.conversations.default.history[0]).toMatchObject({
      role: "user",
      content: "你有什么工具",
      source: "ui",
    });
    expect(refreshCount).toBe(0);

    const persisted = JSON.parse(String(player.getDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY)));
    expect(persisted.conversations).toHaveLength(1);
    expect(persisted.conversations[0].history).toHaveLength(1);
    expect(persisted.conversations[0].history[0]).toMatchObject({
      role: "user",
      content: "你有什么工具",
      source: "ui",
    });

    clearActiveUiState(PLAYER_ID);
  });

  it("routes interleaved players and conversations into their owning buckets", () => {
    const alice = { ...createFakePlayer(), id: "alice-id", name: "Alice" };
    const bob = { ...createFakePlayer(), id: "bob-id", name: "Bob" };
    const aliceState = createAgentUiStateV2();
    const bobState = createAgentUiStateV2();
    __setMockPlayers([alice, bob]);
    setActiveUiState(alice.id, aliceState, alice.name);
    setActiveUiState(bob.id, bobState, bob.name);
    registerResponseSyncHandler();

    const frames = [
      { id: "same", i: 1, n: 2, p: "Alice", r: "assistant", c: "爱", cid: "chat-a" },
      { id: "same", i: 1, n: 2, p: "Bob", r: "assistant", c: "好", cid: "chat-b" },
      { id: "same", i: 2, n: 2, p: "Alice", r: "assistant", c: "你", cid: "chat-a" },
      { id: "same", i: 2, n: 2, p: "Bob", r: "assistant", c: "！", cid: "chat-b" },
    ];
    for (const frame of frames) {
      __emitScriptEvent({ id: TEXT_RESP_MESSAGE_ID, message: JSON.stringify(frame) });
    }

    expect(aliceState.conversations["chat-a"].history[0].content).toBe("爱你");
    expect(bobState.conversations["chat-b"].history[0].content).toBe("好！");
    clearActiveUiState(alice.id);
    clearActiveUiState(bob.id);
  });

  it("normalizes approval owner from outer p/cid and rejects claimed-owner conflicts", () => {
    const player = createFakePlayer();
    const uiState = createAgentUiStateV2();
    __setMockPlayers([player]);
    setActiveUiState(PLAYER_ID, uiState, PLAYER_NAME);
    registerResponseSyncHandler();

    __emitScriptEvent({
      id: TEXT_RESP_MESSAGE_ID,
      message: JSON.stringify({
        id: "approval-1",
        i: 1,
        n: 1,
        p: PLAYER_NAME,
        r: "approval",
        cid: "chat-a",
        c: JSON.stringify({ approval_id: "ap-1", tool_name: "run_world_command" }),
      }),
    });
    expect(uiState.pendingApprovals.get("ap-1")).toMatchObject({
      player_name: PLAYER_NAME,
      cid: "chat-a",
      conversation_id: "chat-a",
    });

    __emitScriptEvent({
      id: TEXT_RESP_MESSAGE_ID,
      message: JSON.stringify({
        id: "approval-2",
        i: 1,
        n: 1,
        p: PLAYER_NAME,
        r: "approval",
        cid: "chat-a",
        c: JSON.stringify({ approval_id: "ap-2", player_name: "Other" }),
      }),
    });
    expect(uiState.pendingApprovals.has("ap-2")).toBe(false);
    clearActiveUiState(PLAYER_ID);
  });

  it("keeps the typed and legacy response callbacks at the complete-message boundary", () => {
    const player = createFakePlayer();
    const typed = vi.fn();
    const legacy = vi.fn();
    __setMockPlayers([player]);
    setTextResponseMessageHandler(typed);
    setTextRespHandler(legacy);
    registerResponseSyncHandler();

    __emitScriptEvent({
      id: TEXT_RESP_MESSAGE_ID,
      message: JSON.stringify({
        id: "callback-1",
        i: 1,
        n: 1,
        p: PLAYER_NAME,
        r: "assistant",
        c: "完成",
        cid: "chat-a",
        u: { i: 2, o: 3 },
      }),
    });

    expect(typed).toHaveBeenCalledWith(
      expect.objectContaining({ playerName: PLAYER_NAME, conversationId: "chat-a", content: "完成" })
    );
    expect(legacy).toHaveBeenCalledWith(PLAYER_NAME, "assistant", "完成");
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
