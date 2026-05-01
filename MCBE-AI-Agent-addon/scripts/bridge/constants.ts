export const BRIDGE_RESPONSE_PREFIX = "MCBEAI|RESP";
export const BRIDGE_UI_CHAT_PREFIX = "MCBEAI|UI_CHAT";
export const BRIDGE_MESSAGE_ID = "mcbeai:bridge_request";
export const TOOL_PLAYER_NAME = "MCBEAI_TOOL";

/**
 * Addon -> Python（UI 聊天上行）单分片字符上限。
 *
 * 注意：上下行阈值不同：
 *   - 上行 (Addon -> Python): 256 字符（受 say/tellraw 命令包装开销限制）
 *   - 下行 (Python -> Addon): 400 字符（由 services/websocket/flow_control.py
 *     中的 FlowControlMiddleware.DEFAULT_MAX_CONTENT_LENGTH 控制，
 *     可通过 .env 的 MAX_CHUNK_CONTENT_LENGTH 调整）
 */
export const BRIDGE_MAX_CHUNK_CONTENT_LENGTH = 256;
export const AI_RESP_MESSAGE_ID = "mcbeai:ai_resp";
