import { system, world } from "@minecraft/server";

import {
  SESSION_REQ_PREFIX,
  SESSION_RESP_MESSAGE_ID,
  TOOL_PLAYER_NAME,
} from "./constants";

// ── Types ──

export type SessionResp = {
  request_id: string;
  v: number;
  ok: boolean;
  action: string;
  data?: Record<string, unknown>;
  error?: string;
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
  resolve: (resp: SessionResp) => void;
  timer: number;
};

// ── State ──

const pendingRequests = new Map<string, PendingRequest>();
/** 5 seconds ≈ 100 ticks (1 tick = 50ms) */
const SESSION_TIMEOUT_TICKS = 100;
let isSessionRespRegistered = false;

/**
 * Reset internal state for tests.
 */
export function resetSessionClientForTests(): void {
  for (const [, pending] of pendingRequests) {
    system.clearRun(pending.timer);
  }
  pendingRequests.clear();
  isSessionRespRegistered = false;
}

/**
 * Register the mcbews:session_resp scriptevent listener.
 * Idempotent — safe to call multiple times.
 */
export function registerSessionRespHandler(): void {
  if (isSessionRespRegistered) {
    return;
  }
  isSessionRespRegistered = true;

  system.afterEvents.scriptEventReceive.subscribe((event) => {
    if (event.id !== SESSION_RESP_MESSAGE_ID) {
      return;
    }

    try {
      const resp = JSON.parse(event.message) as SessionResp;
      const pending = pendingRequests.get(resp.request_id);
      if (!pending) {
        return;
      }
      system.clearRun(pending.timer);
      pendingRequests.delete(resp.request_id);
      pending.resolve(resp);
    } catch {
      // Ignore parse errors
    }
  });
}

/**
 * Send a session request to Python and wait for the response.
 *
 * @param action - Session action (new, switch, list, status, clear, save, restore, saved, delete, compress)
 * @param params - Optional parameters (cid?, sid?, player_name?)
 * @returns Promise that resolves with the session response or a timeout error
 */
export function requestSession(
  action: string,
  params?: { cid?: string; sid?: string; player_name?: string },
): Promise<SessionResp> {
  return new Promise<SessionResp>((resolve) => {
    const requestId = `sess-${Date.now()}-${Math.floor(Math.random() * 0x10000).toString(16)}`;
    const playerName = params?.player_name ?? "";

    // Build the JSON payload
    const payloadObj: Record<string, unknown> = {
      request_id: requestId,
      v: 1,
      action,
      player_name: playerName,
    };
    if (params?.cid) {
      payloadObj.cid = params.cid;
    }
    if (params?.sid) {
      payloadObj.sid = params.sid;
    }
    const payload = JSON.stringify(payloadObj);

    // Set up the pending request (timeout guard)
    // Minecraft scripting sandbox does not support setTimeout — use system.runTimeout.
    const timer = system.runTimeout(() => {
      pendingRequests.delete(requestId);
      resolve({
        request_id: requestId,
        v: 1,
        ok: false,
        action,
        error: "会话同步不可用（服务端版本过旧）",
      });
    }, SESSION_TIMEOUT_TICKS);

    pendingRequests.set(requestId, { resolve, timer });

    // Send via tool player's runCommand
    sendSessionRequest(requestId, payload);
  });
}

/**
 * Send the session request as tell chat from the tool player.
 * Session payloads are small (well under 256 chars) so no chunking is needed.
 */
function sendSessionRequest(requestId: string, payload: string): void {
  const toolPlayer = world
    .getAllPlayers()
    .find((player) => player.name === TOOL_PLAYER_NAME);

  if (!toolPlayer) {
    // No tool player available — resolve with error
    const pending = pendingRequests.get(requestId);
    if (pending) {
      system.clearRun(pending.timer);
      pendingRequests.delete(requestId);
      pending.resolve({
        request_id: requestId,
        v: 1,
        ok: false,
        action: "",
        error: "Tool player is not available",
      });
    }
    return;
  }

  toolPlayer.runCommand(`tell @s ${SESSION_REQ_PREFIX}|${payload}`);
}
