import { describe, expect, it, vi, beforeEach } from "vitest";
import {
  shouldHandleScriptEvent,
  BRIDGE_MESSAGE_ID,
  BRIDGE_REQUEST_MESSAGE_ID,
  handleBridgeScriptEvent,
} from "../../scripts/bridge/router";

// Mock tool player so handleBridgeScriptEvent can finish without a simulated player
vi.mock("../../scripts/bridge/toolPlayer", () => ({
  sendBridgeResponseChunks: vi.fn(),
}));

import { sendBridgeResponseChunks } from "../../scripts/bridge/toolPlayer";

describe("bridge router", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("shouldHandleScriptEvent", () => {
    it("returns true for mcbews:bridge_req", () => {
      expect(shouldHandleScriptEvent("mcbews:bridge_req")).toBe(true);
    });

    it("returns false for other message ids", () => {
      expect(shouldHandleScriptEvent("server:data")).toBe(false);
      expect(shouldHandleScriptEvent("")).toBe(false);
      expect(shouldHandleScriptEvent("mcbeai:bridge_request")).toBe(false);
      expect(shouldHandleScriptEvent("mcbews:other")).toBe(false);
      expect(shouldHandleScriptEvent("other:bridge_req")).toBe(false);
    });

    it("handles edge cases", () => {
      expect(shouldHandleScriptEvent("")).toBe(false);
      expect(shouldHandleScriptEvent("hello")).toBe(false);
      expect(shouldHandleScriptEvent("mcbews")).toBe(false);
    });
  });

  describe("BRIDGE_REQUEST_MESSAGE_ID", () => {
    it("equals mcbews:bridge_req", () => {
      expect(BRIDGE_REQUEST_MESSAGE_ID).toBe("mcbews:bridge_req");
      expect(BRIDGE_MESSAGE_ID).toBe(BRIDGE_REQUEST_MESSAGE_ID);
    });

    it("is consistent with shouldHandleScriptEvent", () => {
      expect(shouldHandleScriptEvent(BRIDGE_MESSAGE_ID)).toBe(true);
    });
  });

  describe("async capability handlers", () => {
    it("awaits handlers and sends JSON response chunks", async () => {
      const event = {
        id: BRIDGE_MESSAGE_ID,
        message: JSON.stringify({
          request_id: "req-async-1",
          capability: "get_capabilities",
          payload: {},
        }),
      };

      await handleBridgeScriptEvent(event as never);

      expect(sendBridgeResponseChunks).toHaveBeenCalledTimes(1);
      const [requestId, payloadStr] = (sendBridgeResponseChunks as ReturnType<typeof vi.fn>).mock
        .calls[0];
      expect(requestId).toBe("req-async-1");
      const response = JSON.parse(payloadStr as string);
      expect(response.ok).toBe(true);
      expect(response.payload.capabilities.block_ops.inspect).toBe(true);
    });

    it("awaits async inspect_block capability", async () => {
      const { __setBlock, __resetBlocks } = await import("../__mocks__/minecraft-server");
      __resetBlocks();
      __setBlock("minecraft:overworld", 0, 64, 0, {
        typeId: "minecraft:stone",
      });

      const event = {
        id: BRIDGE_MESSAGE_ID,
        message: JSON.stringify({
          request_id: "req-inspect",
          capability: "inspect_block",
          payload: {
            coordinate_mode: "absolute",
            dimension: "minecraft:overworld",
            position: { x: 0, y: 64, z: 0 },
          },
        }),
      };

      await handleBridgeScriptEvent(event as never);
      expect(sendBridgeResponseChunks).toHaveBeenCalled();
      const payloadStr = (sendBridgeResponseChunks as ReturnType<typeof vi.fn>).mock.calls.at(
        -1,
      )?.[1] as string;
      const response = JSON.parse(payloadStr);
      expect(response.ok).toBe(true);
      expect(response.payload.blocks[0].type_id).toBe("minecraft:stone");
    });
  });
});
