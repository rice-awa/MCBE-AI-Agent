import { describe, expect, it } from "vitest";
import {
  shouldHandleScriptEvent,
  BRIDGE_MESSAGE_ID,
  BRIDGE_REQUEST_MESSAGE_ID,
} from "../../scripts/bridge/router";

describe("bridge router", () => {
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
});
