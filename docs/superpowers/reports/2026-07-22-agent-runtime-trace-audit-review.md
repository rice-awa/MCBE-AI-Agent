# MCBE Chat Agent 运行追踪与审计现状审查

> 审查日期：2026-07-22  
> 审查范围：入站玩家事件、Message Broker、Agent Worker、PydanticAI Agent 循环、工具策略/审批/执行、MCP、Minecraft/Add-on 操作、下行响应、日志配置、工具审计及相关测试  
> 方法：源码静态审查、近期提交审查、本地日志结构抽样、定向测试、PydanticAI 1.x 与 structlog 当前文档核验  
> 明确排除：真实付费 Provider 端到端调用、真实 Minecraft 世界副作用测试、外部可观测平台选型  
> 结论置信度：高。关键断链和内容泄露均有当前源码或本地日志样本证据。

## 1. 执行摘要

当前项目已经具备运行追踪的部分骨架，但还不能从一个标识完整回答“这次玩家请求中 MCBE Chat Agent 做了什么”。现状不是日志太少，而是记录被分散在三个没有统一事件契约的体系中：

1. `app.log` 记录服务与 Agent 运行过程，但文件不是结构化 JSON，字段由各调用点手写，部分事件没有 `run_id`，并且仍会记录完整工具参数或结果。
2. `runtime_harness_tools.jsonl` 记录工具审计摘要，已经包含新版本的 `run_id` 和 `tool_call_id`，但只覆盖最终工具执行，无法还原模型请求、策略决定、审批、下行交付和整体终态。
3. Minecraft WebSocket、Add-on Bridge 和 PydanticAI 分别产生自己的 request/tool 标识，但没有统一映射到一个可查询的因果链。

近期运行时 Harness 整改已经补齐 `run_id`、结构化 `ToolResult`、工具策略、审批、幂等和异步审计 writer，方向是正确的。下一步不应继续增加零散 `logger.info()`，也不应先部署外部 tracing 平台。推荐先建设一个**本地优先的运行追踪事件流**：

- 在玩家消息进入宿主时创建稳定 `trace_id`，审批暂停和恢复保持同一个 trace。
- 用 `attempt_id` 区分初次 Agent 执行与审批恢复后的新一次 PydanticAI 执行。
- 显式把轻量 TraceContext 传过队列、长期 Worker、工具、Minecraft/Add-on 请求和下行响应；contextvars 只负责当前执行上下文内自动补字段。
- 将模型请求、策略决定、审批、工具执行、外部操作和下行交付写入版本化 `agent_traces.jsonl`。
- 增加本地 CLI，按 trace 输出时间线、模型用量、工具/审批表和最终状态。
- 保留“工具审计”作为风险分析和反馈闭环的数据源，不把它与普通运行日志混为一谈。

综合判断：**当前运行追踪成熟度约为 L1.5 / L4**。已有局部结构化身份和工具结果，但缺跨异步边界的统一上下文、模型与交付 span、可靠终态、隐私一致性和本地查询界面。完成本文 P0-P2 后可达到可用于日常排障的 L2.5；接入标准 OpenTelemetry exporter 属于后续增强，不是本地清晰可查的前置条件。

## 2. 术语边界

本文沿用 [CONTEXT.md](../../../CONTEXT.md#L23) 对“工具审计”的定义，并增加“运行追踪”作为本文专用术语：

| 名称 | 用途 | 应记录 | 不应承担 |
| --- | --- | --- | --- |
| 普通运行日志 | 服务运维、组件状态、非请求级异常 | 服务启停、连接数量、队列健康、writer 状态 | 完整还原一次 Agent 请求 |
| 运行追踪 | 回答一次玩家请求经过了哪些阶段、操作和结果 | 模型、策略、审批、工具、外部调用、交付的元数据和因果关系 | 保存完整正文或代替工具风险留证 |
| 工具审计 | 记录工具选择、参数预览、执行结果和失败原因，用于反馈闭环 | 风险、意图、策略版本、白名单参数摘要、结构化结果 | 记录所有模型和传输细节 |
| Raw 日志 | 临时诊断第三方线协议 | 显式 opt-in 的受限原始数据 | 默认开启或进入普通日志 |

因此，本文不建议把所有日志合并为一个“大审计文件”。推荐让运行追踪和工具审计共享身份字段与结果来源，但继续保持不同的保留策略和消费用途。

## 3. 当前执行链与关联断点

```text
Minecraft / Add-on 玩家消息
  -> HostConnectionHook.create_task(_dispatch)
  -> CommandHandlers 构造 ChatRequest
  -> MessageBroker.request_queue
  -> AgentWorker 获取玩家/对话锁
  -> Worker 此时才生成 run_id
  -> 构建历史、上下文和 AgentDependencies
  -> PydanticAI Agent.iter() / Agent.run()
       -> model request 1
       -> tool proposal
       -> HarnessToolset
            -> idempotency
            -> policy
            -> approval 或 execute
            -> 工具审计 JSONL
       -> model request 2...
  -> Worker 写回历史并生成 StreamChunk / dict response
  -> MessageBroker.response_queue
  -> BrokerResponseBridge
       -> tellraw / scriptevent / mcbews:text_resp
       -> Minecraft commandRequest / Add-on request
  -> 玩家或 UI 收到结果
```

当前主要断点：

- `run_id` 在 Worker 取出请求后才生成，因此入站、鉴权、入队和排队耗时不在同一条链中：[command_handlers.py](../../../services/gateway/command_handlers.py#L283)、[worker.py](../../../services/agent/worker.py#L175)。
- `merge_contextvars` 已配置，但仓库没有调用 `bind_contextvars`、`bound_contextvars` 或 `clear_contextvars`；当前关联字段完全依赖每个日志调用点手写：[logging.py](../../../config/logging.py#L13)。
- `StreamChunk` 没有 `trace_id`、`run_id`、`attempt_id` 或 `tool_call_id`，下行队列消费方不能知道消息属于哪个 trace：[messages.py](../../../models/messages.py#L55)。
- `BrokerResponseBridge` 是独立的长生命周期 task，不会继承生产该消息的 Worker 上下文：[broker_bridge.py](../../../services/gateway/broker_bridge.py#L60)。
- Minecraft command runner、Add-on Bridge 与 PydanticAI 工具各自有 request ID，但没有保存彼此映射：[ws_command_runner.py](../../../services/gateway/ws_command_runner.py#L74)。
- 审批恢复复用了原 `run_id`，这是可复用的正确基础；但没有新的 attempt 身份，因此两个 `processing_chat_request` 看起来像同一段执行被启动两次：[approvals.py](../../../services/agent/harness/approvals.py#L18)、[command_handlers.py](../../../services/gateway/command_handlers.py#L1114)。

## 4. 日志样本观察

以下统计来自审查时的本地日志，只分析结构和状态，没有把玩家正文、命令、工具结果或凭据复制到报告中。

| 样本 | 观察 |
| --- | --- |
| `logs/app.log` | 354 行中 99 行包含 `run_id`，共 10 个不同 `run_id` |
| 请求状态 | 12 次 `processing_chat_request`、9 次完成、1 次错误、2 次审批暂停 |
| 工具审计 | 54 条记录，其中 43 条包含新字段 `run_id/tool_call_id`，11 条为旧结构 |
| 状态一致性 | 7 条记录出现顶层 `status=success`、内部 `result.success=failure` |
| 本地可查询性 | `app.log` 为 ConsoleRenderer 文本，工具审计为 JSONL；没有按 trace 查询命令 |

样本中一次普通 Wiki 工具请求可以通过 `run_id` 人工拼接出 Worker 开始、工具提出、工具结果和整体完成；但同一时间段的 HTTP 请求、Worker 展示日志和下行发送没有该标识。一次审批请求可以看到 `pending` 与后续相同 `run_id` 的重新处理，却看不到是谁在何时批准/拒绝、等待了多久、恢复的是哪个 attempt。

## 5. 关键发现

### P0-1：普通 INFO 日志仍记录工具正文和结果

**证据**

- 模型提出工具时记录完整 `args`：[core.py](../../../services/agent/core.py#L1052)。
- 工具返回时记录 `result_preview`：[core.py](../../../services/agent/core.py#L1080)。
- Worker 展示日志再次记录完整参数，并在结果路径记录完整 `result`：[worker.py](../../../services/agent/worker.py#L381)、[worker.py](../../../services/agent/worker.py#L407)。
- 完成日志把含参数的 `tool_events` 整体写入 INFO：[worker.py](../../../services/agent/worker.py#L531)。
- 共享日志 processor 没有全局脱敏步骤，现有 redaction helper 只在部分 raw hook 和工具审计路径显式调用：[logging.py](../../../config/logging.py#L13)、[redaction.py](../../../config/redaction.py#L1)。

**影响**

`enable_llm_raw_log=false` 和 `enable_ws_raw_log=false` 只能关闭专用 raw logger，不能保证 `app.log` 不含玩家信息、命令、外部网页内容或工具返回。当前“安全默认值”容易给维护者错误预期。

**建议**

- 普通 INFO 日志只记录 `args_keys/args_hash/result_length/status/error_kind`，删除正文、参数对象和结果预览。
- 需要展示的目录白名单参数只进入工具审计，不重复进入普通日志。
- 在 structlog shared processors 中增加统一的字段级 redaction processor，作为调用点遗漏时的最后防线。
- 为嵌套 dict/list、JSON body、URL query、headers 和异常文本使用同一递归脱敏策略。
- 启用 raw logger 时必须 `propagate=False`；当前 `_setup_named_logger()` 设置为 `True`，会使 raw 记录继续进入 root handler：[logging.py](../../../config/logging.py#L65)。

### P0-2：请求身份生成太晚，跨 Queue/Task 后因果链断裂

**证据**

- `ChatRequest` 已有自动生成的消息 `id` 和可选 `run_id`，但入站没有设置或使用它们：[messages.py](../../../models/messages.py#L12)、[messages.py](../../../models/messages.py#L20)。
- Worker 在获得会话锁后才生成 `run_id`：[worker.py](../../../services/agent/worker.py#L175)。
- `structlog.contextvars.merge_contextvars` 没有配套绑定调用，因此无法自动补全跨模块日志。
- 长生命周期 Worker、Broker bridge 和 writer 从各自启动时的上下文运行；即使入口绑定 ContextVar，也不能替代 QueueItem 上的显式上下文。

**影响**

无法计算真实 queue wait、session lock wait 和端到端延迟；无法将入口拒绝、断线、取消或下行失败归属到原始请求。不同玩家共享同一 connection 时，只靠时间和 connection ID 推断存在串错 trace 的风险。

**建议**

- 在 `_build_chat_request()` 创建稳定 `trace_id`，而不是等 Worker 消费。
- 新增小型 `TraceContext` 值对象，并显式进入 request/response envelope。
- Worker 每次执行生成 `attempt_id`，审批恢复保留 `trace_id`、生成新 `attempt_id`。
- `run_id` 暂时保留为兼容字段，并在迁移期与 `trace_id` 相同；不要把现有 `run_id` 改成每次 attempt 都变化，否则会破坏审批恢复和幂等键语义。
- 在每个 Queue 消费点使用 `bound_contextvars(...)` 临时绑定，在 `finally` 中恢复，禁止依赖全局 mutable logger bind。

### P0-3：工具审计存在互相矛盾的成功语义

**证据**

- 审计 record 同时有顶层 `status` 与内部 `result.success`：[audit.py](../../../services/agent/harness/audit.py#L120)。
- 当前样本有 7 条顶层成功、结果失败的记录。
- 统一执行层对非 `ToolResult` 返回默认视为成功，而兼容 wrapper 仍可能从字符串识别出失败：[execution.py](../../../services/agent/harness/execution.py#L632)、[audit.py](../../../services/agent/harness/audit.py#L211)。
- 现有测试明确允许 wrapper 返回失败字符串时顶层仍为 success：[test_runtime_harness_audit.py](../../../tests/test_runtime_harness_audit.py#L204)。

**影响**

同一条工具记录对“是否执行成功”给出两个答案。反馈闭环、失败率统计和人工排障可能得出相反结论，历史记录也难以进行稳定迁移。

**建议**

- 工具执行只产生一个规范化 `ToolExecutionOutcome`，trace 与工具审计都从该对象序列化。
- 分离 `policy_status`、`execution_status` 与 `external_state`，不要用一个 `status` 同时描述 wrapper 是否抛异常和外部操作是否成功。
- 固定执行状态：`succeeded / failed / denied / cancelled / timeout_unknown / not_executed`。
- 旧 JSONL reader 负责兼容并标记 `legacy_inconsistent=true`；不要原地重写历史文件制造虚假精确性。

### P0-4：审批要求在执行前抛出，但没有形成完整审计状态机

**证据**

- `REQUIRE_APPROVAL` 分支直接抛出 `ApprovalRequired`，此前没有写工具审计或运行追踪事件：[execution.py](../../../services/agent/harness/execution.py#L596)。
- Worker 记录 `tool_approval_pending`，但玩家批准/拒绝路径没有对应的结构化 decision 事件：[worker.py](../../../services/agent/worker.py#L1099)、[command_handlers.py](../../../services/gateway/command_handlers.py#L1020)。
- pending store 会自动清理过期项目，但过期和断连清理没有逐项留证：[approvals.py](../../../services/agent/harness/approvals.py#L353)。

**影响**

高风险工具最终未执行时，工具审计中可能完全没有记录；最终执行时，也无法回答批准者、等待时间、策略版本、批准参数摘要和恢复关系。审批是当前运行时 Harness 的关键安全边界，这个缺口优先级高于增加更多模型细节。

**建议**

使用以下状态事件，并共享 `trace_id/tool_call_id/approval_id/batch_id/policy_version/args_hash`：

```text
tool.proposed
  -> policy.decided(allow | deny | require_approval)
  -> approval.requested
  -> trace.suspended
  -> approval.decided(approved | denied)
  -> trace.resumed
  -> tool.execution.started
  -> tool.execution.completed | failed | cancelled
```

过期、断连清理和 owner 校验拒绝也必须产生终态事件，但不得记录完整批准命令或工具参数。

### P1-1：模型请求、重试和 usage 只在整体完成时可见

**证据**

- 流式处理遍历 PydanticAI 节点，但 `ModelRequestNode` 没有项目级 started/completed/failed 事件：[core.py](../../../services/agent/core.py#L908)。
- 整体完成事件只有聚合 usage 和工具列表，无法知道哪次模型请求慢、哪次发生 retry、工具前后分别消耗多少 token：[worker.py](../../../services/agent/worker.py#L531)。
- 项目已有 PydanticAI instrumentation 开关并强制 `include_content=False`，但没有配置 tracer provider、exporter 或本地 trace sink：[core.py](../../../services/agent/core.py#L64)、[settings.py](../../../config/settings.py#L843)。

**影响**

多轮 `model -> tool -> model` 的 trace 只能看到工具，无法定位首 token 延迟、Provider 重试、usage 激增或模型阶段失败。仅打开现有布尔配置也不会自然得到一个可在本地查询的文件。

**建议**

- 第一阶段在 Agent 节点边界记录本地 `model.request.*` 事件，包含 request index、retry index、provider、model、耗时、状态和 token/cache usage。
- 不记录 system prompt、玩家消息、completion 或 reasoning。
- 后续可将 PydanticAI 原生 instrumentation 接到 OpenTelemetry。官方 1.x instrumentation 会生成 Agent run、model request 和 tool call span，并支持 `InstrumentationSettings(include_content=False)`；它应作为 exporter 能力，而不是替代业务 `trace_id/attempt_id/approval_id`。

### P1-2：下行响应和外部操作不可关联到工具调用

**证据**

- Worker 将 `StreamChunk` 或裸 dict 放入 response queue，消息模型缺少 trace 上下文：[worker.py](../../../services/agent/worker.py#L400)、[messages.py](../../../models/messages.py#L55)。
- Broker bridge 只知道 connection 和消息内容，无法记录所属工具或 trace：[broker_bridge.py](../../../services/gateway/broker_bridge.py#L99)。
- Minecraft `commandRequest` 使用自己的 request ID；Add-on capability 也使用自己的请求标识，但这些 ID 没有写回工具 span。
- bridge 遇到 delivery 不存在或部分发送异常时存在直接返回/丢弃路径，没有原 trace 的明确终态。

**影响**

工具日志显示“调用成功”时，无法判断命令是否收到 commandResponse、UI/tellraw 是否送达、分片发送是否中断。断线后仍在运行的 Agent 也可能继续产生副作用，而 trace 没有 cancelled 或 delivery_failed 终态。

**建议**

- 所有 response queue item 使用统一 `CorrelatedEnvelope[T]`，而不是逐个为 dict 增加可选字段。
- 记录 `minecraft_request_id`、`addon_request_id`、`delivery_type`、`target_class`、chunk count 和 byte count，不记录 command line 或 payload。
- `delivery.enqueued -> started -> completed/failed` 必须可回到同一 `trace_id`，工具触发的外部操作还需携带 `tool_call_id`。
- 首期不修改 `mcbe-ws-sdk`；宿主可先记录 host span。只有需要 SDK 内部 chunk 精度时，再单独为 SDK 设计 instrumentation callback。

### P1-3：app 文件不可机器查询，工具审计 writer 仍为 O(N) 重写

**证据**

- 控制台在非 TTY 时使用 JSONRenderer，但 `app.log` 始终使用 ConsoleRenderer 文本：[logging.py](../../../config/logging.py#L122)、[logging.py](../../../config/logging.py#L137)。
- 工具审计 enqueue 已经不阻塞调用方，这是正确改进；但后台每写一条仍读取整个文件、保留末尾 N 行并原子重写：[audit.py](../../../services/agent/harness/audit.py#L222)、[audit.py](../../../services/agent/harness/audit.py#L233)。
- writer 队列满只写 warning，没有持久 dropped count 或审计缺口指标：[audit.py](../../../services/agent/harness/audit.py#L293)。

**影响**

异常 traceback 会把 app 文件拆成多行，简单 awk/jq 无法可靠解析。随着工具记录增长，writer 磁盘开销线性上升；发生积压时，后续 warning 本身也未必能被关联到受影响的 trace。

**建议**

- 控制台保留人读 renderer，文件 handler 改为每行一个 JSON object。
- 新 trace journal 采用 append-only JSONL，按日期/大小轮转并按保留期删除旧文件，不在每次写入时重写全部历史。
- writer 保持 `put_nowait`，增加 `events_enqueued/written/dropped/write_failed/queue_depth` 健康计数。
- shutdown flush 超时应写一个不含业务正文的 `trace_writer_gap` 运维事件。

### P1-4：取消、断线和部分失败缺少可靠终态

**证据**

- hook 创建的 dispatch task 在取消时不记录请求级终态：[hook.py](../../../services/gateway/hook.py#L157)。
- Worker 取消和 Agent 异常会写局部日志，但错误下行消息模型不带 trace 身份。
- `CancelledError` 不应被普通工具异常分类吞掉；工具已经产生外部副作用时，取消后的外部状态可能未知。
- 断开连接会清理部分 session/approval 状态，但队列中已排队或正在运行的请求不一定形成 cancelled/abandoned 终态。

**影响**

按 trace 查看时会出现永久“运行中”的记录，无法区分进程崩溃、玩家断线、预算超限、Provider 错误和工具外部状态未知。

**建议**

- 每个 trace 最终必须是 `completed / failed / cancelled / expired / abandoned` 之一；审批等待期间可暂时为 `suspended`。
- 每个 attempt 必须有且仅有一个终态。
- 对副作用工具取消使用 `external_state=unknown`，不得自动重试。
- 进程启动时可扫描最近 journal 中没有终态的 trace，并在查询层显示为 `abandoned`，不伪造正常完成。

### P2-1：存在未使用的第二套事件模型

`core/events.py` 定义了通用 Event/EventType 和 publish/subscribe 结构，但生产 Agent 路径没有使用。直接扩展这套模型会同时引入新的事件总线与新的持久化语义，增加认知负担。

**建议**

不要为了 trace 激活一个全局业务事件总线。第一版使用窄接口 `TraceRecorder.emit(event_name, context, attributes)`，只负责构造、校验和异步写入追踪事件。若未来其他业务确实需要统一 domain events，再独立评估 `core/events.py` 的去留。

## 6. 当前做得合理的部分

以下能力应直接复用，不需要推倒重写：

- `ChatRequest`、`AgentDependencies`、`PendingApproval` 已显式携带 `run_id`、玩家和对话身份。
- 审批恢复复用原 `run_id`，能够保留逻辑请求身份。
- 工具执行已集中到 Harness wrapper，适合统一产生 policy/tool trace 事件。
- 工具目录已有参数预览白名单和敏感字段规则：[audit.py](../../../services/agent/harness/audit.py#L91)。
- `config/redaction.py` 已覆盖 URL、header、mapping、异常和长度限制，可扩展为统一 processor。
- 工具审计 writer 已做到调用线程非阻塞、AgentRuntime 管理生命周期和 shutdown flush。
- PydanticAI instrumentation 已采用 `include_content=False` 的安全方向。
- raw 日志默认关闭，配置示例与 Settings 默认值一致：[config.example.json](../../../config.example.json#L199)、[settings.py](../../../config/settings.py#L885)。
- 多玩家业务状态仍显式使用当前事件的 `player_name/sender`；trace 改造必须保留这一原则。

## 7. 方案比较

### 方案 A：只规范现有 app.log

改造内容：补 `run_id`、改 JSON 文件 renderer、删除正文、增加几个关键日志。

优点：改动最小，无新 writer 和 CLI。

缺点：普通日志事件仍缺 schema/version/父子关系；工具审计继续分离；审批暂停、多 attempt、transport ID 和终态难以可靠表达。随着业务迭代，调用点会再次产生字段漂移。

结论：只能作为 P0 止血，不足以满足“完整看到一个 trace”。

### 方案 B：本地运行追踪 journal + CLI（推荐）

改造内容：建立版本化 TraceContext/Event，写入 append-only JSONL，提供按 trace 查询的本地 CLI；普通日志和工具审计保持各自职责。

优点：零外部服务；数据契约清晰；能覆盖异步 Queue、审批恢复和下行交付；后续可无损映射到 OpenTelemetry。

缺点：需要新增事件 schema、writer、CLI 和端到端契约测试；必须谨慎处理重复记录、终态和旧审计兼容。

结论：最符合当前部署形态和需求，应作为首期目标。

### 方案 C：OpenTelemetry / Logfire 优先

改造内容：配置 tracer provider、span processor 和 exporter，直接使用 PydanticAI 原生模型/工具 span，再补业务 span。

优点：标准生态、可视化和跨服务扩展能力最好。

缺点：增加依赖和部署面；PydanticAI span 不知道 MCBE 玩家审批、Broker、游戏 commandResponse 等业务语义；没有 exporter 时本地仍不可查；如果先做平台接入，容易把现有正文泄露带到新后端。

结论：作为方案 B 的后续 exporter，而不是首期替代品。

## 8. 推荐架构

### 8.1 三层记录

```text
业务代码
  -> structlog             -> console + app.jsonl（运维日志）
  -> TraceRecorder         -> agent_traces.jsonl（完整运行追踪元数据）
  -> ToolAuditRecorder     -> runtime_harness_tools.jsonl（工具审计/反馈闭环）

TraceRecorder / ToolAuditRecorder
  -> 同一个 ToolExecutionOutcome
  -> 同一个 TraceContext
  -> 同一个异步 JSONL writer 基础设施
```

工具执行结果必须先规范化，再分别投影为运行追踪事件和工具审计记录，避免两个 sink 独立判断成功与否。

### 8.2 TraceContext

建议字段：

| 字段 | 生命周期 | 说明 |
| --- | --- | --- |
| `trace_id` | 一次玩家意图 | 在入站生成；审批暂停/恢复不变 |
| `run_id` | 兼容字段 | 第一版等于 `trace_id`，保留现有幂等与日志兼容 |
| `attempt_id` | 一次 Agent 执行 | 初次执行和每次审批恢复均生成新值 |
| `message_id` | 一条 Broker 消息 | 复用 `BaseMessage.id` 或新 envelope ID |
| `span_id` | 一段操作 | model/tool/external/delivery 各自独立 |
| `parent_span_id` | 父操作 | 建立树状因果关系 |
| `tool_call_id` | 一次模型工具调用 | 使用 PydanticAI 原生 ID |
| `connection_id` | 当前连接 | 内存中完整传递；持久化只存短 ID 或稳定散列 |
| `player_name` | 当前事件玩家 | 业务路径显式传递；持久化按身份策略处理 |
| `conversation_id` | 当前对话 | 不从全局状态推断 |

contextvars 的职责是让当前 task 内的普通 structlog 自动带上这些字段；跨 Queue 和线程必须把 TraceContext 放入 envelope，消费时重新绑定。

### 8.3 TraceEvent v1

```json
{
  "schema_version": 1,
  "event_id": "01...",
  "event_name": "tool.execution.completed",
  "timestamp": "2026-07-22T10:00:00.123456Z",
  "sequence": 12,
  "level": "info",
  "component": "agent.harness",
  "trace_id": "...",
  "run_id": "...",
  "attempt_id": "...",
  "span_id": "...",
  "parent_span_id": "...",
  "tool_call_id": "...",
  "connection_ref": "...",
  "player_ref": "...",
  "conversation_id": "default",
  "status": "succeeded",
  "duration_ms": 42,
  "attributes": {
    "tool_name": "get_player_snapshot",
    "risk": "low",
    "error_kind": null,
    "external_state": "known"
  }
}
```

约束：

- `attributes` 必须按 event schema 白名单序列化，不能接受任意 `**kwargs` 后直接落盘。
- `event_name/schema_version/status` 使用固定枚举或 Pydantic model 校验。
- `timestamp` 用 UTC wall clock；耗时用 monotonic clock 计算。
- `sequence` 在同一 trace 内单调递增；查询时使用 `sequence,event_id` 稳定排序。
- 玩家原文、prompt、completion、reasoning、完整工具结果、完整 command line 和 WebSocket payload默认禁止进入事件。

### 8.4 最小事件集合

| 阶段 | 事件 |
| --- | --- |
| 入站 | `trace.started`, `request.accepted/rejected` |
| 排队 | `queue.enqueued`, `queue.dequeued`, `session_lock.acquired` |
| Agent | `agent.attempt.started/suspended/resumed/completed/failed/cancelled` |
| 上下文 | `context.loaded`, `context.compressed`, `context.budget_rejected` |
| 模型 | `model.request.started/completed/failed` |
| 工具 | `tool.proposed`, `policy.decided`, `tool.execution.started/completed/failed/cancelled`, `tool.idempotent_hit` |
| 审批 | `approval.requested/decided/expired/owner_rejected` |
| MCP/外部 | `mcp.request.*`, `minecraft.command.*`, `addon.request.*` |
| 下行 | `delivery.enqueued/started/completed/failed` |
| Trace 终态 | `trace.completed/failed/cancelled/expired/abandoned` |

第一版不记录每个 LLM token delta 或每个普通文本 chunk。对排障更有价值的是模型请求、工具、审批和交付边界；高频 chunk 只在 delivery completed 上聚合数量和字节。

### 8.5 本地 CLI

建议新增独立顶层命令，避免把“运行追踪”混入只面向工具反馈的 `runtime-harness analyze`：

```bash
python cli.py trace list --recent 20
python cli.py trace list --status failed --player <name>
python cli.py trace show <trace_id>
python cli.py trace show <trace_id> --json
python cli.py trace health
```

默认 `trace show` 输出示例：

```text
TRACE 7e3...  player=fan...  conversation=default  status=completed  total=6.34s

  +0ms     request.accepted                 chat/tellraw
  +2ms     queue.enqueued
  +18ms    agent.attempt.started            attempt=1 worker=0
  +21ms    model.request.started             deepseek/deepseek-v4
  +1.44s   model.request.completed           input=4415 output=77
  +1.45s   tool.proposed                     mcwiki_search call=call_00...
  +1.45s   policy.decided                    allow risk=low
  +1.46s   tool.execution.started            mcwiki_search
  +4.26s   tool.execution.completed          succeeded duration=2806ms
  +4.27s   model.request.started             request=2
  +6.31s   model.request.completed           input=5271 output=124
  +6.32s   delivery.completed                tellraw chunks=2 bytes=318
  +6.34s   trace.completed                   model_requests=2 tools=1
```

审批 trace 应在同一输出中明确显示 `suspended -> approval.decided -> attempt=2 resumed`，而不是把它拆成两个不相关请求。

## 9. 文件级修改建议

| 文件/模块 | 建议修改 |
| --- | --- |
| `models/messages.py` | 增加 `TraceContext`/correlated envelope；让所有下行类型显式携带当前玩家和 trace 身份 |
| `services/gateway/hook.py` | 在 dispatch 边界绑定/清理 context；为异常和取消写入 trace 终态 |
| `services/gateway/command_handlers.py` | 入站创建 `trace_id`；审批 decision/resume 产生事件；恢复生成新 `attempt_id` |
| `core/queue.py` | QueueItem 保存 trace context 和 enqueue monotonic time；记录 queue/lock wait |
| `services/agent/worker.py` | attempt 生命周期、上下文/Provider/完成终态；删除 INFO 正文；response envelope 传递 trace |
| `services/agent/core.py` | model request span；tool proposed/result 只记录元数据；映射 Pydantic tool ID |
| `services/agent/harness/execution.py` | policy/approval/tool execution 事件；统一 `ToolExecutionOutcome` |
| `services/agent/harness/approvals.py` | pending/decision/expired/cleanup 状态事件，保持 owner 与当前 `player_name` |
| `services/agent/harness/audit.py` | 从统一 outcome 生成审计；兼容旧记录；改用通用 append writer |
| `services/gateway/broker_bridge.py` | 消费 response envelope 后临时绑定上下文；记录聚合 delivery span |
| `services/gateway/ws_command_runner.py` | 保存 trace/tool context 与 Minecraft request ID 映射，记录 resolve/timeout/orphan |
| `config/logging.py` | app 文件改 JSONRenderer；加统一脱敏 processor；raw logger `propagate=False` |
| `config/redaction.py` | 增加递归 JSON/body 脱敏和事件 schema allowlist helper |
| `config/settings.py` / `config.example.json` | 新增 trace enabled/path/retention/identity mode/queue size；默认禁用正文 |
| `services/agent/trace.py`（新增） | TraceContext、TraceEvent schema、TraceRecorder、状态校验 |
| `services/agent/event_writer.py`（新增或抽取） | 通用异步 append-only JSONL writer、轮转和健康计数 |
| `cli.py` / `services/agent/trace_query.py` | `trace list/show/health` 与旧 schema 容错读取 |

不要在首期修改 `mcbe-ws-sdk/`。若宿主边界无法获得准确 Add-on/分片 request ID，再单独为 SDK 提交 instrumentation 接口设计。

## 10. 分阶段落地

### P0：日志安全与语义纠正

- 删除普通 INFO 中的完整参数、结果、title 和 tool_events args。
- 增加全局 redaction processor，修复 raw logger propagation。
- 统一工具 outcome，消除顶层成功/内部失败。
- app 文件改为 JSONL；控制台保持人读格式。
- 为现有新记录增加 `schema_version`。

完成标志：关闭 raw 时，所有默认 sink 都不包含完整正文；同一工具记录只有一个明确执行状态。

### P1：贯穿链路的 TraceContext

- 在入站生成 `trace_id`，显式传过 request/response queue。
- 增加 `attempt_id`，审批恢复保持 trace、切换 attempt。
- 在 Gateway、Worker、Harness、Bridge 的消费边界绑定 contextvars。
- 把 Minecraft/Add-on request ID 映射到 trace/tool span。
- 统一正常、失败、取消、过期和断线终态。

完成标志：普通聊天、工具调用和审批恢复都能从入口关联到下行交付。

### P2：事件 journal 与本地查询

- 实现 TraceEvent v1 和 append-only writer。
- 覆盖最小事件集合，增加 writer 健康计数。
- 实现 `trace list/show/health`。
- 保持 `runtime-harness analyze` 继续只消费工具审计，或从 trace 中筛选工具投影，但不改变术语和输出语义。

完成标志：无需读取源码或手工拼 grep，一条 CLI 命令即可解释一个 trace 的阶段、操作、耗时和结果。

### P3：测试与可选 exporter

- 增加完整 trace replay fixture、多玩家并发、审批状态机、取消/断线和 writer 故障测试。
- 修复当前 deferred multi-approval 测试挂起后，将其纳入默认离线门禁。
- 使用内存 exporter 核验 PydanticAI instrumentation 不含正文。
- 需要跨进程或 UI 时再增加 OTLP exporter；本地 journal 保持可独立使用。

完成标志：本地 trace 与可选 OTel span 有稳定 ID 映射，打开 exporter 不改变业务行为或隐私默认值。

## 11. 测试建议

### 11.1 Trace 契约

- 无工具普通聊天：`trace.started -> model.request -> delivery -> trace.completed`。
- 单工具聊天：model/tool/model 的父子关系、usage 与终态完整。
- 审批恢复：一个 trace、两个 attempts、精确 approval/tool call 关联、一次最终执行。
- 策略 deny：有 policy 与 tool `not_executed` 终态，没有执行 span。
- MCP timeout：有 server/operation/error，已执行副作用不重放。

### 11.2 并发与生命周期

- 同 connection 两名玩家交错执行，所有事件仍按 `trace_id/player_name` 正确分桶。
- contextvars 在正常、异常和取消后不泄漏到下一个请求。
- disconnect 后已排队、运行中、等待审批三类 trace 都有明确状态。
- title generation 等后台 task 要么显式 link 原 trace，要么清空上下文后创建内部 trace。

### 11.3 隐私

- 在玩家消息、嵌套工具参数、URL、header、异常、LLM body 和工具结果中注入 canary secret。
- 扫描 app、trace、工具审计和 raw disabled 场景，断言 canary 不出现。
- raw enabled 时断言数据只进入专用 raw 文件，不传播到 app/console。
- 默认不记录 reasoning；配置也不提供“记录完整 chain-of-thought”选项。

### 11.4 Writer 与查询

- enqueue 不执行同步磁盘 I/O。
- 队列满、磁盘满、轮转和 shutdown timeout 都增加 dropped/gap 计数，但不改变工具结果。
- 单行异常仍是有效 JSON，不产生多行 traceback。
- CLI 对乱序事件、未知 schema、截断末行、legacy 工具记录和缺失终态有明确展示。

## 12. 验收标准

1. 任意被接受的玩家聊天都有稳定 `trace_id`，从入站、排队、Agent、工具/审批到下行交付可查询。
2. 审批暂停与恢复保持同一 trace，并以不同 attempt 展示；参数变化或 owner 不匹配不能复用批准。
3. 每个 attempt 恰好一个终态；每个非 suspended trace 最终为 completed/failed/cancelled/expired/abandoned 之一。
4. 每个工具提出都有 policy 结果；被执行的工具都有 started 与 terminal 结果；结果状态不依赖字符串前缀猜测。
5. model request 至少记录 provider/model、request/retry index、耗时、状态和 usage，不记录正文。
6. Minecraft/Add-on/下行 request ID 能回到原 `trace_id`，发送失败不会只留在无关联的普通日志中。
7. 默认 app、trace 和工具审计不含完整玩家消息、prompt、completion、reasoning、完整工具结果、凭据或 raw payload。
8. trace writer 磁盘故障不阻塞或改变 Agent/工具主路径，并能明确报告审计缺口。
9. `python cli.py trace show <trace_id>` 能在单个视图中展示时间线、模型 usage、工具/审批和最终结果。
10. 默认离线测试不访问真实 Provider，且审批/deferred 路径不会挂起。

## 13. 明确不做

- 不迁移 PydanticAI，不引入多 Agent、Planner 或持久工作流。
- 不把 OpenTelemetry、Jaeger、Tempo 或 Logfire 作为首期运行条件。
- 不记录完整 prompt、completion、reasoning、工具结果或 WebSocket payload来换取可见性。
- 不把“工具审计”泛化为所有日志，不破坏反馈闭环的既有语义。
- 不仅依赖 ContextVar 跨 Queue 传递身份。
- 不在首期修改 `mcbe-ws-sdk` 或重复实现 SDK 已拥有的下行分片逻辑。
- 不为每个工具重复实现 trace；横切事件应从统一 Harness wrapper 和 transport 边界产生。

## 14. 推荐决策

采用**方案 B：本地运行追踪 journal + CLI**，按 P0 -> P1 -> P2 -> P3 顺序落地。

首个实现批次应只处理 P0 和 P1：先保证身份贯通、状态一致和默认日志安全，再建设查询界面。否则即使立即生成漂亮的 trace 时间线，底层事件仍可能泄露正文、错误归类或在审批/下行边界断链。

PydanticAI 和 structlog 已经提供所需基础能力：PydanticAI instrumentation 能生成 Agent/model/tool span 并关闭 content；structlog contextvars 能在当前执行上下文自动合并字段。项目真正需要补的是 MCBE 业务身份、Queue envelope、审批/transport 状态机、本地持久化和查询契约，而不是替换框架。

## 15. 参考资料

- 项目术语：[CONTEXT.md](../../../CONTEXT.md)
- 项目架构：[CLAUDE.md](../../../CLAUDE.md)
- 上一轮运行时 Harness 审查：[2026-07-21-runtime-harness-review.md](./2026-07-21-runtime-harness-review.md)
- 上一轮整改计划：[2026-07-21-runtime-harness-review-followups.md](../plans/2026-07-21-runtime-harness-review-followups.md)
- PydanticAI instrumentation：https://ai.pydantic.dev/logfire/
- structlog contextvars：https://www.structlog.org/en/stable/contextvars.html

