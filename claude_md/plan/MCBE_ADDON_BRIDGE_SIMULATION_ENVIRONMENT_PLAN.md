# MCBE 与 mcbeaddon bridge 完整模拟环境计划

## 背景

当前 `tools_mcbe_ws_recorder.py` 已能作为透明代理记录真实 MCBE WebSocket 数据包，并且 `replay-server` 已修正为只回放真实玩家输入 `PlayerMessage`。从现有记录看：

- `test_logs/ws_records/20260609_101315` 是一次真实 MCBE/Addon 环境录制：总包数 58，包含 `client_to_server: 37`、`server_to_client: 21`，过滤了 13 条 `receiver=MCBEAI_TOOL` 回声。
- 真实录制中，服务端下发的每条 `commandRequest` 都会由游戏侧返回对应 `commandResponse`，例如：
  - `scriptevent mcbeai:ai_resp ...` 返回 `statusMessage="Script event mcbeai:ai_resp has been sent"`。
  - `give fantong7038 diamond 64` 返回包含 `itemAmount=64`、`itemName="钻石"`、`statusCode=0` 的命令结果。
  - `say hi` 返回 `message="hi"`、`statusCode=0`，同时产生 `[外部] hi` 的 `PlayerMessage` 事件。
- `test_logs/ws_replays/20260609_103513` 是修复后的回放结果：只向服务端发送 4 条真实玩家输入，但服务端返回的 `commandRequest` 没有 MCBE/Addon 执行侧响应。
- `test_logs/ws_replays/20260609_103513/ws控制台日志.log` 显示 `run_minecraft_command` 因未收到游戏侧 `commandResponse` 超时，随后 `run_world_command` 在连接关闭后失败，并出现 `Future exception was never retrieved` 与 `response_to_unknown_connection`。

因此，当前 replay 客户端只能复现“玩家输入进入服务端”这一半链路，不能复现“服务端命令下发 → MCBE/Addon 执行 → commandResponse/事件回传”的完整闭环。

## 目标

新增一个 MCBE 与 mcbeaddon bridge 完整模拟环境，用于在无真实 Minecraft 客户端或 Addon 的情况下运行真实请求测试，并完整记录整个 WebSocket 协议交互。

核心目标：

1. 模拟 MCBE WebSocket 客户端连接 AI Agent 服务端。
2. 模拟基础 MCBE 行为：握手、接收订阅、接收 `commandRequest`、返回 `commandResponse`、回传由命令触发的 `PlayerMessage` 回声。
3. 模拟 mcbeaddon bridge 行为：识别 `scriptevent mcbeai:bridge_request ...`，根据 capability 生成 bridge 响应事件或命令响应。
4. 支持真实请求测试：发送玩家聊天、帮助、运行命令、工具调用类请求，并让服务端完成完整异步流程。
5. 全量记录模拟会话中的双向数据包、派生命令响应、bridge 请求/响应、超时和错误。
6. 保留当前透明代理录制能力，不破坏真实 MCBE 录制流程。

## 非目标

- 不模拟完整 Minecraft 世界状态、实体、背包、权限系统和区块数据。
- 不实现完整 Bedrock 客户端协议，只覆盖当前 AI Agent 使用的 WebSocket JSON 命令通道。
- 不替代真实 MCBE 集成测试；模拟环境用于稳定复现服务端逻辑和工具调用闭环。
- 不在第一阶段修改 `services/websocket/server.py` 的业务逻辑。

## 现状问题归纳

### 1. 当前 replay-server 不会响应 commandRequest

在 `20260609_103513` 回放中，服务端下发了多条 `commandRequest`：

- 欢迎 `tellraw`。
- 用户输入 echo 的 `scriptevent mcbeai:ai_resp`。
- AI 回复分片 `tellraw` / `scriptevent mcbeai:ai_resp`。
- 工具调用 `give fantong7038 diamond 64`。
- Addon bridge 请求 `scriptevent mcbeai:bridge_request {...}`。
- 帮助文本 `tellraw`。
- 用户显式执行的 `say hi`。

但 replay 客户端只记录这些服务端响应，不会像真实 MCBE 一样回传 `commandResponse`。这会导致依赖命令结果的工具调用超时。

### 2. 真实录制提供了可复用的响应模板

在 `20260609_101315` 真实录制中，相同类型命令已有可参考响应：

- `tellraw` 通常返回：`body.recipient=[玩家名,"MCBEAI_TOOL"]`、`statusCode=0`。
- `scriptevent mcbeai:ai_resp` 返回：`statusCode=0`、`statusMessage="Script event mcbeai:ai_resp has been sent"`。
- `give <player> diamond 64` 返回：`itemAmount=64`、`itemName="钻石"`、`playerName=[玩家名]`、`statusCode=0`、`statusMessage="给予 ..."`。
- `say hi` 返回：`message="hi"`、`statusCode=0`，并额外产生 `PlayerMessage`：`sender="外部"`、`message="[外部] hi"`。

这些数据可作为模拟器默认响应策略和测试 fixture。

### 3. bridge_request 需要 Addon 层模拟

服务端在工具 fallback 中会发出：

```text
scriptevent mcbeai:bridge_request {"request_id":"...","capability":"run_world_command","payload":{"command":"give fantong7038 diamond 64"}}
```

完整模拟环境需要识别该命令，并模拟 Addon 执行 capability 后向服务端回传结果。否则 `run_world_command` 仍会等待到超时。

## 建议架构

新增一个独立测试工具，建议命名为：

```text
tools_mcbe_simulator.py
```

或在现有工具中新增子命令：

```text
python tools_mcbe_ws_recorder.py simulate-client --target ws://127.0.0.1:8080 --scenario scenarios/mcbe_chat_tool.json --out test_logs/ws_simulations
```

推荐第一阶段使用独立文件，避免让当前 recorder/replay 文件继续膨胀；稳定后再考虑合并 CLI。

### 组件划分

1. `SimulatedMcbeClient`
   - 负责连接 AI Agent WebSocket 服务端。
   - 负责发送玩家输入事件。
   - 负责接收服务端 `commandRequest`。
   - 负责把所有收发数据交给 `SessionRecorder` 记录。

2. `CommandRequestDispatcher`
   - 解析 `commandLine`。
   - 按命令类型分发到对应模拟处理器。
   - 生成 `commandResponse`。
   - 必须沿用服务端下发的 `header.requestId`。

3. `McbeCommandSimulator`
   - 模拟 MCBE 原生命令执行结果。
   - 覆盖：`tellraw`、`scriptevent`、`give`、`say`、未知命令。
   - 对部分命令产生额外 `PlayerMessage` 事件，如 `tellraw` 回声和 `say` 广播。

4. `AddonBridgeSimulator`
   - 识别 `scriptevent mcbeai:bridge_request ...`。
   - 解析 JSON payload。
   - 根据 `capability` 调用能力处理器。
   - 回传 bridge 结果事件。

5. `ScenarioRunner`
   - 从 JSON/YAML 场景文件读取玩家输入步骤、等待策略和断言。
   - 支持固定延迟和条件等待。
   - 输出可重复运行的模拟测试日志。

6. `SimulationRecorder`
   - 可以复用现有 `SessionRecorder` 和 `PacketRecord`。
   - 新增方向建议：
     - `sim_client_to_server`：模拟 MCBE 发送给服务端。
     - `server_to_sim_client`：服务端发送给模拟 MCBE。
     - `sim_internal`：模拟器内部派生事件，可选记录。
   - 记录额外 metadata：命令分类、匹配规则、派生原因、原始 requestId。

## 协议模拟设计

### 1. 连接与握手

模拟客户端连接 `ws://127.0.0.1:8080` 后，需要处理服务端初始响应：

- `{"Result":"true"}`：记录即可。
- `messagePurpose="subscribe"`：记录订阅内容，当前主要是 `EventName="commandRequest"`。
- 初始欢迎 `commandRequest`：生成对应 `commandResponse`，并按需要产生 `PlayerMessage` 回声。

### 2. 玩家输入事件

模拟玩家消息使用真实录制格式：

```json
{
  "body": {
    "message": "AI chat hi",
    "receiver": "",
    "sender": "fantong7038",
    "type": "chat"
  },
  "header": {
    "eventName": "PlayerMessage",
    "messagePurpose": "event",
    "version": 17104896
  }
}
```

场景文件应支持配置：

- `sender`：玩家名。
- `message`：聊天内容。
- `type`：默认 `chat`。
- `receiver`：默认空字符串。
- `after`：步骤间延迟。
- `wait_for`：等待某类服务端响应后再继续。

### 3. commandRequest 响应规则

模拟器收到服务端 `commandRequest` 后，必须生成：

```json
{
  "header": {
    "messagePurpose": "commandResponse",
    "requestId": "<原 requestId>",
    "version": 17104896
  },
  "body": {
    "statusCode": 0
  }
}
```

具体 body 按命令类型扩展。

#### tellraw

输入示例：

```text
tellraw @a {"rawtext":[{"text":"§a用户你好呀！"}]}
```

响应：

```json
{
  "recipient": ["fantong7038", "MCBEAI_TOOL"],
  "statusCode": 0
}
```

可选派生回声：

```json
{
  "header": {"eventName":"PlayerMessage","messagePurpose":"event","version":17104896},
  "body": {
    "sender": "外部",
    "receiver": "fantong7038",
    "type": "tell",
    "message": "{...rawtext...}\n"
  }
}
```

默认建议：

- `record` 模式保留回声以模拟真实 MCBE。
- `test` 模式可通过 `--emit-tellraw-echo false` 关闭，避免 AI 把回声当新输入。

#### scriptevent mcbeai:ai_resp

输入示例：

```text
scriptevent mcbeai:ai_resp {"id":"resp-...","i":1,"n":1,"p":"fantong7038","r":"assistant","c":"..."}
```

响应：

```json
{
  "statusCode": 0,
  "statusMessage": "Script event mcbeai:ai_resp has been sent"
}
```

该命令通常不需要产生额外 `PlayerMessage`，但可以在 internal metadata 中记录重组信息。

#### give

输入示例：

```text
give fantong7038 diamond 64
```

响应：

```json
{
  "itemAmount": 64,
  "itemName": "钻石",
  "playerName": ["fantong7038"],
  "statusCode": 0,
  "statusMessage": "给予 fantong7038  钻石 * 64 效果"
}
```

需要支持最小命令解析：

- `give <player> diamond <count>` → 中文 itemName 可用映射表。
- 未知 item → `itemName` 保留原始 ID。
- 非法格式 → `statusCode=-2147483648` 或可配置错误码，并给出 `statusMessage`。

#### say

输入示例：

```text
say hi
```

响应：

```json
{
  "message": "hi",
  "statusCode": 0
}
```

派生事件：

```json
{
  "header": {"eventName":"PlayerMessage","messagePurpose":"event","version":17104896},
  "body": {
    "sender": "外部",
    "receiver": "",
    "type": "say",
    "message": "[外部] hi"
  }
}
```

#### 未知命令

默认返回失败响应，避免测试误判成功：

```json
{
  "statusCode": -1,
  "statusMessage": "Simulated command failed: unsupported command"
}
```

同时在 summary 中记录 unsupported command。

### 4. mcbeaddon bridge_request 模拟

收到：

```text
scriptevent mcbeai:bridge_request {"request_id":"addon-...","capability":"run_world_command","payload":{"command":"give fantong7038 diamond 64"}}
```

模拟器需要做两件事：

1. 先对这个 `scriptevent` 本身返回 MCBE `commandResponse`：

```json
{
  "header": {"messagePurpose":"commandResponse","requestId":"<commandRequest requestId>","version":17104896},
  "body": {"statusCode":0,"statusMessage":"Script event mcbeai:bridge_request has been sent"}
}
```

2. 再模拟 Addon bridge 向服务端发送执行结果事件。

bridge 回传格式需要以当前服务端实际解析逻辑为准，实施前应读取 `services/agent/tools.py` 和相关 Addon bridge 处理代码确认。计划建议格式为 `PlayerMessage` 或已约定的 ScriptEvent 回传事件，包含：

```json
{
  "request_id": "addon-...",
  "capability": "run_world_command",
  "ok": true,
  "result": {
    "statusCode": 0,
    "statusMessage": "给予 fantong7038  钻石 * 64 效果"
  }
}
```

实施时必须以服务端当前等待的 response channel、事件名和 payload 字段为准，不能只按上述示例硬编码。

## 场景文件设计

建议新增目录：

```text
test_scenarios/mcbe_simulation/
```

示例：

```json
{
  "name": "chat_tool_give_and_run_command",
  "player": "fantong7038",
  "connection": {
    "target": "ws://127.0.0.1:8080"
  },
  "simulation": {
    "emit_tellraw_echo": true,
    "emit_say_event": true,
    "command_response_delay_ms": 50,
    "unknown_command_policy": "fail"
  },
  "steps": [
    {"send_player_message": "AI chat hi", "wait_for": {"type": "assistant_response"}},
    {"send_player_message": "AI chat 给我64颗钻石", "wait_for": {"type": "command", "contains": "give fantong7038 diamond 64"}},
    {"send_player_message": "帮助", "wait_for": {"type": "command", "contains": "可用命令"}},
    {"send_player_message": "运行命令 say hi", "wait_for": {"type": "command_response", "command": "say hi"}}
  ],
  "assertions": [
    {"no_server_log_contains": "命令执行超时"},
    {"no_server_log_contains": "response_to_unknown_connection"},
    {"packet_exists": {"direction": "sim_client_to_server", "purpose": "commandResponse"}},
    {"command_response_exists": {"command": "give fantong7038 diamond 64", "statusCode": 0}}
  ]
}
```

## CLI 设计

### simulate-client

```bash
python tools_mcbe_simulator.py simulate-client \
  --target ws://127.0.0.1:8080 \
  --player fantong7038 \
  --scenario test_scenarios/mcbe_simulation/chat_tool_give_and_run_command.json \
  --out test_logs/ws_simulations \
  --speed 1.0
```

用途：运行完整模拟测试。

### replay-record-with-simulation

```bash
python tools_mcbe_simulator.py replay-record-with-simulation \
  --input test_logs/ws_records/20260609_101315/packets.jsonl \
  --target ws://127.0.0.1:8080 \
  --out test_logs/ws_simulations \
  --response-mode synthetic
```

用途：从真实录制中提取玩家输入，并用模拟器补齐 commandResponse。相比当前 `replay-server`，该模式应处理服务端下发命令，而不是只记录。

### inspect-record

```bash
python tools_mcbe_simulator.py inspect-record \
  --input test_logs/ws_records/20260609_101315/packets.jsonl
```

用途：分析真实录制中的命令-响应配对，输出：

- 未匹配 `commandRequest`。
- 未匹配 `commandResponse`。
- commandLine 类型统计。
- requestId 延迟统计。
- 派生 `PlayerMessage` 统计。

## 数据包记录增强

在现有 `packets.jsonl` 字段基础上，建议增加可选 `metadata` 字段：

```json
{
  "metadata": {
    "simulated": true,
    "command_kind": "give",
    "source_command_request_id": "...",
    "derived_from_seq": 14,
    "bridge_request_id": "addon-...",
    "handler": "McbeCommandSimulator.handle_give"
  }
}
```

`SUMMARY.md` 增强统计：

- 总包数、总字节数、方向统计。
- `commandRequest` 总数。
- `commandResponse` 总数。
- requestId 匹配数 / 未匹配数。
- 命令类型统计：`tellraw`、`scriptevent ai_resp`、`scriptevent bridge_request`、`give`、`say`、`unknown`。
- 派生事件统计：tellraw echo、say event、bridge result。
- 超时统计。
- unsupported command 列表。

## 测试策略

### 单元测试

新增测试文件：

```text
tests/test_mcbe_simulator.py
```

覆盖：

1. `CommandRequestDispatcher` 能按 `requestId` 返回 `commandResponse`。
2. `tellraw` 响应包含 recipient 和 statusCode。
3. `scriptevent mcbeai:ai_resp` 响应包含 statusMessage。
4. `give fantong7038 diamond 64` 响应包含 itemAmount、itemName、playerName。
5. `say hi` 响应后派生 `[外部] hi` 的 `PlayerMessage`。
6. `bridge_request` 能解析 request_id、capability、payload。
7. 未知命令返回失败响应并记录 unsupported。
8. 从 `20260609_101315` 真实录制中提取的关键命令能被模拟器识别。

### 集成测试

新增最小集成测试：

1. 启动测试 WebSocket 服务端，模拟 AI Agent 下发 commandRequest。
2. 启动 `SimulatedMcbeClient` 连接。
3. 服务端发送 `give` commandRequest。
4. 断言收到同 requestId 的 commandResponse。
5. 断言 recorder 落盘包含双向包。

如需要对真实 `python cli.py serve` 做端到端测试，建议先提供独立手动命令，不默认纳入 CI，避免依赖 LLM API 和本地配置。

### 手动真实请求测试

推荐流程：

```bash
python cli.py serve
python tools_mcbe_simulator.py simulate-client --target ws://127.0.0.1:8080 --scenario test_scenarios/mcbe_simulation/chat_tool_give_and_run_command.json --out test_logs/ws_simulations
```

验收时检查：

- 服务端日志不再出现 `命令执行超时: 未收到游戏侧 commandResponse`。
- 服务端日志不再出现因模拟器提前断开导致的 `response_to_unknown_connection`。
- 模拟输出目录包含完整 `packets.jsonl`、`packets.json`、`SUMMARY.md`。
- `give` 工具调用能得到模拟成功结果。
- `运行命令 say hi` 能得到模拟 commandResponse。

## 实施步骤

### 阶段 1：协议分析与测试夹具

1. 基于 `test_logs/ws_records/20260609_101315/packets.jsonl` 建立命令-响应配对分析器。
2. 输出 commandLine 类型和 requestId 配对统计。
3. 固化关键样本为测试 fixture。
4. 修正 `tools_mcbe_ws_recorder.py` 顶部说明中过时的 replay 描述。

交付物：

- `inspect-record` 或内部分析函数。
- `tests/test_mcbe_simulator.py` 的解析类测试。

### 阶段 2：MCBE commandRequest 模拟

1. 实现 `McbeCommandSimulator`。
2. 支持 `tellraw`、`scriptevent mcbeai:ai_resp`、`give`、`say`。
3. 实现派生 `PlayerMessage` 事件。
4. 复用 `SessionRecorder` 记录模拟数据包。

交付物：

- 可响应 commandRequest 的模拟客户端。
- 单元测试覆盖主要命令。

### 阶段 3：Addon bridge 模拟

1. 阅读服务端当前 bridge response 等待逻辑，确认回传事件格式。
2. 实现 `AddonBridgeSimulator`。
3. 支持 `run_world_command` capability。
4. 将 bridge 的内部命令执行结果与 MCBE commandResponse 关联记录。

交付物：

- `run_world_command` 不再在模拟回放中超时。
- summary 能列出 bridge_request / bridge_result 配对。

### 阶段 4：场景运行器

1. 实现 JSON 场景文件解析。
2. 支持多步骤玩家输入。
3. 支持条件等待：收到指定命令、收到 assistant 分片、收到 commandResponse。
4. 支持超时失败和 summary 错误记录。

交付物：

- `simulate-client --scenario ...`。
- 示例场景覆盖 `AI chat hi`、`AI chat 给我64颗钻石`、`帮助`、`运行命令 say hi`。

### 阶段 5：验证与文档

1. 添加使用文档到工具头部或 `docs/`。
2. 添加计划落实后的 FIX/SUMMARY 文档到 `claude_md`。
3. 运行单元测试。
4. 手动运行真实服务端模拟请求测试。
5. 对比 `ws_records` 与 `ws_simulations` 的 requestId 配对完整性。

交付物：

- 测试通过。
- 手动端到端模拟输出目录。
- 无 commandResponse 超时。

## 验收标准

1. 能在没有真实 Minecraft 客户端的情况下连接 `python cli.py serve` 启动的服务端。
2. 能发送真实格式的玩家 `PlayerMessage`。
3. 能对服务端下发的每个受支持 `commandRequest` 返回同 requestId 的 `commandResponse`。
4. 能模拟 `give fantong7038 diamond 64` 成功，服务端工具调用不再超时。
5. 能模拟 `scriptevent mcbeai:bridge_request` 的 Addon bridge 回传。
6. 能记录完整双向数据包和模拟派生包。
7. `SUMMARY.md` 能展示 commandRequest/commandResponse 配对统计。
8. 对 `test_logs/ws_records/20260609_101315` 中出现的主要命令类型都有明确处理策略。
9. 当前 `record` 透明代理模式不受影响。
10. 当前 `replay-server` 玩家输入回放模式不受影响。

## 风险与注意事项

- bridge 回传格式必须以现有服务端代码为准，不能仅凭录制日志猜测。
- tellraw 回声可能再次被服务端当作外部玩家输入，需要提供开关，并在测试模式默认谨慎处理。
- LLM 输出不稳定，真实端到端测试断言应优先检查协议行为和工具结果，不应强依赖完整自然语言文本。
- 模拟器返回过快可能掩盖真实时序问题，建议支持可配置响应延迟。
- 过度硬编码 `fantong7038` 会降低复用性，玩家名应来自场景配置。
- 未知命令默认失败比默认成功更安全，避免误判业务逻辑已通过。

## 推荐优先级

P0：实现 commandRequest → commandResponse 自动响应，解决 `run_minecraft_command` 超时。

P1：实现 `scriptevent mcbeai:bridge_request` 的 Addon bridge 模拟，解决 `run_world_command` 闭环。

P2：实现场景文件和条件等待，让模拟请求测试可重复运行。

P3：增强 summary 与 inspect 工具，方便对比真实录制和模拟输出。
