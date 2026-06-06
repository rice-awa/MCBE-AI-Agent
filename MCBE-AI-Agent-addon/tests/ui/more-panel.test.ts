import { beforeEach, describe, expect, it } from "vitest";

import {
  __getLastCustomForm,
  __resetDduiMock,
  __setNextCustomFormInteraction,
} from "@minecraft/server-ui";

import { showMorePanel } from "../../scripts/ui/panels/morePanel";
import { CLOSE_ROUTE } from "../../scripts/ui/panels/routes";
import { createAgentUiState } from "../../scripts/ui/state";

describe("more panel", () => {
  beforeEach(() => {
    __resetDduiMock();
  });

  it("offers settings and stats as secondary actions", async () => {
    await showMorePanel(createFakePlayer(), createAgentUiState());

    const buttons = __getLastCustomForm()
      ?.getComponents()
      .filter((component) => component.startsWith("button:"));

    expect(buttons).toEqual(["button:设置", "button:统计信息"]);
  });

  it("routes to settings from the more menu", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "设置",
      autoCloseAfterButtonClick: true,
    });

    const route = await showMorePanel(createFakePlayer(), createAgentUiState());

    expect(route).toEqual({ panel: "settings" });
  });

  it("routes to stats from the more menu", async () => {
    __setNextCustomFormInteraction({
      clickButtonLabel: "统计信息",
      autoCloseAfterButtonClick: true,
    });

    const route = await showMorePanel(createFakePlayer(), createAgentUiState());

    expect(route).toEqual({ panel: "stats" });
  });

  it("closes when the player dismisses the more menu", async () => {
    __setNextCustomFormInteraction({
      closeReason: "UserClose",
    });

    const route = await showMorePanel(createFakePlayer(), createAgentUiState());

    expect(route).toEqual(CLOSE_ROUTE);
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
