# 多人下行投递串行化设计

**日期：** 2026-07-22  
**状态：** 已确认，待实施

## 背景

同一个 Minecraft 世界内，多名玩家共享一条 `/wsserver` 连接。当前玩家请求、会话历史和 Agent 依赖已经按 `(connection_id, player_name)` 隔离；运行日志也证明不同玩家的模型调用均能完成并生成独立标题。但多人使用时，只有部分玩家能看到 AI 回复，`帮助` 等不经过模型的命令也可能没有可见响应。

2026-07-21 的 `mcbe-ws-sdk` 宿主迁移后，当前宿主实现存在三条可能写入同一个 WebSocket 的下行路径：

1. `CommandHandlers` 和连接欢迎语直接创建 `McbeOutboundDelivery`，调用 `state.send_payload`；
2. Agent 流式分片、Addon UI 同步和工具命令通过 `MessageBroker`，由 `BrokerResponseBridge` 投递；
3. SDK `ConnectionManager` 维护独立的 `response_queue` 和 `HostResponseSink`。

这些路径分别做单条消息内部的分片与延迟，但没有统一的连接级投递所有权。多人并发时，不同路径可以交错向 Bedrock 发送 `commandRequest`，且非工具命令的失败响应缺少结构化记录。该实现缺口与日志中“模型已完成、客户端未显示”的故障边界一致；实施时以失败测试和新增投递证据验证该根因。

## 目标

- 同一连接下所有宿主产生的玩家可见消息按一个队列串行投递。
- 每条下行消息显式携带当前事件的 `player_name` 或最终 `target`。
- 两名玩家并发发送 `帮助` 或 AI 请求时，各自收到定向响应。
- 保留不同玩家 Agent run 的并发能力，不恢复为连接级模型锁。
- 对 Bedrock 拒绝的未跟踪 `commandResponse` 提供可定位的结构化日志。

## 非目标

- 不修改 `mcbe-ws-sdk` 源码或公共 API。
- 不改变 Addon 的 `mcbews v1` 线协议。
- 不调整模型 provider、PydanticAI 流式处理或对话历史结构。
- 不为下行消息增加持久化、重试或跨连接投递保证。

## 方案

### 单一宿主下行队列

复用 `MessageBroker` 已有的每连接响应队列和 `BrokerResponseBridge` 单消费者，使其成为宿主玩家可见消息的唯一投递入口。

新增一个宿主侧的结构化玩家回复类型，至少包含：

- `connection_id`
- `content`
- `color`
- `source`
- `player_name`
- `target`

`target` 是最终投递目标；有当前事件玩家时必须使用该玩家名，只有明确广播时才允许使用 `@a`。`player_name` 保留用于日志和身份审计，不从 `ConnectionState.player_name` 推断多人业务身份。

### 数据流

```text
PlayerMessage.sender
        |
        v
CommandHandlers / AgentWorker
        |
        | 显式 player_name / target
        v
MessageBroker response queue (per connection)
        |
        v
BrokerResponseBridge (one consumer per connection)
        |
        +-- tellraw <player> ...
        +-- scriptevent mcbews:text_resp ... { p: <player> }
        +-- tracked raw commandRequest
        v
Minecraft WebSocket
```

现有 Agent `StreamChunk`、`SystemNotification`、`ai_response_sync` 和 `run_command` 队列项保持原语义。`BrokerResponseBridge` 增加对结构化玩家回复的处理，并继续复用 SDK `McbeOutboundDelivery` 进行分片。

### 调用点迁移

- `CommandHandlers._send_player_reply` 不再直接创建 delivery，改为把结构化玩家回复放入 broker 队列。
- 登录、帮助、上下文、模型切换、模板和设置等所有复用 `_send_player_reply` 的路径自动获得串行投递。
- 连接欢迎语在 broker 队列和 bridge 启动后入队，避免成为独立发送路径。
- Agent worker 已经通过 broker 投递，无需改变模型事件循环。
- `WsCommandRunner` 仍由 bridge 在消费 `run_command` 队列项时调用，以保留 `requestId` 与 future 的关联。
- SDK `HostResponseSink` 保留为 Facade 接口适配，但宿主业务不再主动向 SDK `response_queue` 复制消息，避免双队列重复投递。

### 错误处理与日志

- broker 未注册连接时，入队失败沿用现有 `send_response` 返回值，并记录 `connection_id/player_name/source`。
- bridge 处理玩家回复失败时记录 `connection_id/player_name/target/source/item_type`，但保持消费循环存活。
- `HostConnectionHook.on_command_response` 先交给 `WsCommandRunner`。若响应未被工具 future 跟踪且 `statusCode` 非零，记录 `request_id/status_code/status_message`，使 tellraw 或 scriptevent 被 Bedrock 拒绝时可见。
- 日志不记录完整 AI 文本或敏感 payload，只记录长度、目标和投递类型。

## 并发语义

- 请求队列仍由多个 Agent worker 消费。
- 会话锁仍按 `(connection_id, player_name)` 分桶，因此不同玩家可以并行执行模型 run。
- 下行消息在单个连接内串行。不同连接各有独立 bridge 任务，可并行投递。
- 队列保持各生产者实际入队顺序，不承诺把一个 Agent run 的全部分片作为不可穿插的事务；每个分片都携带自己的玩家目标，因此允许不同玩家的流式分片公平交错。

## 测试设计

1. 两个玩家在同一 `ConnectionState` 上并发发送 `帮助`，验证生成的 tellraw 都被发送，目标分别是两名玩家。
2. 玩家命令回复必须先进入 broker 队列，在 bridge 消费前不直接调用 `state.send_payload`。
3. 欢迎语使用同一 broker 投递路径。
4. 同一连接交错放入两名玩家的 `StreamChunk`，验证每个 payload 的 tellraw 目标正确且无丢失。
5. 同一连接交错发送两名玩家的 `ai_response_sync`，解码 `mcbews:text_resp` 分片并验证 `p` 字段分别正确。
6. 未跟踪且非零的 `commandResponse` 产生结构化警告；成功或已跟踪响应不产生误报。
7. 运行 gateway、worker、多人会话和 Addon 协议相关测试，最后运行完整 `pytest`。

## 验收标准

- 同一世界至少两名玩家可分别触发并看到 `帮助`。
- 两名玩家可以并发触发 AI 对话，聊天 tellraw 和 Addon UI 历史均归属正确玩家。
- 日志中的 `processing_chat_request`、`chat_response_complete` 和下行投递记录可由 `connection_id/player_name` 关联。
- 无玩家状态、历史或模型锁退化为仅按 `connection_id` 分桶。
- 所有新增与既有自动化测试通过；若环境依赖导致完整测试无法运行，交付说明列出命令、错误与影响范围。
