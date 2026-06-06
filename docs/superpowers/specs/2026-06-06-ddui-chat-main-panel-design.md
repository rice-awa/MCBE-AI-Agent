# DDUI 实时聊天主面板设计

## 背景

当前 Addon UI 已迁移到 `@minecraft/server-ui` beta 的 DDUI `CustomForm` 适配层：主面板、发送页、历史页、设置页、统计页分别由独立面板和路由串联。用户发送消息后需要离开发送页再回到主面板，当前对话体验不够连续。

Microsoft Creator 文档确认 DDUI `CustomForm` 支持将 `Observable` 绑定到 `label`、`textField`、按钮状态等控件，并可在表单保持打开时实时更新；表单关闭后需要清理相关定时器或引用。现有 `responseSync.ts` 已通过 `setActiveUiState()` 维护活跃 UI 状态，并在收到 `mcbeai:ai_resp` 分片重组后写入历史和响应预览。

## 目标

- 将主面板重构为实时对话面板。
- 主面板打开时载入最近历史，并展示最近 10 条对话。
- 主面板内直接输入和发送消息，发送后不关闭表单，输入框清空，等待下一轮。
- AI 响应到达后实时追加到主面板记录。
- 保持主面板简洁：只保留聊天、设置入口、统计入口和关闭。
- 设置页与统计页继续作为两个独立页面。
- 清空历史操作放到设置页。

## 非目标

- 不实现完整历史分页入口。
- 不重写 Python 服务、WebSocket 协议或 AI 响应分片协议。
- 不新增复杂主题、布局系统或多会话管理。
- 不改变历史持久化格式，除非实现中发现必须做兼容字段扩展。

## 推荐方案

采用集中改造方案：把 `showAgentConsole()` 改为实时聊天主面板，保留 `settingsPanel` 和 `statsPanel` 两个独立入口，移除主路由中的独立发送页与历史分页入口。

相较于新增平行 `chatPanel` 或全面重写路由，此方案改动集中、状态链路最短，并直接复用当前 DDUI `Observable`、`sendUiChatMessage()`、历史保存和响应同步能力。

## 主面板结构

`showAgentConsole(player, uiState)` 构建一个 `CustomForm`：

1. `closeButton()`。
2. 状态摘要 label：桥接状态、历史条数、发送次数。
3. 实时对话 label：绑定 `conversationBody` Observable，展示最近 10 条历史，按时间从旧到新排列，便于阅读当前上下文。
4. 输入框：`textField("消息内容", messageValue)`，绑定 client-writable Observable。
5. 按钮：
   - `发送`：发送当前输入，保持表单打开。
   - `设置`：关闭当前表单并路由到设置页。
   - `统计信息`：关闭当前表单并路由到统计页。
   - `关闭`：保存状态并退出 UI 循环。

主面板不再显示“发送消息”和“聊天记录”按钮。

## 发送行为

点击“发送”时：

1. 读取并 trim 输入内容。
2. 如果为空，发送玩家提示，表单保持打开。
3. 如果非空：
   - 将 user 历史项追加到 `uiState.history`。
   - 更新 `lastPrompt`、`bridgeStatus`、发送统计和本地历史计数。
   - 刷新 `conversationBody` Observable。
   - 清空 `messageValue`，使输入框等待下一轮。
   - 调用现有 `sendUiChatMessage(player.name, message)`。
   - 成功则设置桥接状态为 `sent`；失败则设置为 `error` 并提示玩家手动发送命令。
   - 保存 UI 状态；保存失败只提示玩家，不关闭表单。

发送后不返回主面板，也不关闭表单。

## 实时响应同步

继续使用现有 `responseSync.ts` 监听 `mcbeai:ai_resp` 分片：

1. 分片按 `id/i/n/p/r/c` 重组。
2. 找到目标玩家。
3. 写入活跃 `AgentUiState` 和持久化状态。
4. 对 assistant 响应更新 `lastResponsePreview`。

为了让主面板实时刷新，`AgentUiState` 增加一个运行期字段，例如 `refreshConversation?: () => void`。该字段不参与持久化，只在主面板打开期间赋值；`responseSync` 在写入活跃状态后调用它。主面板关闭或跳转时清除该回调，避免持有失效表单引用。

`refreshConversation()` 负责重新生成最近 10 条历史文本并写入 `conversationBody`，同时刷新状态摘要。

## 最近 10 条展示规则

- 数据源：`uiState.history.slice(-10)`。
- 排序：按存储顺序从旧到新显示。
- 格式：复用或扩展现有 `summarizeHistoryItem()`，保持 `[role/source] 内容` 的紧凑格式。
- 超长内容：继续使用 `settings.responsePreviewLength` 截断单条内容。
- 空历史：显示“暂无对话记录。输入消息开始聊天。”。

## 设置页调整

设置页继续独立，保留当前设置项：

- 自动保存历史。
- 历史保留条数。
- 显示工具事件。
- 响应预览长度。
- 默认响应方式。

新增“清空历史”按钮：

- 清空 `uiState.history`。
- 同步 `stats.localHistoryCount`。
- 清空或刷新主面板相关预览字段。
- 保存状态并提示玩家。
- 返回主面板。

如果从主面板跳转设置页，设置页关闭后仍回到主面板；点击关闭 UI 时才退出。

## 统计页调整

统计页继续独立，展示当前统计信息并保留“重置统计”“返回主面板”“关闭”。统计页不承担清空历史职责。

## 路由调整

`AgentPanelRoute` 可简化为：

- `main`
- `settings`
- `stats`
- `close`

`chatInput` 和 `history` 路由从 UI 循环中移除。对应旧面板文件可删除，或在实现时确认没有引用后删除。

## 错误处理

- DDUI `CustomForm` 或 `Observable` 不可用时，沿用现有提示：“表单暂时无法打开，请稍后再试。”并保存状态后关闭。
- 发送空消息时只提示，不关闭表单。
- `sendUiChatMessage()` 失败时，设置桥接状态为 `error`，提示玩家手动发送 `AGENT 聊天 <消息>`。
- 保存 DynamicProperty 失败时提示“保存历史失败，本次仅内存生效。”，但不阻断聊天。
- 响应同步 JSON 解析或分片异常继续静默忽略，避免影响游戏体验。

## 测试计划

- 更新 `agent-console.test.ts`：
  - 点击“发送”会追加 user 历史、调用发送链路、清空输入，并保持主面板路由不关闭。
  - 点击“设置”路由到 settings。
  - 点击“统计信息”路由到 stats。
  - 点击“关闭”保存并返回 close。
  - DDUI API 不可用时干净关闭。
- 更新或删除 `chat-input.test.ts` 和 `history-panel.test.ts`，避免测试已移除入口。
- 更新 `settings-panel.test.ts`：覆盖清空历史按钮。
- 补充 `responseSync` 相关测试：活跃状态收到 assistant 响应后会调用主面板刷新回调。
- 保持 `agent-ui-state.test.ts` 覆盖历史追加、截断和摘要格式。
- 运行 `pnpm test` 和 `pnpm build` 验证 Addon。

## 验收标准

- 打开 UI 后直接看到最近 10 条历史。
- 在主面板输入并发送消息后，表单保持打开，输入框清空，用户消息立即出现在实时记录中。
- AI 回复到达后，无需重新打开面板即可追加显示。
- 主面板只有聊天、设置、统计和关闭相关操作。
- 设置页和统计页保持独立。
- 设置页可以清空历史。
- 不再需要通过独立发送页或历史分页页完成日常聊天。