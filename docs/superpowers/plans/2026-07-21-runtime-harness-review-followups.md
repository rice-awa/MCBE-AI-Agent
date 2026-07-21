# 运行时 Harness 架构审查整改实现计划

- 创建日期：2026-07-21
- 输入报告：`docs/superpowers/reports/2026-07-21-runtime-harness-review.md`
- Python/测试环境：复用仓库虚拟环境 `./.venv/bin/python`，执行测试时显式设置 `PYTHONPATH=.`

## 目标

在不迁移 Agent 框架、不引入多 Agent 或持久工作流的前提下，把现有运行时 Harness 从提示约束推进到可受控运行：统一工具结果与错误语义，为内置和 MCP 工具建立不可绕过的服务端策略、审批、幂等和审计入口，阻止副作用整轮重放，并补齐请求预算、上下文预算、可关联 trace 和生命周期边界。

本计划按一条完整运行链归并改动，只保留 4 个实现任务。每个任务同步完成实现和相邻测试，不再为报告中的每条 finding 单独拆任务。

## 已定取舍

- 继续使用 PydanticAI 1.x，先将依赖收敛到已核验的 `pydantic-ai~=1.94.0`；不迁移 LangGraph。
- 使用公开的 `capabilities`、`WrapperToolset`、`DeferredToolRequests`、`DeferredToolResults`、`UsageLimits` 和 `history_processors`，删除对 `_function_toolset` 的生产代码和测试依赖。
- 删除当前 run 内的递归 MCP fallback。MCP 故障只影响本次 run；下一次独立请求根据 MCPManager 的健康状态重新组装工具集。
- 第一版审批状态只要求在单进程内可靠恢复，不实现跨进程 checkpoint。审批、历史和工具执行状态分开保存。
- 通用 `fetch_url_text` 暂时从 MCBE Chat Agent 工具中移除，只保留受控的 MCWiki 工具。未来确有通用网页访问需求时，再以域名 allowlist 独立设计，不在本轮实现通用 SSRF 代理。
- 日志默认只记录标识、状态、耗时、token 和脱敏摘要；完整玩家内容、工具结果与 LLM body 默认关闭。
- 不新增重复的 WebSocket 分片实现。审批提示和恢复结果继续走现有消息事件与 `services/websocket/flow_control.py`。

首版配置采用以下保守默认值，避免实现阶段再次决定行为：`request_limit=8`、`tool_calls_limit=8`、`run_timeout=90s`、`max_tool_concurrency=4`、`context_output_reserve_tokens=1024`、`approval_ttl=120s`、`max_batch_commands=10`。模型输入/总 token 上限默认从模型元数据的 context window 扣除输出预留后派生；元数据缺失时拒绝超长上下文，不回退到无限预算。硬 deny 的命令根首批包含 `op`、`deop`、`stop`、`whitelist`、`permission` 和 `wsserver`，并允许管理员在 `config.json` 中追加但不能清空内置 deny 集合。

## 第一任务：统一 run、工具结果、错误和预算契约

**关联问题：** P0-3、P1-2，以及 P2 中完成事件、Provider 配置和依赖兼容问题。

**主要文件：**

- 修改：`requirements.txt`
- 修改：`config/settings.py`
- 修改：`config.example.json`
- 修改：`models/agent.py`
- 修改：`models/messages.py`
- 修改：`services/agent/tool_results.py`
- 修改：`services/agent/core.py`
- 修改：`services/agent/worker.py`
- 修改：`services/agent/providers.py`
- 修改：`services/agent/mcp.py`
- 修改：`tests/test_agent_worker.py`
- 修改：`tests/test_agent_tools.py`
- 修改：`tests/test_stream_mode.py`
- 修改：`tests/test_agent_provider_lifecycle.py`
- 修改：`tests/test_json_settings.py`

### 实现

- 将 `ToolResult` 扩展为统一执行结果，至少包含 `status`、`error_kind`、`retryable`、`output`、`external_state_unknown` 和面向内部的诊断摘要。错误类别固定为 `INVALID_ARGUMENT`、`MODEL_RETRYABLE`、`TRANSIENT`、`DENIED`、`PERMANENT`、`CANCELLED`、`INTERNAL`。
- 为 `AgentDependencies.run_command` 定义结构化返回类型，明确区分：已成功、已明确失败、连接不可用、超时且外部状态未知。所有命令、消息、标题、actionbar 和 scriptevent 工具必须检查该结果，不能再把失败字符串包装成成功。
- 在 `ChatRequest`、`AgentDependencies` 和完成事件中显式传递 `run_id`、`conversation_id` 和当前事件的 `player_name`。玩家消息只收到稳定的错误类别与可执行建议，异常类型、堆栈和原始响应仅进入脱敏内部诊断。
- 在配置中增加每轮 `request_limit`、`tool_calls_limit`、输入/输出/总 token 上限、wall-clock timeout 和最大工具并发。调用 `Agent.iter()` / `Agent.run()` 时统一传入 `UsageLimits`，并在外层施加 run timeout；取消必须原样传播。
- 把 `MessageBroker.request_done()` 放入 Worker 消费循环的 `finally`，保证正常、异常和取消路径都能完成 queue task。
- 统一完成事件只使用 `tool_events` 作为遥测契约，删除 Worker 和 live 测试对 `tool_calls` / `tool_returns` 的旧读取。
- 修正 Provider 参数传递：DeepSeek 使用配置的 `base_url`，Anthropic/Ollama 与 HTTP MCP 使用同一套配置 timeout。LLM raw hook 只在显式开启时安装，且不得通过 `response.aread()` 缓冲流式 body。

### 必要测试

- 在现有工具测试中用一个参数化用例覆盖成功、明确失败和超时未知，断言模型返回、玩家事件和工具审计使用同一状态；不为每个展示工具复制同类测试。
- 在 Worker 测试中覆盖一次异常和一次取消，断言 `request_done()` 恰好执行一次；增加一个预算超限用例，断言没有继续执行工具。
- 更新一个流式用例和一个非流式用例，统一断言完成事件的 `tool_events`；Provider 生命周期测试只验证配置确实传入，不访问真实网络。

### 完成标准

- 失败不再通过字符串猜测，审计不会把断线、明确失败或超时未知记为成功。
- 所有 run 都有一致的身份、错误、预算和结束语义，queue 不因异常或取消悬挂。
- 生产代码和测试不再访问 PydanticAI `_function_toolset`。

## 第二任务：建立统一工具策略、审批、幂等和 MCP 执行边界

**关联问题：** P0-1、P0-2、P1-3，以及 P2 中真实 Agent 循环覆盖不足。

**主要文件：**

- 新增：`services/agent/harness/execution.py`
- 新增：`services/agent/harness/approvals.py`
- 修改：`services/agent/harness/catalog.py`
- 修改：`services/agent/harness/audit.py`
- 修改：`services/agent/tools.py`
- 修改：`services/agent/core.py`
- 修改：`services/agent/mcp.py`
- 修改：`services/agent/runtime.py`
- 修改：`services/agent/worker.py`
- 修改：`models/messages.py`
- 修改：`services/websocket/server.py`
- 修改：`config/settings.py`
- 修改：`config.example.json`
- 新增：`tests/test_runtime_harness_execution.py`
- 修改：`tests/test_mcp.py`
- 修改：`tests/test_websocket_command.py`

### 实现

- 用一个 `WrapperToolset` capability 包装 Agent 组装后的全部非输出工具。调用顺序固定为：规范化参数 -> 目录与来源解析 -> policy decision -> 审批/幂等检查 -> 执行 -> 错误分类 -> 审计。内置和 MCP 工具必须走同一入口，`tools.py` 不再修改已注册函数对象。
- 扩展工具目录，除现有意图和风险外，记录工具来源、MCP server、schema hash、策略版本和是否可能产生外部副作用。未纳入目录或 allowlist 的 MCP 工具默认不暴露给模型。
- 第一版策略采用保守默认值：低风险查询自动允许；中风险展示工具在目标仍为当前玩家时允许；高/危险工具要求审批；配置中的硬 deny 工具和命令根永远拒绝，不能被 prompt、模板或 MCP 描述覆盖。批量命令同时限制最大条数。
- 使用 PydanticAI `DeferredToolRequests` 暂停需要审批的调用。`PendingApprovalStore` 按 `(connection_id, player_name, conversation_id, approval_id)` 保存原 messages、精确 `tool_call_id`、规范化参数摘要、策略版本和过期时间。
- 增加 `AGENT 工具审批 <approval_id> <允许|拒绝>`。命令处理必须使用本次事件的 `player_name` 校验 pending owner；批准后构造 `DeferredToolResults` 并使用原 messages 恢复同一调用链，不把批准文本作为新 prompt，也不允许跨玩家、跨对话、过期或参数变化的批准复用。
- 在统一执行入口维护有界、带 TTL 的进程内幂等记录，键为 `(run_id, tool_call_id, normalized_args_hash)`。已成功调用返回已保存结果；状态未知的副作用调用不得自动重试；只有明确幂等且分类为 transient 的查询工具可重试。
- 删除 `stream_response_handler()` 中从头递归执行 fallback Agent 的路径。run 开始后发生 MCP 故障即返回结构化错误；MCPManager 成为唯一健康状态源，只标记有证据关联的 server。后续新请求仅装配 `PENDING` / `ACTIVE` server 的工具集。
- 审批提示、拒绝和恢复结果通过现有 Worker/消息发送链下发，必须携带真实 `player_name`；长文本继续复用统一流控中间件。

### 必要测试

- `tests/test_runtime_harness_execution.py` 使用 PydanticAI `TestModel` 或 `FunctionModel` 覆盖一条真实 `model -> tool -> model` 链，并集中验证：低风险直接执行、高风险暂停、精确批准后只执行一次、拒绝不执行、重复 call ID 不重复副作用。
- MCP 只保留两个故障不变量测试：本地工具已执行后 MCP timeout 不重放整轮；一个 server 失败不移除其他健康 server。
- WebSocket 命令只测 owner、过期和跨玩家拒绝三个边界，不复制不同工具或不同文案组合。

### 完成标准

- 模型只能选择工具，不能绕过宿主的 allow / deny / require approval 决策。
- 高/危险工具的批准绑定玩家、对话、精确参数和 `tool_call_id`，恢复时至多执行一次。
- 任意已输出或已开始工具执行的 run 都不会因为 MCP timeout 从头自动重放。

## 第三任务：在模型请求前建立上下文与外部内容信任边界

**关联问题：** P0-4、P1-1，以及报告中的离线压缩测试挂起。

**主要文件：**

- 新增：`services/agent/context.py`
- 修改：`services/agent/core.py`
- 修改：`services/agent/worker.py`
- 修改：`services/agent/prompt.py`
- 修改：`services/agent/tools.py`
- 修改：`services/agent/harness/catalog.py`
- 修改：`core/conversation.py`
- 修改：`config/settings.py`
- 修改：`config.example.json`
- 修改：`tests/test_conversation_manager.py`
- 修改：`tests/test_stream_mode.py`
- 修改：`tests/test_runtime_harness_catalog.py`

### 实现

- 移除 `fetch_url_text` 的 Agent 注册和目录条目。MCWiki 工具继续使用固定配置的受控来源；不保留一个仅靠 prompt 或字符串 URL 校验的通用服务端抓取入口。
- 增加 `ContextBuilder`，在每次模型请求前按模型元数据中的 context window 计算预算，分别预留系统提示、工具 schema、当前输入和输出 token；历史只使用剩余额度。
- 通过公开 `history_processor` 集中处理历史，保留最近完整轮次和完整 tool-call/tool-return pair。过长工具结果只回灌带来源、原长度、截断标记和内容摘要的结果，不能切出孤立的 tool call 或 return。
- `UsageLimits(count_tokens_before_request=True)` 作为最终硬边界；ContextBuilder 的历史裁剪负责正常退化，硬边界负责阻止估算误差导致的超窗请求。
- 历史摘要使用明确的“不可信历史资料”容器，并在版本化系统约束中规定其内容只能作为事实线索、不得作为指令。玩家权限、工具策略、当前会话配置等可信事实始终从运行时依赖重建，不从摘要恢复。
- 把压缩触发从仅按轮次改为“token 预算优先、轮次上限兜底”，压缩发生在模型请求前；保留现有 generation CAS 和本地摘要退化路径。
- 两个会访问默认 Provider 的压缩测试改为显式注入 fake summarizer，默认测试不得因缺少 API key 等待网络 timeout。

### 必要测试

- 用三个上下文不变量覆盖本任务：超长单消息在请求前被硬边界拒绝或安全裁剪；tool call/return 始终成对；包含提示注入文本的摘要不会替代系统策略。
- 压缩测试复用同一个 fake summarizer fixture，只断言触发、保留最近完整轮次和 stale generation 防护，不测试摘要自然语言措辞。
- 工具目录测试只更新“注册工具与目录一致”断言，不保留已删除网页工具的重复 scheme 用例。

### 完成标准

- 任意模型请求在发送前都有可解释的 token 预算，并为输出和工具 schema 留出空间。
- 摘要、网页内容和工具结果均被视为不可信资料，不能成为运行时策略来源。
- 默认离线测试不会创建真实摘要 Provider 请求。

## 第四任务：收敛 trace、审计、持久化所有权和运行时生命周期

**关联问题：** P1-4、P1-5，以及剩余 P2 技术债。

**主要文件：**

- 修改：`services/agent/harness/audit.py`
- 修改：`services/agent/runtime.py`
- 修改：`services/agent/core.py`
- 修改：`services/agent/worker.py`
- 修改：`services/agent/providers.py`
- 修改：`core/conversation.py`
- 修改：`services/agent/prompt.py`
- 修改：`config/settings.py`
- 修改：`config.example.json`
- 修改：`pyproject.toml`
- 修改：`tests/test_runtime_harness_audit.py`
- 修改：`tests/test_agent_provider_lifecycle.py`
- 修改：`tests/test_conversation_manager.py`
- 修改：`tests/test_logging.py`
- 修改：`tests/test_agent_multi_tool_live.py`

### 实现

- 以第一任务生成的 `run_id` 关联请求、模型请求、工具调用、审批、MCP server、最终结果和 token usage。工具记录同时包含 `tool_call_id`、策略版本、错误类别和耗时，但身份字段只保留排障所需的有限玩家/会话标识。
- 接入 PydanticAI 原生 instrumentation 的可配置开关；默认即使不开 exporter，现有 structlog 事件也必须用同一 `run_id` 形成完整因果链。不得用记录完整正文代替 trace。
- 把工具审计改为内存队列加 AgentRuntime 管理的独立 writer，正常路径只 enqueue。writer 以 append 为主并按配置轮转；写入、轮转或 shutdown flush 失败只告警，不改变工具结果。
- `enable_ws_raw_log` 和 `enable_llm_raw_log` 的 Settings 默认值与 `config.example.json` 统一为 `false`。参数、URL query、header、异常和结果摘要进入日志前使用同一脱敏与长度限制 helper。
- `load_conversation()` / `restore_conversation()` / 删除接口要求调用者提供目标 `player_name` 并校验持久化 owner；列表默认只返回当前玩家的会话。任何路径都不能把保存者 A 的历史恢复到玩家 B 的运行时桶。
- 将 PromptManager 和 ConversationManager 的实例归入 AgentRuntime，停止/重启时清除旧 broker/settings 引用。`shutdown()` 对 MCP、Agent、审计 writer、模型元数据和 Provider clients 分别清理并汇总错误，一个组件失败不能阻止其余资源关闭。
- 在 `pyproject.toml` 注册 `live` 标记并让默认 pytest 排除真实 Provider 测试。`test_agent_multi_tool_live.py` 只作为显式 live 门禁，字段改为当前 `tool_events` 契约。

### 必要测试

- 审计测试只验证 enqueue 不阻塞、敏感字段脱敏、shutdown flush 三个不变量，不再对每条工具重复测 JSONL 文案。
- 生命周期用一个聚合测试让 MCP shutdown 抛错，断言 Agent reset、audit flush 和 Provider close 仍被调用。
- 对话持久化只增加同 owner 成功、跨 owner 拒绝两个测试；日志测试验证 raw 默认关闭且关闭时不读取 response body。

### 完成标准

- 可通过一个 `run_id` 串起 model span、tool span、审批/MCP 状态和最终结果。
- 默认日志和工具审计不保存完整玩家内容、凭据或 LLM body，审计磁盘 I/O 不阻塞 Agent 工具执行。
- 会话 owner 和运行时资源生命周期都有明确、可测试的边界。

## 验证策略

测试以风险不变量为单位，不按工具数量、Provider 数量或错误文案做组合爆炸。各任务先运行相邻测试，四个任务完成后只执行以下两级门禁：

```bash
PYTHONPATH=. ./.venv/bin/python -m pytest \
  tests/test_agent_worker.py \
  tests/test_agent_tools.py \
  tests/test_runtime_harness_execution.py \
  tests/test_mcp.py \
  tests/test_conversation_manager.py \
  tests/test_agent_provider_lifecycle.py -q

PYTHONPATH=. ./.venv/bin/python -m pytest -q
```

默认门禁必须完全离线并在项目约定时间内结束。真实 Provider/MCP 测试仅在有凭据时显式运行：

```bash
PYTHONPATH=. ./.venv/bin/python -m pytest -m live -q
```

## 明确不做

- 不迁移 LangGraph、OpenAI Agents SDK 或 Claude Agent SDK。
- 不增加 Planner、多 Agent、动态工具搜索或跨进程 durable execution。
- 不实现通用网页代理、任意域名抓取或复杂 DNS rebinding 防护；当前直接移除通用网页工具。
- 不为每个工具复制 allow/deny/error/audit 测试；统一 WrapperToolset 的契约测试覆盖横切逻辑。
- 不修改 `mcbe-ws-sdk/`，不重写 WebSocket 协议或下行流控。

## 总体验收

- 命令断线、明确失败和超时未知在模型、玩家结果与工具审计中语义一致。
- 高/危险工具执行前经过不可绕过的服务端策略，批准绑定当前玩家、对话、精确参数和 tool call。
- MCP timeout、取消或迟到结果不会导致已经开始的副作用整轮重放。
- 每轮有 request、tool、token、time 和 concurrency budget；历史在模型请求前按预算构建。
- 内置和 MCP 工具统一经过 policy、approval/idempotency、error classifier 和 audit。
- 默认测试完全离线；live 测试不再作为日常合并门禁。
