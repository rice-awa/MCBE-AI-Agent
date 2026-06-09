# MCBE 与 Addon Bridge 模拟环境实现

## 背景

根据 `claude_md/plan/MCBE_ADDON_BRIDGE_SIMULATION_ENVIRONMENT_PLAN.md`，当前 `replay-server` 只能回放真实玩家输入，不能模拟 MCBE/Addon 对服务端 `commandRequest` 的执行与响应，导致工具调用在回放环境中出现 `未收到游戏侧 commandResponse` 超时。

## 实现内容

- 新增 `tools_mcbe_simulator.py`，提供 MCBE WebSocket 与 Addon bridge 模拟客户端。
- 支持构造真实格式 `PlayerMessage`，用于向 AI Agent 发送玩家聊天输入。
- 支持接收服务端 `commandRequest` 并按原 `requestId` 返回 `commandResponse`。
- 支持模拟命令：
  - `tellraw`：返回 recipient/statusCode，并可派生外部 tell 回声。
  - `scriptevent mcbeai:ai_resp`：返回脚本事件发送成功响应。
  - `give <player> diamond <amount>`：返回类似真实 MCBE 的 give 成功 body。
  - `say <text>`：返回命令结果，并可派生 `[外部] <text>` PlayerMessage。
  - `scriptevent mcbeai:bridge_request`：返回脚本事件 commandResponse，并模拟 `MCBEAI_TOOL` 发送 `MCBEAI|RESP|...` bridge 聊天分片。
- 新增 `inspect-record`，可分析录制文件中的 `commandRequest` / `commandResponse` 配对和命令类型统计。
- 新增 `simulate-client` 和 `replay-record-with-simulation` CLI，用于发送场景消息或回放真实录制中的玩家输入并补齐模拟响应。
- 新增示例场景 `test_scenarios/mcbe_simulation/chat_tool_give_help_and_run_command.json`，覆盖当前录制中的 4 条真实玩家输入。
- 新增 `tests/test_mcbe_simulator.py`，覆盖模拟器核心协议行为。

## 关键命令

```bash
python tools_mcbe_simulator.py inspect-record --input test_logs/ws_records/20260609_101315/packets.jsonl
```

```bash
python tools_mcbe_simulator.py simulate-client --target ws://127.0.0.1:8080 --scenario test_scenarios/mcbe_simulation/chat_tool_give_help_and_run_command.json --out test_logs/ws_simulations
```

```bash
python tools_mcbe_simulator.py replay-record-with-simulation --input test_logs/ws_records/20260609_101315/packets.jsonl --target ws://127.0.0.1:8080 --out test_logs/ws_simulations
```

## 验证

```bash
pytest tests/test_mcbe_simulator.py tests/test_mcbe_ws_recorder.py tests/test_addon_bridge_protocol.py tests/test_addon_bridge_service.py -q
```

结果：`39 passed, 4 warnings`。

警告来自现有 `websockets.server` / `websockets.legacy` 弃用提示，本次未改动该路径。

额外验证：

```bash
python tools_mcbe_simulator.py inspect-record --input test_logs/ws_records/20260609_101315/packets.jsonl
```

输出显示真实录制中 `command_requests=19`、`command_responses=19`、`matched_request_ids=19`、`unmatched_request_ids=[]`，命令类型为 `tellraw: 13`、`scriptevent_ai_resp: 4`、`give: 1`、`say: 1`。

## 后续修正

- `simulate-client` 现在会读取场景文件中的 `player`，并优先使用场景玩家名覆盖 CLI 默认玩家名，避免场景改成 `tester` 时仍发送 `fantong7038`。
- 常用 Minecraft 命令模拟从只支持 `give` 扩展到 `summon`、`tp`、`teleport`、`time`、`weather`、`effect`、`gamemode`、`setblock`、`fill`、`kill`、`clear`、`title`、`playsound`、`particle`、`locate`、`xp`、`enchant`、`difficulty`、`gamerule`、`spawnpoint`、`setworldspawn`、`scoreboard`、`tag`、`execute` 等常见命令。
- `run_world_command` bridge fallback 复用同一套命令模拟结果，避免常用命令在普通工具失败后 bridge 仍返回 unsupported。
- 本次失败场景的根因不是模拟器未发送 `commandResponse`，而是场景快速发送 `AI 对话 status` 时，服务端 `handle_conversation()` 等待同玩家会话锁，堵住 WebSocket 读循环，导致已到达 socket 的 `commandResponse` 无法在工具 10 秒超时前被读取。
- `AI 对话 status` 属于只读状态查询，现在绕过会话锁直接生成状态响应，避免阻塞同连接上的 commandResponse 处理；会修改状态的对话命令仍继续持有同玩家会话锁。
- 新增回归测试 `test_conversation_status_does_not_block_command_response`，覆盖会话锁被占用时仍能处理 commandResponse 的场景。
- 最新相关验证：`pytest tests/test_websocket_conversation_commands.py tests/test_connection_manager.py tests/test_mcbe_simulator.py -q`，结果 `20 passed, 4 warnings`。
- 快速连续发送多条 `AI chat` 时，后续请求会携带入队瞬间的旧 `conversation_generation`；虽然同玩家请求已由 worker 串行处理，但完成写历史时仍用旧 generation 校验，导致正常串行聊天被误判为 stale 并出现 `chat_history_stale_write_skipped`。
- `AgentWorker` 现在在持有同玩家会话锁后读取当前对话 generation 作为本轮写入基线，不再使用入队时旧 generation；`MessageBroker.set_conversation_history(..., expected_generation=...)` 仍保留对清空、压缩等真正过期写入的保护。
- 新增回归测试 `test_worker_keeps_serial_chat_history_when_requests_share_initial_generation`，覆盖两条同玩家聊天共享初始 generation 时历史仍保留完整轮次。
- 最新 stale 写入验证：`pytest tests/test_queue_context.py tests/test_conversation_manager.py tests/test_websocket_conversation_commands.py tests/test_connection_manager.py tests/test_mcbe_simulator.py -q`，结果 `73 passed, 4 warnings`。
