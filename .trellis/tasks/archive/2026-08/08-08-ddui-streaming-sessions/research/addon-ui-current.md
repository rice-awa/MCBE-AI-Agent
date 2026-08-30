# Research: Addon 侧现有 UI 代码模式（A 部分）

- **Query**: 探索 addon 仓库 scripts/ui 与 scripts/bridge 的现有代码模式（DDUI 表单包装、主面板、响应同步、状态/历史/存储/统计、线协议常量、分片、工具玩家、路由、启动流程、manifest 依赖）
- **Scope**: internal
- **Date**: 2026-08-08

## 1. formAdapter.ts — DDUI v2.1.0 包装完整清单

文件：`scripts/ui/forms/formAdapter.ts`

### 类型别名（面板代码签名不变）

- `DduiObservable<T>` = `{ getData(): T; setData(value: T): void }`（formAdapter.ts:14-17）
- `DduiDropdownOption<T>` = `{ label: string; value: T; description?: string }`（formAdapter.ts:19-23）
- `DduiCustomForm` 方法链签名（formAdapter.ts:25-44）：

| 方法 | 签名 |
|---|---|
| `divider()` | `(): DduiCustomForm` |
| `header(text)` | `text: string \| DduiObservable<string>` |
| `label(text)` | `text: string \| DduiObservable<string>` |
| `textField(label, value, options?)` | `value: DduiObservable<string>`, `options?: { description?: string }` |
| `spacer()` | `(): DduiCustomForm` |
| `closeButton()` | `(): DduiCustomForm` |
| `close()` | `(): void`（程序化关闭表单） |
| `toggle(label, value, options?)` | `value: DduiObservable<boolean>`, `options?: { description?: string }` |
| `slider(label, value, min, max, options?)` | `value: DduiObservable<number>`, `options?: { description?: string; step?: number }` |
| `dropdown(label, value, options)` | `value: DduiObservable<number>`（**索引而非选项值**）, `options: DduiDropdownOption<number>[]` |
| `button(label, callback, options?)` | `callback: () => void`, `options?: { tooltip?: string }` |
| `show()` | `(): Promise<unknown>`（返回关闭原因） |

- `DduiCustomFormShowResult` = `{ ok: boolean; closedByUser: boolean; closeReason?: unknown }`（formAdapter.ts:46-50）

### createDduiObservable 支持的类型（formAdapter.ts:57-68）

```ts
export function createDduiObservable<T>(initialValue: T): DduiObservable<T> {
  if (typeof initialValue === "string") {
    return new ObservableString(initialValue as string, { clientWritable: true }) as unknown as DduiObservable<T>;
  }
  if (typeof initialValue === "number") {
    return new ObservableNumber(initialValue as number, { clientWritable: true }) as unknown as DduiObservable<T>;
  }
  if (typeof initialValue === "boolean") {
    return new ObservableBoolean(initialValue as boolean, { clientWritable: true }) as unknown as DduiObservable<T>;
  }
  throw new Error(`createDduiObservable: unsupported type ${typeof initialValue}`);
}
```

- 仅支持 string / number / boolean；**不支持 undefined / object / array**。
- 构造选项 `{ clientWritable: true }`。
- 工厂函数：`createCustomForm(player, title)` = `new CustomForm(player, title)`（formAdapter.ts:73-75）；另有 `createActionForm` / `createModalForm`（formAdapter.ts:77-83）。

### showCustomFormSafely 错误处理模式（formAdapter.ts:108-120）

```ts
export async function showCustomFormSafely(player: Player, form: DduiCustomForm): Promise<DduiCustomFormShowResult> {
  try {
    const closeReason = await form.show();
    return {
      ok: closeReason !== false && !isUserBusyReason(closeReason),
      closedByUser: isUserClosedReason(closeReason),
      closeReason,
    };
  } catch {
    player.sendMessage("MCBE AI Agent: 表单暂时无法打开，请稍后再试。");
    return { ok: false, closedByUser: false };
  }
}
```

- `isUserClosedReason`（formAdapter.ts:124-143）：兼容 string 枚举（`"UserClose"`/`"UserClosed"`/`DataDrivenScreenClosedReason.ClientClosed`）、boolean（`!reason`）、对象（`canceled===true` 或嵌套 `closeReason`/`cancelationReason` 递归）。
- `isUserBusyReason`（formAdapter.ts:145-156）：string 枚举 `"UserBusy"` / `DataDrivenScreenClosedReason.UserBusy`，对象递归。

### 按钮列表定义与响应（惯例，来自各面板）

按钮通过链式 `.button(label, callback)` 注册；callback 内设置 `nextRoute` 并调用 `form.close()` 程序化关闭，然后 `showCustomFormSafely` 返回、调用方根据 `nextRoute` 决定路由（见 agentConsole.ts:81-85、morePanel.ts:15-22）。

## 2. agentConsole.ts — 主面板全文要点

文件：`scripts/ui/panels/agentConsole.ts`（共 121 行）

### 表单构建与 refreshConversation 注入（agentConsole.ts:15-27）

```ts
export async function showAgentConsole(player: Player, uiState: AgentUiState): Promise<AgentPanelRoute> {
  try {
    const summary = createDduiObservable(buildSummary(uiState));
    const conversationBody = createDduiObservable(buildConversationBody(uiState));
    const messageValue = createDduiObservable("");
    let nextRoute: AgentPanelRoute = MAIN_ROUTE;

    const refreshConversation = () => {
      summary.setData(buildSummary(uiState));
      conversationBody.setData(buildConversationBody(uiState));
    };
    uiState.refreshConversation = refreshConversation;   // 注入到 state，供 responseSync 外部调用
```

- **关键机制**：`uiState.refreshConversation` 是响应同步模块实时刷新的钩子（responseSync.ts:140 调用 `activeState.refreshConversation?.()`）；面板关闭时清为 `undefined`（agentConsole.ts:90-96）。

### 表单元素顺序（agentConsole.ts:28-38）

`closeButton().label(summary).spacer().divider().spacer().label(conversationBody).spacer().divider().spacer().textField("消息内容", messageValue, { description: "发送后面板会保持打开" }).spacer()`

### 发送流程（agentConsole.ts:40-80，按钮 "发送" callback）

1. `messageValue.getData().trim()` 判空
2. `appendHistoryItem(uiState.history, { id: createHistoryId("ui", now), role: "user", content: message, createdAt: now, source: "ui" }, uiState.settings.maxHistoryItems)` — 本地先行写入，`source: "ui"`
3. `uiState.lastPrompt.setData(message)`；`uiState.bridgeStatus.setData("connecting")`
4. `uiState.stats = syncLocalHistoryCount(recordPromptSent(uiState.stats, now), uiState.history.length)`
5. `messageValue.setData("")`；`refreshConversation()`
6. `sendUiChatMessage(player.name, message)`（try/catch）→ 成功 `bridgeStatus.setData("sent")` + sendMessage；失败 `setData("error")` + 提示手动命令 `buildAgentChatCommand(message)`
7. `refreshConversation()`；`saveAgentUiState(player, uiState)`（失败仅提示）

### 按钮路由（agentConsole.ts:81-85）

```ts
.button("其他", () => {
  nextRoute = { panel: "more" };
  saveAgentUiState(player, uiState);
  form.close();
})
```

### show 后的路由决策（agentConsole.ts:87-97）

```ts
const shown = await showCustomFormSafely(player, form);
if (!shown.ok || (shown.closedByUser && nextRoute.panel === "main")) {
  saveAgentUiState(player, uiState);
  uiState.refreshConversation = undefined;
  return CLOSE_ROUTE;
}
if (nextRoute.panel !== "main") {
  uiState.refreshConversation = undefined;
}
return nextRoute;
```

### 视图构建（agentConsole.ts:106-121）

- `buildConversationBody`：`uiState.history.slice(-CONVERSATION_PREVIEW_LIMIT)`（`CONVERSATION_PREVIEW_LIMIT = 6`），空则 "暂无对话记录。输入消息开始聊天。"，否则 `items.map((item) => summarizeHistoryItem(item, uiState.settings.responsePreviewLength)).join("\n\n---\n\n")`
- `buildSummary`：三行文本（桥接状态 / 历史条数 / 发送次数），`\n\n` 连接

## 3. responseSync.ts — 响应同步机制全文

文件：`scripts/bridge/responseSync.ts`（共 182 行）

### 块格式（responseSync.ts:17-24）

```ts
type AiRespChunk = {
  id: string;   // 消息 ID（msg_id）
  i: number;    // 分片索引（1-based）
  n: number;    // 总分片数
  p: string;    // player name
  r: string;    // role（"user" | "assistant"）
  c: string;    // 内容片段
};
```

### 缓冲区与活跃状态

- `chunkBuffers: Map<string, Map<number, AiRespChunk>>`（msg_id → index → chunk）（responseSync.ts:12）
- `activeUiStates: Map<string, AgentUiState>`（player.id → 内存 state）（responseSync.ts:15）；`setActiveUiState` / `clearActiveUiState` 由 entry.ts 管理（responseSync.ts:31-40）
- `resetResponseSyncForTests` 清空三者（responseSync.ts:42-46）

### 注册（responseSync.ts:51-69）

```ts
export function registerResponseSyncHandler(): void {
  if (isRegistered) return;
  isRegistered = true;
  system.afterEvents.scriptEventReceive.subscribe((event) => {
    if (event.id !== TEXT_RESP_MESSAGE_ID) return;
    try {
      const chunk = JSON.parse(event.message) as AiRespChunk;
      handleChunk(chunk);
    } catch { /* 忽略解析错误 */ }
  });
}
```

- **scriptevent 监听模式确认**：`system.afterEvents.scriptEventReceive.subscribe`（幂等保护 `isRegistered`）。

### handleChunk（responseSync.ts:71-101）

- 校验：`!id || i <= 0 || n <= 0 || i > n` 则丢弃
- 按 `id` 找/建 buffer，`buffer.set(i, chunk)`；`buffer.size < n` 则等下一片
- 收齐后 `[...buffer.values()].sort((a, b) => a.i - b.i).map(c => c.c).join("")` 重组，`chunkBuffers.delete(id)`，调 `onMessageComplete(playerName, role as HistoryRole, fullText)`

### onMessageComplete（responseSync.ts:103-165）

1. `world.getAllPlayers().find((p) => p.name === playerName)` — **按玩家名找目标**
2. 构造 `HistoryItem { id: createHistoryId("py", Date.now()), role, content: text, createdAt: Date.now(), source: "python" }`
3. 若内存活跃 state 存在：先 `isDuplicateUiUserEcho` 去重；`appendHistoryItem`；assistant 角色时 `bridgeStatus.setData("ready")` + `lastResponsePreview.setData(截断)`；`activeState.refreshConversation?.()` 实时刷新 DDUI
4. 再 `loadAgentUiState(targetPlayer)` 走 DynamicProperty 持久化（同样去重、追加、更新 preview、save），失败静默

### isDuplicateUiUserEcho（responseSync.ts:172-181）

```ts
function isDuplicateUiUserEcho(history: HistoryItem[], item: HistoryItem): boolean {
  if (item.role !== "user" || item.source !== "python") return false;
  return history.some(
    (existing) =>
      existing.role === "user" && existing.source === "ui" && existing.content.trim() === item.content.trim()
  );
}
```

- 用途：用户从 UI 发送时本地已写 `source:"ui"` 条目；Python 回写历史 `source:"python"` 内容相同则跳过，防重复。

## 4. state.ts / history.ts / storage.ts / stats.ts

### state.ts（`scripts/ui/state.ts`，91 行）

- `BridgeStatus = "disconnected" | "connecting" | "ready" | "sent" | "error"`（state.ts:4）
- `ObservableLike<T> = { getData(): T; setData(value: T): void }`（state.ts:6-9）— **与 DDUI Observable 接口一致，可互换**
- `AgentUiDelivery = "tellraw" | "scriptevent"`（state.ts:11）
- `AgentUiSettings`（state.ts:13-19）：`autoSaveHistory: boolean; maxHistoryItems: number; showToolEvents: boolean; responsePreviewLength: number; defaultDelivery: AgentUiDelivery`
- `AgentUiState`（state.ts:21-29）：

```ts
export type AgentUiState = {
  bridgeStatus: ObservableLike<BridgeStatus>;
  lastPrompt: ObservableLike<string>;
  lastResponsePreview: ObservableLike<string>;
  history: HistoryItem[];
  settings: AgentUiSettings;
  stats: AgentUiStats;
  refreshConversation?: () => void;   // 由 agentConsole 注入，responseSync 调用
};
```

- `DEFAULT_AGENT_UI_SETTINGS`（state.ts:40-46）：`autoSaveHistory: true, maxHistoryItems: 30, showToolEvents: true, responsePreviewLength: 120, defaultDelivery: "tellraw"`
- `DEFAULT_AGENT_UI_STATS`（state.ts:48-55）：`openCount: 0, sentCount: 0, localHistoryCount: 0, responseChunkCount: 0, lastOpenedAt: 0, lastSentAt: 0`
- `createObservable`（state.ts:57-67）：纯内存闭包实现（非 DDUI 实例）
- `createAgentUiState(initialState)`（state.ts:69-91）：settings/history/stats 合并默认值；`localHistoryCount: Math.min(initialStats.localHistoryCount, history.length)`

### history.ts（`scripts/ui/history.ts`，76 行）

- `HistoryRole = "user" | "assistant" | "system" | "tool"`（history.ts:1）
- `HistorySource = "ui" | "python" | "system" | "local"`（history.ts:3）
- `ChatHistoryItem`（history.ts:5-11）：`{ id: string; role; content: string; createdAt: number; source }`
- `appendHistoryItem(history, item, maxHistoryItems)`：`[...history, item].slice(-limit)`，`limit=Math.max(0, Math.floor(maxHistoryItems))`，0 返回 `[]`（history.ts:23-34）
- `getHistoryPage(history, pageIndex, pageSize)`（history.ts:36-53）：newestFirst 反转分页；`totalPages = Math.max(1, Math.ceil(...))`
- `formatHistoryItem` / `summarizeHistoryItem`（history.ts:55-67）：`[role/source] content` 前缀；summarize 截断加 `...`
- `createHistoryId(prefix = "ui", createdAt = Date.now())`（history.ts:73-75）：`${prefix}-${createdAt}-${Math.floor(Math.random() * 100000)}`

### storage.ts（`scripts/ui/storage.ts`，165 行）

- 常量（storage.ts:6-8）：`AGENT_UI_STATE_PROPERTY_KEY = "mcbeai:ui_state"`；`AGENT_UI_STATE_VERSION = 1`；`PERSISTED_HISTORY_LIMIT = 20`（保存时只写最近 20 条，storage.ts:155）
- `DynamicPropertyOwner = { getDynamicProperty(identifier: string): unknown; setDynamicProperty(identifier: string, value?: string): void }`（storage.ts:10-13）
- `PersistedAgentUiState`（storage.ts:15-21）：`{ version: 1; bridgeStatus?; settings?; history?; stats? }`
- `loadAgentUiState(owner)`（storage.ts:25-48）：非 string / JSON 解析失败 / `persisted.version !== AGENT_UI_STATE_VERSION` 时回退 `createAgentUiState()`；history 经 `normalizeHistory`
- `normalizeHistory`（storage.ts:84-103）：逐条校验 `id/content/createdAt` 类型 + `isHistoryRole` + `isHistorySource`
- `normalizeSettings`（storage.ts:60-82）：`maxHistoryItems` clamp 10-50；`responsePreviewLength` clamp 60-240；`defaultDelivery` 白名单
- `normalizeStats`（storage.ts:105-124）：非负数；`localHistoryCount` 取 min 于 rawHistoryCount
- `saveAgentUiState(owner, state)`（storage.ts:146-164）：`owner.setDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY, JSON.stringify(persisted))`；返回 `{ ok: true } | { ok: false; error }`

### stats.ts（`scripts/ui/stats.ts`，50 行）

- `AgentUiStats`（stats.ts:1-8）：`openCount; sentCount; localHistoryCount; responseChunkCount; lastOpenedAt; lastSentAt`（全部 number）
- 纯函数更新：`recordUiOpened` / `recordPromptSent` / `recordResponseChunk` / `syncLocalHistoryCount` / `resetStats`（stats.ts:10-49）

## 5. bridge/constants.ts + chunking.ts

### constants.ts（`scripts/bridge/constants.ts`，30 行）

```ts
export const BRIDGE_RESPONSE_PREFIX = "MCBEWS|BRIDGE";
export const BRIDGE_UI_CHAT_PREFIX = "MCBEWS|UI_CHAT";
export const BRIDGE_REQUEST_MESSAGE_ID = "mcbews:bridge_req";
export const BRIDGE_MESSAGE_ID = BRIDGE_REQUEST_MESSAGE_ID;  // deprecated alias
export const BRIDGE_SENDER = "MCBEWS_BRIDGE";
export const TOOL_PLAYER_NAME = BRIDGE_SENDER;
export const BRIDGE_MAX_CHUNK_CONTENT_LENGTH = 256;      // 上行（addon→python）字符上限
export const BRIDGE_COMMAND_LINE_BYTE_BUDGET = 461;       // MCBE commandLine 实测字节上限
export const BRIDGE_MAX_CHUNK_CONTENT_CODE_POINTS = 256;
export const TEXT_RESP_MESSAGE_ID = "mcbews:text_resp";
export const AI_RESP_MESSAGE_ID = TEXT_RESP_MESSAGE_ID;   // deprecated alias
```

- 上下行阈值不同：上行 256 字符（say/tellraw 包装开销），下行 400 字符（SDK FlowControlSettings，constants.ts:12-17 注释）

### chunking.ts（`scripts/bridge/chunking.ts`，57 行）

- `formatChunk(prefix, id, index, total, content)`（chunking.ts:3-11）：`${prefix}|${id}|${index}/${total}|${content}`
- `chunkPayload(prefix, id, payload, maxChunkContentLength)`（chunking.ts:22-40）：按 code-point 切分（`payload.slice`），空 payload 产出 1 个空分片；`formatChunk(prefix, id, idx+1, total, content)`
- `chunkBridgePayload(requestId, payload, len)` / `chunkUiChatPayload(id, payload, len)`（chunking.ts:42-56）

## 6. toolPlayer.ts — sendUiChatMessage 上行通道

文件：`scripts/bridge/toolPlayer.ts`（共 108 行）

- 模拟玩家：`TOOL_PLAYER_NAME = "MCBEWS_BRIDGE"`，`spawnSimulatedPlayer`（@minecraft/server-gametest）在 `overworld (300000, 100, 300000)`，`GameMode.Creative`（toolPlayer.ts:18-19, 41-48）
- `ensureToolPlayer()`：幂等，存在则跳过；`initializeToolPlayer()` 内 `system.runInterval(ensureToolPlayer, 20*30)` 保活（toolPlayer.ts:24-54, 90-107）

```ts
export function sendUiChatMessage(playerName: string, message: string): void {
  const toolPlayer = world.getAllPlayers().find((player) => player.name === TOOL_PLAYER_NAME);
  if (!toolPlayer) throw new Error("Tool player is not available");
  const id = `ui-${Date.now()}-${++uiChatSeq}`;
  const payload = JSON.stringify({ player: playerName, message });
  const chunks = chunkUiChatPayload(id, payload, BRIDGE_MAX_CHUNK_CONTENT_LENGTH);
  for (const chunk of chunks) {
    toolPlayer.runCommand(`tell @s ${chunk}`);
  }
}
```

- **上行走 tell 通道**：模拟玩家 `runCommand("tell @s <chunk>")`；负载格式 `{ player, message }` JSON，前缀 `MCBEWS|UI_CHAT|<msg_id>|<i>/<n>|...`
- 同文件 `sendBridgeResponseChunks(requestId, payload)` 用 `MCBEWS|BRIDGE` 前缀回传能力响应（toolPlayer.ts:56-69）

## 7. 路由循环、面板注册、启动流程

### routes.ts（`scripts/ui/panels/routes.ts`）

- `AgentPanelRoute = { panel: "main" } | { panel: "more" } | { panel: "settings" } | { panel: "stats" } | { panel: "close" }`（routes.ts:1-6）
- `MAIN_ROUTE` / `CLOSE_ROUTE` 常量（routes.ts:8-9）

### entry.ts（`scripts/ui/entry.ts`，77 行）

- 触发器：`world.afterEvents.itemUse.subscribe`，物品为 `minecraft:command_block`（`isUiTriggerItem`，commands.ts:1-5）
- 20 tick 冷却（`OPEN_COOLDOWN_TICKS`）+ `openPanels` Set 防重（entry.ts:14-16, 24-31）
- 路由循环（entry.ts:52-69）：

```ts
let route: AgentPanelRoute = { panel: "main" };
while (route.panel !== "close") {
  switch (route.panel) {
    case "main": route = await showAgentConsole(player, uiState); break;
    case "more": route = await showMorePanel(player, uiState); break;
    case "settings": route = await showSettingsPanel(player, uiState); break;
    case "stats": route = await showStatsPanel(player, uiState); break;
  }
}
saveAgentUiState(player, uiState);
// finally: clearActiveUiState(player.id); openPanels.delete(player.id);
```

- 打开时：`loadAgentUiState(player)` → `recordUiOpened` → `saveAgentUiState` → `setActiveUiState(player.id, uiState)`（entry.ts:44-50）

### 其他面板（简洁说明）

- `morePanel.ts`：两个按钮（设置/统计信息）路由（morePanel.ts:13-22）
- `settingsPanel.ts`：toggle（autoSaveHistory）+ slider（maxHistoryItems 10-50 step5）+ toggle（showToolEvents）+ slider（responsePreviewLength 60-240 step20）+ dropdown（defaultDelivery，`DELIVERY_OPTIONS.map((option, index) => ({ label: option, value: index }))`，**选项值为索引**）+ 保存/清空历史按钮；保存时 `clampToStep` 修正 + `uiState.history = uiState.history.slice(-maxHistoryItems)`（settingsPanel.ts:29-69）
- `statsPanel.ts`：label 显示统计（`createStatsBody` 六行 `\n\n` 连接），按钮返回/重置（statsPanel.ts:14-33）
- `commands.ts`：`isUiTriggerItem(typeId)` 与 `buildAgentChatCommand(input)` = `AGENT 聊天 ${input.trim()}`（commands.ts:1-9）

### 启动流程

- `main.ts`：`initializeAddonEarly()` + `initializeAddonAfterWorldLoad()`（main.ts:1-7）
- `bootstrap.ts`：`initializeAddonEarly` 注册 `registerBridgeRouter()` + `registerResponseSyncHandler()` + `registerUiEntry()`（bootstrap.ts:19-25）；`initializeAddonAfterWorldLoad` 订阅 `worldLoad` + `system.run` 兜底初始化 ToolPlayer（bootstrap.ts:35-67）

### router.ts（`scripts/bridge/router.ts`，112 行）

- `system.afterEvents.scriptEventReceive.subscribe` 监听 `mcbews:bridge_req`（router.ts:104-111）
- 请求格式（router.ts:74-78）：`{ request_id: string; capability: string; payload?: Record<string, unknown> }`
- 能力 handler 表：`get_player_snapshot` / `get_inventory_snapshot` / `find_entities` / `run_world_command` / `get_look_block` / `get_capabilities` / `inspect_block` / `edit_blocks`（router.ts:33-60），动态 `import()` 加载
- 响应：`sendBridgeResponseChunks(request.request_id, JSON.stringify(response))`，响应体 `{ ok: boolean; payload: ... }`（router.ts:84-94）
- 未知能力返回 `{ ok: false, payload: { error: "未知桥接能力: ..." } }`

## 8. manifest.json 依赖版本

文件：`behavior_packs/MCBE-AI-Agent/manifest.json`

- `@minecraft/server` `2.8.0`
- `@minecraft/server-ui` `2.1.0`
- `@minecraft/server-gametest` `1.0.0-beta`
- `min_engine_version: [1, 21, 80]`；BP 版本 `2.5.0`；entry `scripts/main.js`

## 9. 测试 mock 确认的 DDUI 行为

文件：`tests/__mocks__/minecraft-server-ui.ts`

- `MockObservable` 实现 `getData/setData/subscribe/unsubscribe`；`setData` 会通知订阅者（mock:58-63）——label 通过 `getData()` 渲染（mock:142），**setData 之后 label 内容按官方实现应实时刷新**（测试断言用 `getLabelTexts()` 读取当前值）
- `CustomForm` mock 记录 `#components`（方法调用顺序）与 `#fields`（label → observable 映射）、`#buttons`（label → callback）
- 导出：`ObservableString = MockObservable<string>`、`ObservableNumber = MockObservable<number>`、`ObservableBoolean = MockObservable<boolean>`、`DataDrivenScreenClosedReason`（mock:190-196）

## 关键可复用要点（供重构参考）

1. 面板 = 工厂函数 `showXxxPanel(player, uiState) => Promise<AgentPanelRoute>`，统一 try/catch + `saveAgentUiState` 收尾
2. 实时刷新 = `createDduiObservable` + `uiState.refreshConversation` 注入 + `setData`
3. 去重 = `isDuplicateUiUserEcho`（source 区分 ui/python）
4. 分片缓冲 = `Map<msg_id, Map<index, chunk>>`，收齐后按 index 排序重组
5. 上行 = 模拟玩家 `tell @s` 聊天分片；下行 = `system.afterEvents.scriptEventReceive` 订阅
6. 持久化 = `mcbeai:ui_state` DynamicProperty，version 1，`PERSISTED_HISTORY_LIMIT = 20`
