# Addon Bridge 与协议

## 权威协议

运行时只使用 mcbews v1：

| 方向/用途 | 值 |
|---|---|
| Python → Addon bridge 请求 | `mcbews:bridge_req` |
| Python → Addon UI/AI 文本响应 | `mcbews:text_resp` |
| Addon → Python bridge 回传前缀 | `MCBEWS|BRIDGE` |
| Addon → Python UI 聊天前缀 | `MCBEWS|UI_CHAT` |

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
