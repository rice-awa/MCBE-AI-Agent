import { describe, expect, it } from "vitest";

import { extractCommandEntrypoints, findDeniedCommand } from "../../scripts/bridge/capabilities/commandSafety";

describe("commandSafety", () => {
  it("extracts top-level and execute-run entry commands", () => {
    expect(extractCommandEntrypoints("execute as @a run stop")).toEqual(["execute", "stop"]);
  });

  it("detects denied command from execute run chain", () => {
    expect(findDeniedCommand("execute if entity @a run op Steve")).toBe("op");
  });

  it("returns undefined for allowed command", () => {
    expect(findDeniedCommand("say hello")).toBeUndefined();
  });
});
