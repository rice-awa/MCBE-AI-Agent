# PR #24 审查问题修复计划

- 创建日期：2026-06-19
- 修复分支：`fix/pr24-review-followups`
- 输入报告：`docs/superpowers/reports/2026-06-18-pr-24-code-review.md`
- Python/测试环境：复用仓库虚拟环境 `./.venv/bin/python`

## 目标

修复 PR #24 合并后审查报告列出的所有风险点，并新增多人 AI 聊天广播控制指令：默认 AI 对话仅触发玩家可见；可开启全服广播，或只广播指定玩家的 AI 聊天；全服广播时向 `@a` 提示 `AI正在为xxx思考`。

## 当前代码判断

- 普通玩家命令响应大量通过 `create_error_message()` / `create_info_message()` / `create_success_message()` 默认发往 `@a`，需要改成玩家私聊目标。
- AI 流式内容目前通过 `StreamChunk.player_name` 定向玩家，适合扩展一个独立 `target`/广播字段，避免把真实玩家名和发送目标混用。
- `ConnectionState` 是多人共享 WebSocket 连接的自然落点，适合保存连接级 AI 广播策略。
- `ChatRequest.conversation_generation` 当前混合了承载“历史 revision”和“管理操作失效 epoch”的职责，会导致连续聊天和 clear/switch stale guard 互相冲突，需要拆分。
- `FlowControlMiddleware` 已有 tellraw 按最终 commandLine 检查的思路，但 `create_tellraw()` 仍手写 JSON，AI response/scriptevent/raw command 仍有预算与校验缺口。
- `ChatAgentManager` 已有 `reset()` 和 fallback 状态，但缺少 reload 后恢复 MCP toolsets 的公开方法。

## 新指令设计

新增命令：`AGENT 广播`

建议默认配置：

```python
"AGENT 广播": {
    "type": "ai_broadcast",
    "aliases": ["AGENT broadcast", "AI 广播", "AI broadcast"],
    "description": "控制多人 AI 聊天广播",
    "usage": "<状态/关闭/全服 开启|关闭/玩家 <玩家名> 开启|关闭>"
}
```

语义：

- `AGENT 广播 状态`：显示当前连接的 AI 广播策略。
- `AGENT 广播 关闭`：关闭全服广播并清空指定玩家广播名单，AI 对话仅自己可见。
- `AGENT 广播 全服 开启`：所有玩家的 AI 聊天响应发往 `@a`。
- `AGENT 广播 全服 关闭`：关闭全服广播，但保留指定玩家广播名单。
- `AGENT 广播 玩家 Alice 开启`：只把 Alice 触发的 AI 聊天响应广播给 `@a`。
- `AGENT 广播 玩家 Alice 关闭`：取消 Alice 的指定广播。

实现约束：

- 广播策略只影响 `AGENT 聊天` 的 tellraw 流式输出和工具调用/工具结果可见性，不改变 Addon UI 历史同步的真实 `player_name`。
- `AGENT 脚本`/scriptevent 不做玩家目标路由，暂不应用该广播策略。
- 全服广播时，在 worker 真正开始处理请求前发送 `SystemNotification(level="info", player_name="@a", message=f"AI正在为{player}思考")`。
- 广播开关当前按 WebSocket 连接生效；项目没有管理员角色模型，因此权限沿用现有“连接已认证即可管理”的规则。

## 修改计划

### 1. 统一玩家命令私聊响应

文件：

- `services/websocket/server.py`
- `services/websocket/minecraft.py`
- `tests/test_websocket_conversation_commands.py`
- 新增或扩展 WebSocket server 命令测试

步骤：

- 增加 `_reply_target(state, player_name)` 和 `_send_player_reply(state, msg, source, player_name)` helper。
- 所有由玩家消息触发的命令响应默认替换为触发玩家目标：auth、login、chat 空内容、队列繁忙、context、conversation、template、setting、mcp、switch_model、help、run_command。
- 保留连接欢迎消息和显式广播消息的 `@a` 语义。
- `handle_mcp()`、`handle_help()`、`handle_login()` 增加 `player_name` 参数，避免走 `state.player_name`。
- 增加多玩家回归测试，断言上述命令响应 `commandLine.startswith("tellraw Alice ")`，且 Bob 不会收到 Alice 的私有状态。

### 2. 新增 AI 广播控制状态与命令

文件：

- `config/settings.py`
- `services/websocket/connection.py`
- `services/websocket/server.py`
- `models/messages.py`
- `services/agent/worker.py`
- `services/websocket/connection.py`
- `tests/test_websocket_command.py`
- 新增 `tests/test_ai_broadcast.py` 或扩展现有 WebSocket 测试

步骤：

- 在 `ConnectionState` 增加 `ai_broadcast_all: bool` 和 `ai_broadcast_players: set[str]`，并提供 `should_broadcast_ai_chat(player_name)`。
- 在命令配置中注册 `AGENT 广播`，路由到 `handle_ai_broadcast()`。
- 在 `ChatRequest` 增加 `broadcast_ai_chat: bool = False`，由 `handle_chat()` 入队时按当前策略捕获。
- 在 `StreamChunk` 增加 `target: str | None = None`，发送层使用 `chunk.target or chunk.player_name` 作为 tellraw 目标。
- worker 对广播请求生成所有可见流式 chunk 时设置 `target="@a"`，但 `ai_response_sync` 仍写真实玩家名。
- worker 开始处理广播请求时发送 `AI正在为xxx思考` 给 `@a`。
- 测试默认私聊、全服开启、全服关闭、指定玩家开启/关闭、广播提示、Addon 同步仍归属原玩家。

### 3. 修复 tellraw JSON 构造与字节预算

文件：

- `models/minecraft.py`
- `services/websocket/flow_control.py`
- `tests/test_flow_control.py`
- `tests/test_websocket_delivery.py`

步骤：

- `MinecraftCommand.create_tellraw()` 改为用 `json.dumps({"rawtext": [{"text": f"{color}{message}"}]}, ensure_ascii=False, separators=(",", ":"))` 构造 rawtext。
- 保留并收紧 target sanitizer，避免玩家名/selector 注入命令结构。
- tellraw 分片继续以最终 `create_tellraw(...).body.commandLine.encode("utf-8") <= 461` 为唯一安全标准。
- 增加测试：尾随反斜杠、连续反斜杠、引号、换行、控制字符、emoji、中文、长玩家名；解析 rawtext JSON 并断言每条 commandLine 不超过 461B。

### 4. 修复 scriptevent / AI response / raw command 预算与校验

文件：

- `models/minecraft.py`
- `services/websocket/flow_control.py`
- `services/websocket/delivery.py`
- `tests/test_flow_control.py`
- `tests/test_websocket_delivery.py`
- `tests/test_websocket_conversation_commands.py`

步骤：

- 在 `MinecraftCommand.create_scriptevent()` 校验 `message_id`，使用白名单：`^[a-z0-9_.-]+:[a-z0-9_./-]+$`。
- `chunk_scriptevent()` 不再使用固定 wrapper 估算，改为按实际 `message_id` 和最终 commandLine 试探分片。
- `chunk_ai_response()` 用最终 `scriptevent mcbeai:ai_resp {json}` 的真实 commandLine 字节数决定每个 chunk，metadata 必须参与预算。
- 处理 `n` 总数变化：先分片，再用实际 total 校验；若 total 位数导致超预算，继续细分并重新校验。
- `chunk_raw_command()` 直接检查 `MinecraftCommand.create_raw(command).body.commandLine` 的 UTF-8 字节数，错误信息改为面向用户的 `raw command too long in bytes (... > 461); cannot be safely chunked`。
- 增加大量引号、反斜杠、换行、emoji、中文、长玩家名、非法 message_id、多字节 raw command 超预算测试。

### 5. 拆分 history revision 与 invalidation epoch

文件：

- `models/messages.py`
- `core/session.py`
- `core/queue.py`
- `services/websocket/server.py`
- `services/agent/worker.py`
- `tests/test_queue_context.py`

步骤：

- 在 session store 中新增 `_conversation_invalidation_epochs`，普通聊天写历史只递增 history revision，不递增 invalidation epoch。
- 在 `ChatRequest` 增加 `conversation_invalidation_epoch`，入队时捕获当前 epoch。
- worker 在玩家锁内读取最新历史，但写回时传入 `expected_invalidation_epoch=request.conversation_invalidation_epoch`。
- clear/switch/new 覆盖已有会话、restore、delete runtime 会话、switch_model 等管理操作递增对应玩家/对话的 invalidation epoch。
- 兼容保留现有 `conversation_generation` 作为 history revision API，避免无关调用一次性大改名。
- 测试两类场景：连续两条聊天共享初始 epoch 仍都写入；聊天入队后 clear/switch 再处理不会重建旧历史。

### 6. 修复 MCP reload 后 fallback 恢复

文件：

- `services/agent/core.py`
- `services/agent/runtime.py`
- `services/websocket/server.py`
- `tests/test_agent_provider_lifecycle.py` 或 `tests/test_mcp.py`

步骤：

- 在 `ChatAgentManager` 增加 `restore_mcp(toolsets)` 或 `refresh_mcp_toolsets(toolsets)`。
- 方法内设置 `_mcp_available=True`，清理 `_fallback_agent`，更新 `_mcp_toolsets`，重建 primary `_agent`。
- 在 `AgentRuntime` 增加 `refresh_mcp_tools(settings)`，从 MCP manager 取最新 toolsets 并调用 agent manager。
- `AGENT MCP reload <name>` 成功后调用 runtime refresh；失败时保持 fallback 状态不变。
- 测试 `mark_mcp_failed()` 后 reload 成功，后续 `get_active_agent()` 不再返回 fallback agent。

### 7. 注销连接时清理 PromptManager 状态

文件：

- `services/websocket/connection.py`
- `tests/test_connection_manager.py` 或 `tests/test_prompt_template.py`

步骤：

- 在 `ConnectionManager.unregister()` 中调用 `get_prompt_manager().clear_connection(str(connection_id))`。
- 调用顺序放在 pending future 清理之后、broker unregister 附近。
- 测试同一连接下两个玩家设置模板/变量，注销后对应 PromptManager 状态清空。

### 8. 收紧 agent 工具广播与 scriptevent 边界

文件：

- `services/agent/tools.py`
- `models/minecraft.py`
- `tests/test_agent_tools.py`

步骤：

- `build_title_commands()`、`build_actionbar_command()`、`build_tellraw_command()` 增加 `target` 参数。
- agent 消息类工具默认使用 `ctx.deps.player_name`，不再硬编码 `@a`。
- 需要全服广播时增加显式 `broadcast: bool = False` 参数，只有 `broadcast=True` 才使用 `@a`。
- `send_script_event()` 复用 `MinecraftCommand.create_scriptevent()` 的 message_id 校验。
- 测试默认目标为触发玩家、显式 broadcast 才是 `@a`、非法 scriptevent ID 返回工具失败。

### 9. 整理历史文档与脆弱测试

文件：

- `docs/superpowers/reports/2026-06-14-tech-debt-scan-plan-review.md`
- `docs/superpowers/plans/2026-06-14-tech-debt-scan-plan-review-fixes.md`
- `tests/test_stream_mode.py`
- `tests/test_flow_control.py`

步骤：

- 在两个 2026-06-14 历史文档顶部添加 superseded/historical 状态说明，指向本计划和 2026-06-18 审查报告。
- 将 flow-control 测试中的精确 chunk 数断言改为不变量：内容可重组、索引/总数一致、每条 commandLine <= 461B。
- 替换 `tests/test_stream_mode.py` 中全局 monkeypatch `builtins.isinstance` 的做法，改为 fake event class 或 adapter 局部测试。

## 验证命令

所有命令使用虚拟环境：

```bash
./.venv/bin/python -m pytest tests/test_websocket_conversation_commands.py -q
./.venv/bin/python -m pytest tests/test_flow_control.py tests/test_websocket_delivery.py -q
./.venv/bin/python -m pytest tests/test_queue_context.py -q
./.venv/bin/python -m pytest tests/test_agent_tools.py tests/test_mcp.py tests/test_prompt_template.py -q
./.venv/bin/python -m pytest -q
```

若涉及前端/Addon UI 同步，只能通过现有 Addon bridge 单元测试验证命令 payload；本仓库没有可直接启动的 MCBE 客户端 UI 自动化环境。

## 建议提交批次

1. 命令私聊目标 + AI 广播控制指令。
2. tellraw/scriptevent/AI response/raw command 字节安全。
3. invalidation epoch stale guard。
4. MCP reload recovery + PromptManager cleanup + agent tools target。
5. 历史文档和脆弱测试整理。
