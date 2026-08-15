# 调研与源码审查收口记录

调研日期：2026-08-15
代码快照：`dev@4d9888be093279b8dbf66b427c944f4ce3f242fc`
项目依赖：`pydantic-ai 1.94.0`

## 子代理分工与采用方式

- `openai_codex_research`：OpenAI Responses API、Agents SDK、Codex CLI 公开源码；仅采信 OpenAI 官方网站和 `openai/*` 官方仓库。
- `anthropic_mcp_research`：Anthropic 工具调用、Claude Code、XML 提示建议和 MCP `2026-07-28` 规范；通过 Firecrawl 完成 12 个检索方向并抓取 50 个以上一手页面。
- `framework_research`：PydanticAI 1.94/当前版本边界与 LangGraph；通过 Context7 和官方源码核验。
- `prompt_context_audit`：本仓提示词、会话、上下文预算和 provider 差异；只读源码审查。
- `tool_runtime_audit`：本仓工具目录、Harness、审批、幂等、MCP、审计和 Trace；只读源码审查。

主代理对高风险结论重新读取源码，并对动态系统提示词与 ToolResult 生产路径执行无外部副作用的离线复现。子代理提出但尚未复现、或存在合理替代解释的结论只作为设计风险，不写成确定故障。

## 已确认的现代实践

1. XML/Markdown 是模型可读分隔方式，不是 OpenAI/Anthropic/MCP 工具调用 wire protocol，也不提供权限或安全边界。
2. OpenAI 原生工具调用使用 JSON Schema `function` ToolSpec，模型返回 `function_call`，应用以相同 `call_id` 回传 `function_call_output`。
3. Anthropic 原生工具调用使用 `tool_use` / `tool_result` content blocks，并以 `tool_use_id` 关联；MCP 使用 JSON-RPC `tools/list` / `tools/call` 与 `inputSchema` / `outputSchema`。
4. Codex CLI 公开提示模板主要采用 Markdown；权限审批、sandbox、AGENTS.md 注入和工具执行由 Harness 完成，而不是 XML 完成。
5. 系统/开发者指令、工具说明、参数 schema、运行时授权和不可信数据应分层；软提示不能替代硬策略。
6. 工具错误必须区分协议错误、模型可修正参数错误、业务执行失败、可重试瞬态失败和副作用状态未知。
7. 上下文应保持高信号、按需暴露工具/资料并支持压缩；评测应覆盖工具正负触发、参数、错误、审批、注入、成本和延迟。
8. LangGraph 更适合显式长流程、分支和持久恢复；本项目当前 PydanticAI tool loop + Harness 没有仅为“主流形式”而迁移的充分收益。

## 已确认缺陷

### P0：结构化工具失败在生产路径被提前字符串化

- `services/agent/tools.py:91-105` 把所有注册工具返回值转换成字符串，`services/agent/tools.py:1262-1266` 在生产注册路径启用。
- `services/agent/tool_results.py:93-211` 的 `status/error_kind/retryable/external_state_unknown` 因此在 Harness 收尾前丢失。
- `services/agent/harness/execution.py:1479-1578` 对非 `ToolResult` 默认按成功处理、写入成功幂等缓存并记录成功 Trace/审计。
- 主代理用真实 `register_agent_tools + HarnessCapability + TestModel` 离线复现：底层返回命令失败时，模型收到失败文本，但审计顶层 `status=success`，内部字符串启发式又判定 failure，记录自相矛盾。

### P0：stdio MCP 子进程获得完整父进程环境

- `services/agent/mcp.py:253-270` 和兼容入口 `services/agent/mcp.py:523-540` 都执行 `dict(os.environ)` 并传给 `MCPServerStdio`。
- PydanticAI 1.94 官方源码明确：`MCPServerStdio.env` 默认不继承父环境，只有显式传 `env=os.environ` 才继承；本项目主动绕过了安全默认值。
- 结果是任意被配置并启动的 stdio MCP 包都可读取 provider API key、JWT/密码等进程环境，属于凭据泄露面。

### P1：动态系统提示词在有历史的后续轮次不会重算

- `services/agent/core.py:350-353` 使用裸 `@agent.system_prompt`，没有 `dynamic=True`。
- PydanticAI 1.94 的默认值是 `dynamic=False`；有 `message_history` 中已有 SystemPromptPart 时不重新执行函数。
- 主代理离线双轮实验得到 `decorator_calls=[1]`，第二轮继续使用 `prompt-v1`。因此旧会话中的模板、变量、provider/model、服务器时间和上下文信息可能陈旧。

### P1：部分模板静默丢弃全局 system prompt

- `services/agent/prompt.py:57-85` 中只有 `default` 包含 `{system_prompt}`，`concise` / `detailed` 均不包含。
- `config/settings.py:785` 的管理员 system prompt 在切换这两个模板后不再进入模型上下文，且 detailed 的 Markdown 指令与默认风格要求冲突。

### P1：工具与系统提示预算显著低估

- 本地测量：21 个内置工具；运行时 Harness 提示 4,816 字；工具描述 8,999 字；参数 schema 6,513 字；不含协议开销合计约 20,328 字。
- `services/agent/context.py:38-43,346-354` 默认只预留系统提示 800 tokens 和 8 个工具的 1,600 tokens，MCP 工具未纳入动态计算。
- 该预算只裁剪历史，不能反映实际最终请求，长会话和小上下文模型存在超窗风险。

## 高优先级设计风险

- 命令工具目录标为 HIGH，但未命中少量 hard-deny/approval root 时策略自动允许；未知副作用命令没有默认审批。
- 自定义变量由玩家命令写入，未经长度限制或不可信容器处理便进入 system prompt，并记录原始值；这是软策略覆盖和日志泄露风险，不等同于突破 Harness 硬权限。
- Harness 系统提示列出完整目录，而 `prepare_tools` 会按能力动态隐藏工具；模型可见指南与实际工具面不一致，且 MCP allowlist 工具不进入动态目录卡片。
- 日志路径记录完整工具参数/结果，和项目“有界预览/脱敏”的规范不一致。
- 审批批次完成后先从 pending 移除，再提交 Broker；队列满时可能失去可恢复状态。
- 副作用状态未知后的再次恢复、审批恢复异常的审计/Trace 收尾、非 loopback Trace API 无认证需要专项复现或威胁建模。
- MCP allowlist 仅按工具名，不绑定 server 与 schema hash；`MCPServerInfo.tools` 没有填充，工具数量和 schema 漂移不可观察。
- 当前测试擅长 deterministic 控制流、会话隔离、审批和 tool-pair 清洗，但缺少真实生产注册组合测试与工具选择/拒绝/参数/注入的离线 eval 数据集。

## 应保留的设计

- 运行时 Harness 统一包裹工具集，硬策略、审批、幂等、审计和 Trace 不依赖模型自觉。
- 工具使用 Pydantic 生成的 JSON Schema 和原生 provider tool calling，不靠正则解析模型文本。
- 审批恢复保留 call ID、冻结参数、owner/run/conversation 校验。
- 玩家身份和历史主路径按 `(connection_id, player_name, conversation_id)` 显式分桶。
- 历史裁剪保持 tool-call/tool-return 成对，摘要与长工具结果有不可信标记，缺失窗口元数据时 fail-closed。
- 长文本下行统一经过 `BrokerResponseBridge` / SDK delivery。
- 观测默认不采集正文，审计 writer 异步且 fail-soft；这些原则应保留并修正记录准确性。

## 报告中的 XML 结论

不建议把当前提示词机械地全量改写成 XML。建议先修语义丢失、安全和动态刷新，再用跨 DeepSeek/OpenAI/Anthropic/Ollama 的评测集比较：

- 现有 Markdown/纯文本分层；
- 仅对不可信资料使用 XML 容器；
- 对稳定段落使用 XML 的 provider-specific 渲染。

评测指标至少包含工具选择准确率、拒绝准确率、参数 schema 通过率、间接注入成功率、平均输入 tokens、延迟和成本。

## 主要一手来源入口

- OpenAI：[Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)、[Function calling](https://developers.openai.com/api/docs/guides/function-calling)、[Programmatic Tool Calling](https://developers.openai.com/api/docs/guides/tools-programmatic-tool-calling)、[Codex approvals and security](https://developers.openai.com/codex/agent-approvals-security)、[Codex repository](https://github.com/openai/codex)。
- Anthropic：[Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)、[Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)、[Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)、[Mitigate prompt injections](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks)。
- MCP：[2026-07-28 overview](https://modelcontextprotocol.io/specification/2026-07-28)、[Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)、[Authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)、[Security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)。
- PydanticAI：[v1.94 Agent source](https://github.com/pydantic/pydantic-ai/blob/v1.94.0/pydantic_ai_slim/pydantic_ai/agent/__init__.py)、[v1.94 MCP source](https://github.com/pydantic/pydantic-ai/blob/v1.94.0/pydantic_ai_slim/pydantic_ai/mcp.py#L989-L1024)、[Deferred tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools)、[Instrumentation](https://pydantic.dev/docs/ai/capabilities/instrumentation)。
- LangGraph：[Workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)、[Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)。
