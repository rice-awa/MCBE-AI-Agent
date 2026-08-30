# MCBE Chat Agent 架构与提示词审查

- 调研日期：2026-08-15
- 成稿日期：2026-08-16
- 报告版本：1.0
- 代码分支：`dev`
- 报告编写基线：`b472ca9`
- 业务代码快照：`4d9888be093279b8dbf66b427c944f4ce3f242fc`
- 关键依赖：Python 3.11+，PydanticAI 1.94.0

> `b472ca9` 相对业务代码快照只新增本次 Trellis 调研材料，没有改变被审查的运行时代码。因此正文中的源码行号以报告落盘时的工作树为准，行为判断以 `4d9888b` 的业务代码为准。

## 1. 执行摘要

本项目不需要为了“现代 Agent 架构”而把提示词整体改写成 XML，也没有充分理由仅为获得显式图形结构而从 PydanticAI 迁移到 LangGraph。当前 MCBE Chat Agent 已经具备一套有价值的运行时 Harness：原生工具调用、工具目录、审批、幂等、审计、Trace、多人会话隔离和统一下行交付都已形成主链路。真正需要优先处理的是工具结果语义、安全边界和动态上下文的一致性。

本次审查确认两项 P0 缺陷：

1. 生产注册路径把结构化 `ToolResult` 在运行时 Harness 收尾前转换成字符串。模型仍可能看到“失败”文字，但运行时 Harness 已丢失 `status`、`retryable` 和 `external_state_unknown`，进而可能把失败或外部状态未知记录成成功、写入成功幂等缓存，并产生自相矛盾的审计/Trace。
2. stdio MCP 子进程被显式传入完整父进程环境，绕过 PydanticAI 1.94.0 默认“不继承父环境”的安全行为。任何被配置并启动的本地 MCP 包都可能读取 provider API key、JWT 密钥、连接密码等宿主凭据。

随后应处理四组 P1 问题：

- 动态系统提示词在已有历史时不重新求值，且 `concise` / `detailed` 模板会静默丢弃管理员配置的基础系统提示词。
- 上下文预算按 800 个系统提示 tokens 和 8 个工具的粗略 schema 预算裁剪历史，明显低于实际 21 个内置工具、运行时 Harness 文本和 MCP 工具的总开销。
- 命令审批对未列入少量命令根的操作默认放行；审批批次、外部状态未知和恢复异常仍有需要收紧的事务与幂等边界。
- 短工具结果、模板变量、完整工具参数/结果日志和动态 MCP 工具描述之间的信任与观测边界不一致。

推荐顺序是：先保住结构化结果和凭据边界，再修动态提示与预算，然后收紧审批/恢复和日志，最后建设跨 provider 的离线评测集。XML 只应作为最后一阶段的可消融渲染变量，而不是第一阶段的重构目标。

## 2. 范围、方法与证据等级

### 2.1 审查范围

外部调研覆盖 OpenAI Responses API、OpenAI Agents SDK、Codex CLI 公开源码、Anthropic Messages API、Claude Code、MCP、PydanticAI 1.94.0 和 LangGraph。仓库审查追踪以下完整路径：

```text
玩家事件
  -> Gateway / HostConnectionHook / CommandHandlers
  -> ChatRequest
  -> MessageBroker
  -> AgentWorker
  -> PydanticAI Agent
  -> 运行时 Harness
  -> 内置工具 / Addon Bridge / MCP
  -> 工具结果、审批、审计与 Trace
  -> BrokerResponseBridge / SDK delivery
  -> Minecraft 玩家或 Addon UI
```

本任务只产出审查报告，没有修改业务代码、配置、协议或测试；没有运行付费模型、真实 Minecraft 世界或任何有外部副作用的工具。

### 2.2 调研方法

- 五个独立子代理分别研究 OpenAI/Codex、Anthropic/MCP、PydanticAI/LangGraph、提示词/上下文链路、工具/运行时 Harness 链路。
- 外部事实优先使用官方 API 文档、协议规范、官方公开源码和官方工程文章；正文与附录共列出 40 余个一手入口。
- PydanticAI 语义以 `v1.94.0` tagged source 为准；Context7 当前文档只用于补充和识别版本边界，不能反向覆盖项目锁定版本。
- MCP 比较以调研时最新的 `2026-07-28` 规范为主，同时单列 `2025-11-25` 旧生命周期，避免把新旧能力协商混写。
- 主代理复核了高风险代码路径，并对动态系统提示词和真实生产工具注册组合执行了无外部副作用的离线复现。

### 2.3 结论标签

- **已确认缺陷**：源码、锁定版本语义与离线复现共同证明行为错误。
- **设计风险**：存在明确攻击面或失效条件，但没有生产事故证据，或尚缺端到端复现。
- **改进机会**：当前行为可用，改动主要提升效率、可维护性和可评测性。

严重级别：

- **P0**：可能泄露凭据、破坏权限边界，或让核心执行/审计事实失真。
- **P1**：影响常用功能正确性、上下文稳定性或高频运行质量。
- **P2**：可维护性、成本、可观察性或长期兼容性问题。

## 3. 现代 Agent 的提示词与工具调用机制

### 3.1 应把六个层次分开

现代 Agent 不是一段“写得很聪明的 system prompt”，而是六个相互配合、责任不同的层次：

| 层次 | 应承载的内容 | 不应承载的内容 |
|---|---|---|
| 高优先级指令 | 稳定角色、业务目标、回答边界、工具选择策略 | 运行时凭据、权限授予、动态工具全集 |
| 工具描述 | 工具做什么、何时用/不用、副作用、返回与限制 | 只有运行时 Harness 才能保证的授权承诺 |
| 参数 schema | 字段、类型、枚举、必填、长度和结构约束 | 玩家是否有权执行、世界状态是否允许 |
| 原生调用协议 | 工具名、参数、call ID、工具结果关联 | 人类可读 XML 约定 |
| 运行时 Harness | 暴露工具、校验、审批、超时、取消、幂等、审计 | 依赖模型自觉遵守的软规则 |
| 不可信数据 | 玩家输入、历史摘要、网页、MCP/工具结果 | 可覆盖高优先级策略的“新指令” |

OpenAI 把 Markdown/XML 视为提高提示可读性和分段清晰度的写作手段，而 function tool 使用 JSON Schema，并通过 `function_call` / `function_call_output` 和相同 `call_id` 完成循环。[OpenAI Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)、[OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)

Anthropic 建议复杂提示可使用一致的 XML 标签，但其原生工具协议是 `tool_use` / `tool_result` content blocks，通过 `tool_use_id` 关联；XML 不是 API wire protocol。[Anthropic Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)、[Anthropic Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)

MCP 则使用 JSON-RPC、`tools/list` / `tools/call` 和 JSON Schema `inputSchema` / `outputSchema`；tools、resources、prompts 的控制主体不同，不能都当作“自动注入 system prompt 的工具”。[MCP overview](https://modelcontextprotocol.io/specification/2026-07-28)、[MCP tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)、[MCP resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources)、[MCP prompts](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts)

### 3.2 三种协议的关键关联字段

| 体系 | 模型请求工具 | 应用回传结果 | 关联字段 | 结构错误与执行失败 |
|---|---|---|---|---|
| OpenAI Responses | `function_call` | `function_call_output` | `call_id` | API/JSON Schema 与应用执行错误需分层 |
| Anthropic Messages | `tool_use` | `tool_result` | `tool_use_id` | API 错误、`is_error` 与业务失败需分层 |
| MCP | JSON-RPC `tools/call` | `CallToolResult` | JSON-RPC request ID + 调用上下文 | JSON-RPC error 与结果 `isError` 不同 |

无论使用哪一套协议，应用都必须保留调用关联字段，不能把多个并行调用的结果按“看起来像对应”来配对。严格 schema 只能约束结构与类型，不能证明业务参数正确、玩家有权操作、调用具备幂等性，或工具结果没有提示注入。[Anthropic Strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)

### 3.3 工具执行循环

通用循环应是：

```text
构造高优先级指令 + 本轮可见工具定义 + 不可信上下文
  -> 模型返回零个、一个或多个工具调用
  -> 运行时 Harness 校验身份、schema、策略、审批和幂等键
  -> 执行工具并分类结果
  -> 按原 call ID 回传结构化工具结果
  -> 模型继续推理或生成最终答复
  -> outcome/trace/eval 验证结果
```

并行调用只是模型一次返回多个调用的能力，不代表应用必须并发执行。独立、只读、无共享状态的调用可以并行；写操作、有顺序依赖或共享世界状态的调用应串行或通过锁/事务协调。OpenAI 可用 `parallel_tool_calls:false` 限制一次最多一个调用；Anthropic 允许一次返回多个 `tool_use`，但执行顺序仍由应用决定。[OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)、[Anthropic Parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)

### 3.4 错误不是一个布尔值

运行时至少要区分：

1. 协议或请求格式错误；
2. 模型可通过修正参数解决的错误；
3. 明确的业务执行失败；
4. 可安全重试的瞬态失败；
5. 外部状态未知，尤其是请求可能已经产生副作用但响应超时；
6. 玩家或管理员拒绝；
7. 取消、断线和进程关闭。

PydanticAI 1.94.0 的 `ModelRetry` 会把修正要求回传模型并消耗重试预算；当前新版文档中的 `ToolFailed` 不是项目锁定版本的 API，不能直接照搬。Deferred approval 需要使用原历史和按 tool call ID 映射的结果恢复。[PydanticAI v1.94 tools source](https://github.com/pydantic/pydantic-ai/blob/v1.94.0/pydantic_ai_slim/pydantic_ai/tools.py)、[PydanticAI Deferred tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools)、[PydanticAI v2 ToolFailed 对照](https://github.com/pydantic/pydantic-ai/blob/v2.31.0/pydantic_ai_slim/pydantic_ai/exceptions.py)

### 3.5 权限与提示词是两回事

工具描述会影响模型是否选择工具，但不会授予权限。Claude Code 的 allow/ask/deny、workspace trust 和 sandbox 由客户端执行层落实；Codex 同样把审批与 sandbox 分开。MCP 规范也明确依赖 host 向用户说明数据访问并取得同意。[Claude Code permissions](https://code.claude.com/docs/en/permissions)、[Claude Code security](https://code.claude.com/docs/en/security)、[Codex approvals and security](https://developers.openai.com/codex/agent-approvals-security)、[MCP security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)

因此以下措施不能由 XML、Markdown 或一句“不要越权”代替：

- 根据当前玩家、会话和连接检查 owner；
- 对高影响工具和未知命令采取审批或拒绝；
- 最小化 MCP server、网络、文件系统和环境变量权限；
- 在执行前冻结参数并在恢复时重新校验 hash/call ID；
- 对副作用状态未知实施不可自动重试；
- 对日志、Trace 和审计做截断、脱敏和内容默认关闭。

### 3.6 上下文工程与评测

上下文窗口应优先保留高信号信息，按需发现工具和资源，压缩旧历史并清理过期工具结果，而不是不断增长 system prompt。Anthropic 将 system instructions、tools、MCP、历史和外部资料都视为有限上下文；OpenAI Responses 也提供 compaction 机制。[Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)、[Anthropic Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)、[OpenAI Compaction](https://developers.openai.com/api/docs/guides/compaction)

评测对象应是“模型 + 运行时 Harness”，不是只看最终回答。一个最小工具评测集应同时包含“应该调用”和“不应该调用”，并记录：工具选择、参数 schema、调用数量、拒绝准确率、错误恢复、审批路径、注入成功率、最终 outcome、tokens、延迟和成本。[OpenAI Agent evals](https://developers.openai.com/api/docs/guides/agent-evals)、[Anthropic Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)、[Anthropic Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)

### 3.7 是否需要显式图框架

PydanticAI 1.94.0 内部已经形成 `UserPromptNode -> ModelRequestNode -> CallToolsNode -> End` 的隐式图；本项目又在外层增加审批恢复、会话和运行时 Harness。LangGraph 的优势是把 state、节点、条件边、checkpoint 和 interrupt 显式建模，尤其适合长流程、跨重启恢复、多分支和 time travel。[PydanticAI v1.94 agent graph](https://github.com/pydantic/pydantic-ai/blob/v1.94.0/pydantic_ai_slim/pydantic_ai/_agent_graph.py)、[LangGraph workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)、[LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)

本项目当前主要是单轮或连续对话中的顺序工具循环，已有 pending approval store 和会话持久化。迁移 LangGraph 会引入 state/reducer/checkpointer 的新复杂度，但不能自动修复本报告的 P0/P1 问题。只有当需求明确转向跨重启的多阶段工作流、复杂分支或需要图级回放时，才值得另立架构决策。

## 4. XML 标签专项结论

### 4.1 回答

XML 标签不是必需的，也不是本项目当前最有价值的升级。它能做的是：帮助部分模型区分 instructions、examples、documents 等语义段落；让人类更容易检查嵌套结构；在经过转义和评测后，为不可信资料提供醒目的模型可读容器。

它不能做的是：

- 不能代替 OpenAI、Anthropic 或 MCP 的原生工具调用协议；
- 不能授予或撤销工具权限；
- 不能保证参数通过业务校验；
- 不能阻止玩家或工具结果伪造闭合标签；
- 不能解决 call ID、并发、审批、幂等、超时和状态未知；
- 不能修复本项目的模板丢失、动态提示不刷新和上下文预算低估。

Anthropic 官方建议复杂提示可使用 XML，但同时指出随着模型能力提高，精确格式本身的重要性低于上下文质量。Codex 公开基础提示主要使用 Markdown 标题；OpenAI 文档也把 Markdown/XML 作为分隔约定，而非协议。[Anthropic Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)、[Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)、[Codex prompt source](https://github.com/openai/codex/blob/12933b69551394328319dcdd1bcee7907326dc85/codex-rs/core/gpt_5_2_prompt.md#L1-L32)、[OpenAI Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)

### 4.2 建议

不要执行全量 XML 迁移。先完成 P0/P1 修复，再做三组跨 provider 消融：

1. 现有 Markdown/纯文本分层；
2. 只对不可信历史、检索资料和工具结果使用有转义的 XML 容器；
3. 对稳定段落使用 XML 的 provider-specific 渲染。

至少比较 DeepSeek、OpenAI、Anthropic 和 Ollama，指标包括工具选择准确率、拒绝准确率、参数 schema 通过率、间接注入成功率、平均输入 tokens、延迟和成本。若 XML 没有稳定收益，就保持更短的 Markdown/纯文本结构。

## 5. 本项目现状架构

### 5.1 玩家消息与会话身份

Gateway 从当前事件取得 `player_name`，构造 `ChatRequest` 时显式携带连接、玩家、provider、conversation、审批模式和 correlation IDs；它没有使用连接级 `ConnectionState.player_name` 做业务判断。[`services/gateway/command_handlers.py:118-153`](../../services/gateway/command_handlers.py#L118-L153)

`ChatRequest` 将 `player_name`、`conversation_id`、`run_id`、`trace_id`、`attempt_id` 和审批恢复材料定义为正式字段。[`models/messages.py:30-57`](../../models/messages.py#L30-L57) `MessageBroker` 的文档与实现把历史按 `(连接, 玩家, 对话)` 分桶，把串行锁按 `(连接, 玩家)` 分桶，并用每连接响应队列将 Agent 与 WebSocket 解耦。[`core/queue.py:57-86`](../../core/queue.py#L57-L86) [`core/queue.py:451-484`](../../core/queue.py#L451-L484)

这一身份模型符合项目共享约定，应保留。任何后续会话、模板、工具、审批、Trace 和下行修改都必须继续显式传递当前事件的 `player_name` / `sender`。

### 5.2 Worker 与依赖注入

Worker 从 Broker 取出请求，加载或恢复对应玩家/对话的历史，清理孤立 tool-call/tool-return，并在每次运行构造 `AgentDependencies`。依赖中显式绑定玩家、conversation、provider、`run_command`、`send_to_game`、Addon Bridge、run/attempt 和 Trace 上下文。[`services/agent/worker.py:559-617`](../../services/agent/worker.py#L559-L617) [`services/agent/worker.py:618-647`](../../services/agent/worker.py#L618-L647) [`models/agent.py:163-186`](../../models/agent.py#L163-L186)

随后 Worker 执行对话压缩，按流式/非流式模式调用 Agent，并把工具调用、工具结果、内容、错误和审批事件转成带玩家身份的响应对象。[`services/agent/worker.py:661-689`](../../services/agent/worker.py#L661-L689) [`services/agent/worker.py:890-938`](../../services/agent/worker.py#L890-L938)

### 5.3 提示词实际组合

实际组合顺序是：

1. 按 `(connection_id, player_name)` 选择模板；
2. 选择运行时 Harness 工具提示或 legacy 工具指南；
3. 构造 `player_name`、provider、model、server time、context、基础 system prompt 等变量；
4. 用顺序 `str.replace` 替换模板占位符；
5. 把没有被模板消费的 `custom_*` 变量追加到末尾；
6. 追加版本化信任约束。

对应实现见 [`services/agent/prompt.py:311-397`](../../services/agent/prompt.py#L311-L397)。动态构造函数从 `AgentDependencies` 取得当前玩家、provider 和上下文信息。[`services/agent/prompt.py:400-443`](../../services/agent/prompt.py#L400-L443)

Agent 使用 `@agent.system_prompt` 注册这个函数，同时挂载运行时 Harness capability、history processor、toolsets 和 instrumentation。[`services/agent/core.py:322-358`](../../services/agent/core.py#L322-L358) 当前 instrumentation 开启时明确关闭 prompt/completion 正文采集，这是正确的隐私默认值。[`services/agent/core.py:70-85`](../../services/agent/core.py#L70-L85)

### 5.4 工具目录、schema 与运行时 Harness

工具目录为内置工具定义意图、风险、适用/禁用场景、参数约束、预览策略、来源、MCP server、schema hash 和副作用标志。[`services/agent/harness/catalog.py:29-77`](../../services/agent/harness/catalog.py#L29-L77) 提示层从目录投影决策树和工具卡片，注册层把目录说明前缀追加到 PydanticAI 工具描述。[`services/agent/harness/prompting.py:35-74`](../../services/agent/harness/prompting.py#L35-L74) [`services/agent/tools.py:83-105`](../../services/agent/tools.py#L83-L105)

模型通过 PydanticAI 原生工具调用产生 tool call；运行时 Harness 负责动态暴露、策略判断、参数冻结、审批、幂等、执行、审计和 Trace。命令工具先匹配硬拒绝根和审批根；其他 HIGH/DANGEROUS 工具默认审批；MCP 工具只有在 allowlist 中才暴露，并仍需审批。[`services/agent/harness/execution.py:376-505`](../../services/agent/harness/execution.py#L376-L505)

审批恢复保留原始 run/trace ID、生成新 attempt ID，并带回原消息历史和 DeferredToolResults。[`services/gateway/command_handlers.py:1330-1367`](../../services/gateway/command_handlers.py#L1330-L1367) 运行时 Harness 还对 call ID、冻结参数和幂等键做关联，这些都是值得保留的设计。

### 5.5 MCP

项目支持 stdio、SSE 和 streamable HTTP MCP。MCP 工具集与内置 FunctionToolset 一起交给 PydanticAI，随后由同一运行时 Harness 做暴露和审批。当前 allowlist 只按工具名判断，目录虽预留 `mcp_server` / `schema_hash`，但没有形成强绑定。[`services/agent/harness/catalog.py:35-47`](../../services/agent/harness/catalog.py#L35-L47) [`services/agent/harness/execution.py:437-505`](../../services/agent/harness/execution.py#L437-L505)

stdio 构造路径显式复制 `os.environ` 后再覆盖 server-specific env；同一模式在两个兼容入口重复存在。[`services/agent/mcp.py:252-270`](../../services/agent/mcp.py#L252-L270) [`services/agent/mcp.py:523-542`](../../services/agent/mcp.py#L523-L542)

### 5.6 上下文、错误、观测与下行

`ContextBuilder` 对历史做 token 粗估、成对保留 tool call/result、截断长工具结果、包装历史摘要，并在模型窗口元数据缺失时使用有限 fallback，而不是无限发送。[`services/agent/context.py:28-43`](../../services/agent/context.py#L28-L43) [`services/agent/context.py:500-537`](../../services/agent/context.py#L500-L537) [`services/agent/context.py:541-585`](../../services/agent/context.py#L541-L585)

命令回调明确区分成功、失败、断线和超时外部状态未知。[`services/agent/tool_results.py:40-90`](../../services/agent/tool_results.py#L40-L90) [`services/agent/worker.py:2040-2075`](../../services/agent/worker.py#L2040-L2075) 结构化 `ToolResult` 进一步表达 error kind、retryable、external state unknown 和内部诊断摘要。[`services/agent/tool_results.py:93-191`](../../services/agent/tool_results.py#L93-L191)

响应通过类型化 GatewayOutbound 和 `BrokerResponseBridge` 交给 SDK `McbeOutboundDelivery` / `McbewsV1Delivery`，长文本分片没有在业务调用点重复实现。[`services/gateway/broker_bridge.py:61-89`](../../services/gateway/broker_bridge.py#L61-L89) [`services/gateway/broker_bridge.py:190-230`](../../services/gateway/broker_bridge.py#L190-L230)

## 6. 值得保留的设计

1. **身份显式传播**：主路径按 connection/player/conversation 隔离，工具回调、审批恢复和下行都携带玩家身份。
2. **原生工具协议**：使用 Pydantic 生成 JSON Schema 和 provider 原生工具调用，不用正则解析模型文本。
3. **运行时 Harness 统一入口**：策略、审批、幂等、审计和 Trace 不依赖模型服从提示词。
4. **审批恢复关联**：保留 call ID、冻结参数、owner、run 和 conversation 校验。
5. **上下文防护基础**：历史 tool pair 成对保留、旧摘要和长工具结果有不可信标记、缺失窗口元数据时有限 fallback。
6. **副作用语义已经建模**：`CommandResult` / `ToolResult` 已能表达 retryable 和 external state unknown；问题在于生产路径过早丢弃，而不是领域模型缺失。
7. **观测隐私默认**：PydanticAI instrumentation 与 Trace 默认关闭正文，audit writer 异步且 fail-soft。
8. **统一下行**：长文本继续经过 `BrokerResponseBridge` 或 SDK delivery，避免分片逻辑漂移。

## 7. 差距矩阵

| ID | 等级 | 类型 | 发现 | 主要影响 | 首要动作 |
|---|---|---|---|---|---|
| F01 | P0 | 已确认缺陷 | 生产工具返回被提前 stringify | 失败/未知被按成功缓存和观测 | 保留 `ToolResult` 到运行时 Harness 收尾边界 |
| F02 | P0 | 已确认缺陷 | stdio MCP 继承完整父环境 | provider key/JWT/密码泄露面 | per-server 最小 env allowlist |
| F03 | P1 | 已确认缺陷 | 有历史时动态 system prompt 不重算 | 模板、变量、provider、时间陈旧 | `dynamic=True` 或改用请求级 instructions，并做历史回归 |
| F04 | P1 | 已确认缺陷 | concise/detailed 丢弃基础 system prompt | 管理员规则静默失效 | 模板契约强制包含稳定基础层 |
| F05 | P1 | 已确认缺陷 | 系统提示与工具 schema 预算低估 | 小窗口/长会话超窗，裁剪不准 | 按实际渲染和实际可见工具动态计数 |
| F06 | P1 | 设计风险 | 未列入审批根的命令自动允许 | 未知副作用命令旁路审批 | 显式安全 allowlist；未知根默认审批 |
| F07 | P1 | 设计风险 | 外部状态未知的方块 plan 可再次恢复 | place/fill 重复执行 | unknown 也作为不可重放终态缓存 |
| F08 | P1 | 设计风险 | 审批批次先弹出、后入队 | QueueFull 时不可恢复 | 事务化提交或失败回滚 pending |
| F09 | P1 | 设计风险 | 日志记录完整 prompt/参数/结果 | 隐私与密钥泄露 | 全局有界预览、脱敏和 hash |
| F10 | P1 | 设计风险 | 短工具结果和摘要 marker 信任边界不完整 | 间接注入与伪造已包装标记 | 所有外部结果统一 typed envelope |
| F11 | P1 | 设计风险 | raw 模板插值和无限 custom vars | 软策略覆盖、token 膨胀、日志泄露 | 长度/字符限制、数据层隔离、转义 |
| F12 | P1 | 设计风险 | 提示卡片与实际暴露工具不一致 | 模型选到不可用工具，MCP 语义缺失 | 从本轮 prepare_tools 投影可见卡片 |
| F13 | P2 | 设计风险 | Trace API 非 loopback 时无认证 | 读取/删除玩家 Trace | 强制 loopback 或 token auth |
| F14 | P2 | 改进机会 | 目录、注册 schema 和测试并非同一事实源 | schema/说明长期漂移 | 真实 Agent 注册契约测试与 schema hash |
| F15 | P2 | 改进机会 | 缺少工具行为离线 eval | 无法量化 XML、提示或 provider 改动 | 建立正负触发、注入和恢复数据集 |
| F16 | P2 | 设计风险 | 无完成事件但有文本时可能缺少历史消息 | 最终文本成功但历史不落盘 | salvage all/new_messages 并回归测试 |

## 8. 详细发现

### F01 / P0：结构化工具失败在生产路径被提前字符串化

**证据。** 注册完成后 `_stringify_tool_results` 把所有工具函数返回值无条件变成 `str(result)`，生产路径明确调用它。[`services/agent/tools.py:91-105`](../../services/agent/tools.py#L91-L105) [`services/agent/tools.py:1262-1266`](../../services/agent/tools.py#L1262-L1266) 但 `ToolResult` 中的状态、错误类型、可重试性和外部状态未知字段只存在于结构化对象。[`services/agent/tool_results.py:93-191`](../../services/agent/tool_results.py#L93-L191)

运行时 Harness 收尾只有在 `isinstance(raw_result, ToolResult)` 时才按真实状态处理；字符串分支会写入幂等缓存，`success` 初始值也保持为 true。[`services/agent/harness/execution.py:1479-1578`](../../services/agent/harness/execution.py#L1479-L1578)

**离线复现。** 使用真实 `register_agent_tools + HarnessCapability + TestModel`，让底层命令返回明确失败：模型看到失败文本，但顶层审计状态为 success，内部字符串启发式又识别为 failure。若命令响应超时，本应得到 `external_state_unknown=true`，却可能被当成成功缓存。[`services/agent/worker.py:2057-2063`](../../services/agent/worker.py#L2057-L2063)

**建议。** 让生产工具保持 `ToolResult` 到 `_finish_tool_execution`；只在模型返回边界调用 `materialize_tool_result`。建立真实注册组合测试，分别断言 success、明确 failure、retryable transient、denied、cancelled、timeout unknown 的模型输出、幂等、审计和 Trace 一致。

### F02 / P0：stdio MCP 子进程继承完整父进程环境

**证据。** 两条 stdio 构造路径都执行 `merged_env = dict(os.environ)` 并传给 `MCPServerStdio`。[`services/agent/mcp.py:252-270`](../../services/agent/mcp.py#L252-L270) [`services/agent/mcp.py:523-542`](../../services/agent/mcp.py#L523-L542) PydanticAI 1.94.0 源码明确其默认是不继承父进程环境，只有显式传 env 才继承。[PydanticAI v1.94 MCP source](https://github.com/pydantic/pydantic-ai/blob/v1.94.0/pydantic_ai_slim/pydantic_ai/mcp.py#L989-L1024)

**影响。** 被配置并启动的 stdio MCP 包可以读取整个服务进程环境。即使 server 本身可信，其依赖供应链或后续更新也扩大了 API key、JWT secret、WebSocket password 等凭据暴露面。

**建议。** 默认空环境，从 `PATH`、必要 runtime path 和每个 server 显式配置中建立最小 allowlist；把 secret 只授予确实需要的 server。上线修复时审计已启用的 MCP 包，并按暴露假设评估密钥轮换。增加子进程探针测试，断言未声明变量不可见。

### F03 / P1：动态系统提示词在已有历史时不重新求值

**证据。** 当前使用裸 `@agent.system_prompt`，没有 `dynamic=True`。[`services/agent/core.py:350-353`](../../services/agent/core.py#L350-L353) PydanticAI 1.94.0 默认 `dynamic=False`；提供非空 `message_history` 且其中已有 system prompt 时，框架沿用历史而不重新解析该函数。[PydanticAI v1.94 Agent source](https://github.com/pydantic/pydantic-ai/blob/v1.94.0/pydantic_ai_slim/pydantic_ai/agent/__init__.py)、[PydanticAI Message history](https://pydantic.dev/docs/ai/core-concepts/message-history)

**离线复现。** 连续两轮改变依赖值，装饰器函数只调用一次，第二轮仍使用第一轮 prompt。因此后续轮次的模板、自定义变量、provider/model、server time 和上下文使用信息可能陈旧。

**建议。** 先用锁定版本集成测试比较 `dynamic=False`、`dynamic=True` 和请求级 instructions，再选择语义。无论采用哪种方式，审批恢复必须验证原历史与当前稳定策略如何合并，不能盲目把新 prompt 追加成重复 system parts。

### F04 / P1：部分模板静默丢弃基础系统提示词

`default` 模板包含 `{system_prompt}`，`concise` 和 `detailed` 均不包含；`detailed` 还允许 Markdown，与默认基础提示“尽量不要使用 markdown”冲突。[`services/agent/prompt.py:52-87`](../../services/agent/prompt.py#L52-L87) [`config/settings.py:784-786`](../../config/settings.py#L784-L786)

模板是表现层选择，不应决定管理员基础策略是否存在。建议将稳定基础层在模板外统一拼接，或在模板注册时强制校验必要槽位。用 sentinel system prompt 遍历内置和自定义模板，断言基础层恰好出现一次。

### F05 / P1：上下文预算低估实际提示与工具开销

默认系统提示预留 800 tokens，工具 schema 按每项 200 tokens、未给 tool_count 时按 8 项计算。[`services/agent/context.py:35-43`](../../services/agent/context.py#L35-L43) [`services/agent/context.py:335-354`](../../services/agent/context.py#L335-L354) 本地测量却有 21 个内置工具；运行时 Harness 提示约 4,816 字、工具描述约 8,999 字、参数 schema 约 6,513 字，未含协议开销已约 20,328 字。MCP 工具还未计入。

模型元数据关闭或不可用时，provider 配置统一回退 128k，真实小窗口兼容模型可能被高估。[`config/settings.py:969-977`](../../config/settings.py#L969-L977)

建议在每轮 `prepare_tools` 后基于实际 system prompt、实际可见 ToolDefinition 和 provider tokenizer/count API 计算预算；无法精确计数时以实际序列化字符做保守估算。输入前硬检查最终请求，而不是只裁剪历史。

### F06 / P1：命令审批对未知根默认允许

目录把 `run_minecraft_command` 标为 HIGH，但策略先处理少量 hard deny/approval roots；如果命令根没有命中审批列表，就直接返回 ALLOW。[`services/agent/harness/catalog.py:80-90`](../../services/agent/harness/catalog.py#L80-L90) [`services/agent/harness/execution.py:393-435`](../../services/agent/harness/execution.py#L393-L435) 默认审批根只有 `clear/clone/damage/fill/kill/replaceitem/setblock/structure/summon`。[`config/settings.py:860-873`](../../config/settings.py#L860-L873) 测试还明确断言 `give` 和 `time` 自动允许。[`tests/test_runtime_harness_execution.py:701-727`](../../tests/test_runtime_harness_execution.py#L701-L727)

风险不只来自传统“破坏性命令”。`tp`、`gamemode`、`effect`、`execute`、`event` 或服务器扩展命令都可能有高影响。建议改成显式安全命令根 allowlist；未知根默认审批，危险根硬拒绝。策略应基于解析后的根和子命令，而不是单纯字符串前缀。

### F07 / P1：外部状态未知的方块计划可再次恢复

Addon Bridge 已正确把写操作超时映射为 `external_state_unknown=true` 且 `retryable=false`。[`services/agent/block_ops/bridge.py:218-245`](../../services/agent/block_ops/bridge.py#L218-L245) 但 `execute_block_plan` 只在成功时把 plan 标记为 executed；失败允许再次恢复。[`services/agent/block_ops/tools_impl.py:150-193`](../../services/agent/block_ops/tools_impl.py#L150-L193)

如果世界已经修改但响应丢失，再次恢复可能重复 place/fill。建议把 external state unknown 作为“不可自动重放终态”缓存，并要求玩家重新查询世界或创建新计划。测试应模拟“实际修改后抛超时”，验证同 plan 再次 resume 不发送第二次请求。

### F08 / P1：审批批次与 Broker 入队不是事务

当一批审批全部决定后，PendingApprovalStore 先弹出整个批次。[`services/agent/harness/approvals.py:365-392`](../../services/agent/harness/approvals.py#L365-L392) Gateway 随后提交 Broker；若 `QueueFull`，只返回繁忙错误，没有恢复 pending 批次。[`services/gateway/command_handlers.py:1366-1388`](../../services/gateway/command_handlers.py#L1366-L1388)

建议采用 prepare/commit 状态，只有 Broker 接受后才移除；或在 QueueFull 时原子回滚。测试应把队列填满，验证原 owner 仍可重试，同批 ID 不会重复执行。

### F09 / P1：结构化审计较克制，但普通日志仍记录正文

Agent debug 日志记录完整用户 prompt；Worker info 日志记录完整工具参数和结果。[`services/agent/core.py:1061-1067`](../../services/agent/core.py#L1061-L1067) [`services/agent/worker.py:890-933`](../../services/agent/worker.py#L890-L933) 这与工具审计“有界预览、敏感字段、正文默认关闭”的方向不一致。

建议建立统一日志 processor：字段级 allowlist、长度上限、敏感键脱敏、未知大对象只记录类型/大小/hash。开发环境如需全文，应显式 opt-in 且醒目标注。用 caplog 注入 token、Bearer、玩家私密文本和超长结果，断言所有 logger/Trace/audit 输出都不含原文。

### F10 / P1：不可信数据包装只覆盖部分历史材料

长工具结果超过 800 字才包装并截断；短 tool-return 保持原文。[`services/agent/context.py:541-560`](../../services/agent/context.py#L541-L560) 摘要是否已包装的判断只要正文任意位置出现 `factual_hints_only` 等 marker 就直接放行，恶意摘要可以伪造 marker。[`services/agent/context.py:247-268`](../../services/agent/context.py#L247-L268)

XML 不能修复这个问题。应把工具结果、检索内容和历史摘要统一建模为带 source/trust/type 的应用对象，再由 provider adapter 序列化；marker 只作为展示，不作为安全判断。至少增加短恶意工具结果、闭合标签、正文伪造 marker 和历史恢复的注入测试。[Anthropic Mitigate prompt injections](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks)

### F11 / P1：模板变量是 raw 插值且可无限追加

所有变量按字典顺序执行 `str.replace`，未使用的 `custom_*` 直接追加到 system prompt；没有长度、换行或占位符限制。[`services/agent/prompt.py:367-395`](../../services/agent/prompt.py#L367-L395) 这不会直接突破运行时 Harness 的硬权限，但可覆盖软策略、伪造分段、膨胀上下文并进入日志。

建议把管理员配置、运行时身份、玩家自定义数据分成不同 typed sections；对玩家变量设置单项/总量上限和允许字符，序列化时转义。连接级兼容 API 还会回退匿名桶，应标记为 deprecated 并确保 Gateway 主路径不调用。[`services/agent/prompt.py:143-154`](../../services/agent/prompt.py#L143-L154) [`services/agent/prompt.py:292-309`](../../services/agent/prompt.py#L292-L309)

### F12 / P1：模型可见工具提示与实际暴露工具不一致

运行时 Harness prompt 从完整目录渲染所有卡片，而 `PolicyEngine.is_tool_exposed` 会按 Addon capability 动态隐藏 block tools；MCP allowlist 工具可以暴露，却没有对应目录卡片和 server/schema 信息。[`services/agent/harness/prompting.py:42-63`](../../services/agent/harness/prompting.py#L42-L63) [`services/agent/harness/execution.py:500-525`](../../services/agent/harness/execution.py#L500-L525)

建议以本轮 `prepare_tools` 的实际输出为唯一投影输入：只为可见工具生成短卡片，并把 MCP 工具绑定 `(server, tool, schema_hash)`。工具描述负责 what/when/parameters/side effects，system prompt 只保留决策原则，减少重复 token。

### F13-F16 / P2：长期一致性与评测缺口

- Trace API 的 GET/DELETE 没有认证；默认 host 是 loopback，但配置可改。非 loopback 时应拒绝启动或要求 token。[`services/agent/trace_api.py:28-55`](../../services/agent/trace_api.py#L28-L55) [`services/agent/trace_api.py:110-120`](../../services/agent/trace_api.py#L110-L120) [`config/settings.py:880-886`](../../config/settings.py#L880-L886)
- 目录测试名为“all registered tools”，实际只把 `list_tool_names()` 与自身投影比较，没有实例化真实 Agent。[`tests/test_runtime_harness_catalog.py:25-45`](../../tests/test_runtime_harness_catalog.py#L25-L45) 应比较真实注册名、ToolDefinition schema 与目录 schema hash。
- MCP 状态摘要读取 `info.tools` 数量，但工具发现信息没有形成稳定可观测契约；allowlist 也只按名称。[`services/agent/mcp.py:86-100`](../../services/agent/mcp.py#L86-L100)
- 流式迭代若没有 `is_complete` 但已有文本，会返回 success，却没有从 active run salvage all/new messages；可能导致文本成功但历史没有落盘。[`services/agent/worker.py:1397-1420`](../../services/agent/worker.py#L1397-L1420)
- 现有测试擅长 deterministic 控制流、玩家隔离、审批和 tool-pair 清洗，但生产注册组合测试依赖测试专用 unwrap/wrap，恰好绕开了 F01。[`tests/test_agent_tools.py:41-84`](../../tests/test_agent_tools.py#L41-L84)

## 9. 目标参考架构

### 9.1 提示词分层

建议把当前单字符串构造改成先建模、后渲染：

```text
StablePolicy
  - MCBE Chat Agent 的角色与回答边界
  - 玩家/会话身份不得由不可信文本覆盖
  - 工具选择与拒绝的一般原则

RuntimeIdentity
  - connection_id（模型通常无需完整值）
  - player_name
  - conversation_id
  - provider/model

VisibleToolProjection
  - 只含本轮实际暴露工具
  - what / when / not-when / side effects
  - schema 由 ToolDefinition 单独发送，不在 prompt 重复全文

UntrustedContext
  - 玩家自定义变量
  - 历史摘要
  - 检索资料
  - MCP/工具结果
  - 每项带 source/trust/type/size，渲染时转义
```

`StablePolicy` 应每轮语义稳定且不可被模板移除；模板只决定回答风格。`RuntimeIdentity` 和 `VisibleToolProjection` 按当前 deps 和 capability 每轮刷新。不可信数据永远不参与 owner、审批或权限计算。

### 9.2 工具执行状态机

```text
DISCOVERED
  -> VALIDATED
  -> POLICY_DENIED | APPROVAL_PENDING | READY
APPROVAL_PENDING
  -> APPROVED | DENIED | EXPIRED
READY / APPROVED
  -> EXECUTING
  -> SUCCEEDED
  -> FAILED_RETRYABLE
  -> FAILED_FINAL
  -> EXTERNAL_STATE_UNKNOWN
  -> CANCELLED
```

每个状态都保留 `trace_id/run_id/attempt_id/tool_call_id/player_name/conversation_id/args_hash`。只有 `SUCCEEDED` 可写成功幂等缓存；`EXTERNAL_STATE_UNKNOWN` 必须写不可重放 tombstone；`FAILED_RETRYABLE` 只有在工具无副作用且策略允许时才能重试。

### 9.3 MCP 信任模型

- 配置实体以 server 为边界，工具身份使用 `(server_id, tool_name, schema_hash)`。
- stdio 默认空环境，只传最小 runtime 变量和 server-specific secrets。
- HTTP MCP 使用最小 OAuth scope；server instructions、tool annotations、resources 和 prompts 均视为外部内容。
- tools/resources/prompts 分开建模；list-changed 后重新发现、重算 schema hash 和审批策略。
- 明确 pin 项目实际采用的 MCP SDK/规范版本；不要把 2025 initialize 握手与 2026 request `_meta` 协商混在一个兼容路径。[MCP 2025 lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)、[MCP 2026 versioning](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning)、[MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)

## 10. 分阶段路线图

### Phase 0：阻断语义失真与凭据暴露

| 项目 | 收益 | 成本/依赖 | 验收指标 |
|---|---|---|---|
| 保留 `ToolResult` 到运行时 Harness 收尾 | 修复幂等、审计、Trace 和状态未知 | 中；需要调整工具返回适配与测试 | 六类结果在模型/缓存/audit/Trace 中一致 |
| stdio MCP 最小 env | 关闭宿主凭据泄露面 | 低到中；需逐 server 列必要变量 | 未声明 secret 在探针子进程不可见 |
| 暴露评估与密钥轮换预案 | 缩短潜在泄露窗口 | 运营动作 | 所有启用 server 有 owner、版本与 env 清单 |

Phase 0 不应捆绑 XML、模板重写或框架迁移。

### Phase 1：恢复提示与预算正确性

| 项目 | 收益 | 成本/依赖 | 验收指标 |
|---|---|---|---|
| 明确 dynamic system prompt / instructions 语义 | 模板和身份每轮一致 | 中；需覆盖历史与审批恢复 | 改模板/provider/变量后下一轮生效且不重复注入 |
| 强制基础系统层不被模板移除 | 管理员策略稳定 | 低 | 所有模板 sentinel 恰好出现一次 |
| 动态计算真实 prompt/tool 预算 | 防止小窗口超限 | 中；依赖 provider tokenizer 能力 | 最终请求预算误差有界，超限在发送前拒绝 |
| 本轮可见工具投影 | 减少 prompt/schema 重复和不可用工具幻觉 | 中 | prompt 卡片集合等于 prepare_tools 输出集合 |

### Phase 2：收紧执行、审批和信任边界

| 项目 | 收益 | 成本/依赖 | 验收指标 |
|---|---|---|---|
| 未知命令根默认审批 | 防止高影响命令旁路 | 中；需定义安全 allowlist | 未知根 100% 不自动执行 |
| approval queue 事务化 | 避免批准后丢批 | 中 | QueueFull 可重试且不重复执行 |
| unknown tombstone | 防止重复世界修改 | 中 | 修改后超时再 resume 不二次发送 |
| typed untrusted envelope | 统一注入防线 | 中到高 | 短/长工具结果、摘要、MCP 内容都带来源和信任类型 |
| 日志/Trace 全链路脱敏 | 降低隐私风险 | 中 | caplog/Trace/audit 注入样本零正文泄露 |

### Phase 3：建立评测与版本门禁

1. 建立 80-150 个离线 case，平衡应调用/不应调用、参数边界、工具失败、审批、注入、多人隔离和上下文增长。
2. 每个 provider 至少运行 deterministic outcome grader、trace grader 和小比例人工复核。
3. 记录选择准确率、拒绝准确率、schema 通过率、最终成功率、重复副作用率、注入成功率、tokens、p50/p95 延迟和成本。
4. 把 PydanticAI 1.94.x、未来 v2、MCP SDK/规范升级做成显式迁移任务；当前文档 API 不得未经 tagged-source 和集成测试核验直接进入代码。
5. 完成上述基线后再做 XML/no-XML 消融；只有稳定收益才合入默认渲染。

## 11. 建议测试清单

### 11.1 生产组合测试

- 真实 `register_agent_tools + HarnessCapability + TestModel`，不 unwrap 工具函数。
- 对 `ToolResult.ok/failure`、retryable、denied、cancelled、timeout unknown 逐一断言模型结果、idempotency、audit 和 Trace。
- 实例化真实 Agent，比较注册工具名、实际 JSON Schema、目录 entry 和 schema hash。
- 动态 Addon capability 与 MCP allowlist 改变后，比较 prompt 卡片、provider ToolDefinition 和运行时 Harness 暴露集合。

### 11.2 会话与提示测试

- 用 sentinel 基础 prompt 遍历全部内置/自定义模板。
- 在已有历史时切换模板、provider、player variable、context 开关，验证下一轮生效。
- 审批恢复时验证 system prompt 不缺失、不重复，原 call ID 和当前 owner 仍匹配。
- 两玩家 × 两 conversation 并发，验证历史、模板、变量、审批和下行完全隔离。

### 11.3 安全与故障测试

- stdio MCP 子进程枚举环境，断言未 allowlist 的 provider/JWT/密码变量不可见。
- 未知命令根、嵌套 `execute`、扩展命令和大小写/空白变体默认审批或拒绝。
- 世界实际修改后响应超时，再次 resume 不重复执行。
- QueueFull、断线、取消、过期审批和恢复异常均产生完整、相互一致的审计/Trace 收尾。
- 短工具结果、长工具结果、摘要和 MCP 结果包含伪造 marker、闭合 XML、Markdown 指令和 secret；验证权限不变、数据被标记、日志不泄露。

### 11.4 评测集

- 正例：明确执行命令、查询世界、查询 Wiki、向当前玩家展示。
- 负例：上下文已有答案、纯聊天、缺少关键参数、要求越权、未知命令、应先澄清。
- 恢复例：参数可修正、瞬态只读失败、明确失败、外部状态未知、审批拒绝。
- 跨 provider：相同语义分别使用 DeepSeek/OpenAI/Anthropic/Ollama，比较工具选择与错误恢复。

## 12. 反方观点、限制与开放问题

### 12.1 反方观点

- **“字符串工具结果更兼容模型。”** 模型边界确实需要文本或 provider content blocks，但 stringify 应发生在运行时 Harness 已完成分类之后；不能用模型兼容性解释内部状态提前丢失。
- **“MCP server 本来就是管理员配置的，所以可继承环境。”** 管理员信任 server 不等于信任其所有传递依赖和未来版本。最小权限仍是默认正确选择，确需 secret 的 server 可以显式授予。
- **“所有高风险命令都审批会影响体验。”** 可以维护显式安全 allowlist和会话级自动批准；未知操作默认放行的代价高于一次审批。
- **“XML 能快速改善模型服从。”** 可能对特定模型/任务有效，但当前没有跨 provider 的项目评测证据；它无法解决已确认的 P0/P1。
- **“LangGraph 能统一审批和恢复。”** 显式图有价值，但迁移成本高，且仍需正确的结果语义、权限、env 和日志设计。

### 12.2 限制

- 本次是静态审查与无副作用离线复现，没有真实付费模型、真实 MCBE 世界或生产日志样本。
- 外部文档在 2026-08-15 时有效；OpenAI、Anthropic、PydanticAI 和 MCP 仍会演进，实施前应重新核验锁定版本。
- MCP `2026-07-28` 用于现代实践对照，不表示本项目当前依赖已经实现该版本。
- 部分 P1/P2 被标为设计风险，需通过建议测试确定实际可达性与生产影响。

### 12.3 开放问题

1. 运行时 Harness 的总开关究竟只控制 prompt/schema，还是也应关闭 policy/approval/audit？需要明确产品语义。
2. 玩家自定义变量是纯显示偏好，还是允许定义持久行为？不同答案会改变其信任层和长度上限。
3. 哪些 Minecraft 命令根可以在无需审批时安全执行？应由产品策略和真实服务器权限共同决定。
4. MCP server 是否允许第三方包、远程 URL 和动态更新？若允许，需要供应链、OAuth 和版本 pin 方案。
5. 是否存在跨重启的多阶段世界构建需求？只有该需求成立，显式 durable graph 才可能超过当前隐式 tool loop 的收益。

## 13. 外部来源目录

以下均为本报告实际采用或用于边界核验的一手入口；相同主题优先以锁定源码或规范版本为准。

### OpenAI 与 Codex

1. [OpenAI Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)
2. [OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)
3. [OpenAI Programmatic Tool Calling](https://developers.openai.com/api/docs/guides/tools-programmatic-tool-calling)
4. [OpenAI Agents: Define agents](https://developers.openai.com/api/docs/guides/agents/define-agents)
5. [OpenAI Agents: Run agents](https://developers.openai.com/api/docs/guides/agents/running-agents)
6. [OpenAI Agents SDK FunctionTool source](https://github.com/openai/openai-agents-python/blob/9aba9002935b56d7253d28d99b2a8bca2d2450a7/src/agents/tool.py#L441-L520)
7. [Codex approvals and security](https://developers.openai.com/codex/agent-approvals-security)
8. [Codex public prompt source](https://github.com/openai/codex/blob/12933b69551394328319dcdd1bcee7907326dc85/codex-rs/core/gpt_5_2_prompt.md#L1-L32)
9. [Codex AGENTS.md discovery source](https://github.com/openai/codex/blob/12933b69551394328319dcdd1bcee7907326dc85/codex-rs/core/src/agents_md.rs#L50-L90)
10. [Codex tool loop source](https://github.com/openai/codex/blob/12933b69551394328319dcdd1bcee7907326dc85/codex-rs/core/src/session/turn.rs#L139-L151)
11. [OpenAI Skills](https://developers.openai.com/api/docs/guides/tools-skills)
12. [OpenAI Compaction](https://developers.openai.com/api/docs/guides/compaction)
13. [OpenAI Agent evals](https://developers.openai.com/api/docs/guides/agent-evals)

### Anthropic 与 Claude Code

14. [Anthropic Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)
15. [Anthropic Tool use overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)
16. [Anthropic Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)
17. [Anthropic Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)
18. [Anthropic Strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)
19. [Anthropic Parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)
20. [Anthropic Messages API](https://platform.claude.com/docs/en/api/messages)
21. [Anthropic Mitigate jailbreaks and prompt injections](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks)
22. [Claude Code permissions](https://code.claude.com/docs/en/permissions)
23. [Claude Code security](https://code.claude.com/docs/en/security)
24. [Claude Code MCP approvals](https://code.claude.com/docs/en/mcp#require-approval-for-a-specific-tool)
25. [Anthropic MCP connector limitations](https://docs.anthropic.com/en/docs/agents-and-tools/mcp-connector)
26. [Anthropic Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
27. [Anthropic Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)
28. [Anthropic Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
29. [Anthropic Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)

### MCP

30. [MCP 2026-07-28 overview](https://modelcontextprotocol.io/specification/2026-07-28)
31. [MCP tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)
32. [MCP resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources)
33. [MCP prompts](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts)
34. [MCP versioning](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning)
35. [MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
36. [MCP security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)
37. [MCP 2025-11-25 lifecycle compatibility reference](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
38. [MCP sampling deprecation reference](https://modelcontextprotocol.io/specification/2026-07-28/client/sampling)

### PydanticAI 与 LangGraph

39. [PydanticAI v1.94 Agent source](https://github.com/pydantic/pydantic-ai/blob/v1.94.0/pydantic_ai_slim/pydantic_ai/agent/__init__.py)
40. [PydanticAI v1.94 agent graph source](https://github.com/pydantic/pydantic-ai/blob/v1.94.0/pydantic_ai_slim/pydantic_ai/_agent_graph.py)
41. [PydanticAI v1.94 tools source](https://github.com/pydantic/pydantic-ai/blob/v1.94.0/pydantic_ai_slim/pydantic_ai/tools.py)
42. [PydanticAI v1.94 MCP source](https://github.com/pydantic/pydantic-ai/blob/v1.94.0/pydantic_ai_slim/pydantic_ai/mcp.py#L989-L1024)
43. [PydanticAI v1.94 usage source](https://github.com/pydantic/pydantic-ai/blob/v1.94.0/pydantic_ai_slim/pydantic_ai/usage.py)
44. [PydanticAI Deferred tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools)
45. [PydanticAI Message history](https://pydantic.dev/docs/ai/core-concepts/message-history)
46. [PydanticAI Instrumentation](https://pydantic.dev/docs/ai/capabilities/instrumentation)
47. [PydanticAI Evals](https://pydantic.dev/docs/ai/evals/getting-started/core-concepts)
48. [LangGraph Workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
49. [LangGraph Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)

## 14. 复跑输入

### 14.1 Firecrawl 检索问题集

以下是可重新交给 Firecrawl search/scrape 的 12 个问题角度；抓取时优先限制到官方域名和官方 GitHub 组织：

1. OpenAI Responses API system/developer/user hierarchy and previous_response_id instructions behavior
2. OpenAI function calling strict JSON Schema call_id parallel_tool_calls and error loop
3. OpenAI Agents SDK tool approval timeout allowed callers guardrails and tracing
4. Codex CLI public prompt AGENTS.md discovery tool loop approvals and sandbox
5. Anthropic XML prompt structure benefits limitations and injection boundary
6. Anthropic tool_use tool_result strict tool use parallel calls and error handling
7. Claude Code permissions workspace trust sandbox and MCP approval
8. MCP 2026 tools resources prompts versioning authorization security and list-changed
9. MCP 2025 lifecycle versus 2026 version negotiation compatibility
10. PydanticAI 1.94 system_prompt message_history ToolReturn ModelRetry DeferredToolRequests and MCP stdio env
11. LangGraph StateGraph ToolNode checkpointer durable execution interrupt and Command resume
12. Agent context engineering tool design trace eval positive/negative trigger datasets

### 14.2 Context7 输入

```text
resolve-library-id: Pydantic AI
query-docs: system_prompt dynamic behavior with message_history
query-docs: ToolReturn, ModelRetry and deferred approval call ID mapping
query-docs: MCPServerStdio environment inheritance

resolve-library-id: LangGraph
query-docs: StateGraph LLM-to-tool loop and tool_call_id
query-docs: checkpointers, durable execution, interrupts and Command resume
```

Context7 当前 PydanticAI 文档可能指向 v2；项目结论必须继续以 v1.94.0 tagged source 与锁定环境集成测试为准。

### 14.3 本地核验输入

```bash
git rev-parse HEAD
python -c "from importlib.metadata import version; print(version('pydantic-ai'))"
pytest tests/test_runtime_harness_execution.py tests/test_agent_tools.py tests/test_agent_context.py tests/test_prompt_template.py tests/test_mcp.py
git diff --check
```

建议新增的复现不应执行真实命令：使用 PydanticAI `TestModel` / `FunctionModel`、假的 `run_command`、内存 audit/Trace writer 和只打印环境变量名的受控 MCP 探针。
