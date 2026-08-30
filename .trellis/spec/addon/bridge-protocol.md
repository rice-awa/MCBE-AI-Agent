# Addon Bridge 与协议

## 权威协议

运行时只使用 mcbews v1：

| 方向/用途 | 值 |
|---|---|
| Python → Addon bridge 请求 | `mcbews:bridge_req` |
| Python → Addon UI/AI 文本响应 | `mcbews:text_resp` |
| Addon → Python bridge 回传前缀 | `MCBEWS|BRIDGE` |
| Addon → Python UI 聊天前缀 | `MCBEWS|UI_CHAT` |
| Addon → Python 会话请求前缀 | `MCBEWS|SESSION` |
| Python → Addon 会话响应 | `mcbews:session_resp` |

这些值的权威来源是 SDK `0.2.1` wheel 的 manifest/vectors，Addon 的
[`scripts/bridge/constants.ts`](../../../MCBE-AI-Agent-addon/scripts/bridge/constants.ts)
只是同步后的投影，协议说明是 [`docs/addon-bridge-protocol.md`](../../../docs/addon-bridge-protocol.md)。不要把 `mcbeai:*` 或 `MCBEAI|*` 当作运行时兼容分支。`mcbeai:ui_state` DynamicProperty 键是旧世界状态兼容保留值，不属于线协议。

协议资产来自 SDK `0.2.1` wheel 的 manifest/vectors。必须分别记录以下版本轴：兼容线
`MCBEWS/1`、capability request schema `2`、session schema `1`、text response framing `1`、
DDUI persistence `2`。DDUI persistence 只描述玩家 DynamicProperty 的 per-conversation 格式，
不代表当前产品已经接入官方 DDUI API。

`COMMAND_LINE_BYTE_BUDGET=461` 是项目实测兼容预算，manifest 标记为 `empirical`；它不是
Minecraft 官方 API 保证，分片实现可以配置更低预算。

## 请求路由

[`scripts/bridge/router.ts`](../../../MCBE-AI-Agent-addon/scripts/bridge/router.ts) 采用以下边界：

1. `shouldHandleScriptEvent()` 先精确匹配 message id，非 bridge 事件直接忽略。
2. `handleBridgeScriptEvent()` 解析 request id、capability 和 payload，并通过集中 capability map 找到 handler。
3. handler 返回 `{ ok: true, payload }` 或 `{ ok: false, payload: { error } }` 形状，再交给 `sendBridgeResponseChunks()`。
4. 未知 capability 返回明确失败，不应静默当作成功。

能力 advertisement 必须从同一 registry 投影 handler 与 metadata；方块能力的
`multiblock_placement` 统一为 `command_fallback`。该值只表示宿主可能在安全策略/审批允许时
执行受控原生命令回退，不表示 Addon handler 无条件完成多格放置。

新增能力要提供 capability 名称、payload 类型边界、成功/失败响应和 router 测试；不要在每个能力模块中自行发送 scriptevent 或实现响应分片。

## 分片与重组

`chunking.ts` 的 `chunkPayload(prefix, id, payload, maxChunkContentLength)` 负责按字符长度生成 `prefix|id|index/total|content`，最大长度必须大于 0，空 payload 仍产生一个 `1/1` 分片。修改分片格式时必须同步 Python 协议文档、SDK 适配和测试。

`responseSync.ts` 处理 `mcbews:text_resp` JSON 分片：检查 id、index、total 的合法性，按消息 id 缓冲，收到足量分片后按 index 排序拼接，再按 `player_name` 查找目标玩家并更新对应 UI。不要使用到达顺序代替 index，也不要把一个玩家的响应写入全局当前 panel。

Python 出站长文本的分片由 SDK 的 `McbewsV1Delivery` / `McbeOutboundDelivery` 拥有；Addon 侧只维护自己的 bridge response/UI response 接收与回传格式，不能复制 Python 的 command line budget 逻辑。

参考测试：[`tests/bridge/router.test.ts`](../../../MCBE-AI-Agent-addon/tests/bridge/router.test.ts)、[`tests/bridge/chunking.test.ts`](../../../MCBE-AI-Agent-addon/tests/bridge/chunking.test.ts)、[`tests/bridge/getCapabilities.test.ts`](../../../MCBE-AI-Agent-addon/tests/bridge/getCapabilities.test.ts)。

## 会话请求与响应

会话同步（会话列表、切换、保存、删除）使用独立的 `sessionClient` 协议，不走 bridge router：

1. Addon 通过可信 ToolPlayer `MCBEWS_BRIDGE` 的 `tell @s MCBEWS|SESSION|json_payload` 发送一条完整请求，
   不使用通用 bridge 分片。
2. Python 处理后返回 `mcbews:session_resp` 的 scriptevent，Addon 通过 `registerSessionRespHandler()` 订阅接收。
3. 请求/响应都必须先通过完整 command 的 UTF-8 budget probe；响应超限时返回单帧
   `SESSION_RESPONSE_TOO_LARGE`，不能发送碎 JSON。Addon 对解析/发送失败立即返回结构化错误；超时默认 5 秒。

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
  "error": {"code": "...", "message": "..."}
}
```

超时响应也必须使用结构化错误，例如
`ok: false, error: {"code": "SESSION_UNAVAILABLE", "message": "会话同步不可用（服务端版本过旧）"}`。

参考实现：[`scripts/bridge/sessionClient.ts`](../../../MCBE-AI-Agent-addon/scripts/bridge/sessionClient.ts)。Python 侧需要实现 `mcbews:session_resp` 的响应发射才能完整工作。

## Scenario: 显式切换到 default 会话

### 1. Scope / Trigger

- 修改 SDK `SessionRequest`、Host 会话切换或 Addon `sessionClient` 的 `switch` 行为时适用。
- 该契约防止把合法默认 ID `"default"` 误判成调用方没有提供 `cid`。

### 2. Signatures

```python
conversation_id: str = Field(default="default", alias="cid")
ConversationOperations.execute(..., player_name: str, action="switch", conversation_id: str | None)
```

```json
{"v":1,"action":"switch","player_name":"Steve","cid":"default"}
```

### 3. Contracts

- SDK 必须按 `conversation_id` 是否存在于 `model_fields_set` 判断 `cid` 是否显式提供；alias
  `cid` 与字段名 `conversation_id` 都归一为该字段名。
- 显式非空 `cid`（包括 `"default"`）合法；省略或空白 `cid` 非法。
- Host 只拒绝 `None` / 空白目标，不得把 `DEFAULT_CONVERSATION_ID` 当成缺失哨兵。
- Addon 保持一个预算内 `MCBEWS|SESSION|<json>` 原子请求，并显式携带业务
  `player_name`；session schema 仍为 `1`。

### 4. Validation & Error Matrix

| 输入 | SDK | Host |
|---|---|---|
| `switch`, `cid="default"` | 接受 typed request | 切换到 default |
| `switch`, `cid="chat-a"` | 接受 typed request | 切换到 chat-a |
| `switch`, 省略 `cid` | `switch requires cid` | `INVALID_ARGUMENT` |
| `switch`, 空白 `cid` | `value must not be empty` | `INVALID_ARGUMENT` |

### 5. Good / Base / Bad Cases

- Good：Alice 从 `chat-a` 切换到 `default`，Bob 在同连接下仍停留在自己的会话。
- Base：显式 `cid="chat-a"` 沿用普通切换行为。
- Bad：用 `conversation_id == "default"` 推断字段缺失，或用 ToolPlayer sender 替代
  payload 中的 `player_name`。

### 6. Tests Required

- SDK：alias、字段名、省略、空白四类模型输入，以及用户原始帧经
  `AddonBridgeService.handle_player_message()` 得到 typed control message。
- Host：切换到 default 后断言 active conversation，并断言同连接另一玩家不变；`None` 与空白
  目标都必须返回 `INVALID_ARGUMENT`。
- Addon：断言 switch/default 只调用一次 `runCommand`，完整命令 UTF-8 字节数不超过
  `COMMAND_LINE_BYTE_BUDGET`，payload 保留当前 `player_name` / `cid="default"`，并按
  request id/action 关联响应。
- 协议资产：权威 vector、Python fixture、SDK reference Addon 与产品 Addon 投影必须通过生成检查。
- wheel contract：隔离安装后断言精确版本 `0.2.1`、`session-switch-default` vector 存在，且
  `mcbe_ws_sdk.__file__` 不位于 Host 的 nested SDK checkout。

### 7. Wrong vs Correct

```python
# Wrong: 合法默认值被当成“字段缺失”。
if self.action == "switch" and self.conversation_id == "default":
    raise ValueError("switch requires cid")

# Correct: 检查调用方是否显式提供字段。
if self.action == "switch" and "conversation_id" not in self.model_fields_set:
    raise ValueError("switch requires cid")
```

## 流式响应协议（mcbews:text_resp）

### 分片 payload 格式（text framing schema 1）

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
- `cid` — 所属会话 id，缺省回退到 `"default"`；同一响应的相关帧必须一致
- `t` — 会话标题；同一响应的相关帧必须一致
- `u` — token 统计 `{ i: input, o: output }`，只允许在完成帧（`i == n`）出现

接收端必须拒绝同一响应中互相冲突的 `cid` / `t` 元数据；`u` 只能出现在完成帧。

### 审批帧（r="approval"）

工具需要审批时，后端通过同一 `mcbews:text_resp` 通道发送 `r="approval"` 帧：

```json
{
  "id": "<message-id>",
  "i": 1, "n": 1,
  "p": "<player_name>",
  "r": "approval",
  "c": "{\"approval_id\":\"ap-xxx\",\"tool_name\":\"run_world_command\",\"args_summary\":\"/time set day\",\"reason\":\"需要确认\",\"batch_id\":\"b-1\",\"batch_size\":2,\"batch_index\":1}"
}
```

- `r="approval"` — 角色标识，`responseSync.ts` 在 `ensureStreamingState` 之前按此分支解析
- `c` — 内层 JSON 序列化的 `ApprovalInfo`：`approval_id`（决策回传用）、`tool_name`、`args_summary`、`reason`、`batch_id/batch_size/batch_index`（批次语义）
- 审批帧**不进对话历史**，仅刷新 `uiState.pendingApprovals` Map
- 决策命令复用 `tell @s` 通道：`MCBEWS|TOOL_APPROVE|<id>` / `MCBEWS|TOOL_DENY|<id>`
- 后端 `hook.on_player_message` 按前缀路由到 `handle_tool_approval`
- 多 tool 批次：后端为批内每个 tool 各发一帧，全部决策后才 resume

### 流式渲染规则

1. 按 `id` 缓冲区累积分片，按 `i` 排序后拼接 `c`。
2. 首次分片到达时创建 `StreamingState`，设置 `isStreaming = true`、`streamingConversationId`。
3. 每收到一个分片，计算 delta 更新 `streamingChars`，更新 `lastResponsePreview` 带有 `▌` 光标。
4. 最后分片 `buffer.size === n` 时触发 `onMessageComplete`：写入历史、更新 token stats、重置 streaming 标志。
5. 如果流不完整（面板关闭时 `!streamState.done`），`clearActiveUiState` 触发 `finalizeStreamItem` 保存已累积的文本。
6. Token 统计使用 `recordTokenUsage()` 同步更新 round/session/total 三级计数器。

### DDUI persistence 2 兼容

`responseSync.ts` 通过 `isV2State()` 运行时类型守卫区分旧版扁平 `history` 和 DDUI persistence `2`
（`conversations[id].history` 桶）。活跃 UI 状态的 `activeUiStates` map 类型为
`AgentUiStateV2 | AgentUiState`。新增代码应只使用 `AgentUiStateV2`；旧状态仅在加载/迁移路径
归入 `default`。

`r="approval"` 帧必须同时携带外层 `player_name` / `cid` 与 approval id；未知 role 在进入
history 前拒绝。审批决定的新 JSON 必须携带真实 owner，旧 id-only 形式只能通过 connection 内
唯一且未过期的 pending record 反查。

参考实现：[`scripts/bridge/responseSync.ts`](../../../MCBE-AI-Agent-addon/scripts/bridge/responseSync.ts)。
