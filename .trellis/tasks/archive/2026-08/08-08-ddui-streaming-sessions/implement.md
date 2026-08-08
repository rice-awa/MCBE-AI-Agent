# Implement: Addon UI 全面重构（流式打字机 + 会话管理协议化）

## 前置
- 阅读 `.trellis/tasks/08-08-ddui-streaming-sessions/research/` 三份研究文件（addon-ui-current.md / python-protocol-points.md / constraints.md）
- 分支：addon 仓库 + 父仓库各建 `feature/ddui-streaming-sessions`（trellis-implement 不 commit，只写代码）

## 实施顺序（按依赖排序）

### 阶段 1：SDK 协议扩展（mcbe-ws-sdk）
- [ ] 1.1 `src/mcbe_ws_sdk/profiles/mcbews_v1/codec.py`：`encode_text_response_commands` 加可选参数 `conversation_id/title/usage`，frame 输出 `cid/t/u` 可选字段（缺省输出与旧格式一致）
- [ ] 1.2 `src/mcbe_ws_sdk/profiles/mcbews_v1/profile.py`：加 `session_request_message_id` / `session_response_message_id` 常量
- [ ] 1.3 `src/mcbe_ws_sdk/profiles/mcbews_v1/delivery.py`：`send_response` 透传 conversation_id/title/usage
- [ ] 1.4 UI_CHAT 上行：SDK 侧 ui_chat payload 解析支持可选 `cid` 字段并透传给 host（检查 SDK reassemble 处）
- [ ] 1.5 SDK 测试更新（tests 目录）

### 阶段 2：Python 侧透传与会话入口（父仓库）
- [ ] 2.1 `services/agent/worker.py:1113-1119`：ai_response_sync dict 加 `conversation_id`（request.conversation_id）与 `usage`（usage_dict 已在 worker.py:1009）
- [ ] 2.2 `services/gateway/broker_bridge.py`：`_ai_sync` 透传 conversation_id/usage（title 由 Addon 从 session_resp 获取，可不透传）；`_handle` 加 session_resp 分发
- [ ] 2.3 `services/gateway/command_handlers.py`：新增 `handle_session_req`（解析 session_req → 复用 `_handle_conversation` 逻辑 → 结构化包装 session_resp）；hook 侧 fire-and-forget 注册（asyncio.create_task，非阻塞约束）
- [ ] 2.4 `services/gateway/hook.py`（或对应注册点）：监听 `mcbews:session_req` scriptevent
- [ ] 2.5 `docs/addon-bridge-protocol.md`：新增"会话管理协议（session v1）"一节 + 更新线常量表

### 阶段 3：Addon 侧桥接层
- [ ] 3.1 `scripts/bridge/constants.ts`：新增 session_req/resp 事件 id 常量
- [ ] 3.2 `scripts/bridge/sessionClient.ts`（新）：request_id 生成、session_req 发送、session_resp 匹配唤醒、超时（5s，返回"会话同步不可用"）
- [ ] 3.3 `scripts/bridge/responseSync.ts`：逐块增量追加（保留 chunkBuffers 乱序重组，新增 streaming 状态 + 光标 + 状态行）；解析 cid/t/u 可选字段；完成帧入库分桶；isDuplicateUiUserEcho 沿用
- [ ] 3.4 `scripts/bootstrap.ts`：注册 session_resp 监听（幂等）

### 阶段 4：Addon 侧 UI 数据结构
- [ ] 4.1 `scripts/ui/state.ts`：v2 结构（分桶 conversations / activeConversationId / streaming 状态 / settings v2 / stats v2）
- [ ] 4.2 `scripts/ui/history.ts`：分桶 append/查询；streaming 条目支持
- [ ] 4.3 `scripts/ui/storage.ts`：v1→v2 迁移（旧 history 归 default 桶）；PERSISTED_HISTORY_LIMIT 20→100；桶上限 20
- [ ] 4.4 `scripts/ui/stats.ts`：token 累计（会话维度内存 + 全局维度持久化，input/output/合计）

### 阶段 5：Addon 侧面板
- [ ] 5.1 `scripts/ui/panels/agentConsole.ts`：重构（标题行 + 切换入口 / 状态行 / 5 条完整隐藏 tool / 输入 + 四按钮 / 生成中禁用发送 / 新会话不弹确认）
- [ ] 5.2 `scripts/ui/panels/conversationPreviewPanel.ts`（新）：全量预览每页 5 条倒序翻页只读
- [ ] 5.3 `scripts/ui/panels/conversationListPanel.ts`（新）：会话列表每页 5 条点击切换 + 新会话
- [ ] 5.4 `scripts/ui/panels/sessionFilesPanel.ts`（新）：保存当前会话 + saved 列表 + 恢复/删除二次确认
- [ ] 5.5 `scripts/ui/panels/morePanel.ts`：4 入口
- [ ] 5.6 `scripts/ui/panels/settingsPanel.ts`：删 responsePreviewLength/maxHistoryItems
- [ ] 5.7 `scripts/ui/panels/statsPanel.ts`：token 统计展示
- [ ] 5.8 `scripts/ui/panels/routes.ts`：新增 3 route

### 阶段 6：测试与验证
- [ ] 6.1 Addon：运行现有测试（npm test / vitest），修复回归
- [ ] 6.2 Addon：新增测试（迁移逻辑、分桶、流式增量、token 累计、面板翻页）
- [ ] 6.3 父仓库：pytest 运行现有测试，修复回归
- [ ] 6.4 SDK：pytest 运行现有测试
- [ ] 6.5 类型检查：tsc / 语法检查通过

## 验证命令
```bash
# Addon（目录 MCBE-AI-Agent-addon）
npm test          # 或项目实际测试命令（先看 package.json）
npx tsc --noEmit  # 类型检查

# 父仓库
python3 -m pytest -x -q

# SDK（目录 mcbe-ws-sdk）
python3 -m pytest -x -q
```

## 审查门
- 阶段 1/2 完成 → 协议层可独立验证（新旧 Addon 兼容）
- 阶段 3/4 完成 → 桥接层可验证（打字机 + 分桶）
- 阶段 5 完成 → 全功能可验证
- 最后跑 trellis-check 全量复查
