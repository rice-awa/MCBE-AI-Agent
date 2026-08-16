# 外部调研角度 C：PydanticAI 机制与 LangGraph 对照

> 只读研究备忘录；未修改项目源码。来源以官方锁定版本源码、官方文档为主。Context7 已先 resolve `/pydantic/pydantic-ai` 与官方 LangGraph 文档库，并按单概念查询；同时核验了官方页面和 GitHub tagged source。

## 版本边界

仓库 `requirements.txt` 声明 `pydantic-ai~=1.94.0`，PEP 440 含义为 `>=1.94.0,<1.95.0`；当前环境实际为 1.94.0。Context7 可查询到的 v1.97.0 和当前 pydantic.dev 文档不能直接当作本项目 API。当前 PydanticAI v2 已有破坏性变化，尤其 MCP；下文凡标注“当前/v2”者仅用于迁移对照。

## 核实主张

1. **隐式图与 tool loop（High，v1.94 源码）**：PydanticAI 内部可看作 `UserPromptNode → ModelRequestNode → CallToolsNode → End`；工具返回写入消息历史后继续下一次模型请求。默认适合顺序对话/工具循环，不等于没有图，只是图由框架隐藏。

2. **`system_prompt`（High，v1.94 源码）**：静态或动态系统提示保存为 `SystemPromptPart`。`@agent.system_prompt` 裸装饰器的 `dynamic` 默认是 `False`；非空 `message_history` 下，初始静态系统提示不会重算。`@agent.system_prompt(dynamic=True)` 会在有历史时重新解析动态系统提示。

3. **`instructions`（High，但有版本语义风险）**：它是 `ModelRequest` 的指令通道，不是同一类 system prompt；v1.94 源码在构造模型请求时解析 agent/toolset instructions，动态指令函数按请求重算。当前官方文档还警告显式 `message_history` 会影响初始 instructions 的发送，不能把 v2/current 文档直接套到 1.94；应以锁定源码和集成测试为准。

4. **动态 prompt/deps（High）**：`deps_type=AgentDependencies` 后，`RunContext.deps` 可被 system prompt、工具、输出 validator 使用。动态函数本身不会改变“是否重算”的规则；本仓 `build_dynamic_prompt` 虽名为 dynamic，当前裸装饰器实际是 `dynamic=False`。

5. **tools/toolsets 与 JSON Schema（High）**：`@agent.tool` 注入 `RunContext`，`@agent.tool_plain` 无上下文；函数签名及 Pydantic 类型生成 `ToolDefinition.parameters_json_schema`，返回类型可生成 return schema；MCP/local toolsets 可合并进同一工具目录。

6. **工具返回如何回流模型（High）**：普通返回会规范化为 `ToolReturnPart`。`ToolReturn.return_value` 是模型看到的工具结果，`ToolReturn.content` 可追加模型可见内容，`metadata` 仅应用侧可用。本仓工具多数返回字符串或 `ToolResult`，由 `_stringify_tool_results` 处理，尚未普遍使用结构化 `ToolReturn`。

7. **`ModelRetry`（High，v1.94）**：工具、输出 validator 或能力 hook 抛出 `ModelRetry` 后，框架生成 `RetryPromptPart`，把错误/修正要求送回模型；工具重试会消耗 tool retry budget。MCP 调用失败在 v1.94 也主要转换为 `ModelRetry`。

8. **`ToolFailed`（High，版本边界）**：官方 v1.94/v1.97 源码没有 `ToolFailed`；当前 v2.31 才有该终止失败异常。v2 语义是“模型可见失败，但不添加 correction prompt、不消耗工具 retry budget”。项目锁定 1.94，不能直接导入或假定该 API；终止失败应先约定 1.94 的普通结构化错误返回/应用协议。

9. **Deferred/approval tools（High）**：`DeferredToolRequests` 包含待审批/外部调用，调用方构造 `DeferredToolResults` 并使用原始 `message_history` 启动后续 run；call ID 必须全部匹配。恢复是新 `run_id`，但应保留/关联同一 `conversation_id`。本仓 `output_type=[str, DeferredToolRequests]`、pending owner key 和恢复流程总体正确。

10. **Message history（High）**：PydanticAI 的消息历史是可序列化的 `ModelMessage` 列表；`ModelMessagesTypeAdapter` 可用于 JSON 存储/恢复。显式历史会改变 system prompt/instruction 注入，动态 prompt 还可能因依赖状态变化而改变历史语义；必须测试压缩、恢复、重放和不可信客户端历史清洗。

11. **Usage limits（High，v1.94）**：`UsageLimits` 有 request/tool/input/output/total 限制；request/tool limit 在调用前检查，token limit 在模型响应后检查（可按 provider 提前计数）。计数是一次 run 的累计量；本仓 request 默认 8、tool calls 16、token 限制可选，Anthropic 才打开 `count_tokens_before_request`，逻辑合理。

12. **Retry 配置迁移（High）**：v1.94 `retries=` 仍可用但已发出弃用警告，并同时影响 tool/output retries；`tool_retries`、`output_retries` 可拆开配置。当前 v2 改成 `int | {tools, output}` 形式。仓库 `retries=settings.agent_retries`（默认 3）行为可用，但应补迁移测试。

13. **MCP API 与生命周期（High）**：v1.94 使用 `MCPServerStdio`、`MCPServerSSE`、`MCPServerStreamableHTTP`，可发现工具 schema、前缀工具名、注入 server instructions，并把 MCP 错误转 retry。v2 主要改为 `MCPToolset`，不能无评估升级。

14. **MCP stdio 环境变量是 P0 风险（High，一手官方证据）**：v1.94 `MCPServerStdio.env` 文档明确写着：默认子进程“不继承父进程任何环境变量”；如需继承必须显式 `env=os.environ`。本仓 `services/agent/mcp.py` 使用 `env=dict(os.environ)`，语义等价于完整继承，可能把 API key、密码等全部暴露给 MCP 子进程。应改为最小 allowlist，并审计每个 stdio server 的必要 `PATH/HOME/运行时变量`。

15. **Instrumentation/evals（High，当前官方文档；版本需确认）**：instrumentation 通过 OpenTelemetry 记录 agent/model/tool spans，`InstrumentationSettings(include_content=False, include_binary_content=False)` 可做隐私控制；本仓关闭内容采集合理。`pydantic-evals` 是独立包，虽当前环境已有 1.94.0，`requirements.txt` 未声明；应作为明确的 dev/optional dependency，覆盖 prompt-history、deferred approval、tool return、usage-limit、MCP 安全等案例。

16. **LangGraph 对照（High，官方文档；当前版本未纳入本仓）**：LangGraph 用显式 `TypedDict/MessagesState`、命名节点、普通/条件边和 `ToolNode` 实现 LLM→工具→LLM；工具结果是带 `tool_call_id` 的 `ToolMessage` 写回 state。checkpointer 按 `thread_id` 保存每个 super-step，支持 durable execution、恢复和 time travel；`interrupt`/`Command(resume=...)` 实现审批。它比 PydanticAI 的隐式 loop 更适合分支、长流程和审计，但需要设计 state/reducer/checkpointer，复杂度和迁移成本显著更高。

## 对本仓的适配判断与清单

- **P0**：收紧 `MCPServerStdio` 的 `env`，禁止默认 `dict(os.environ)`；逐 server allowlist，并评估现有密钥轮换。
- **P0/P1**：为 `@agent.system_prompt` 增加 history 回归测试：改变 `player_name`/session/template 后检查 `dynamic=False`、`dynamic=True` 和 `instructions` 三种路径的实际消息；继续显式传递 `player_name`/`sender`。
- **P1**：保留 deferred approval 的 connection/player/conversation owner 校验；测试重复、过期、跨玩家 approval 与缺失 call ID。
- **P1**：锁定 `pydantic-ai~=1.94.0`，建议同时验证 `mcp<2`；不要把 v2 文档示例混入当前实现。
- **P1**：补充 `ModelRetry`、普通终止错误、ToolReturn 的模型可见性测试；不要在 1.94 代码中使用 `ToolFailed`。
- **P1**：显式声明 `pydantic-evals`/Logfire/OTel exporter 的开发依赖，避免依赖本机环境偶然安装。
- **P2/架构决策**：当前 MCBE Chat Agent 的顺序工具循环和应用层 pending store 不需要迁移 LangGraph；只有需要多分支工作流、跨重启 durable state、可视化审计时，再设计显式 state graph，而不是直接替换 Agent。

## 直接来源（官方一手；S1–S5 为锁定版本，S6 为 v2 对照；当前文档均标注版本不确定）

- **S1** [PydanticAI v1.94 Agent source](https://github.com/pydantic/pydantic-ai/blob/v1.94.0/pydantic_ai_slim/pydantic_ai/agent/__init__.py) — Agent、system prompt/instructions、retries、run/history API。
- **S2** [PydanticAI v1.94 agent graph source](https://github.com/pydantic/pydantic-ai/blob/v1.94.0/pydantic_ai_slim/pydantic_ai/_agent_graph.py) — 节点循环、retry prompt、tool return 回流。
- **S3** [PydanticAI v1.94 tools source](https://github.com/pydantic/pydantic-ai/blob/v1.94.0/pydantic_ai_slim/pydantic_ai/tools.py) — Tool、ToolDefinition、DeferredToolRequests/Results、ToolReturn。
- **S4** [PydanticAI v1.94 MCP source（含 `MCPServerStdio.env` 注释）](https://github.com/pydantic/pydantic-ai/blob/v1.94.0/pydantic_ai_slim/pydantic_ai/mcp.py#L989-L1024) — stdio env 继承、安全发现及 MCP 语义。
- **S5** [PydanticAI v1.94 usage source](https://github.com/pydantic/pydantic-ai/blob/v1.94.0/pydantic_ai_slim/pydantic_ai/usage.py) — UsageLimits/UsageLimitExceeded。
- **S6** [PydanticAI v2.31 exceptions source](https://github.com/pydantic/pydantic-ai/blob/v2.31.0/pydantic_ai_slim/pydantic_ai/exceptions.py) — 当前 `ToolFailed` 与 `ModelRetry` 对照；不是项目锁定 API。
- **S7** [PydanticAI deferred tools docs](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools) — approvals、外部执行、恢复与结果事件。
- **S8** [PydanticAI message history docs](https://pydantic.dev/docs/ai/core-concepts/message-history) — 历史重放、system prompt reinjection、序列化/清洗。
- **S9** [PydanticAI instrumentation docs](https://pydantic.dev/docs/ai/capabilities/instrumentation) — OpenTelemetry spans 与隐私设置。
- **S10** [PydanticAI evals docs](https://pydantic.dev/docs/ai/evals/getting-started/core-concepts) — Dataset/Case/Evaluator/Experiment/EvaluationReport。
- **S11** [LangGraph workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents) — StateGraph、节点/边、tool loop。
- **S12** [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) — thread checkpoint、durable execution、恢复。
