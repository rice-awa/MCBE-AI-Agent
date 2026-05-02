# 多人会话串扰 BUG 修复总结

> 日期: 2026-05-02
> 关联报告: [MULTIPLAYER_BUG_REPORT.md](../report/MULTIPLAYER_BUG_REPORT.md)
> 分支: `feature/ddui-chat-panel`

## 修复目标

解决多人测试中暴露的三个核心问题：
1. 玩家识别错误（`player_name` 用错人）
2. 上下文/对话历史在玩家之间互串
3. UI/聊天框命令被同一连接覆盖，无法区分多个用户

## 根因回顾

MCBE 一个游戏服务器只有一条 `/wsserver` 连接，所有玩家共享同一 `connection_id`。但代码把会话级状态（历史、锁、模板、provider、上下文开关、`state.player_name`）全部按 `connection_id` 单值存储。`state.player_name` 还采用"先到先占"逻辑（`if not state.player_name`），导致第一个发言者把自己的名字烙在所有人的请求上。

## 修复策略：把"会话"键从 `connection_id` 升级为 `(connection_id, player_name)`

下行 WebSocket 路由仍然按 `connection_id`（addon 会按玩家名分发），但所有"上行处理"和"持久化状态"都引入玩家维度。

## 改动清单

### 1. `core/queue.py` — MessageBroker 复合键化
- 新增 `SessionKey = tuple[UUID, str]` 与 `make_session_key()` 工厂函数；空玩家名落到匿名占位 `__anonymous__`，保证旧路径仍可工作。
- `_conversation_histories` 与原 `_connection_locks` 改名为 `_session_locks`，键全部升级为 `SessionKey`。
- 公开 API 都新增 `player_name` 参数：`get_conversation_history`、`set_conversation_history`、`clear_conversation_history`、`get_session_lock`。
- 新增 `list_session_players(connection_id)` 与 `clear_connection_sessions(connection_id)`。后者在 `unregister_connection` 时清理该连接下所有玩家桶，避免内存泄漏。

### 2. `services/websocket/connection.py` — 引入 `PlayerSession`
- 新增 `PlayerSession` dataclass，承载 `context_enabled / current_provider / current_template / custom_variables`。
- `ConnectionState` 移除上述单值字段，改用 `_player_sessions: dict[str, PlayerSession]`，外加 `get_player_session(player_name)` 工厂方法。
- `state.player_name` 保留为"最近一次发言者"指针，仅供日志/兼容路径使用，**多人路由不允许从这里读**。

### 3. `services/websocket/server.py` — 每条消息显式携带玩家名
- `handle_message` 移除"先到先占"逻辑：每次都用 `player_event.sender` 更新，并把 sender 直传给 `handle_command`。
- `handle_command` 增加 `player_name` 形参，并把它向下传递给 `handle_chat / handle_context / handle_template / handle_setting / handle_switch_model`。
- 聊天框命令的 `ai_response_sync` 用 `player_name`，不再从 `state.player_name` 读。
- `_handle_ui_chat_message`：UI 路径同样直传 `player_name`，并在前置环节回流"用户消息"到 Addon UI 历史（与聊天框命令一致）。
- 上下文/模板/变量等命令处理统一改用 `state.get_player_session(player_name)` 与新 broker / PromptManager API。

### 4. `services/websocket/minecraft.py` — `create_chat_request` 接收 `player_name`
- 新增 `player_name` 形参；`use_context / provider` 现在从 `state.get_player_session(sender).context_enabled / current_provider` 读取。

### 5. `services/agent/worker.py` — Worker 锁与历史按玩家分桶
- `_process_request` 用 `broker.get_session_lock(connection_id, request.player_name)`：同玩家串行、跨玩家并行（消除"一个连接=一把锁"的吞吐瓶颈）。
- `_process_request_locked` 内的所有历史读写、`check_and_compress`、`get_context_info` 都带上 `request.player_name`。
- `processing_chat_request` / `chat_response_complete` / `chat_history_loaded` / `auto_compression_triggered` 日志加上 `player` 字段。

### 6. `core/conversation.py` — 对话管理器适配
- `check_and_compress / compress_history` 必须传 `player_name`，按玩家维度查/写历史。
- `save_conversation` / `restore_conversation` 增加 `player_name` 入参，恢复时按对应桶写回。

### 7. `services/agent/prompt.py` — 模板与变量按 (连接, 玩家) 分桶
- 新增 `_session_templates / _session_variables` + `set/get_session_template / set/get_session_variables` API。
- 旧 `*_connection_*` API 保留，作为 `(connection_id, "__anonymous__")` 的薄封装；`build_system_prompt` 在玩家维度无配置时回退到匿名桶，保持外部调用方的向后兼容。
- 日志事件改为 `session_template_changed / session_variable_set / session_variable_removed / session_template_cleared`。

### 8. 测试同步更新
- `tests/test_queue_context.py`：`set/get_conversation_history` 全部带 `player_name`；新增两个验证多玩家隔离 / 锁分粒度的用例。
- `tests/test_conversation_manager.py`：`MockBroker` 升级为复合键，`save / restore` 测试按统一的 `player_name` 写读，避免桶错位。
- `tests/test_prompt_template.py`：未改测试，依靠新的 `build_system_prompt` 回退逻辑保持兼容。

## 验证结果

```
$ pytest tests/ \
    --ignore=tests/test_stream_mode.py \
    --ignore=tests/test_agent_multi_tool_live.py \
    --deselect tests/test_queue_context.py::test_worker_tool_events_should_not_be_sent_twice \
    --deselect tests/test_mcp.py::TestMCPConfig::test_mcp_server_config_creation_stdio
147 passed, 2 deselected, 4 warnings in 19.49s
```

新增的两个回归用例：

- `test_message_broker_history_per_player_isolation` 直接验证：同连接不同玩家历史互不串扰。
- `test_session_lock_per_player` 验证：同玩家拿到同一把锁；不同玩家拿到不同锁。

预先失败、与本次改动无关的测试（已在 master 之前的提交里就失败）：
- `test_stream_mode.py::*` — `MockAgent` 与 PydanticAI 泛型不兼容
- `test_agent_multi_tool_live.py::*` — 需要真实 DeepSeek API key
- `test_queue_context.py::test_worker_tool_events_should_not_be_sent_twice` — `tool_response_verbose` 默认 False，与测试期望不符
- `test_mcp.py::TestMCPConfig::test_mcp_server_config_creation_stdio` — MCP 模型字段差异

## 行为变更对比

| 场景 | 改动前 | 改动后 |
|------|--------|--------|
| 玩家 A 先发言后 B 发言 | B 的请求 `player_name` 仍是 A | 各自正确 |
| AI 工具 `get_player_snapshot(target=...)` | 接收的 target 是先到的 A | 接收的是当前发言者 |
| 对话历史 | 全连接共享一份 | 按玩家独立 |
| `AGENT 上下文 关闭` | 清空全员历史 | 仅清空发起者历史 |
| `切换模型` | 全员被切 + 历史清空 | 仅切发起者 |
| `AGENT 设置 变量` | 全员变量同步变更 | 仅作用于发起者 |
| 同连接多玩家并发请求 | 串行（连接锁） | 跨玩家并行（玩家锁） |
| `ai_response_sync` 回写 UI | player 名错位，常推到第一个发言者面板 | 与本次发言者匹配 |

## 兼容性

- `PromptManager` 的 `*_connection_*` 旧 API 仍可用，自动落到匿名玩家桶。
- `build_system_prompt` 在指定玩家无变量时自动回退到匿名桶，旧测试和外部调用方无需修改。
- `MessageBroker` 的旧调用方必须升级到带 `player_name` 的新签名。仓库内已全部迁移。
