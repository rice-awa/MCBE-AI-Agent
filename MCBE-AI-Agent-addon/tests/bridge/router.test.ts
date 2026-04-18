import { describe, expect, it } from "vitest";
import { shouldHandleScriptEvent, BRIDGE_MESSAGE_ID } from "../../scripts/bridge/router";

describe("bridge router", () => {
  describe("shouldHandleScriptEvent", () => {
    it("returns true for mcbeai:bridge_request", () => {
      expect(shouldHandleScriptEvent("mcbeai:bridge_request")).toBe(true);
    });

    it("returns false for other message ids", () => {
      expect(shouldHandleScriptEvent("server:data")).toBe(false);
      expect(shouldHandleScriptEvent("")).toBe(false);
      expect(shouldHandleScriptEvent("mcbeai:other")).toBe(false);
      expect(shouldHandleScriptEvent("mcbeai:")).toBe(false);
      expect(shouldHandleScriptEvent("other:bridge_request")).toBe(false);
    });

    it("handles edge cases", () => {
      // Empty string
      expect(shouldHandleScriptEvent("")).toBe(false);
      // Random strings
      expect(shouldHandleScriptEvent("hello")).toBe(false);
      expect(shouldHandleScriptEvent("mcbeai")).toBe(false);
    });
  });

  describe("BRIDGE_MESSAGE_ID", () => {
    it("equals mcbeai:bridge_request", () => {
      expect(BRIDGE_MESSAGE_ID).toBe("mcbeai:bridge_request");
    });

    it("is consistent with shouldHandleScriptEvent", () => {
      expect(shouldHandleScriptEvent(BRIDGE_MESSAGE_ID)).toBe(true);
    });
  });
});

// Note: handleBridgeScriptEvent and registerBridgeRouter require @minecraft/server
// module which cannot be resolved in test environment. These should be tested via
// integration tests with the actual Minecraft runtime.
//
// For unit testing the async event handling, consider:
// 1. Using vi.mock() with a virtual mock for @minecraft/server
// 2. Or extracting the pure routing logic into a separate testable module
//
// The following patterns are suggested for future test additions:
//
// ```typescript
// // For testing handleBridgeScriptEvent with mocks:
// vi.mock("@minecraft/server", async () => ({
//   system: {
//     afterEvents: {
//       scriptEventReceive: {
//         subscribe: vi.fn(),
//       },
//     },
//   },
// }));
//
// // For testing registerBridgeRouter idempotency:
// // Use vi.resetModules() between tests to reset isBridgeRouterRegistered state
// ```
