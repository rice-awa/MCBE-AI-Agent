export const BRIDGE_RESPONSE_PREFIX = "MCBEAI|RESP";
export const BRIDGE_UI_CHAT_PREFIX = "MCBEAI|UI_CHAT";
export const BRIDGE_MESSAGE_ID = "mcbeai:bridge_request";
export const TOOL_PLAYER_NAME = "MCBEAI_TOOL";

/**
 * Addon -> Python（UI 聊天上行）分片长度上限。
 * Python -> Addon（AI 响应下行）由 services/websocket/flow_control.py 统一管理，
 * 默认阈值 400 字符，与 Python 侧 AI_RESP_MAX_CHUNK_LENGTH 对齐。
 */
export const BRIDGE_MAX_CHUNK_CONTENT_LENGTH = 256;
export const AI_RESP_MESSAGE_ID = "mcbeai:ai_resp";
