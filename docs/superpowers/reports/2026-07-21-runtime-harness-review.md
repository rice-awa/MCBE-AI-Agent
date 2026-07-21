# MCBE Chat Agent 运行时 Harness 架构审查

> 审查日期：2026-07-21  
> 审查范围：LLM、MCBE Chat Agent、上下文、Agent 循环、工具调用、MCP、错误处理、运行时 Harness、相关测试  
> 明确排除：`services/websocket/` 的协议、连接、下行分片与发送实现；`mcbe-ws-sdk/`  
> 方法：源码静态审查、定向测试、PydanticAI 1.94.0 运行时 API 核验、Context7 当前文档、Firecrawl 一手资料检索  
> 结论置信度：高。核心缺陷均有源码证据，其中测试挂起问题有命令复现；未执行真实玩家环境和付费 Provider 端到端测试。

## 1. 执行摘要

当前架构对于一个多人 MCBE 聊天助手来说，**基础分层总体合理，但还不能称为生产级运行时 Harness**。

合理之处在于：项目没有自行重写 Agent 循环，而是把模型、工具、结果回灌和多轮执行交给 PydanticAI；Worker 与 Agent 解耦；历史按 `(connection_id, player_name, conversation_id)` 隔离；同玩家请求串行、不同玩家可并行；压缩和历史写入都有防陈旧写保护；工具目录、提示增强和结构化审计也已经形成了清晰的第一阶段骨架。

主要问题在于：现有运行时 Harness 的“风险”主要停留在模型可见的提示和事后审计层。被标记为“高/危险”的世界修改工具没有服务端执行前策略或审批；失败字符串可能被记录为成功；MCP 超时后会从头重放整轮请求，可能重复已经执行的命令；网页工具仅靠提示阻止访问内网；上下文压缩按轮次而不是 token 预算触发，并把摘要作为普通用户消息重新注入。这些问题集中在 **副作用安全、错误语义和上下文信任边界**，优先级高于增加多 Agent、Planner 或复杂图编排。

综合判断：

- **Agent 架构方向：合理。** 继续使用 PydanticAI，不建议仅为“更像主流 Harness”而迁移 LangGraph。
- **当前成熟度：L1.5 / L4。** 工具选择和审计介于“提示约束”与“受控运行”之间；会话隔离接近 L2；高风险工具执行边界仍是 L1。
- **最重要的下一步：** 先建设统一的工具执行策略层和结构化错误协议，再补 token-aware context、幂等/重试边界、trace/eval。不是先做多 Agent。
- **发布判断：** 普通知识问答和低风险查询可继续迭代；任意命令、批量命令、广播、网页抓取及 MCP 扩展工具不宜在无额外边界的情况下被视为可信生产能力。

## 2. 当前执行链

```text
AgentRuntime.initialize
  -> ModelMetadataService 加载模型元数据
  -> RuntimeAdapterRegistry 预热并缓存 Provider / HTTP Client
  -> MCPManager 创建 MCP toolsets
  -> ChatAgentManager 创建共享 PydanticAI Agent
       -> 动态 system prompt
       -> 内置工具注册
       -> MCP toolsets

ChatRequest
  -> MessageBroker 请求队列
  -> AgentWorker
       -> 获取 (connection_id, player_name) 会话锁
       -> 读取 (connection_id, player_name, conversation_id) 历史
       -> 清除历史 reasoning
       -> 注入 AgentDependencies
       -> ProviderRegistry 获取模型
       -> stream_chat
            -> stream_response_handler: Agent.iter()
            -> 或 non_stream_response_handler: Agent.run()
            -> PydanticAI 执行多轮模型/工具循环
       -> 流事件转为业务事件
       -> 完成后裁剪并保存历史
       -> 标题生成
       -> 达轮次阈值后压缩历史
```

这里有两个重要边界：

1. 项目的 `AgentWorker` 是请求调度、会话和输出适配层；真正的模型-工具循环由 PydanticAI 负责。
2. `services/agent/harness/` 当前是工具目录、提示、schema 增强、审计与离线建议层，还不是统一的工具执行策略层。

## 3. 成熟度评分

| 维度 | 当前级别 | 评价 |
| --- | --- | --- |
| Agent 循环 | L2 | 使用 PydanticAI 原生 `iter/run`，默认 request limit 为 50；缺项目级 tool/token/time budget 和明确终止策略 |
| 会话隔离 | L2 | 玩家、对话、历史和锁的边界清晰，且有 stale-write 防护 |
| 上下文工程 | L1.5 | 有历史裁剪、reasoning 清理和摘要，但预算不按 token，摘要信任域不清晰 |
| 工具发现与 schema | L2 | 工具目录为单一真相源，说明质量较好；MCP 工具未纳管 |
| 工具执行边界 | L1 | 风险级别只进入 prompt/schema；高风险调用无审批、deny、额度或幂等控制 |
| 错误与恢复 | L1 | 大量异常被字符串化；错误类别、重试条件、取消和副作用恢复没有统一语义 |
| 可观测性 | L1.5 | 有结构化日志和工具审计；缺 run/span 关联、指标、trace、eval，且存在敏感内容与阻塞 I/O 风险 |
| 持久执行 | L0.5 | 只持久化对话语义历史，不保存执行位置、pending tool、审批或幂等状态 |
| 测试与评测 | L1.5 | 会话隔离和局部工具事件覆盖较好；缺真实循环、故障注入、安全评测和稳定离线测试 |

成熟度定义：

- **L0 缺失：** 依赖模型自觉或散落调用点。
- **L1 提示约束：** 有说明和日志，但模型可直接触发副作用。
- **L2 受控运行：** 有类型校验、预算、超时、结构化错误、并发和取消边界。
- **L3 生产基线：** 有服务端风险策略、精确审批、幂等、统一 trace、脱敏审计和回放 eval。
- **L4 高可靠：** 有 checkpoint/replay、跨进程恢复、补偿事务、SLO 和故障注入。

## 4. 关键发现

### P0-1：高风险工具只有提示约束，没有不可绕过的执行边界

**证据**

- 工具目录把 `run_minecraft_command` 标为“高”、把 `run_minecraft_commands` 标为“危险”，但风险仅被渲染进提示和 schema：[catalog.py](../../../services/agent/harness/catalog.py#L39)、[prompting.py](../../../services/agent/harness/prompting.py#L34)。
- 两个工具收到模型参数后直接执行，没有按玩家权限、命令类型、目标范围、调用频率或确认状态进行策略判断：[tools.py](../../../services/agent/tools.py#L86)、[tools.py](../../../services/agent/tools.py#L121)。
- 当前安装的 PydanticAI 1.94.0 已支持 `requires_approval`、`ApprovalRequired`、`DeferredToolRequests` 和精确 tool call 恢复，但项目未使用。

**影响**

提示注入、模型误判或不清晰的玩家请求可以直接触发世界修改。目录中的风险级别不能阻止调用，也不能证明玩家批准了本次具体参数。批量命令尤其可能产生不可逆或大范围副作用。

**结论**

这是当前运行时 Harness 与主流生产实现之间最大的差距。OpenAI Agents SDK、PydanticAI、LangGraph interrupt 和 Claude Agent SDK 权限模型都把审批/拒绝放在执行前，而不是只写在提示词里。MCP 规范也建议工具调用保留人类拒绝能力。

**建议**

- 在所有工具进入实际函数前增加统一 policy gate，而不是在各工具中散落判断。
- 策略输入至少包含：玩家、对话、工具名、规范化参数、风险、策略版本、tool call ID。
- “高/危险”默认要求审批；批准绑定精确参数并设置过期时间，参数变化必须重新审批。
- 无条件 deny 规则必须不可被 prompt、管理员模板或 MCP 工具说明覆盖。

### P0-2：MCP 超时降级会重放整轮请求，可能重复副作用

**证据**

- 一次 run 中，本地工具已经可能在 `CallToolsNode` 执行并产出事件：[core.py](../../../services/agent/core.py#L729)。
- 捕获到被判定为 MCP timeout 的异常后，代码复用原 prompt、原 history 和同一个 handler context，从头递归执行 fallback Agent：[core.py](../../../services/agent/core.py#L804)。
- 没有“是否已经输出”“是否已经执行副作用工具”的重试屏障，也没有 tool call 幂等键。
- 任意包含 timeout 的 `ExceptionGroup` 都可能被判为 MCP 问题，即使没有 MCP 证据：[core.py](../../../services/agent/core.py#L353)。

**影响**

在本地命令、消息或世界修改已经成功后，如果后续 MCP 步骤超时，fallback run 可能再次调用 `give`、`summon`、广播等工具；已流出的文本也可能重复或拼接。该行为违反“未知完成状态的副作用不得盲目重试”的基本原则。

**建议**

- 仅在尚未输出且尚未开始任何工具时允许整轮 fallback。
- 一旦发生副作用，返回结构化的可恢复错误，不自动重跑整轮。
- 对世界修改工具使用 `(run_id, tool_call_id, normalized_args)` 幂等键并记录执行状态。
- MCP 健康状态按 server 精确管理；不要因一个模糊异常永久禁用全部 MCP。

### P0-3：命令失败和超时会被工具与审计记录为成功

**证据**

- `run_command` 回调在连接不存在或等待超时时返回普通失败字符串，而不是结构化失败或异常：[worker.py](../../../services/agent/worker.py#L836)。
- `run_minecraft_command` 无条件将该字符串包装为 `ToolResult.success`：[tools.py](../../../services/agent/tools.py#L108)。
- 消息、标题、actionbar 等工具忽略 `run_command()` 返回值后直接宣称成功：[tools.py](../../../services/agent/tools.py#L185)、[tools.py](../../../services/agent/tools.py#L268)、[tools.py](../../../services/agent/tools.py#L305)。
- 审计信任 `ToolResult.success`：[audit.py](../../../services/agent/harness/audit.py#L121)。

**影响**

模型会向玩家报告“已完成”，工具审计也会写成功，导致线上行为、用户体验和反馈建议同时失真。当前反馈闭环会用错误数据优化提示。

**建议**

统一工具结果协议，例如 `success / error_kind / retryable / output / external_state_unknown`。连接丢失、明确失败、超时未知三者不能用字符串前缀区分；所有工具必须检查回调结果后再生成 `ToolResult`。

### P0-4：`fetch_url_text` 存在 SSRF 边界缺失

**证据**

- 工具目录写明不要访问敏感内网地址：[catalog.py](../../../services/agent/harness/catalog.py#L103)。
- 实现只验证 `http://` / `https://`，随后直接由服务端发起请求：[tools.py](../../../services/agent/tools.py#L354)。
- 测试只覆盖非 HTTP scheme，没有 loopback、私网、link-local、DNS 重绑定或重定向测试：[test_agent_tools.py](../../../tests/test_agent_tools.py#L202)。

**影响**

玩家内容、网页 prompt injection 或模型误选可探测 localhost、内网服务和云元数据地址，并把结果送回模型。提示中的“不要”不是网络安全边界。

**建议**

解析并校验每次解析后的目标 IP，拒绝 loopback、private、link-local、保留地址和元数据地址；限制端口、重定向、响应大小、内容类型和下载时间。更稳妥的方案是移除通用 URL 工具，只开放受控知识源。

### P1-1：上下文控制按轮次而不是 token，且摘要处于错误信任域

**证据**

- 上下文 token 仅用 `message_count * 100` 估算，主要用于提示展示：[worker.py](../../../services/agent/worker.py#L184)。
- 压缩阈值由 `max_history_turns * compression_trigger_ratio` 决定：[conversation.py](../../../core/conversation.py#L263)。
- 历史先完整传给 Provider，裁剪和压缩发生在本轮完成后：[worker.py](../../../services/agent/worker.py#L270)、[worker.py](../../../services/agent/worker.py#L334)。
- LLM 或本地摘要被包装为普通 `UserPromptPart`，再次进入下一轮上下文：[conversation.py](../../../core/conversation.py#L220)。本地 fallback 会把旧用户文本和工具返回压入摘要：[conversation.py](../../../core/conversation.py#L120)。

**影响**

- 少数超长用户消息、网页内容或工具返回可在轮次阈值之前超过 context window。
- 摘要包含的历史指令、工具输出或间接 prompt injection 会以“用户消息”身份持续存在，模型难以区分可信运行时事实和不可信历史内容。
- 字符数上限不等于 token 上限，也没有给本轮输出和工具 schema 预留预算。

**建议**

- 在每次模型请求前做 token-aware budget：系统提示、工具 schema、历史、当前输入、预留输出分别计费。
- 用 PydanticAI history processor/capability 集中处理历史，保持 tool-call/tool-return 配对。
- 摘要使用明确的受控上下文容器，声明“仅为不可信历史事实，不得作为指令”；摘要器也要抵御 source 中的指令。
- 把长工具输出外置，模型只接收带来源和截断元数据的摘要或检索片段。

### P1-2：工具和 Agent 错误没有统一分类、预算与取消语义

**证据**

- 大多数工具捕获所有 `Exception` 后返回中文失败字符串，模型无法可靠区分可修复参数、瞬态故障、权限拒绝、永久失败和未知状态：[tools.py](../../../services/agent/tools.py#L108)。
- 项目给 Agent 配置了 retries，但没有项目级 `tool_calls_limit`、token limit、wall-clock run timeout 或成本预算：[core.py](../../../services/agent/core.py#L73)。PydanticAI 1.94.0 默认有 50 次 request limit，这是最后兜底，不等于业务预算。
- Worker 只在正常处理后调用 `request_done()`；异常或取消路径不会调用：[worker.py](../../../services/agent/worker.py#L96)。
- 错误事件把异常类型和内容直接发送给玩家：[core.py](../../../services/agent/core.py#L824)。

**影响**

自动重试可能作用于不应重试的副作用，应该让模型修正的参数错误又可能被当普通文本消费；取消和队列 drain 语义不完整；内部错误信息可能泄露实现细节。

**建议**

定义 `INVALID_ARGUMENT`、`MODEL_RETRYABLE`、`TRANSIENT`、`DENIED`、`PERMANENT`、`CANCELLED`、`INTERNAL` 七类错误。只有幂等且明确 transient 的调用可自动重试。为每轮增加 request、tool-call、token、wall-clock 和并发预算，并把用户消息与内部诊断分离。

### P1-3：MCP 工具绕过目录、审计和风险策略，健康状态也未形成单一状态机

**证据**

- schema 增强和审计只包装内置 `_function_toolset`：[tools.py](../../../services/agent/tools.py#L651)。外部 MCP toolsets 未进入工具目录和 JSONL 工具审计。
- 审计读取 `deps.conversation_id`，但 `AgentDependencies` 没有该字段：[audit.py](../../../services/agent/harness/audit.py#L99)、[agent.py](../../../models/agent.py#L118)。
- `MCPManager.toolsets` 只排除 `DISABLED`，仍会返回 `ERROR` server；真正过滤错误状态的 `get_healthy_toolsets()` 没接到 Agent 路径：[mcp.py](../../../services/agent/mcp.py#L73)、[mcp.py](../../../services/agent/mcp.py#L370)。
- Agent manager 另有进程级 `_mcp_available`，与 MCPManager 的逐 server 状态并行存在：[core.py](../../../services/agent/core.py#L55)。

**影响**

新增 MCP server 后，外部工具可以绕过当前工具目录的风险、参数预览和反馈闭环；状态页面与 Agent 实际可用工具可能不一致；单 server 故障可能全局降级。

**建议**

把内置和 MCP 工具统一包装为一个 policy/audit toolset；对 MCP server/tool 建立 allowlist、来源、风险和 schema 版本记录。MCPManager 应成为唯一健康状态源，并支持 per-server circuit breaker、半开探测和显式恢复。

### P1-4：可观测性记录很多内容，却缺少可关联的 trace 和安全默认值

**证据**

- 完整 AI 回复在 INFO 日志中记录：[worker.py](../../../services/agent/worker.py#L419)。工具参数也在 INFO 日志中记录：[core.py](../../../services/agent/core.py#L742)。
- LLM response hook 无条件 `await response.aread()`，并且无论 raw log 是否启用都会安装：[providers.py](../../../services/agent/providers.py#L278)。这会缓冲完整响应并削弱真实流式传输。
- `Settings` 的 raw log 字段默认是 `True`，但 `config.example.json` 又将其配置为 `false`，安全默认值取决于启动配置是否完整：[settings.py](../../../config/settings.py#L760)。
- 工具审计的异步 wrapper 同步读取整个 JSONL、`fsync` 并原子重写：[audit.py](../../../services/agent/harness/audit.py#L137)。异常文本未统一脱敏或截断：[audit.py](../../../services/agent/harness/audit.py#L109)。
- 项目没有统一 `run_id` / trace ID，PydanticAI instrumentation、OpenTelemetry/Logfire 或可回放 eval 未接入。

**影响**

当前系统能看到日志，但难以从一次玩家请求串起模型请求、工具调用、MCP、重试和最终结果；同时玩家内容、命令、网页内容或异常中的凭据可能进入日志。同步审计 I/O 会阻塞事件循环，记录越多越明显。

**建议**

- 每次请求生成 `run_id`，所有模型和工具 span 带玩家、对话、provider、tool_call_id、策略版本和错误类别。
- 默认只记元数据和脱敏摘要；raw body 显式 opt-in，并避免在 response hook 中读取完整流。
- 审计改为内存队列 + 独立 writer append/轮转；写失败不得影响工具主路径。
- 使用 PydanticAI 原生 instrumentation 或标准 OpenTelemetry，建立 trace-based eval。

### P1-5：对话持久化和运行时生命周期仍有边界缺口

**证据**

- 保存内容包含玩家名，但加载和恢复按 session ID 读取，没有在此层验证保存者与目标玩家一致：[conversation.py](../../../core/conversation.py#L553)、[conversation.py](../../../core/conversation.py#L650)、[conversation.py](../../../core/conversation.py#L697)。
- `AgentRuntime.shutdown()` 按顺序关闭组件，没有 `finally` 隔离；MCP shutdown 失败会阻止后续 Agent reset 和 Provider client close：[runtime.py](../../../services/agent/runtime.py#L83)。
- `ConversationManager` 和 `PromptManager` 是 AgentRuntime 之外的全局单例，stop/start 或测试重建时可能继续引用旧 broker/settings：[conversation.py](../../../core/conversation.py#L850)、[prompt.py](../../../services/agent/prompt.py#L389)。

**影响**

如果上层没有额外所有权校验，已保存对话可能被其他玩家恢复；进程内重新初始化和异常 shutdown 的资源状态不可靠。

**建议**

持久化 API 强制验证 owner；将 PromptManager、ConversationManager 纳入 AgentRuntime 生命周期；shutdown 对每个资源独立清理并汇总异常。

### P2：兼容性、遥测契约和测试存在技术债

- 依赖声明只有 `pydantic-ai>=1.0.0`，本地实际是 1.94.0；代码直接访问 `_function_toolset` 等私有字段：[requirements.txt](../../../requirements.txt)、[tools.py](../../../services/agent/tools.py#L47)。升级可破坏工具包装。
- DeepSeek 创建时未应用配置的 `base_url`；Anthropic/Ollama 没有统一使用 Provider timeout；HTTP MCP 也没有应用配置 timeout：[providers.py](../../../services/agent/providers.py#L79)、[providers.py](../../../services/agent/providers.py#L121)、[mcp.py](../../../services/agent/mcp.py#L228)。
- 完成事件发出 `tool_events`，Worker 和在线测试却读取 `tool_calls/tool_returns`：[core.py](../../../services/agent/core.py#L989)、[worker.py](../../../services/agent/worker.py#L429)、[test_agent_multi_tool_live.py](../../../tests/test_agent_multi_tool_live.py#L113)。
- 本地多工具测试主要验证事件计数，未真正覆盖 `model -> tool -> model` 的第二次模型请求、并行乱序、部分失败和副作用幂等：[test_stream_mode.py](../../../tests/test_stream_mode.py#L506)。
- 两个压缩测试使用默认 Provider，离线执行会实际等待网络超时：[test_conversation_manager.py](../../../tests/test_conversation_manager.py#L524)、[test_conversation_manager.py](../../../tests/test_conversation_manager.py#L570)。

## 5. 做得合理的部分

1. **没有重复实现 Agent 循环。** `Agent.iter()` 和 `Agent.run()` 负责多轮模型/工具编排，项目只消费节点事件。这比自行维护脆弱的 while-loop 更稳妥。
2. **多人会话隔离设计正确。** 历史按连接、玩家、对话分桶；锁按玩家串行，避免上下文乱序，同时允许不同玩家并行。
3. **历史一致性保护较强。** clear/restore 后用 invalidation epoch 阻止在途请求重建旧历史；压缩使用 generation CAS 防止旧摘要覆盖新消息。
4. **reasoning 不持续回灌。** 历史保存前清除 `ThinkingPart` 和 `reasoning_content`，降低带宽和隐式推理泄露风险。
5. **压缩有退化路径。** LLM 摘要有 timeout，本地摘要可回退；压缩写入也有 stale generation 防护。
6. **工具目录的方向正确。** 意图、风险、适用/禁用场景、参数约束与审计预览集中定义，避免 prompt、schema 和分析各维护一份。
7. **工具结果开始结构化。** `ToolResult` 让审计能够区分成功与失败；问题是底层失败协议尚未贯通。
8. **Provider 生命周期大体清晰。** 模型和 HTTP Client 按配置缓存，cache key 包含 API key 指纹，正常 shutdown 会释放资源。

## 6. 与主流 Harness 的对比

评级：A = 一等能力且有生产路径；B = 支持但需要应用组装；C = 主要由宿主自行实现。

| 能力 | 本项目 | PydanticAI | LangGraph | OpenAI Agents SDK | Claude Agent SDK |
| --- | --- | --- | --- | --- | --- |
| Agent loop | B | A | A | A | A |
| 类型化工具/schema | B | A | A | A | B+ |
| 工具风险/审批 | C | A | A | A | A |
| 上下文裁剪/摘要 | B- | A | A | A | A |
| durable checkpoint/resume | C | B（依赖集成） | A | B+ | B |
| 错误分类/恢复 | C | A | A | A | B |
| trace/eval | C | A | A | A | B+ |
| MCP | B- | A | A | A | A |
| 并发/取消 | B- | B+ | A | A- | B+ |

### PydanticAI

PydanticAI 最适合当前项目继续演进：已有类型化依赖、工具 schema、`iter()`、UsageLimits、history processors、deferred tools/approval、toolset wrapper、instrumentation 和 durable execution 集成。项目当前缺口大多能在原框架内补齐，无需迁移。

需要注意：项目本地为 1.94.0，外部对标同时参考了 2026-07-21 的滚动文档和 Context7 可用的 2.0.0 文档。应先锁定兼容版本，并用公开 toolset/capability API 替代 `_function_toolset` 私有字段。

### LangGraph

LangGraph 的优势是显式状态图、super-step checkpoint、interrupt/resume、故障恢复和 time travel，适合长任务、跨进程审批或多阶段工作流。当前 MCBE 对话大多是短生命周期请求，仅为获得这些能力迁移会增加状态图和持久化复杂度。

当未来出现“玩家发起长时间建造任务、等待人工审批、进程重启后继续、需要补偿动作”时，再评估 LangGraph 或 PydanticAI 的 Temporal/DBOS/Prefect 集成更合理。

### OpenAI Agents SDK

其基线值得借鉴：Runner 有明确 `max_turns`，工具可声明 approval，暂停时返回可序列化 RunState，恢复的是同一次 run；input/output/tool guardrails 各有明确边界；内置 tracing 让 handoff 和 tool spans 可关联。重点不是换 SDK，而是采用相同控制语义。

### Claude Agent SDK

其权限模型强调 allow/ask/deny、执行前 hook 和动态 `canUseTool`。对本项目最有价值的启示是：高风险工具的服务端 deny 必须不可被“模型觉得可以”覆盖；会话 resume 也不能等同于副作用执行 checkpoint。

### Google ADK 与 MCP

Google ADK 把 Session、State、Memory 分成不同层，并通过 callbacks 在模型/工具前后观察和控制执行。本项目目前把历史、模板变量、压缩摘要和执行状态混在较少的对象中，后续可借鉴这种边界，但无需引入 ADK。

MCP 官方实践强调 progressive discovery 和逐调用授权。当前工具量较少，无需立即做动态 tool search；但 MCP 工具一旦增长，应避免把所有 schema 永久塞入上下文，并必须让 MCP 工具走与内置工具相同的 policy gate。

## 7. 建议的目标架构

```text
ChatRequest
  -> RunController
       -> RunBudget
            request / tool-call / token / wall-clock / concurrency
       -> ContextBuilder
            trusted system constraints
            untrusted conversation summary
            recent complete turns
            selected tool schemas
       -> PydanticAI Agent.iter
            -> UnifiedToolset
                 -> PolicyGate
                      allow / deny / require approval
                 -> IdempotencyGuard
                 -> ToolExecutor
                 -> ErrorClassifier
                 -> AuditEmitter
            -> model continues or run pauses/completes
       -> Trace / metrics / history persistence
```

关键原则：

- **模型负责选择，宿主负责授权。**
- **重试绑定错误类别和幂等性。**
- **历史、记忆、运行状态、审批状态分开存储。**
- **所有工具，包括 MCP，都通过同一策略与审计入口。**
- **先建立单 Agent 的可靠闭环，再考虑多 Agent。**

## 8. 分阶段改进路线

### 第一阶段：修正错误事实和安全边界（P0）

验收标准：

- 命令断线、明确失败、超时未知在工具结果、玩家结果和审计中一致。
- MCP 在任何已产生输出/副作用的 run 中都不会整轮自动重放。
- 高/危险工具执行前经过服务端策略；危险工具有精确参数审批。
- `fetch_url_text` 无法访问 loopback、私网、link-local、元数据地址或不受控重定向。

### 第二阶段：建立受控运行（P1）

验收标准：

- 每轮都有 request/tool/token/time/concurrency budget。
- context 在模型请求前按 token 预算构建，保留完整 tool pair 和输出余量。
- 错误统一分类，取消不会被误判为 MCP timeout，queue task 总能在 finally 结束。
- 内置和 MCP 工具统一经过 policy、idempotency、error classifier 和 audit。

### 第三阶段：可观测和可评测（P1/P2）

验收标准：

- `run_id -> model span -> tool span -> result` 可完整关联。
- 日志默认不保存完整玩家内容、工具结果和 LLM body；审计写入不阻塞事件循环。
- 建立离线 trajectory eval：工具选择、参数正确、拒绝危险操作、失败恢复、最终答案。
- 将真实 Provider 测试明确标记为 live/integration；默认测试完全离线且有总超时。

### 第四阶段：按业务需要增加持久执行

只有出现跨分钟任务、审批等待、进程重启续跑或补偿事务时再实施。此时可选择 PydanticAI durable execution 集成或 LangGraph checkpoint，不建议提前把普通聊天请求图化。

## 9. 建议测试矩阵

| 维度 | 场景 | 核心断言 |
| --- | --- | --- |
| Agent 循环 | `model -> tool -> model`、3+ 次工具、空最终文本 | 节点顺序正确，完成事件唯一，历史完整 |
| 工具错误 | 抛异常、失败结果、超时未知、批量部分失败 | 玩家、模型、审计三处状态一致 |
| 副作用 | 同 call ID 重复、fallback、取消后迟到结果 | 世界修改至多一次，未知状态不盲重试 |
| 审批 | allow、deny、过期、参数被修改、不同玩家复用 | 批准绑定身份和精确参数，不能跨请求复用 |
| Provider | 429、5xx、timeout、断流、retry exhaustion | 有界重试、取消传播、单一错误响应 |
| Context | 长单消息、长工具结果、小窗口模型、注入摘要 | 请求前不超预算，摘要不成为指令 |
| MCP | 部分 server 失败、ERROR toolset、schema 变化 | 只暴露健康且授权的工具，逐 server 降级 |
| Observability | 敏感 header/query/异常、并发工具 | 全面脱敏，trace 关联完整，审计不阻塞 |
| 生命周期 | shutdown 某组件抛错、Worker 被取消 | 其余资源仍关闭，queue 不悬挂 |

## 10. 验证结果

环境：仓库 `.venv`，Python 3.14.4，PydanticAI 1.94.0。

首次直接运行 `pytest` 失败，因为当前 shell 没有全局 `pytest`，且仓库需要显式 `PYTHONPATH=.`。改用虚拟环境后：

- `65 passed, 5 warnings`：Agent Worker、工具、Provider 生命周期、运行时 Harness 目录/提示/审计/分析。
- `49 passed, 14 warnings`：MCP 与流式模式。
- 会话与队列组未在 60 秒内完成；单独复现以下两项均在 12 秒超时：
  - `tests/test_conversation_manager.py::test_compress_history`
  - `tests/test_conversation_manager.py::test_check_and_compress_force`

原因是这两个名为单元测试的用例使用默认 `Settings()` 进入真实 LLM 摘要 Provider 路径，而不是 mock。测试中还出现未 await coroutine 和 PydanticAI `event.result` 弃用告警。在线多工具测试默认依赖凭据，而且断言了生产完成事件并不存在的字段，因此不能作为当前可靠门禁。

## 11. 反方观点与取舍

1. **并非所有短对话都需要 durable graph。** 当前请求通常短、会话锁明确，引入 LangGraph checkpoint 可能让架构更重。先解决策略、错误、幂等和预算更有价值。
2. **审批会降低游戏内交互速度。** 因此应按风险分层：只读查询自动允许；低风险展示按范围限制；世界修改按命令类型或影响面决定自动允许、拒绝或审批。
3. **完整 trace 与内容日志有隐私成本。** 可观测性不是“记录更多正文”，而是用 ID、状态、耗时、token、错误类别和脱敏摘要建立因果链。
4. **摘要本身不可完全可信。** 即使使用更强模型，也会遗漏或误写。关键约束必须来自版本化系统配置，不能只存在摘要记忆中。
5. **框架升级不是架构修复。** PydanticAI 新版本已经提供许多所需原语，但只有把业务权限、身份、错误和幂等语义接进去，才会变成有效的运行时 Harness。

## 12. 开放问题

- 哪些 Minecraft 命令允许普通玩家自动执行，哪些必须管理员审批，哪些永远禁止？
- 命令超时后，游戏侧是否可能仍然执行成功？若会，如何查询或去重未知状态？
- 保存/恢复会话是否设计为同玩家私有，还是管理员可跨玩家操作？
- MCP server 是否只由可信管理员配置？未来是否允许玩家动态选择或添加？
- 玩家对话、工具参数、审计摘要和 LLM raw body 的保留期与隐私政策是什么？
- 是否需要支持跨进程恢复的长任务，还是所有请求都可在一次在线连接内结束？

## 13. 外部资料

以下优先使用官方文档和官方工程文章。框架发布频率很高；资料快照日期为 2026-07-21，滚动文档能力不应反推到所有旧版本。

### PydanticAI

1. [PydanticAI message history](https://github.com/pydantic/pydantic-ai/blob/v2.0.0/docs/message-history.md)：history processor、裁剪、摘要和序列化。
2. [PydanticAI deferred tools](https://github.com/pydantic/pydantic-ai/blob/v2.0.0/docs/deferred-tools.md)：精确工具审批、拒绝、外部执行与恢复。
3. [PydanticAI agent](https://github.com/pydantic/pydantic-ai/blob/v2.0.0/docs/agent.md)：UsageLimits 与 Agent run 行为。
4. [PydanticAI durable execution with Restate](https://github.com/pydantic/pydantic-ai/blob/v2.0.0/docs/durable_execution/restate.md)：持久执行集成边界。
5. [PydanticAI capabilities](https://github.com/pydantic/pydantic-ai/blob/v2.0.0/docs/capabilities.md)：toolset wrapper、history、metadata 和横切控制。

### LangGraph

6. [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)：durable execution、streaming、human-in-the-loop 和 persistence 定位。
7. [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：thread checkpoint 与 store 的职责边界。
8. [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)：故障恢复、durability mode 和 pending writes。
9. [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)：持久化暂停与 `Command(resume=...)`。
10. [LangGraph fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)：timeout、retry、error handler 和恢复模式。

### OpenAI Agents SDK

11. [Running agents](https://openai.github.io/openai-agents-python/running_agents/)：Runner loop、`max_turns`、异常和运行配置。
12. [Human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/)：tool approval、可序列化 RunState 与同一 run 恢复。
13. [Guardrails and human review](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals)：input/output/tool guardrail 的执行边界。
14. [Sessions](https://openai.github.io/openai-agents-python/sessions/)：会话历史持久化。
15. [Tracing](https://openai.github.io/openai-agents-python/tracing/)：Agent、LLM、tool、handoff 和 guardrail spans。

### Anthropic / Claude Agent SDK

16. [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)：简单可组合工作流优先、框架取舍和 Agent 模式。
17. [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)：工具描述、工具评测和迭代。
18. [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)：harness 与 model 联合评测、trajectory 评测。
19. [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)：工具选择、可观测性与反馈闭环。
20. [Claude Agent SDK permissions](https://code.claude.com/docs/en/agent-sdk/permissions)：allow/ask/deny、hook 与动态权限。
21. [Claude Agent SDK sessions](https://code.claude.com/docs/en/agent-sdk/sessions)：resume/fork 与会话持久化边界。

### Google ADK / MCP

22. [Google ADK sessions](https://google.github.io/adk-docs/sessions/)：Session、State、Memory 的边界。
23. [Google ADK callbacks](https://google.github.io/adk-docs/callbacks/)：模型和工具前后的观察、修改与阻断。
24. [MCP tools specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)：工具调用的用户控制与拒绝能力。
25. [MCP client best practices](https://modelcontextprotocol.io/docs/develop/clients/client-best-practices)：progressive discovery、programmatic tool calling 与逐调用授权。

## 14. 复查输入

```yaml
workflow: firecrawl-deep-research
topic: MCBE Chat Agent 运行时 Harness 架构审查与主流框架对标
depth: thorough
runtime_target: 15 minutes
output: markdown
scope_excluded:
  - services/websocket
  - mcbe-ws-sdk
code_changes: none
```
