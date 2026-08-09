import type { ScriptEventCommandMessageAfterEvent } from "@minecraft/server";
import { system } from "@minecraft/server";

import { CAPABILITY_REQUEST_SCRIPT_EVENT_ID } from "./protocol";
import {
  capabilityRegistry,
  defaultCapabilityRegistry,
  type CapabilityContext,
  type CapabilityHandler,
} from "./capabilities/registry";

export type BridgeErrorCode =
  | "MALFORMED_JSON"
  | "INVALID_REQUEST"
  | "UNSUPPORTED_VERSION"
  | "UNSUPPORTED_CAPABILITY"
  | "CAPABILITY_FAILED"
  | "RESPONSE_SEND_FAILED"
  | "BRIDGE_NOT_READY_QUEUE_FULL";

export type BridgeErrorResponse = {
  ok: false;
  error: { code: BridgeErrorCode; message: string };
};

export type BridgeRequest = {
  v: 1 | 2;
  request_id: string;
  capability: string;
  payload: Record<string, unknown>;
};

export type ResponseSender = (requestId: string, jsonBody: string) => Promise<void> | void;
export type RouterEvent = Pick<ScriptEventCommandMessageAfterEvent, "id" | "message" | "sourceType"> &
  Partial<Pick<ScriptEventCommandMessageAfterEvent, "sourceEntity" | "sourceBlock">>;
export type ParseResult =
  | { ok: true; request: BridgeRequest }
  | { ok: false; requestId?: string; response: BridgeErrorResponse };

export { CAPABILITY_REQUEST_SCRIPT_EVENT_ID as BRIDGE_REQUEST_MESSAGE_ID };
/** Deprecated compatibility alias; internals use CAPABILITY_REQUEST_SCRIPT_EVENT_ID. */
export const BRIDGE_MESSAGE_ID = CAPABILITY_REQUEST_SCRIPT_EVENT_ID;
export { defaultCapabilityRegistry };
export type { CapabilityContext, CapabilityHandler };

let isBridgeRouterRegistered = false;
let capabilityHandler: CapabilityHandler | null = null;
let responseSender: ResponseSender | null = null;
let bridgeActive = false;
const preReadyQueue: RouterEvent[] = [];
let processingTail: Promise<void> = Promise.resolve();

export const MAX_PRE_READY_REQUESTS = 64;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function responseError(code: BridgeErrorCode, message: string): BridgeErrorResponse {
  return { ok: false, error: { code, message } };
}

function invalidRequest(requestId?: string): ParseResult {
  return { ok: false, requestId, response: responseError("INVALID_REQUEST", "invalid bridge request") };
}

function boundedRequestId(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() && value.length <= 128 ? value : undefined;
}

export function shouldHandleScriptEvent(messageId: string): boolean {
  return messageId === CAPABILITY_REQUEST_SCRIPT_EVENT_ID;
}

export function parseBridgeRequest(message: string): ParseResult {
  let value: unknown;
  try {
    value = JSON.parse(message);
  } catch {
    return { ok: false, response: responseError("MALFORMED_JSON", "invalid JSON") };
  }
  const requestId = isRecord(value) ? boundedRequestId(value.request_id) : undefined;
  if (!isRecord(value)) return invalidRequest(requestId);

  const rawVersion = value.v ?? 1;
  if (rawVersion !== 1 && rawVersion !== 2) {
    return {
      ok: false,
      requestId,
      response: responseError("UNSUPPORTED_VERSION", "unsupported bridge version"),
    };
  }
  if (!requestId || typeof value.capability !== "string" || !value.capability.trim()) {
    return invalidRequest(requestId);
  }
  if (value.payload !== undefined && !isRecord(value.payload)) {
    return invalidRequest(requestId);
  }
  return {
    ok: true,
    request: {
      v: rawVersion,
      request_id: requestId,
      capability: value.capability.trim(),
      payload: value.payload ?? {},
    },
  };
}

function logRouterError(message: string): void {
  console.error(`[bridge] ${message}`);
}

async function sendResponse(requestId: string, response: unknown): Promise<void> {
  let body: string;
  try {
    const encoded = JSON.stringify(response);
    if (typeof encoded !== "string") throw new Error("unserializable_response");
    body = encoded;
  } catch {
    logRouterError(`code=RESPONSE_SEND_FAILED requestId=${requestId} reason=unserializable_response`);
    return;
  }
  try {
    if (responseSender) {
      await responseSender(requestId, body);
    } else {
      // Product Addon bootstrap uses the ToolPlayer fallback; SDK/reference
      // integrations inject an explicit delivery sender through activateBridge.
      const { sendBridgeResponseChunks } = await import("./toolPlayer");
      await sendBridgeResponseChunks(requestId, body);
    }
  } catch (error) {
    logRouterError(
      `code=RESPONSE_SEND_FAILED requestId=${requestId} reason=${error instanceof Error ? error.constructor.name : "send_failed"}`
    );
  }
}

function schedule(event: RouterEvent): void {
  processingTail = processingTail
    .then(() => handleBridgeScriptEvent(event))
    .catch((error: unknown) => {
      logRouterError(`code=CAPABILITY_FAILED reason=${error instanceof Error ? error.message : "router_failure"}`);
    });
}

export function enqueueOrHandle(event: ScriptEventCommandAfterEvent): void {
  if (!shouldHandleScriptEvent(event.id)) return;
  const snapshot: RouterEvent = {
    id: event.id,
    message: event.message,
    sourceType: event.sourceType,
    sourceEntity: event.sourceEntity,
    sourceBlock: event.sourceBlock,
  };
  if (!bridgeActive) {
    if (preReadyQueue.length >= MAX_PRE_READY_REQUESTS) {
      logRouterError(`code=BRIDGE_NOT_READY_QUEUE_FULL queueSize=${preReadyQueue.length}`);
      return;
    }
    preReadyQueue.push(snapshot);
    return;
  }
  schedule(snapshot);
}

export async function activateBridge(sender: ResponseSender): Promise<void> {
  responseSender = sender;
  bridgeActive = true;
  while (preReadyQueue.length > 0) {
    const event = preReadyQueue.shift();
    if (event) schedule(event);
  }
  await processingTail;
}

export function setCapabilityHandler(handler: CapabilityHandler | null): void {
  capabilityHandler = handler;
}

export async function handleBridgeScriptEvent(event: RouterEvent): Promise<void> {
  const parsed = parseBridgeRequest(event.message);
  if (!parsed.ok) {
    if (parsed.requestId) await sendResponse(parsed.requestId, parsed.response);
    return;
  }

  const request = parsed.request;
  const context: CapabilityContext = {
    caller: { kind: "server" },
    requestVersion: request.v,
    event,
  };
  const registration = capabilityRegistry[request.capability];
  if (!capabilityHandler && !registration) {
    await sendResponse(
      request.request_id,
      responseError("UNSUPPORTED_CAPABILITY", `unsupported capability: ${request.capability}`)
    );
    return;
  }

  let result: Record<string, unknown>;
  try {
    result = capabilityHandler
      ? await capabilityHandler(request.capability, request.payload, context)
      : await registration!.handler(request.capability, request.payload, context);
    if (!isRecord(result)) throw new Error("capability returned invalid response");
  } catch {
    result = responseError("CAPABILITY_FAILED", "capability handler failed");
  }
  await sendResponse(request.request_id, result);
}

export function registerBridgeRouter(): void {
  if (isBridgeRouterRegistered) return;
  isBridgeRouterRegistered = true;
  system.afterEvents.scriptEventReceive.subscribe((event) => {
    enqueueOrHandle(event);
  });
}

/** @internal */
export function _testingGetQueueSize(): number {
  return preReadyQueue.length;
}

/** @internal */
export function _testingFlush(): Promise<void> {
  return processingTail;
}

/** @internal */
export function _testingReset(): void {
  preReadyQueue.length = 0;
  responseSender = null;
  capabilityHandler = null;
  bridgeActive = false;
  isBridgeRouterRegistered = false;
  processingTail = Promise.resolve();
}

type ScriptEventCommandAfterEvent = ScriptEventCommandMessageAfterEvent;
