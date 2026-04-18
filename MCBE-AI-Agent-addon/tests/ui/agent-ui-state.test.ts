import { describe, expect, it } from "vitest";

import { createAgentUiState } from "../../scripts/ui/state";

describe("agent ui state", () => {
  it("starts with bridge disconnected state", () => {
    const state = createAgentUiState();

    expect(state.bridgeStatus.getData()).toBe("disconnected");
    expect(state.lastPrompt.getData()).toBe("");
    expect(state.lastResponsePreview.getData()).toBe("");
  });
});
