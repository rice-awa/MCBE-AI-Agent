# Design: Addon UI 全面重构（流式打字机 + 会话管理协议化）

## 1. 协议层设计（mcbe-ws-sdk 扩展，可扩展优先）

### 1.1 text_resp frame 扩展（向后兼容）
现状（codec.py:178-208）：frame 固定 6 字段 `{id, i, n, p, r, c}` 紧凑 JSON。
扩展：追加**可选字段**，缺省时输出与旧格式逐字节一致：
```json
{"id":"resp-xxx","i":0,"n":2,"p":"Steve","r":"assistant","c":"...","cid":"chat-...","t":"标题","u":{"i":128,"o":456}}
```
- `cid` = conversation_id（缺省不输出）
- `t` = title（缺省不输出）
- `u` = usage `{i: input_tokens, o: output_tokens}`（缺省不输出）
- 编码函数加 keyword-only 可选参数；`send_response` 透传；Addon `AiRespChunk` 类型同步加可选字段
- 兼容性：旧 Addon 解析器按未知字段忽略处理（JSON 解析天然兼容）；新 Addon 收到缺字段帧 → 归入当前活跃会话桶

### 1.2 session_req / session_resp 事件对
- SDK profile.py 新增常量：`session_request_message_id = "mcbews:session_req"`、`session_response_message_id = "mcbews:session_resp"`（冻结 dataclass，默认值向后兼容）
- 请求帧（Addon → Python）：`scriptevent mcbews:session_req <json>`，格式：
  ```json
  {"request_id":"sess-<hex>","v":1,"action":"list","player_name":"Steve","cid":"chat-...","sid":"..."}
  ```
  可选参数 `cid`（conversation_id，new/switch 用）、`sid`（session_id，save/restore/delete 用）
- 响应帧（Python → Addon）：`scriptevent mcbews:session_resp <json>`，格式：
  ```json
  {"request_id":"sess-<hex>","v":1,"ok":true,"action":"list","data":{...},"error":null}
  ```
  - `data` 按 action 结构化：
    - `list` → `{"conversations":[{"id","short_id","title","message_count","is_active"}]}`
    - `saved` → `{"saved":[{"session_id","title","message_count","updated_at"}]}`
    - `status` → `{"conversation_id","turns","max_history_turns","context_enabled","title_status"}`
    - `new`/`switch` → `{"conversation_id","short_id","title","message_count"}`
    - `save` → `{"session_id"}`
    - `clear`/`restore`/`delete`/`compress` → 文本结果 `{"message"}`
- **版本字段 `v`**：request/response 均带，未来演进用

### 1.3 UI_CHAT 上行扩展
现状：payload `{"player":"Steve","message":"hello"}`。
扩展：加可选字段 `cid`（conversation_id）。SDK 侧 reassemble 后透传给 host；host 在 `handle_ui_chat` 中带 cid 时按 `AGENT 对话 switch <cid>` 语义先切换再聊天（或传入 ChatRequest，实现时按最小侵入选型——优先复用现有 `handle_chat` 路径，在 broker 层以 cid 覆盖活跃会话）。

### 1.4 会话操作实现（Python 侧）
- `command_handlers.py` 的 `_handle_conversation` 已实现全部 10 个 action（文本返回）。新增协议入口 `handle_session_req`：解析 session_req JSON → 按 action 调用 `_handle_conversation` 同一套逻辑 → 将 TellrawMessage 文本 + 结构化数据包装为 session_resp 回发。
- 结构化数据来源：
  - `list` → `core/session.py:213-234 list_player_conversation_metadata`（short_id/title）+ `:374-387 list_player_conversations`（message_count）
  - `status` → `core/session.py` active conversation + metadata
  - `saved` → `core/conversation.py:863-910 list_conversations`
- 非阻塞：host 侧 hook 内 fire-and-forget（`asyncio.create_task`），与现有 `on_ui_chat_reassembled` 模式一致

## 2. Addon 侧架构

### 2.1 数据结构 v2（分桶镜像）
```ts
interface AgentUiStateV2 {
  version: 2;
  activeConversationId: string;          // 默认 "default"
  conversations: Record<string, ConversationBucket>;  // 分桶，上限 20
  conversationOrder: string[];           // 会话列表顺序（最近活跃优先）
  settings: AgentUiSettings;             // 删 2 项保留 3 项
  stats: UiStats;                        // 含 token 累计
}
interface ConversationBucket {
  id: string;
  shortId: number;
  title: string;                         // 本地缓存，session_resp 刷新
  history: ChatHistoryItem[];            // 内存全量
  lastActiveAt: number;
}
```
- DynamicProperty key 不变（`mcbeai:ui_state`），version 1→2
- 迁移：v1 的 `history` 单数组 → `conversations["default"].history`，`activeConversationId="default"`
- 持久化：每桶最近 100 条（PERSISTED_HISTORY_LIMIT 20→100），桶数上限 20（超出淘汰最旧桶）

### 2.2 流式打字机（responseSync 重构）
- `chunkBuffers` 保留（乱序重组用），但新增**增量追加**：每收到块 → 若 UI 处于生成中状态，将"排序后新增的连续前缀"追加到 `streamingChunk` ObservableString
- 新状态：`streaming: {conversationId, assembled: string, startedAt, done: boolean}`，`▌` 光标在生成中附加
- 状态行 Observable：`⏳ 生成中… (x chars)` → 完成时 `✓ 完成 · 本轮 token in/out`（usage 从块 `u` 字段取，完成帧带）
- 发送禁用：`isStreaming` 标志，agentConsole 发送按钮绑定
- 关闭 UI（`clearActiveUiState`）：若 streaming 未完成，将已收部分作为完整条目入库（标记 source:"python"）
- 切面板：流继续写入共享 state，返回主面板时 ObservableString 重建显示

### 2.3 sessionClient.ts（新增）
- `requestSession(action, params) → Promise<SessionResp>`：生成 request_id（`sess-<timestamp>-<rand>`），发送 `scriptevent mcbews:session_req`，注册 pending future，监听 `mcbews:session_resp` 按 request_id 匹配唤醒
- 超时（如 5s）：resolve 为 `{ok:false, error:"会话同步不可用（服务端版本过旧）"}`
- 复用 toolPlayer 发送路径（`sendUiChatMessage` 同源）或直接 `runCommand` scriptevent（按现有实现选型）

### 2.4 面板（scripts/ui/panels/）
- `agentConsole.ts`：重构主面板（标题行/状态行/5 条完整/输入+四按钮/生成中禁用发送）
- `conversationPreviewPanel.ts`（新）：全量预览，每页 5 条倒序翻页，showToolEvents 控制 tool 显示
- `conversationListPanel.ts`（新）：会话列表，每页 5 条，点击切换，＋新会话
- `sessionFilesPanel.ts`（新）：保存当前会话 + saved 列表 + 恢复/删除（二次确认）
- `morePanel.ts`：4 入口
- `settingsPanel.ts`：删 responsePreviewLength/maxHistoryItems
- `statsPanel.ts`：token 统计（会话/全局/合计）
- 路由 `routes.ts`：新增 3 个面板 route

## 3. 数据流

```
玩家点发送 → agentConsole（生成中禁用）→ sendUiChatMessage(cid) → Python handle_ui_chat(cid)
Python worker 回复 → ai_response_sync{conversation_id, usage} → broker_bridge._ai_sync
→ SDK text_resp frame{cid,t,u} → Addon responseSync 逐块追加 streamingChunk（▌光标）
→ 完成帧 → 入库（分桶）→ 状态行 ✓ token → 持久化
会话操作 → sessionClient.requestSession → scriptevent session_req → Python handle_session_req
→ session_resp → 匹配 request_id → 更新会话列表/标题缓存 → 面板刷新
```

## 4. 兼容与回滚
- 协议：旧 Addon 忽略新字段；新 Addon 对缺字段帧归 default 桶；session_req 无响应提示不可用（不崩溃）
- 数据：v1→v2 迁移幂等（version 检查 + normalize）；回滚 = 旧版本 Addon 读 v2 数据时 version 不匹配 → 重置为默认（可接受，用户已确认不做过多向后兼容）
- 分支：addon + 父仓库各建 feature/ddui-streaming-sessions，SDK 改动随父仓库或独立 PR（用户已批准直接改源码）
