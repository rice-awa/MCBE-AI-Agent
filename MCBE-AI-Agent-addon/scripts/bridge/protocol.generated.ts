/** Generated MCBEWS/1 projection; run pnpm protocol:sync to refresh. */

export const MCBEWS_PROTOCOL_LINE = "MCBEWS/1" as const;
export const CAPABILITY_REQUEST_SCRIPT_EVENT_ID = "mcbews:bridge_req" as const;
export const CAPABILITY_RESPONSE_CHAT_PREFIX = "MCBEWS|BRIDGE" as const;
export const UI_CHAT_CHUNK_PREFIX = "MCBEWS|UI_CHAT" as const;
export const SESSION_REQUEST_CHAT_PREFIX = "MCBEWS|SESSION" as const;
export const SESSION_REQUEST_SCRIPT_EVENT_ID = "mcbews:session_req" as const;
export const SESSION_RESPONSE_SCRIPT_EVENT_ID = "mcbews:session_resp" as const;
export const TEXT_RESPONSE_SCRIPT_EVENT_ID = "mcbews:text_resp" as const;
export const APPROVAL_ALLOW_CHAT_PREFIX = "MCBEWS|TOOL_APPROVE" as const;
export const APPROVAL_DENY_CHAT_PREFIX = "MCBEWS|TOOL_DENY" as const;
export const TRUSTED_BRIDGE_PLAYER_NAME = "MCBEWS_BRIDGE" as const;
export const CAPABILITY_REQUEST_SCHEMA_VERSION = 2 as const;
export const SESSION_SCHEMA_VERSION = 1 as const;
export const TEXT_RESPONSE_FRAMING_VERSION = 1 as const;
export const DDUI_PERSISTENCE_VERSION = 2 as const;
export const COMMAND_LINE_BYTE_BUDGET = 461 as const;
export const COMMAND_LINE_BUDGET_SOURCE = "empirical" as const;
export const UPSTREAM_MAX_CONTENT_CODE_POINTS = 256 as const;
export const RESPONSE_MAX_BUFFERS = 64 as const;
export const RESPONSE_MAX_CHUNKS_PER_MESSAGE = 128 as const;
export const RESPONSE_MAX_MESSAGE_BYTES = 65536 as const;
export const RESPONSE_MAX_TOTAL_BUFFER_BYTES = 262144 as const;
export const RESPONSE_BUFFER_TTL_MS = 30000 as const;
export const SESSION_RESPONSE_MAX_COMMAND_BYTES = 461 as const;
export const TEXT_RESPONSE_ALLOWED_ROLES = ["user", "assistant", "approval"] as const;
export const TEXT_RESPONSE_USAGE_FIELD = "u" as const;
export const TEXT_RESPONSE_USAGE_COMPLETION_ONLY = true as const;
export const TEXT_RESPONSE_CONVERSATION_ID_FIELD = "cid" as const;
export const TEXT_RESPONSE_TITLE_FIELD = "t" as const;

export const MCBEWS_V1_ERROR_CODES = [
  "MALFORMED_JSON",
  "INVALID_REQUEST",
  "UNSUPPORTED_VERSION",
  "UNSUPPORTED_CAPABILITY",
  "CAPABILITY_FAILED",
  "RESPONSE_SEND_FAILED",
  "BRIDGE_NOT_READY_QUEUE_FULL",
  "SESSION_RESPONSE_TOO_LARGE",
  "INVALID_SESSION_REQUEST",
  "INVALID_APPROVAL_DECISION",
  "UNKNOWN_TEXT_ROLE",
] as const;

export const MCBEWS_V1_MANIFEST = {
  protocolLine: "MCBEWS/1",
  wire: {
    capabilityRequestScriptEventId: "mcbews:bridge_req",
    capabilityResponseChatPrefix: "MCBEWS|BRIDGE",
    uiChatChunkPrefix: "MCBEWS|UI_CHAT",
    sessionRequestChatPrefix: "MCBEWS|SESSION",
    sessionRequestScriptEventId: "mcbews:session_req",
    sessionResponseScriptEventId: "mcbews:session_resp",
    textResponseScriptEventId: "mcbews:text_resp",
    approvalAllowChatPrefix: "MCBEWS|TOOL_APPROVE",
    approvalDenyChatPrefix: "MCBEWS|TOOL_DENY",
    trustedBridgePlayerName: "MCBEWS_BRIDGE",
  },
  versions: {
    capabilityRequestSchema: 2,
    sessionSchema: 1,
    textResponseFraming: 1,
    dduiPersistence: 2,
  },
  limits: {
    commandLineByteBudget: 461,
    commandLineBudgetSource: "empirical",
    upstreamMaxContentCodePoints: 256,
    responseMaxBuffers: 64,
    responseMaxChunksPerMessage: 128,
    responseMaxMessageBytes: 65536,
    responseMaxTotalBufferBytes: 262144,
    responseBufferTtlMs: 30000,
    sessionResponseMaxCommandBytes: 461,
  },
  textResponse: {
    allowedRoles: ["user", "assistant", "approval"],
    usageField: "u",
    usageCompletionOnly: true,
    conversationIdField: "cid",
    titleField: "t",
  },
  errorCodes: [
    "MALFORMED_JSON",
    "INVALID_REQUEST",
    "UNSUPPORTED_VERSION",
    "UNSUPPORTED_CAPABILITY",
    "CAPABILITY_FAILED",
    "RESPONSE_SEND_FAILED",
    "BRIDGE_NOT_READY_QUEUE_FULL",
    "SESSION_RESPONSE_TOO_LARGE",
    "INVALID_SESSION_REQUEST",
    "INVALID_APPROVAL_DECISION",
    "UNKNOWN_TEXT_ROLE",
  ],
} as const;

export const MCBEWS_V1_WIRE_VECTORS = {
  bridgeRequests: [
    {
      name: "legacy-v1-without-version",
      version: 1,
      message: '{"request_id":"r-1","capability":"greet","payload":{"name":"Steve"}}',
    },
    {
      name: "current-v2",
      version: 2,
      message: '{"v":2,"request_id":"r-1","capability":"greet","payload":{"name":"Steve"}}',
    },
  ],
  uiChat: [
    {
      name: "ui-chat-cid",
      message: 'MCBEWS|UI_CHAT|ui-1|1/1|{"player":"Steve","message":"你好 😀","cid":"chat-a"}',
      playerName: "Steve",
      conversationId: "chat-a",
      text: "你好 😀",
    },
  ],
  textResponse: [
    {
      name: "assistant-final-usage",
      frame: {
        id: "resp-1",
        i: 1,
        n: 1,
        p: "Steve",
        r: "assistant",
        c: "ok",
        cid: "chat-a",
        t: "Chat",
        u: {
          i: 3,
          o: 5,
        },
      },
    },
    {
      name: "approval-role",
      frame: {
        id: "approval-1",
        i: 1,
        n: 1,
        p: "Steve",
        r: "approval",
        c: '{"approval_id":"ap-1"}',
        cid: "chat-a",
      },
    },
  ],
  session: [
    {
      name: "session-list",
      request: '{"request_id":"sess-1","v":1,"action":"list","player_name":"Steve","cid":"chat-a"}',
    },
  ],
  approval: [
    {
      name: "approval-allow",
      message: 'MCBEWS|TOOL_APPROVE|{"v":1,"approval_id":"ap-1","player_name":"Steve","cid":"chat-a"}',
      decision: "approve",
    },
    {
      name: "approval-legacy-id",
      message: "MCBEWS|TOOL_DENY|ap-1",
      decision: "deny",
      legacy: true,
    },
  ],
  behavior: {
    chunking: [
      {
        name: "unicode-bounded",
        prefix: "MCBEWS|BRIDGE",
        id: "unicode-1",
        payload: "中文😀中文😀中文😀中文😀中文😀中文😀中文😀中文😀",
        budget: 80,
        maxContentCodePoints: 256,
        wrapperPrefix: "tell @s ",
      },
      {
        name: "empty-payload",
        prefix: "MCBEWS|BRIDGE",
        id: "empty-1",
        payload: "",
        budget: 461,
        maxContentCodePoints: 256,
        wrapperPrefix: "tell @s ",
      },
      {
        name: "empty-wrapper-no-room",
        prefix: "MCBEWS|BRIDGE",
        id: "empty-2",
        payload: "",
        budget: 20,
        maxContentCodePoints: 256,
        wrapperPrefix: "tell @s wrapper-that-does-not-fit ",
      },
    ],
    textResponse: [
      {
        name: "final-first-identical-duplicate",
        chunks: [
          {
            id: "dup-1",
            i: 3,
            n: 3,
            p: "Steve",
            r: "assistant",
            c: "done",
            cid: "chat-a",
            u: {
              i: 1,
              o: 2,
            },
          },
          {
            id: "dup-1",
            i: 1,
            n: 3,
            p: "Steve",
            r: "assistant",
            c: "answer ",
            cid: "chat-a",
          },
          {
            id: "dup-1",
            i: 3,
            n: 3,
            p: "Steve",
            r: "assistant",
            c: "done",
            cid: "chat-a",
            u: {
              i: 1,
              o: 2,
            },
          },
          {
            id: "dup-1",
            i: 2,
            n: 3,
            p: "Steve",
            r: "assistant",
            c: "",
            cid: "chat-a",
          },
        ],
        expected: {
          playerName: "Steve",
          role: "assistant",
          text: "answer done",
          responseId: "dup-1",
          conversationId: "chat-a",
          usage: {
            i: 1,
            o: 2,
          },
        },
      },
      {
        name: "metadata-conflict",
        chunks: [
          {
            id: "meta-1",
            i: 1,
            n: 2,
            p: "Steve",
            r: "assistant",
            c: "a",
            cid: "chat-a",
            t: "one",
          },
          {
            id: "meta-1",
            i: 2,
            n: 2,
            p: "Steve",
            r: "assistant",
            c: "b",
            cid: "chat-a",
            t: "two",
          },
        ],
        expectedError: "metadata",
      },
      {
        name: "duplicate-content-conflict",
        chunks: [
          {
            id: "dup-2",
            i: 1,
            n: 2,
            p: "Steve",
            r: "assistant",
            c: "a",
          },
          {
            id: "dup-2",
            i: 1,
            n: 2,
            p: "Steve",
            r: "assistant",
            c: "A",
          },
        ],
        expectedError: "duplicate",
      },
    ],
  },
} as const;
