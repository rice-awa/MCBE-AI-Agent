# Addon Bridge 与协议

## 权威协议

运行时只使用 mcbews v1：

| 方向/用途 | 值 |
|---|---|
| Python → Addon bridge 请求 | `mcbews:bridge_req` |
| Python → Addon UI/AI 文本响应 | `mcbews:text_resp` |
| Addon → Python bridge 回传前缀 | `MCBEWS|BRIDGE` |
| Addon → Python UI 聊天前缀 | `MCBEWS|UI_CHAT` |
| Addon → Python 会话请求前缀 | `MCBEWS|SESSION_REQ` |
| Python → Addon 会话响应 | `mcbews:session_resp` |

这些值的代码源是 [`scripts/bridge/constants.ts`](../../../MCBE-AI-Agent-addon/scripts/bridge/constants.ts)，协议说明是 [`docs/addon-bridge-protocol.md`](../../../docs/addon-bridge-protocol.md)。不要把 `mcbeai:*` 或 `MCBEAI|*` 当作运行时兼容分支。`mcbeai:ui_state` DynamicProperty 键是旧世界状态兼容保留值，不属于线协议。

## 请求路由

[`scripts/bridge/router.ts`](../../../MCBE-AI-Agent-addon/scripts/bridge/router.ts) 采用以下边界：

1. `shouldHandleScriptEvent()` 先精确匹配 message id，非 bridge 事件直接忽略。
2. `handleBridgeScriptEvent()` 解析 request id、capability 和 payload，并通过集中 capability map 找到 handler。
3. handler 返回 `{ ok: true, payload }` 或 `{ ok: false, payload: { error } }` 形状，再交给 `sendBridgeResponseChunks()`。
4. 未知 capability 返回明确失败，不应静默当作成功。

新增能力要提供 capability 名称、payload 类型边界、成功/失败响应和 router 测试；不要在每个能力模块中自行发送 scriptevent 或实现响应分片。

## 分片与重组

`chunking.ts` 的 `chunkPayload(prefix, id, payload, maxChunkContentLength)` 负责按字符长度生成 `prefix|id|index/total|content`，最大长度必须大于 0，空 payload 仍产生一个 `1/1` 分片。修改分片格式时必须同步 Python 协议文档、SDK 适配和测试。

`responseSync.ts` 处理 `mcbews:text_resp` JSON 分片：检查 id、index、total 的合法性，按消息 id 缓冲，收到足量分片后按 index 排序拼接，再按 `player_name` 查找目标玩家并更新对应 UI。不要使用到达顺序代替 index，也不要把一个玩家的响应写入全局当前 panel。

Python 出站长文本的分片由 SDK 的 `McbewsV1Delivery` / `McbeOutboundDelivery` 拥有；Addon 侧只维护自己的 bridge response/UI response 接收与回传格式，不能复制 Python 的 command line budget 逻辑。

参考测试：[`tests/bridge/router.test.ts`](../../../MCBE-AI-Agent-addon/tests/bridge/router.test.ts)、[`tests/bridge/chunking.test.ts`](../../../MCBE-AI-Agent-addon/tests/bridge/chunking.test.ts)、[`tests/bridge/getCapabilities.test.ts`](../../../MCBE-AI-Agent-addon/tests/bridge/getCapabilities.test.ts)。

## 会话请求与响应

会话同步（会话列表、切换、保存、删除）使用独立的 `sessionClient` 协议，不走 bridge router：

1. Addon 通过 ToolPlayer 的 `tell @s MCBEWS|SESSION_REQ|request_id|json_payload` 发送请求。
2. Python 处理后返回 `mcbews:session_resp` 的 scriptevent，Addon 通过 `registerSessionRespHandler()` 订阅接收。
3. 请求使用 `chunkPayload()` 分片，超时默认 5 秒。

会话请求 payload 格式：

```json
{
  "request_id": "sess-<ts>-<random>",
  "v": 1,
  "action": "new" | "switch" | "list" | "status" | "clear" | "save" | "restore" | "saved" | "delete" | "compress",
  "player_name": "",
  "cid": "<conversation-id>",
  "sid": "<saved-session-id>"
}
```

会话响应格式：

```json
{
  "request_id": "...",
  "v": 1,
  "ok": true | false,
  "action": "...",
  "data": { ... },
  "error": "..."
}
```

超时响应：`ok: false, error: "会话同步不可用（服务端版本过旧）"`。

参考实现：[`scripts/bridge/sessionClient.ts`](../../../MCBE-AI-Agent-addon/scripts/bridge/sessionClient.ts)。Python 侧需要实现 `mcbews:session_resp` 的响应发射才能完整工作。

## 流式响应协议（mcbews:text_resp）

### 分片 payload 格式（含 v2 字段）

```json
{
  "id": "<message-id>",
  "i": 1,
  "n": 5,
  "p": "<player_name>",
  "r": "user" | "assistant",
  "c": "<chunk-content>",
  "cid": "<conversation-id>",
  "t": "<conversation-title>",
  "u": { "i": 0, "o": 50 }
}
```

- `id` / `i` / `n` — 消息 id 与分片序号（1-based），用于缓冲和排序
- `p` — 目标玩家名，`responseSync.ts` 通过 `world.getAllPlayers().find(p => p.name === playerName)` 查找
- `r` — 角色，决定是否写入 assistant 历史
- `c` — 分片文本内容，接收方按 `i` 排序拼接
- `cid` — （v2 新增）所属会话 id，缺省回退到 `"default"`
- `t` — （v2 新增）会话标题，面板用它显示标题
- `u` — （v2 新增）token 统计 `{ i: input, o: output }`，通常在最后一帧携带

### 流式渲染规则

1. 按 `id` 缓冲区累积分片，按 `i` 排序后拼接 `c`。
2. 首次分片到达时创建 `StreamingState`，设置 `isStreaming = true`、`streamingConversationId`。
3. 每收到一个分片，计算 delta 更新 `streamingChars`，更新 `lastResponsePreview` 带有 `▌` 光标。
4. 最后分片 `buffer.size === n` 时触发 `onMessageComplete`：写入历史、更新 token stats、重置 streaming 标志。
5. 如果流不完整（面板关闭时 `!streamState.done`），`clearActiveUiState` 触发 `finalizeStreamItem` 保存已累积的文本。
6. Token 统计使用 `recordTokenUsage()` 同步更新 round/session/total 三级计数器。

### v1/v2 兼容

`responseSync.ts` 通过 `isV2State()` 运行时类型守卫区分 v1（扁平 `history`）和 v2（`conversations[id].history` 桶）。活跃 UI 状态的 `activeUiStates` map 类型为 `AgentUiStateV2 | AgentUiState`。新增代码应只使用 `AgentUiStateV2`；v1 兼容守卫仅在加载/迁移路径保留。

参考实现：[`scripts/bridge/responseSync.ts`](../../../MCBE-AI-Agent-addon/scripts/bridge/responseSync.ts)。
