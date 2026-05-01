# DDUI 游戏内聊天面板开发文档

**日期：** 2026-04-19

**目标：** 在现有 `MCBE-AI-Agent-addon` 的 UI 预留层基础上，构建一个可在游戏内打开的聊天面板，支持发送 AI 聊天、查看聊天记录、调整 UI 设置、查看统计信息，并为后续切换到官方 DDUI 响应式表单保留清晰边界。

**执行策略：** 创建 Git worktree 然后在新分支完成开发，不影响主分支的工作；优先快速落地可运行面板，减少非必要测试，最终以 Minecraft 游戏内联调为准。

---

## 1. 文档调研结论

### 1.1 官方 DDUI 能力

Context7 查询到官方 Minecraft Creator 文档中的 DDUI 入口：

- DDUI 入门：<https://github.com/microsoftdocs/minecraft-creator/blob/main/creator/Documents/scripting/intro-to-ddui.md>
- `@minecraft/server-ui` 表单：<https://github.com/microsoftdocs/minecraft-creator/tree/main/creator/ScriptAPI/minecraft/server-ui>
- `scriptEventReceive`：<https://github.com/microsoftdocs/minecraft-creator/blob/main/creator/ScriptAPI/minecraft/server/ScriptEventCommandMessageAfterEvent.md>
- Dynamic Properties：<https://github.com/microsoftdocs/minecraft-creator/blob/main/creator/ScriptAPI/minecraft/server/World.md>

DDUI 文档里的关键 API 是：

- `CustomForm.create(player, title)`：创建响应式表单。
- `Observable.create<T>(value, { clientWritable: true })`：创建可由客户端控件写回的状态。
- 控件属性支持 `description`、`tooltip`、`visible`、`disabled` 等配置。
- 表单支持 `closeButton()`、`spacer()`、`divider()`、`label()` 等布局能力。

### 1.2 当前项目的实际限制

当前仓库本地依赖为 `@minecraft/server-ui@2.0.0`，其类型声明 `MCBE-AI-Agent-addon/node_modules/@minecraft/server-ui/index.d.ts` 只暴露：

- `ActionFormData`
- `ModalFormData`
- `MessageFormData`
- 对应的 `ActionFormResponse`、`ModalFormResponse`、`MessageFormResponse`

当前类型声明没有 `CustomForm` 和 `Observable`。因此第一版不能直接使用官方 DDUI 示例代码，否则 `pnpm build` 会失败。

本开发文档采用两层方案：

- 第一阶段：使用现有 `ActionFormData` / `ModalFormData` 实现完整可用的聊天面板。
- 第二阶段：通过 UI 表单适配层切换到真正 DDUI 的 `CustomForm` / `Observable`，业务状态和面板流程不重写。

### 1.3 Bedrock UI 事件约束

`ActionFormData.show(player)` 和 `ModalFormData.show(player)` 不能在 read-only mode 调用。

本方案不使用 `chatSend` 监听作为 UI 入口，原因如下：

- `chatSend` 属于测试版接口，不适合作为稳定功能入口。
- 聊天监听会侵入并破坏现有聊天链路，与本项目「不影响原有聊天命令」的目标冲突。

第一阶段改为使用 `world.afterEvents.itemUse` 监听玩家使用物品事件，并先绑定为原版命令方块物品 `minecraft:command_block` 打开 UI 菜单。

推荐写法：

```ts
world.afterEvents.itemUse.subscribe((event) => {
  if (event.itemStack.typeId !== "minecraft:command_block") {
    return;
  }

  void openAgentUi(event.source);
});
```

如果后续出现重复触发或误触问题，再补充简单节流或 Sneak 条件判断，但第一版先保持最小实现。

---

## 2. 当前代码基线

### 2.1 Addon 入口

当前入口链路：

```text
scripts/main.ts
  -> initializeAddon()
  -> initializeToolPlayer()
  -> registerBridgeRouter()
  -> registerUiEntry()
```

相关文件：

- `MCBE-AI-Agent-addon/scripts/main.ts`
- `MCBE-AI-Agent-addon/scripts/bootstrap.ts`
- `MCBE-AI-Agent-addon/scripts/ui/entry.ts`
- `MCBE-AI-Agent-addon/scripts/ui/state.ts`
- `MCBE-AI-Agent-addon/scripts/ui/panels/agentConsole.ts`

### 2.2 当前 UI 状态

`state.ts` 目前是自定义 `ObservableLike`：

- `bridgeStatus`
- `lastPrompt`
- `lastResponsePreview`

它不是官方 DDUI `Observable`，但可以作为第一阶段状态容器继续扩展。

### 2.3 当前聊天命令链路

Python 侧通过 Minecraft WebSocket 订阅 `PlayerMessage`，解析聊天命令：

- `AGENT 聊天 <消息>`
- `AGENT 脚本 <消息>`
- `AGENT 上下文 <启用/关闭/状态/压缩/保存/恢复/列表/删除/清除>`
- `AGENT 模板`
- `AGENT 设置`
- `AGENT MCP`
- `切换模型`
- `运行命令`
- `帮助`

第一阶段 UI 不替代这些命令，只作为更易用的游戏内入口。

---

## 3. 设计目标

### 3.1 必须实现

- 玩家可以在游戏内通过使用原版命令方块物品打开面板。
- 面板可以输入并发送 AI 聊天消息。
- 面板可以查看最近聊天记录。
- 面板可以查看基础统计信息。
- 面板可以修改 UI 设置。
- 面板不破坏现有聊天命令。
- 面板在 Python 服务未连接或响应未同步时也有明确提示。

### 3.2 第一阶段不做

- 不直接依赖 `CustomForm` / `Observable`。
- 不做复杂动画或自定义材质 UI。
- 不做完整多玩家云同步历史。
- 不在 addon 内直接调用外部 HTTP。
- 不引入新的 Python 网络通道。
- 不为所有 UI 交互写完整单元测试。

### 3.3 成功标准

- `cd MCBE-AI-Agent-addon && pnpm build` 通过。
- 游戏内手持原版命令方块并使用后能打开主面板。
- 通过面板发送消息后，Python 侧能收到等价于 `AGENT 聊天 <消息>` 的请求。
- 聊天记录页面能看到 UI 发出的消息和本地记录。
- 设置页面保存后，再次打开面板能读取保存值。
- 统计页面能展示发送次数、打开次数、最近活跃时间等基础数据。

---

## 4. 总体架构

```text
玩家物品使用入口
  -> world.afterEvents.itemUse
  -> 判断 itemStack.typeId === "minecraft:command_block"
  -> openAgentUi
  -> UI 主面板
  -> 发送消息 / 历史记录 / 设置 / 统计

发送消息
  -> UI 状态记录 user 消息
  -> 复用现有聊天命令路径
  -> Python WebSocket Server
  -> Agent Worker
  -> Minecraft tellraw 或 scriptevent 响应

响应同步（第二阶段）
  -> Python ConnectionManager
  -> scriptevent mcbeai:ui_event <json>
  -> Addon UI event router
  -> UI 状态记录 assistant 消息
```

第一阶段的关键点是复用既有命令链路，避免同时改 Python 和 addon 两端协议。第二阶段再增加 Python 到 addon 的 UI 同步事件，使历史记录能包含完整 AI 响应。

---

## 5. 文件规划

### 5.1 Addon UI 文件

修改：

- `MCBE-AI-Agent-addon/scripts/ui/entry.ts`
  - 注册 UI 入口物品事件。
  - 负责从 `itemUse` 事件进入 UI。
  - 不承载具体面板逻辑。

- `MCBE-AI-Agent-addon/scripts/ui/state.ts`
  - 扩展 UI 状态模型。
  - 维护聊天记录、设置、统计。
  - 提供持久化读写函数。

- `MCBE-AI-Agent-addon/scripts/ui/panels/agentConsole.ts`
  - 改造为主面板。
  - 只负责路由到各子面板。

创建：

- `MCBE-AI-Agent-addon/scripts/ui/commands.ts`
  - 判断 UI 入口物品。
  - 构造发送给现有聊天链路的命令文本。

- `MCBE-AI-Agent-addon/scripts/ui/storage.ts`
  - 封装 dynamic property 的 JSON 读写。
  - 控制保存数据长度和默认值。

- `MCBE-AI-Agent-addon/scripts/ui/history.ts`
  - 聊天记录类型。
  - 追加、截断、分页、清空。

- `MCBE-AI-Agent-addon/scripts/ui/stats.ts`
  - 统计类型。
  - 打开次数、发送次数、响应片段数、最近活跃时间。

- `MCBE-AI-Agent-addon/scripts/ui/panels/chatInput.ts`
  - 消息输入表单。
  - 发送后写入本地历史。

- `MCBE-AI-Agent-addon/scripts/ui/panels/historyPanel.ts`
  - 历史记录分页面板。
  - 支持清空历史。

- `MCBE-AI-Agent-addon/scripts/ui/panels/settingsPanel.ts`
  - 设置表单。
  - 支持保存偏好。

- `MCBE-AI-Agent-addon/scripts/ui/panels/statsPanel.ts`
  - 统计展示面板。

- `MCBE-AI-Agent-addon/scripts/ui/forms/formAdapter.ts`
  - 第一阶段封装传统表单。
  - 第二阶段替换为 DDUI 实现时，尽量不影响业务面板。

### 5.2 Python 文件

第一阶段不改 Python 业务代码。

第二阶段再考虑修改：

- `services/websocket/connection.py`
  - 在 `StreamChunk` 发送时额外同步 UI 事件。

- `models/minecraft.py`
  - 如需专用 `scriptevent mcbeai:ui_event`，增加创建函数或复用 `create_scriptevent`。

- `docs/addon-bridge-protocol.md`
  - 补充 UI 同步事件协议。

---

## 6. 状态模型设计

### 6.1 聊天记录

```ts
export type ChatRole = "user" | "assistant" | "system";

export type ChatHistoryItem = {
  id: string;
  role: ChatRole;
  content: string;
  createdAt: number;
  source: "ui" | "python" | "system";
};
```

规则：

- 内存中最多保留 `maxHistoryItems` 条，默认 30 条。
- 持久化时只保存最近 10 条摘要。
- 单条内容展示时截断到 120 个字符。
- 历史页每页展示 5 条，避免表单过长。

### 6.2 UI 设置

```ts
export type AgentUiSettings = {
  autoSaveHistory: boolean;
  maxHistoryItems: number;
  showToolEvents: boolean;
  responsePreviewLength: number;
  defaultDelivery: "tellraw" | "scriptevent";
};
```

默认值：

```ts
{
  autoSaveHistory: true,
  maxHistoryItems: 30,
  showToolEvents: true,
  responsePreviewLength: 120,
  defaultDelivery: "tellraw",
}
```

第一阶段 `defaultDelivery` 只作为 UI 偏好保存，不强行切换 Python 侧行为。

### 6.3 统计信息

```ts
export type AgentUiStats = {
  openCount: number;
  sentCount: number;
  localHistoryCount: number;
  responseChunkCount: number;
  lastOpenedAt: number;
  lastSentAt: number;
};
```

第一阶段 `responseChunkCount` 可以保持为 0，并在统计页标注「响应同步未启用」。第二阶段接入 Python -> Addon UI 事件后再累加。

### 6.4 根状态

```ts
export type AgentUiState = {
  bridgeStatus: ObservableLike<BridgeStatus>;
  lastPrompt: ObservableLike<string>;
  lastResponsePreview: ObservableLike<string>;
  history: ChatHistoryItem[];
  settings: AgentUiSettings;
  stats: AgentUiStats;
};
```

---

## 7. 面板流程设计

### 7.1 主面板

主面板展示：

```text
MCBE AI Agent

桥接状态：ready / connecting / disconnected
最近问题：...
最近响应：...
历史条数：...
发送次数：...

[发送消息]
[聊天记录]
[设置]
[统计信息]
[刷新]
[关闭]
```

按钮行为：

- `发送消息`：打开输入表单。
- `聊天记录`：打开历史分页。
- `设置`：打开设置表单。
- `统计信息`：打开统计面板。
- `刷新`：重新打开主面板。
- `关闭`：结束流程。

### 7.2 发送消息表单

使用 `ModalFormData`：

- `textField("消息内容", "输入要发送给 AI 的内容", { defaultValue })`
- `toggle("发送后返回主面板", { defaultValue: true })`
- `submitButton("发送")`

提交规则：

- 空消息：提示玩家「消息不能为空」，返回输入表单。
- 非空消息：写入本地历史，增加发送统计，触发现有命令链路。
- 发送后根据 toggle 决定返回主面板或关闭。

### 7.3 聊天记录面板

使用 `ActionFormData`：

- body 展示当前页历史摘要。
- 按钮：上一页、下一页、清空历史、返回主面板。

分页规则：

- 每页 5 条。
- 最新消息靠前。
- 内容过长时截断。

### 7.4 设置面板

使用 `ModalFormData`：

- `toggle("自动保存历史", defaultValue)`
- `slider("历史保留条数", 10, 50, { defaultValue, valueStep: 5 })`
- `toggle("显示工具事件", defaultValue)`
- `slider("响应预览长度", 60, 240, { defaultValue, valueStep: 20 })`
- `dropdown("默认响应方式", ["tellraw", "scriptevent"], defaultValueIndex)`

保存后：

- 更新 `uiState.settings`。
- 写入 dynamic property。
- 返回主面板并显示保存成功。

### 7.5 统计面板

使用 `ActionFormData.body` 展示：

```text
打开面板次数：12
发送消息次数：4
本地历史条数：8
响应片段数：0（响应同步未启用）
最近打开：2026-04-19 20:10:30
最近发送：2026-04-19 20:12:01
```

按钮：

- 返回主面板
- 重置统计
- 关闭

---

## 8. 发送消息实现策略

### 8.1 第一阶段推荐方案

UI 提交后，通过游戏内聊天命令复用 Python 现有解析逻辑：

```text
AGENT 聊天 <玩家输入内容>
```

实现上优先使用玩家可执行命令路径。如果 Minecraft Script API 无法直接让真实玩家发送聊天消息，则改为：

- 用 `player.sendMessage` 给玩家提示「已记录，请在聊天框发送」作为降级。
- 或使用现有桥接能力发起 Python 可识别事件，但这会扩大第一阶段范围。

推荐先尝试最小可行路径，并在游戏内验证真实行为。

### 8.2 第二阶段增强方案

新增 UI 专用事件：

```text
scriptevent mcbeai:ui_event {"type":"assistant_chunk","content":"...","sequence":1}
```

Addon 侧新增 UI event router：

- 监听 `system.afterEvents.scriptEventReceive`。
- 识别 `mcbeai:ui_event`。
- 将响应片段写入 `uiState.history`。
- 更新 `lastResponsePreview` 和 `responseChunkCount`。

该方案需要修改 Python 响应发送路径，放在第一版面板稳定后执行。

---

## 9. 持久化策略

### 9.1 Dynamic Property Key

建议使用玩家维度保存：

- `mcbeai:ui_settings`
- `mcbeai:ui_history`
- `mcbeai:ui_stats`

保存位置：

- 设置和历史优先写入 `player.setDynamicProperty`。
- 全局默认配置可在后续写入 `world.setDynamicProperty`。

### 9.2 JSON 读写规则

- 读取失败时返回默认值，并给玩家发送简短提示。
- 写入前截断历史，避免 JSON 字符串过长。
- 保存失败时不阻塞 UI 主流程，只提示「设置保存失败，本次仅内存生效」。

### 9.3 数据版本

持久化根对象包含版本号：

```ts
export type PersistedAgentUiData = {
  version: 1;
  settings: AgentUiSettings;
  history: ChatHistoryItem[];
  stats: AgentUiStats;
};
```

第一阶段只支持 `version: 1`。

---

## 10. 开发任务拆分

当前追踪状态（2026-04-19）：

- 代码实现：已完成。
- Addon 构建：已通过 `cd MCBE-AI-Agent-addon && npm exec -- pnpm -- build`。
- Addon 测试：已通过 `cd MCBE-AI-Agent-addon && npm exec -- pnpm -- test`，共 22 个用例。
- 规格审查：已通过子代理静态规格审查。
- 游戏内手测：尚未执行，所有实机验证项保持未勾选。

### 任务 1：注册 UI 入口物品

文件：

- 修改：`MCBE-AI-Agent-addon/scripts/ui/entry.ts`
- 创建：`MCBE-AI-Agent-addon/scripts/ui/commands.ts`

步骤：

- [x] 增加 `isUiTriggerItem(itemTypeId: string): boolean`。
- [x] 第一阶段只匹配原版命令方块 `minecraft:command_block`。
- [x] 在 `registerUiEntry()` 中订阅 `world.afterEvents.itemUse`。
- [x] 命中目标物品时调用 `openAgentUi(event.source)`。
- [x] 补充 per-player 冷却和 in-flight 锁，避免长按或连点导致重复打开。
- [x] 执行 `cd MCBE-AI-Agent-addon && npm exec -- pnpm -- build`。
- [ ] 进入游戏后手持原版命令方块并使用，确认能打开当前面板。（待游戏内手测）

### 任务 2：扩展 UI 状态和持久化

文件：

- 修改：`MCBE-AI-Agent-addon/scripts/ui/state.ts`
- 创建：`MCBE-AI-Agent-addon/scripts/ui/storage.ts`
- 创建：`MCBE-AI-Agent-addon/scripts/ui/history.ts`
- 创建：`MCBE-AI-Agent-addon/scripts/ui/stats.ts`

步骤：

- [x] 定义 `ChatHistoryItem`、`AgentUiSettings`、`AgentUiStats`。
- [x] 实现默认设置和默认统计。
- [x] 实现历史追加、截断、分页、清空。
- [x] 实现 dynamic property JSON 读取。
- [x] 实现 dynamic property JSON 写入。
- [x] 在 `openAgentUi(player)` 前加载玩家持久化状态。
- [x] 面板关闭或设置保存时写入玩家状态。
- [x] 执行 `cd MCBE-AI-Agent-addon && npm exec -- pnpm -- build`。
- [ ] 游戏内打开面板两次，确认打开次数递增。（待游戏内手测）

### 任务 3：改造主面板

文件：

- 修改：`MCBE-AI-Agent-addon/scripts/ui/panels/agentConsole.ts`
- 创建：`MCBE-AI-Agent-addon/scripts/ui/forms/formAdapter.ts`

步骤：

- [x] 将当前主面板改造成「状态摘要 + 功能入口」。
- [x] 增加按钮：发送消息、聊天记录、设置、统计信息、刷新、关闭。
- [x] 根据 `response.selection` 路由到不同子面板。
- [x] 所有子面板返回时重新打开主面板。
- [x] 执行 `cd MCBE-AI-Agent-addon && npm exec -- pnpm -- build`。
- [ ] 游戏内点击每个按钮，确认不会报错或卡死。（待游戏内手测）

### 任务 4：实现发送消息面板

文件：

- 创建：`MCBE-AI-Agent-addon/scripts/ui/panels/chatInput.ts`
- 修改：`MCBE-AI-Agent-addon/scripts/ui/panels/agentConsole.ts`

步骤：

- [x] 使用 `ModalFormData` 创建消息输入表单。
- [x] 空消息时提示玩家并返回输入表单。
- [x] 非空消息按 `autoSaveHistory` 设置写入本地历史，角色为 `user`。
- [x] 增加 `sentCount` 和 `lastSentAt`。
- [x] 设置 `lastPrompt`。
- [x] 给出等价 `AGENT 聊天 <消息>` 的降级提示。
- [x] 执行 `cd MCBE-AI-Agent-addon && npm exec -- pnpm -- build`。
- [ ] 游戏内通过 UI 发送「你好」，确认 Python 或降级提示行为符合预期。（待游戏内手测）

### 任务 5：实现聊天记录面板

文件：

- 创建：`MCBE-AI-Agent-addon/scripts/ui/panels/historyPanel.ts`
- 修改：`MCBE-AI-Agent-addon/scripts/ui/panels/agentConsole.ts`

步骤：

- [x] 展示最近历史，最新消息靠前。
- [x] 每页展示 5 条。
- [x] 增加上一页和下一页。
- [x] 增加清空历史按钮。
- [x] 清空时写入 dynamic property。
- [x] 执行 `cd MCBE-AI-Agent-addon && npm exec -- pnpm -- build`。
- [ ] 游戏内发送 6 条本地消息，确认分页可用。（待游戏内手测）

### 任务 6：实现设置面板

文件：

- 创建：`MCBE-AI-Agent-addon/scripts/ui/panels/settingsPanel.ts`
- 修改：`MCBE-AI-Agent-addon/scripts/ui/panels/agentConsole.ts`

步骤：

- [x] 使用 `ModalFormData` 展示设置项。
- [x] 保存 `autoSaveHistory`。
- [x] 保存 `maxHistoryItems`。
- [x] 保存 `showToolEvents`。
- [x] 保存 `responsePreviewLength`。
- [x] 保存 `defaultDelivery`。
- [x] 保存后立即写入 dynamic property。
- [x] 执行 `cd MCBE-AI-Agent-addon && npm exec -- pnpm -- build`。
- [ ] 游戏内修改设置后重新打开面板，确认配置被读取。（待游戏内手测）

### 任务 7：实现统计面板

文件：

- 创建：`MCBE-AI-Agent-addon/scripts/ui/panels/statsPanel.ts`
- 修改：`MCBE-AI-Agent-addon/scripts/ui/panels/agentConsole.ts`

步骤：

- [x] 展示打开次数、发送次数、本地历史条数、响应片段数。
- [x] 展示最近打开和最近发送时间。
- [x] 标注第一阶段响应同步状态。
- [x] 增加重置统计按钮。
- [x] 执行 `cd MCBE-AI-Agent-addon && npm exec -- pnpm -- build`。
- [ ] 游戏内打开统计页，确认数值随操作变化。（待游戏内手测）

### 任务 8：补充 README 与联调说明

文件：

- 修改：`README.md`
- 可选修改：`docs/addon-bridge-protocol.md`

步骤：

- [x] 补充 UI 入口物品说明。
- [x] 补充第一阶段能力边界。
- [x] 补充游戏内验证步骤。
- [x] 补充 DDUI API 当前不可直接使用的说明。
- [x] 执行 `git diff -- README.md docs/addon-bridge-protocol.md` 检查文档变更。

---

## 11. 轻量验证策略

### 11.1 每轮必须执行

只要修改 addon TypeScript：

```bash
cd MCBE-AI-Agent-addon && pnpm build
```

预期：

```text
TypeScript 编译通过，lib/scripts 下生成对应 JS 文件。
```

### 11.2 选择性执行

如果修改了纯函数模块，例如 `history.ts`、`storage.ts`、`commands.ts`：

```bash
cd MCBE-AI-Agent-addon && pnpm test -- history
```

或：

```bash
cd MCBE-AI-Agent-addon && pnpm test -- commands
```

只有在新增测试文件时执行对应 Vitest，不强制跑完整测试集。

### 11.3 暂不执行

第一阶段不改 Python 业务代码时，不跑完整 `pytest`。

如果第二阶段修改 `services/websocket/connection.py` 或协议模型，再执行相关测试：

```bash
pytest tests/test_addon_bridge_protocol.py tests/test_addon_bridge_service.py -v
```

### 11.4 游戏内手测清单

- [ ] 启动 Python 服务。
- [ ] 进入 Minecraft 世界并连接 WebSocket。
- [ ] 手持原版命令方块并使用，确认主面板打开。
- [ ] 点击「发送消息」，输入「你好」。
- [ ] 确认聊天命令链路或降级提示可见。
- [ ] 打开「聊天记录」，确认消息已记录。
- [ ] 修改设置，关闭并重新打开面板，确认设置保留。
- [ ] 打开「统计信息」，确认打开次数和发送次数变化。
- [ ] 输入原有 `AGENT 聊天 你好`，确认旧命令仍可用。

---

## 12. 风险与处理

### 12.1 DDUI 类型不可用

风险：直接引入 `CustomForm` 或 `Observable` 会导致编译失败。

处理：第一阶段只用 `ActionFormData` / `ModalFormData`。所有业务面板通过 `formAdapter.ts` 使用表单能力，等待官方类型可用后再替换适配层。

### 12.2 入口事件选择不当导致 UI 或聊天链路异常

风险：如果继续使用 `chatSend` 作为入口，会引入测试版接口依赖，并破坏现有聊天链路；如果在 read-only 上下文直接打开表单，也可能报错。

处理：第一阶段只使用 `world.afterEvents.itemUse` 监听原版命令方块物品，不再把 UI 打开逻辑绑定到聊天事件。

### 12.3 UI 发送消息无法模拟真实聊天

风险：Script API 不一定允许 addon 伪造真实玩家聊天消息进入 WebSocket 的 `PlayerMessage` 链路。

处理：先在游戏内验证。如果不可行，第一阶段采用显式降级提示；第二阶段再增加 UI 专用桥接事件，由 Python 识别 addon 发出的 UI 请求。

### 12.4 Dynamic Property 数据过大

风险：保存完整聊天记录会导致 JSON 过长或写入失败。

处理：持久化只保存最近 10 条摘要，内存中按设置保留 10 到 50 条。

### 12.5 多玩家状态串扰

风险：全局 `uiState` 会导致多个玩家共享最近问题和历史。

处理：将状态改为按玩家维度读取和保存。内存中可用 `Map<string, AgentUiState>`，key 使用 `player.id` 或 `player.name`；持久化写入 player dynamic property。

---

## 13. 第二阶段：真正 DDUI 切换计划

触发条件：

- 本地 `@minecraft/server-ui` 类型声明中出现 `CustomForm` 和 `Observable`。
- 游戏运行时确认 `CustomForm.create()` 可用。
- 第一阶段传统表单面板已经在游戏内验证稳定。

切换策略：

- 保留 `state.ts` 的业务状态结构。
- 在 `forms/formAdapter.ts` 中新增 DDUI 实现。
- 设置页优先切换为 `CustomForm`，因为它最需要 `Observable` 的客户端写回能力。
- 主面板和历史页可继续使用 `ActionFormData`，除非 DDUI 能明显改善体验。

DDUI 设置页目标形态：

```ts
CustomForm.create(player, "MCBE AI Agent 设置")
  .toggle("自动保存历史", autoSaveHistory)
  .slider("历史保留条数", maxHistoryItems, 10, 50, {
    step: 5,
    description: "控制本地聊天记录数量",
  })
  .toggle("显示工具事件", showToolEvents)
  .closeButton()
  .show();
```

---

## 14. 推荐提交顺序

建议按可运行切片提交：

```bash
git commit -m "feat(addon-ui): 注册游戏内面板入口"
git commit -m "feat(addon-ui): 添加聊天面板状态与持久化"
git commit -m "feat(addon-ui): 实现主面板与消息输入"
git commit -m "feat(addon-ui): 添加历史记录和设置面板"
git commit -m "feat(addon-ui): 添加统计面板和联调文档"
```

如果开发节奏需要更快，可以合并为 2 次提交：

```bash
git commit -m "feat(addon-ui): 实现游戏内聊天面板"
git commit -m "docs(addon-ui): 补充 DDUI 面板联调说明"
```

---

## 15. 执行检查点

第一检查点：入口可打开。

- [ ] 手持原版命令方块并使用，能打开主面板。（待游戏内手测）
- [x] 旧聊天命令不受影响：未新增聊天监听，未修改 Python 聊天命令解析。

第二检查点：本地状态可用。

- [x] 设置可保存：已写入玩家 dynamic property。
- [x] 历史可分页：已实现每页 5 条、上一页、下一页和清空。
- [x] 统计会更新：已实现打开次数、发送次数、历史条数、响应片段数和重置。
- [ ] 游戏内确认设置、历史、统计在真实客户端可用。（待游戏内手测）

第三检查点：发送链路可用。

- [x] UI 输入给出明确降级提示：提示玩家发送等价 `AGENT 聊天 <消息>`。
- [ ] 游戏内没有脚本报错。（待游戏内手测）

第四检查点：文档完成。

- [x] README 有入口物品和限制说明。
- [x] 本文档风险项与实际实现一致。
