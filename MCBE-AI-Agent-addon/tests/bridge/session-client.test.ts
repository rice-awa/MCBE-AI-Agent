import { beforeEach, describe, expect, it, vi } from "vitest";
import { __emitScriptEvent, __resetMinecraftServerMock, __setMockPlayers } from "@minecraft/server";

import { SESSION_RESP_MESSAGE_ID, TOOL_PLAYER_NAME } from "../../scripts/bridge/constants";
import {
  registerSessionRespHandler,
  requestSession,
  resetSessionClientForTests,
} from "../../scripts/bridge/sessionClient";

describe("session client", () => {
  beforeEach(() => {
    __resetMinecraftServerMock();
    resetSessionClientForTests();
  });

  it("correlates a typed response and sends one atomic request", async () => {
    const runCommand = vi.fn(() => ({ successCount: 1 }));
    __setMockPlayers([{ name: TOOL_PLAYER_NAME, runCommand }]);
    registerSessionRespHandler();

    const pending = requestSession("list", { player_name: "Alice", cid: "chat-a" });
    const request = JSON.parse(String(runCommand.mock.calls[0][0]).split("|").slice(2).join("|"));
    expect(request).toMatchObject({ v: 1, action: "list", player_name: "Alice", cid: "chat-a" });

    __emitScriptEvent({
      id: SESSION_RESP_MESSAGE_ID,
      message: JSON.stringify({
        v: 1,
        request_id: request.request_id,
        action: "list",
        ok: true,
        data: { conversations: [] },
      }),
    });

    await expect(pending).resolves.toMatchObject({ ok: true, request_id: request.request_id });
  });

  it("returns an immediate structured send failure when ToolPlayer is missing or throws", async () => {
    __setMockPlayers([]);
    const missing = await requestSession("status", { player_name: "Alice" });
    expect(missing).toMatchObject({ ok: false, error: { code: "SESSION_SEND_FAILED" } });

    const runCommand = vi.fn(() => {
      throw new Error("not available");
    });
    __setMockPlayers([{ name: TOOL_PLAYER_NAME, runCommand }]);
    const thrown = await requestSession("status", { player_name: "Alice" });
    expect(thrown).toMatchObject({ ok: false, error: { code: "SESSION_SEND_FAILED" } });
  });

  it("validates action and response shape/correlation", async () => {
    const runCommand = vi.fn(() => ({ successCount: 1 }));
    __setMockPlayers([{ name: TOOL_PLAYER_NAME, runCommand }]);
    registerSessionRespHandler();

    await expect(requestSession("not-an-action", { player_name: "Alice" })).resolves.toMatchObject({
      ok: false,
      error: { code: "INVALID_SESSION_REQUEST" },
    });

    const pending = requestSession("status", { player_name: "Alice" });
    const requestId = JSON.parse(String(runCommand.mock.calls[0][0]).split("|").slice(2).join("|")).request_id;
    __emitScriptEvent({
      id: SESSION_RESP_MESSAGE_ID,
      message: JSON.stringify({ v: 2, request_id: requestId, action: "status", ok: true }),
    });
    // A malformed/mismatched version is ignored; the request remains pending.
    expect(runCommand).toHaveBeenCalledTimes(1);
    resetSessionClientForTests();
    await expect(pending).resolves.toMatchObject({ ok: false, error: { code: "SESSION_CANCELLED" } });
  });

  it("surfaces the server atomic oversize error without parsing fragments", async () => {
    const runCommand = vi.fn(() => ({ successCount: 1 }));
    __setMockPlayers([{ name: TOOL_PLAYER_NAME, runCommand }]);
    registerSessionRespHandler();
    const pending = requestSession("list", { player_name: "Alice" });
    const requestId = JSON.parse(String(runCommand.mock.calls[0][0]).split("|").slice(2).join("|")).request_id;
    __emitScriptEvent({
      id: SESSION_RESP_MESSAGE_ID,
      message: JSON.stringify({
        v: 1,
        request_id: requestId,
        action: "list",
        ok: false,
        error: { code: "SESSION_RESPONSE_TOO_LARGE", message: "response too large" },
      }),
    });
    await expect(pending).resolves.toMatchObject({
      ok: false,
      error: { code: "SESSION_RESPONSE_TOO_LARGE" },
    });
  });
});
