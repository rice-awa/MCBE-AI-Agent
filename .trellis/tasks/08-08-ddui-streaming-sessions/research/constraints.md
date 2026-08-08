# Research: 关键约束确认与可复用模式（C 部分）

- **Query**: 确认 scriptevent 监听注册方式、request_id 关联模式、DDUI observable 表单打开期间的实时更新机制；以及 SDK 只读边界
- **Scope**: mixed (internal)
- **Date**: 2026-08-08

## 1. scriptevent 监听注册方式 — session_req 应挂在哪

### Addon 侧现有两个监听者（全部用 `system.afterEvents.scriptEventReceive.subscribe`）

1. `scripts/bridge/router.ts:104-111`（`registerBridgeRouter`）— 监听 `mcbews:bridge_req`
2. `scripts/bridge/responseSync.ts:57-68`（`registerResponseSyncHandler`）— 监听 `mcbews:text_resp`

两者均在 `bootstrap.ts:19-25`（`initializeAddonEarly`）注册，幂等保护（`isRegistered` 标志）。示例（router.ts:97-111）：

```ts
export function registerBridgeRouter(): void {
  if (isBridgeRouterRegistered) return;
  isBridgeRouterRegistered = true;
  system.afterEvents.scriptEventReceive.subscribe((event) => {
    if (!shouldHandleScriptEvent(event.id)) return;   // event.id === "mcbews:bridge_req"
    void handleBridgeScriptEvent(event);
  });
}
```

- **session_req 可选挂载点**：
  - 方案 A（推荐，零 SDK 改动）：作为新 `capability`（如 `session_list` / `session_new` / `session_status`）**挂在现有 `mcbews:bridge_req` 通道** —— router.ts 的 `loadCapabilityHandlers()` 表加一行即可（router.ts:33-60），响应自动走 `sendBridgeResponseChunks(request_id, ...)` 回到 Python 的 `AddonBridgeService.request_capability` future。
  - 方案 B（独立通道）：新增 `mcbews:session_req` message id，需要 SDK profile 加常量 + Addon 新增 `registerSessionRouter()` + Python 侧新 handler；跨两个仓库，需 SDK PR。
- **注意**：Addon 侧 `handleBridgeScriptEvent` 是 `void` 异步（fire-and-forget），handler 内部 await 能力处理；响应通过 `MCBEWS_BRIDGE` 模拟玩家聊天分片回传（router.ts:93-94）。

## 2. request_id / 请求响应关联模式（可复用）

### 现有模式：Python 侧（SDK AddonBridgeService）

- `mcbe-ws-sdk/src/mcbe_ws_sdk/addon/service.py`：
  - `request_capability(connection_id, capability, payload, send_command)`（service.py:88-...）：`session.create_request(...)` 创建带 `request_id` 的请求和 future；`encode_bridge_request(request_id=..., ...)` 编码 `scriptevent mcbews:bridge_req <json>`；`asyncio.wait_for(request.future, timeout)` 等待 Addon 回片；**发送失败 fail-fast**（不等超时）
  - `AddonBridgeClient.request(capability, payload) -> dict` 协议（service.py:46-50）—— Agent 侧统一入口
- 分片重组在 SDK `addon/session.py`：`handle_bridge_chunk` / `reassemble_bridge_chunks`（codec.py:80-106：校验 request_id 一致、total 一致、索引连续，JSON 解析）
- 工具玩家 sender 过滤：`bridge_sender = "MCBEWS_BRIDGE"`，`AddonBridgeService.is_bridge_chat_message(sender, message)` 按前缀识别（service.py:173-176）

### Addon 侧（scripts/bridge/router.ts）

- 请求：`{ request_id, capability, payload }`（router.ts:74-78）
- 响应：`sendBridgeResponseChunks(request.request_id, JSON.stringify(response))` —— **request_id 原样带回**，SDK 靠它唤醒 future（router.ts:93-94；toolPlayer.ts:56-69 `MCBEWS|BRIDGE|<request_id>|<i>/<n>|<content>`）

### text_resp 下行：目前无关联

- `McbewsV1Delivery.send_response` 有 `response_id` 参数（delivery.py:31），`encode_text_response_commands` 生成 `resp-<hex>` 或指定 id（codec.py:187），块字段 `id` 承载；但 **host 的 broker_bridge._ai_sync 从不传 response_id**，Addon 的 AiRespChunk.id 只是聚合缓冲键，**没有与上游请求/会话关联**。若要做「会话管理协议化 + 流式打字机」，`response_id`/`id` 字段是现成的关联位。

### 可复用结论

- 「会话查询」完全可以走 **bridge_req capability 模式**（Python `AddonBridgeClient.request` → Addon router handler → 聊天分片回传）——双向都已成型，Addon 侧只需新增 handler，Python 侧只需新增 capability 名与参数校验。
- 「Python → Addon 推送」（会话状态变化、token 统计、流式文本）走 **text_resp 通道**，需要扩展块字段（SDK 编码）或新 message id。

## 3. DDUI observable 在表单打开期间的更新机制

### 结论：支持 — 面板打开期间 `setData` 能实时刷新 label

证据链：

1. **agentConsole.ts:22-26**：`refreshConversation = () => { summary.setData(...); conversationBody.setData(...) }` 注入 `uiState.refreshConversation`；这两个是 `createDduiObservable` 创建的 DDUI Observable，传给 `.label()`（agentConsole.ts:30, 34）。
2. **responseSync.ts:128-141**：Python text_resp 重组完成后，在**面板仍然打开期间**调用 `activeState.refreshConversation?.()` → `setData` → label 内容变化。这套「实时刷新历史/状态」机制已在生产代码中运行。
3. **mock 佐证**（tests/__mocks__/minecraft-server-ui.ts）：`label(value: string | MockObservable<unknown>)` 存引用（mock:102-106），`getLabelTexts()` 每次调用 `value.getData()` 实时读取（mock:141-143）；`MockObservable.setData` 通知订阅者（mock:58-63）。官方 `ObservableString` 语义即「变更后 UI 自动更新」。
4. 测试（tests/ui/agent-console.test.ts）断言 `getLabelTexts()` 在 `setData` 后返回新值。

### 注意点

- **label 之外**：`textField` 的值是 Observable，`getData()` 读取玩家输入（agentConsole.ts:41）；`toggle/slider/dropdown` 的 observable 由玩家交互写入，`setData` 程序化写入用于初始值（settingsPanel.ts:15-20）。
- **dropdown 用索引值**：`dropdown(label, value: DduiObservable<number>, options)` —— 面板代码负责 `DELIVERY_OPTIONS[index]` 反查（settingsPanel.ts:37-41, 49）。
- **按钮 callback 中直接访问 observable 状态并 `form.close()`** 是标准导航模式（agentConsole.ts:81-85）。
- 表单在 `await show()` 期间**会阻塞该函数**（async 挂起），但其他模块（responseSync 的 scriptevent 回调）可以并行执行 `setData` —— 这正是现有刷新机制能工作的原因。

## 4. 其他已确认约束

### SDK 只读边界（重要）

- `requirements.txt:3`：`mcbe-ws-sdk>=0.1.0`（pip 依赖）。本地 `mcbe-ws-sdk/` 目录是源码副本，但 CLAUDE.md 规定「勿改 SDK 源码除非另开 SDK PR」。
- 影响：text_resp 块格式（6 字段）与 bridge_req 协议结构默认视为稳定契约；**扩展线协议 = SDK 改动**。宿主侧可自由做的是：复用 bridge_req 通道（新 capability）、复用 text_resp 通道（仅用现有字段）、在 broker_bridge 内部处理新 dict type。

### 非阻塞约束

- hook 内禁止 await LLM / 桥 RTT；一律 `asyncio.create_task`（hook.py:184-201 的 `on_ui_chat_reassembled` 即此模式；`on_player_message` 同样）。新 session_req 处理必须同样 fire-and-forget。

### 多人会话隔离

- 身份只用 `event.sender` / 显式 `player_name`；禁止读 `ConnectionState.player_name`。`_reply_target`（command_handlers.py:81-83）即 `player_name or "@a"`。
- 会话状态按 `(connection_id, player_name, conversation_id)` 分桶（core/session.py:12-13 `SessionKey`）。

### 出站流控

- 下行长文本必须走 SDK delivery（`McbeOutboundDelivery` / `McbewsV1Delivery` / `FlowControlSettings`），宿主不手写分片。text_resp 块延迟：`chunk_delays.text_resp`（默认由 SDK profile 的 0.15s 控制，host 侧 FlowControlSettings 可映射）。
- 上行阈值 256 字符（Addon constants.ts:18），下行 400 字符（SDK 流控）。

### 去重与回显

- Addon 侧 `isDuplicateUiUserEcho`（responseSync.ts:172-181）：UI 发送的 user 条目 `source:"ui"` 已在本地，Python 回写的 `source:"python"` 同内容跳过 —— 新协议（如 session 操作触发 Python 回写 user 文本）需注意同样去重。

### 现有会话数据缺口

- 运行时对话元数据（title/short_id/状态）**只在 Python 内存**（core/session.py），Addon 无法直接读取；持久化会话在 `storage/conversations_dir/*.json`（`{connection_id}_{timestamp}_{hex}.json`），owner 校验严格（conversation.py:729-739）—— session 协议化后 Addon 获取这些数据必须走 Python 查询（bridge_req 或 text_resp 推送），不能直接读文件。
