export const BRIDGE_RESPONSE_PREFIX = "MCBEWS|BRIDGE";
export const BRIDGE_UI_CHAT_PREFIX = "MCBEWS|UI_CHAT";
/** Wire message id for bridge requests (mcbews v1). */
export const BRIDGE_REQUEST_MESSAGE_ID = "mcbews:bridge_req";
/** @deprecated Use BRIDGE_REQUEST_MESSAGE_ID — kept as alias for host-side imports. */
export const BRIDGE_MESSAGE_ID = BRIDGE_REQUEST_MESSAGE_ID;
export const BRIDGE_SENDER = "MCBEWS_BRIDGE";
/** Simulated tool player that speaks bridge responses. */
export const TOOL_PLAYER_NAME = BRIDGE_SENDER;

/**
 * Addon -> Python（UI 聊天上行）单分片字符上限。
 *
 * 注意：上下行阈值不同：
 *   - 上行 (Addon -> Python): 256 字符（受 say/tellraw 命令包装开销限制）
 *   - 下行 (Python -> Addon): 400 字符（由 SDK FlowControlSettings 控制）
 */
export const BRIDGE_MAX_CHUNK_CONTENT_LENGTH = 256;

/** MCBE commandLine 实测安全字节上限 */
export const BRIDGE_COMMAND_LINE_BYTE_BUDGET = 461;

/** 单分片内容 code-point 上限（字符数，非字节数） */
export const BRIDGE_MAX_CHUNK_CONTENT_CODE_POINTS = 256;

/** Downstream AI / text response scriptevent id (mcbews v1). */
export const TEXT_RESP_MESSAGE_ID = "mcbews:text_resp";
/** @deprecated Use TEXT_RESP_MESSAGE_ID */
export const AI_RESP_MESSAGE_ID = TEXT_RESP_MESSAGE_ID;
