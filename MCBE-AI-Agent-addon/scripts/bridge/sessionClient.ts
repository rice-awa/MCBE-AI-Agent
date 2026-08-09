import { system, world } from "@minecraft/server";

import {
  COMMAND_LINE_BYTE_BUDGET,
  SESSION_REQUEST_CHAT_PREFIX,
  SESSION_RESPONSE_SCRIPT_EVENT_ID,
  SESSION_SCHEMA_VERSION,
  TRUSTED_BRIDGE_PLAYER_NAME,
} from "./protocol";
import { utf8ByteLength } from "./chunking";

export const SESSION_TIMEOUT_TICKS = 100;

export type SessionAction =
  | "new"
  | "switch"
  | "list"
  | "status"
  | "clear"
  | "save"
  | "restore"
  | "saved"
  | "delete"
  | "compress";

export type SessionRequestParams = {
  cid?: string;
  sid?: string;
  player_name?: string;
};

export type SessionError = {
  code: string;
  message: string;
};

export type SessionResp = {
  request_id: string;
  v: number;
  ok: boolean;
  action: SessionAction | string;
  data?: Record<string, unknown>;
  error?: SessionError;
};

export type SessionConversationInfo = {
  id: string;
  short_id: number;
  title: string;
  message_count: number;
  is_active: boolean;
};

export type SessionSavedInfo = {
  session_id: string;
  title: string;
  message_count: number;
  updated_at: string;
};

type PendingRequest = {
  resolve: (response: SessionResp) => void;
  timer: number;
  action: SessionAction;
};

const SESSION_ACTIONS: readonly SessionAction[] = [
  "new",
  "switch",
  "list",
  "status",
  "clear",
  "save",
  "restore",
  "saved",
  "delete",
  "compress",
];
const pendingRequests = new Map<string, PendingRequest>();
let isSessionRespRegistered = false;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function isSessionAction(value: unknown): value is SessionAction {
  return typeof value === "string" && SESSION_ACTIONS.includes(value as SessionAction);
}

function failure(requestId: string, action: SessionAction | string, code: string, message: string): SessionResp {
  return {
    request_id: requestId,
    v: SESSION_SCHEMA_VERSION,
    ok: false,
    action,
    error: { code, message },
  };
}

function formatResponseError(error: SessionError | undefined): string {
  return error ? `${error.code}: ${error.message}` : "未知错误";
}

export { formatResponseError };

export function parseSessionResponse(value: unknown): SessionResp | null {
  if (!isRecord(value)) return null;
  if (
    typeof value.request_id !== "string" ||
    !value.request_id.trim() ||
    value.v !== SESSION_SCHEMA_VERSION ||
    typeof value.ok !== "boolean" ||
    !isSessionAction(value.action)
  ) {
    return null;
  }
  if (value.data !== undefined && !isRecord(value.data)) return null;
  let error: SessionError | undefined;
  if (value.error !== undefined) {
    if (!isRecord(value.error) || typeof value.error.code !== "string" || typeof value.error.message !== "string") {
      return null;
    }
    error = { code: value.error.code, message: value.error.message };
  }
  if (value.ok && error !== undefined) return null;
  if (!value.ok && error === undefined) return null;
  return {
    request_id: value.request_id,
    v: SESSION_SCHEMA_VERSION,
    ok: value.ok,
    action: value.action,
    ...(value.data === undefined ? {} : { data: value.data }),
    ...(error === undefined ? {} : { error }),
  };
}

function settle(requestId: string, response: SessionResp): void {
  const pending = pendingRequests.get(requestId);
  if (!pending) return;
  system.clearRun(pending.timer);
  pendingRequests.delete(requestId);
  pending.resolve(response);
}

export function resetSessionClientForTests(): void {
  for (const [requestId, pending] of pendingRequests) {
    system.clearRun(pending.timer);
    pending.resolve(failure(requestId, pending.action, "SESSION_CANCELLED", "session request was cancelled"));
  }
  pendingRequests.clear();
  isSessionRespRegistered = false;
}

export function registerSessionRespHandler(): void {
  if (isSessionRespRegistered) return;
  isSessionRespRegistered = true;
  system.afterEvents.scriptEventReceive.subscribe((event) => {
    if (event.id !== SESSION_RESPONSE_SCRIPT_EVENT_ID) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(event.message);
    } catch {
      return;
    }
    const response = parseSessionResponse(parsed);
    if (!response) return;
    const pending = pendingRequests.get(response.request_id);
    if (!pending || pending.action !== response.action) return;
    settle(response.request_id, response);
  });
}

export function requestSession(action: string, params: SessionRequestParams = {}): Promise<SessionResp> {
  const requestId = `sess-${Date.now()}-${Math.floor(Math.random() * 0x10000).toString(16)}`;
  if (!isSessionAction(action)) {
    return Promise.resolve(failure(requestId, action, "INVALID_SESSION_REQUEST", "unsupported session action"));
  }
  const playerName = params.player_name?.trim() ?? "";
  if (!playerName) {
    return Promise.resolve(failure(requestId, action, "INVALID_SESSION_REQUEST", "player_name is required"));
  }
  if (action === "switch" && !params.cid?.trim()) {
    return Promise.resolve(failure(requestId, action, "INVALID_SESSION_REQUEST", "switch requires cid"));
  }
  if ((action === "restore" || action === "delete") && !params.sid?.trim()) {
    return Promise.resolve(failure(requestId, action, "INVALID_SESSION_REQUEST", `${action} requires sid`));
  }

  const payloadObj: Record<string, unknown> = {
    request_id: requestId,
    v: SESSION_SCHEMA_VERSION,
    action,
    player_name: playerName,
    cid: params.cid?.trim() || "default",
    ...(params.sid?.trim() ? { sid: params.sid.trim() } : {}),
  };
  const payload = JSON.stringify(payloadObj);
  return new Promise<SessionResp>((resolve) => {
    const timer = system.runTimeout(() => {
      settle(requestId, failure(requestId, action, "SESSION_TIMEOUT", "session request timed out"));
    }, SESSION_TIMEOUT_TICKS);
    pendingRequests.set(requestId, { resolve, timer, action });
    void sendSessionRequest(payload).catch((error: unknown) => {
      settle(
        requestId,
        failure(
          requestId,
          action,
          "SESSION_SEND_FAILED",
          error instanceof Error ? error.message : "session request could not be sent"
        )
      );
    });
  });
}

/** Send one complete session request; it is never passed through bridge chunking. */
async function sendSessionRequest(payload: string): Promise<void> {
  const toolPlayer = world.getAllPlayers().find((player) => player.name === TRUSTED_BRIDGE_PLAYER_NAME);
  if (!toolPlayer) throw new Error("Tool player is not available");
  const command = `tell @s ${SESSION_REQUEST_CHAT_PREFIX}|${payload}`;
  if (utf8ByteLength(command) > COMMAND_LINE_BYTE_BUDGET) {
    throw new Error("session request exceeds atomic command budget");
  }
  await Promise.resolve(toolPlayer.runCommand(command));
}
