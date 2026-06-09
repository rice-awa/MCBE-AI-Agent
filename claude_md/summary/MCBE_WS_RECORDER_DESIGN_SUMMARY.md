# MCBE WebSocket 数据包记录与回放工具设计

## 背景

当前项目是一个 Minecraft Bedrock Edition AI 聊天机器人服务器，核心通信链路为 MCBE 客户端通过 `/wsserver` 连接本项目 WebSocket 服务端。为了复现完整玩家流程、分析协议数据、构建可重复的服务端测试环境，需要一个可记录双向 WebSocket 数据包并能回放客户端侧数据包的 Python 工具。

## 目标

- 记录完整 WebSocket 数据包，包括 MCBE 客户端发出的包和项目服务端返回的包。
- 请求路径清晰，能区分真实录制路径和回放测试路径。
- 复用现有协议模型，避免重复实现 Minecraft WebSocket 消息结构。
- 支持从录制文件中筛选客户端包，模拟 MCBE 客户端连接本项目服务端进行回放测试。
- 输出既适合机器处理，也适合人工排查。

## 非目标

- 不在第一版内模拟完整 Minecraft Bedrock 世界行为。
- 不把记录逻辑内置进主 WebSocket 服务端。
- 不默认回放服务端发给 MCBE 的命令包到游戏客户端。
- 不解析或重写所有 Minecraft 协议语义，第一版以完整保留原始 WebSocket 帧为主。

## 推荐方案

新增独立工具脚本 `tools_mcbe_ws_recorder.py`，提供两个子命令：

- `record`：启动 WebSocket 代理，MCBE 连接代理端口，代理连接真实项目服务端，透明转发并记录双向数据包。
- `replay-server`：读取录制文件，只筛选 `client_to_server` 方向数据包，连接目标项目服务端并按顺序回放，用于服务端测试。

采用单脚本多子命令方案，可以共享记录格式、解析逻辑和 CLI 参数，同时避免把测试工具逻辑混入主服务端。

## 请求路径

### 录制路径

```text
MCBE Client /wsserver -> Recorder Proxy -> mcbe_ai_agent WebSocket Server
```

记录方向：

- `client_to_server`：MCBE 客户端发给项目服务端的数据包。
- `server_to_client`：项目服务端发给 MCBE 客户端的数据包。

### 回放路径

```text
ReplayClient -> mcbe_ai_agent WebSocket Server
```

回放行为：

- 只发送录制文件中的 `client_to_server` 包。
- 服务端响应不与原始录制做强校验，第一版只记录到新的 replay 输出目录。
- 后续可以增加响应断言、请求 ID 匹配或快照对比。

## 主要组件

### PacketRecord

轻量记录结构，用于描述每个 WebSocket 帧。

建议字段：

- `seq`：会话内自增序号。
- `timestamp`：ISO 格式记录时间。
- `elapsed_ms`：距离会话开始的毫秒数。
- `direction`：数据方向，例如 `client_to_server`、`server_to_client`、`server_to_replay_client`。
- `path`：清晰请求路径，例如 `mcbe->proxy->server`。
- `raw`：原始文本帧内容。
- `binary`：是否为二进制帧。
- `bytes`：原始帧 UTF-8 字节数或二进制长度。
- `json_valid`：是否成功解析为 JSON。
- `header`：解析成功时保留的 header。
- `body`：解析成功时保留的 body。
- `request_id`：`header.requestId`。
- `message_purpose`：`header.messagePurpose`。
- `event_name`：`header.eventName` 或 `header.EventName`。
- `error`：解析或转发异常说明。

### SessionRecorder

负责一个录制或回放会话的持久化。

职责：

- 创建会话目录。
- 流式写入 `packets.jsonl`。
- 维护包数量、字节数、方向统计、开始时间、结束时间和错误信息。
- 结束时生成完整 `packets.json`。
- 结束时生成 `SUMMARY.md`，便于人工查看。

默认输出路径：

```text
test_logs/ws_records/<session_id>/
test_logs/ws_replays/<session_id>/
```

### ProxyRecorder

负责代理录制。

职责：

- 在本地监听 WebSocket 端口。
- 接受 MCBE 客户端连接。
- 连接真实项目 WebSocket 服务端。
- 用两个异步任务分别转发两个方向的数据。
- 每个方向先调用 `SessionRecorder` 记录原始帧，再执行转发，确保转发失败时仍保留触发包。
- 任意一端断开时关闭另一端，并完成会话摘要。

### ReplayClient

负责服务端回放测试。

职责：

- 读取 `packets.jsonl` 或 `packets.json`。
- 筛选 `direction == "client_to_server"` 且非二进制的数据包。
- 连接目标项目服务端。
- 按原始顺序发送数据包。
- 默认按原始时间间隔回放，支持通过速度参数调整。
- 记录服务端响应到 replay 会话目录。

## 现有模块复用

- `models.minecraft.MinecraftMessage`：用于通用 Minecraft WebSocket 消息结构识别。
- `models.minecraft.MinecraftSubscribe`：后续需要构造订阅包时复用。
- `models.minecraft.MinecraftCommand`：后续需要构造命令包或补充测试包时复用。
- `tools_ws_tester.py`：复用其 WebSocket 工具脚本风格、日志目录思路和请求 ID 观察经验，但不复用压力测试交互命令。

## CLI 设计

### record

示例：

```bash
python tools_mcbe_ws_recorder.py record \
  --listen-host 0.0.0.0 \
  --listen-port 18080 \
  --target ws://127.0.0.1:8080 \
  --out test_logs/ws_records
```

MCBE 连接示例：

```text
/wsserver <机器IP>:18080
```

### replay-server

示例：

```bash
python tools_mcbe_ws_recorder.py replay-server \
  --input test_logs/ws_records/<session_id>/packets.jsonl \
  --target ws://127.0.0.1:8080 \
  --speed 1.0 \
  --out test_logs/ws_replays
```

速度语义：

- `--speed 1.0`：按原始时间间隔回放。
- `--speed 2.0`：两倍速回放。
- `--speed 0`：不等待，尽快发送。

## 错误处理

- 目标服务端连接失败：记录错误，关闭 MCBE 代理连接，生成摘要。
- JSON 解析失败：不丢包，保留 `raw`，标记 `json_valid=false`。
- 二进制帧：记录方向和长度，默认不在 `replay-server` 中回放。
- 转发失败：记录当前方向、序号和异常消息，关闭相关连接。
- 回放时服务端提前断开：记录最后已发送包序号、断开原因和已收到响应。

## 测试设计

优先添加单元测试：

- `PacketRecord` 能完整保留 JSON 文本帧。
- 非 JSON 文本帧不会丢失，且 `json_valid=false`。
- 回放筛选逻辑只选取 `client_to_server` 的非二进制包。
- `--speed` 延迟计算符合预期。
- `SessionRecorder` 能生成 `packets.jsonl`、`packets.json` 和摘要文件。

可选集成测试：

- 使用本地临时 WebSocket 假服务端验证 `ProxyRecorder` 双向转发和记录。
- 使用本地临时 WebSocket 假服务端验证 `ReplayClient` 能按顺序发送客户端包并记录响应。

## 验收标准

- 可以通过代理记录一次完整 MCBE `/wsserver` 会话。
- `packets.jsonl` 中能看到双向完整原始包和清晰方向。
- 会话结束后生成完整 `packets.json` 和 `SUMMARY.md`。
- 可以用 `replay-server` 读取录制文件并连接本项目服务端回放客户端包。
- 回放过程能记录服务端响应，方便对比和排查。
- 工具不影响主服务端正常运行，不修改现有服务端通信逻辑。
