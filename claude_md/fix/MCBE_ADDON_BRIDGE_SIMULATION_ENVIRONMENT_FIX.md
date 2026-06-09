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
