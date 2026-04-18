import { describe, expect, it } from "vitest";

import { shouldHandleScriptEvent } from "../../scripts/bridge/router";

describe("bridge router", () => {
  it("only accepts mcbeai bridge message ids", () => {
    expect(shouldHandleScriptEvent("mcbeai:bridge_request")).toBe(true);
    expect(shouldHandleScriptEvent("server:data")).toBe(false);
  });
});
