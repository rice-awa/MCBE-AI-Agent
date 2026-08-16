# 调研备忘录 B：Anthropic、Claude Code、MCP 与现代提示词结构

调研时间：2026-08-15。已用 Firecrawl 完成 12 个方向检索、抓取 50 个以上一手页面；未修改仓库文件。来源优先级：A=Anthropic 官方 API/Platform 文档，B=Claude Code 官方文档，C=MCP 官方规范，D=Anthropic Engineering 一手工程文章。

版本说明：当前日期下 MCP 最新规范为 2026-07-28。本文以 2026-07-28 的 MCP tools/resources/prompts、versioning、authorization 和 security best practices 为主；2025-11-25 lifecycle 只作为 legacy 兼容性对照，不把旧版握手流程误写成最新规范。

## 核实主张

1. **XML 标签是模型提示词建议，不是工具协议。** Anthropic 建议在混合 instructions/context/examples/input 时使用一致、可嵌套的 XML 标签；其作用是帮助模型区分语义段落。证据：“XML tags help Claude parse complex prompts unambiguously”。来源：A，官方模型指导：[Prompting best practices §Structure prompts with XML tags](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)。

2. **Anthropic API 不把 XML 当作 wire protocol。** 工具定义页明确描述，API 会把工具定义编入特殊 system prompt，但旧式工具调用文本“not expected to be valid XML and is parsed with regular expressions”。真正的 API 协议是 JSON content blocks；MCP 则是 JSON-RPC。来源：A，官方 API 设计说明：[Define tools §Tool use system prompt](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)；C：[MCP specification overview](https://modelcontextprotocol.io/specification/2026-07-28)。

3. **XML 不是安全边界。** 第三方网页、文档、邮件和工具结果仍应视为不可信数据；不能因为包进 `<document>` 或 `<context>` 就当成可信指令。Anthropic 推荐把不可信内容放进 `tool_result`，并在 system prompt 中明确其不具备指令优先级。来源：A：[Mitigate jailbreaks and prompt injections §Indirect prompt injection](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks)。

4. **XML 可能无益甚至制造噪声。** Anthropic 的 context-engineering 文章同时推荐 XML/Markdown 分段，又指出“exact formatting … less important as models become more capable”。这说明 XML 是可测试的模型提示策略，不是必须遵循的格式；它会增加 token，并可能造成过度结构化。此处“增加噪声”是基于上下文有限性的工程推论，应通过 XML/no-XML 消融评测确认。来源：D：[Effective context engineering for AI agents §The anatomy of effective context](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)。

5. **system prompt 与 tool description 是不同职责，但都面向模型。** system prompt 适合角色、行为、策略和任务级约束；工具描述适合工具做什么、何时使用、参数含义、返回值、限制和副作用。工具定义页明确列出 `name`、`description`、`input_schema`，并建议详细说明“what/when/parameters/caveats”。来源：A：[Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)。

6. **工具描述会影响选工具，但不授予权限。** Anthropic 说 Claude 根据用户请求和工具描述决定是否调用工具；但权限应由 agent harness 执行。Claude Code 文档明确：“Permission rules are enforced by Claude Code, not by the model.” 不能靠 system prompt 或 `CLAUDE.md` 授予越权能力。来源：A：[Tool use overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)；B：[Claude Code permissions](https://code.claude.com/docs/en/permissions)。

7. **Anthropic 原生工具调用是 `tool_use`/`tool_result` content-block 协议。** `tool_use` 至少含 `id`、`name`、`input`；`input` 应符合工具的 `input_schema`。客户端执行后，必须在后续 `user` 消息中返回对应 `tool_result`，通过 `tool_use_id` 关联，并可用 `is_error` 表示执行失败。来源：A：[Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)；A：[Messages API ToolUseBlock/ToolResultBlockParam](https://platform.claude.com/docs/en/api/messages)。

8. **工具输入 schema 是 JSON Schema，不是 XML Schema。** Anthropic 工具定义使用 `input_schema`；MCP 使用 camelCase 的 `inputSchema`，并可选 `outputSchema`。适配器不能只做字段名替换，还要区分 Anthropic content-block 协议和 MCP JSON-RPC `tools/call`。来源：A：[Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)；C：[MCP tools §Data types](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)。

9. **`strict: true` 只保证结构/类型约束，不保证业务正确性或授权。** Anthropic 说明 strict tool use 使用 grammar-constrained sampling，使工具名和输入符合支持的 JSON Schema 子集；不支持的 schema 关键字仍会失败或需去掉 strict。schema 中不应放 PHI、密钥等敏感信息。来源：A：[Strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)。

10. **并行工具调用的执行顺序由 harness 决定。** API 可以在一个 assistant turn 中返回多个 `tool_use`，但不规定客户端必须并发还是串行。独立、只读调用适合并发；共享状态、有副作用或有顺序依赖的调用应串行。所有结果必须在同一个后续 `user` 消息中返回，并且 tool results 位于文本之前。来源：A：[Parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)。

11. **错误反馈有三层，不能混淆。**

   - Anthropic API 请求/消息错误；
   - Anthropic `tool_result.is_error`，表示工具执行失败并把失败反馈给模型；
   - MCP JSON-RPC error 或 MCP `tools/call` 返回的 `isError`，分别表示协议级失败和工具业务执行失败。

   项目适配层应明确映射这三类错误、重试策略、幂等性和用户可见信息。来源：A：[Handle tool calls §Handling errors](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)；C：[MCP tools §Calling tools / Tool result](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)。

12. **`tool_choice` 是 wire-level 的调用控制，system prompt 只是软指导。** `auto`、`any`、指定工具、`none` 和 `disable_parallel_tool_use` 可直接约束模型；提示词只能调整触发倾向，不能替代 harness 权限或硬约束。来源：A：[Tool use overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)；A：[Messages API ToolChoice](https://platform.claude.com/docs/en/api/messages)。

13. **MCP 能力协商因规范版本不同而不同，必须 pin 版本。** 2025-11-25 及以前是 `initialize`/`initialized` 握手和 capability negotiation；当前 2026-07-28 规范改为每个请求携带 `_meta` 中的 protocol version、client info、client capabilities，并支持 `server/discover`。不要把两套流程混写。来源：C：[2025 lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)；C：[2026 versioning](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning)。

14. **MCP 的三类 server primitive 有不同的控制主体。**

   - `tools`：model-controlled，模型可根据上下文发现和调用；
   - `resources`：application-driven，由 host 决定如何呈现、选择和注入上下文；
   - `prompts`：user-controlled，通常由用户显式选择或通过 slash command 使用。

   因而 MCP prompt 不是 system prompt，resource 也不是自动工具调用。来源：C：[Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)、[Resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources)、[Prompts](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts)。

15. **MCP 的能力发现不只是一次性列工具。** server 必须声明 `tools`、`resources`、`prompts` 等 capability；客户端通过 `tools/list`、`resources/list`、`prompts/list` 发现内容，可分页、缓存，并通过 list-changed 通知更新。工具列表应稳定排序；聚合多个 server 时必须防止工具名冲突。来源：C：[MCP tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)；C：[MCP resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources)。

16. **MCP 协议本身不能代替 host 的审批和权限系统。** MCP 最新概览明确写出协议无法在层面强制安全原则；host 应让用户理解数据访问和工具操作，并在调用前取得同意。工具 annotations 也应视为不可信，除非来自受信任 server。来源：C：[MCP overview §Security and Trust & Safety](https://modelcontextprotocol.io/specification/2026-07-28)；C：[MCP tools §User interaction model](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)。

17. **Claude Code 的 allow/ask/deny、workspace trust、sandbox 是 harness 层能力。** Claude Code 默认对修改文件、Bash、MCP 等动作请求权限；deny/ask/allow 按 harness 规则执行，模型无法通过提示词绕过。MCP server 还需要信任确认；`requiresUserInteraction` 可要求某工具每次都人工批准。来源：B：[Permissions](https://code.claude.com/docs/en/permissions)；B：[Security](https://code.claude.com/docs/en/security)；B：[MCP §Require approval for a specific tool](https://code.claude.com/docs/en/mcp#require-approval-for-a-specific-tool)。

18. **Anthropic API 的 MCP connector 不是完整 MCP host。** 当前 connector 文档明确只支持 MCP tool calls，要求远程 HTTP/SSE，不能直接连接本地 stdio；MCP prompts/resources 需要自行运行 MCP client 或使用 SDK helper。来源：A：[MCP connector §Limitations](https://docs.anthropic.com/en/docs/agents-and-tools/mcp-connector)。

19. **MCP HTTP 授权是 OAuth/least-privilege 问题，不是 prompt 问题。** 2026 规范要求 HTTP authorization server 使用 OAuth 2.1/Protected Resource Metadata，并建议按当前操作申请最小 scope；stdio 通常从环境读取凭据。MCP security guide 还专门描述 confused-deputy 和 per-client consent。来源：C：[MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)；C：[MCP security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)。

20. **间接 prompt injection 的推荐防线是数据/指令分离和最小权限。** Anthropic 建议第三方内容只进入 `tool_result`，在工具描述和 system prompt 中标明来源，必要时 JSON encode、筛查 tool output、限制敏感工具、沙箱化并做 red-team。XML 标签本身不能替代这些措施。来源：A：[Mitigate jailbreaks and prompt injections](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks)；B：[Claude Code Security](https://code.claude.com/docs/en/security)。

21. **现代 agent 更应做 context engineering，而不是无限堆 system prompt。** Anthropic 将 system instructions、tools、MCP、外部数据、消息历史都视为有限上下文；推荐最小高信号上下文、just-in-time retrieval、progressive disclosure、compaction、structured notes 和清理旧 tool results。来源：D：[Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)；D：[Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)。

22. **评测对象是“模型 + agent harness”，不只是模型。** Anthropic 将 agent harness 定义为处理输入、编排工具、返回结果的 scaffold；评测应同时记录 transcript/trace、最终 outcome、工具调用、错误、token、延迟和成本。应结合 deterministic、model-based、human graders，并避免只检查唯一工具调用路径。来源：D：[Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)。

23. **工具触发评测必须同时覆盖“应该调用”和“不应该调用”。** 只测“该搜索时搜索”会导致过度触发；应包含已有上下文即可回答、工具应拒绝、权限不足、工具失败和副作用操作等负例。来源：D：[Demystifying evals §Balanced problem sets](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)；D：[Writing effective tools](https://www.anthropic.com/engineering/writing-tools-for-agents)。

24. **MCP sampling 在当前 2026-07-28 规范中已 deprecated。** 新实现不应把 server 发起的 nested model generation 当作未来默认能力；规范建议新系统直接集成 provider API。已有实现仍需 capability 声明和人工审批。来源：C：[MCP sampling](https://modelcontextprotocol.io/specification/2026-07-28/client/sampling)。

## 反方观点与不确定项

- Anthropic 推荐 XML，但没有在这些官方页面中给出跨模型、跨任务的统一因果增益；应把 XML 作为 prompt ablation 的变量。
- 标签命名没有协议语义；`<instructions>`、`<context>` 等只是模型可见文本。不能把标签解析结果当作权限、身份或审批依据。
- `strict: true` 只解决 schema conformance，不解决越权、错误业务参数、资源归属、幂等性或 prompt injection。
- 并行调用没有 API 级执行顺序；若项目默认 `gather()`，必须显式识别写操作、共享状态、锁和取消传播。
- MCP 2025 与 2026 规范差异较大；项目若依赖旧 SDK，应锁定版本并覆盖 legacy/modern 互操作，不要直接照抄最新页面。
- MCP “human in the loop”多为规范 SHOULD 或 host 原则；真正的审批、超时、审计和 fail-closed 必须由项目 harness 实现。
- Anthropic MCP connector 的 MCP 能力范围比 Claude Code 本地 host 小，不能据此推断整个 MCP 生态都支持 resources/prompts。
- LLM-as-judge 有偏差和非确定性；必须用 deterministic outcome checks、held-out 集合和人工校准。

## 项目审查检查清单

- 明确分层：system prompt=行为/策略；tool description=用途/触发条件/参数/副作用；schema=结构；harness=执行、授权、重试和审计。
- XML 只用于模型可读分段；不要把 XML 当 MCP、API wire protocol 或安全边界。
- 保留完整 assistant `tool_use`，按 ID 返回每个 `tool_result`；结果必须紧跟且位于后续消息文本之前。
- 区分 Anthropic `is_error`、MCP `isError`、JSON-RPC error；定义错误重试、幂等和用户可见信息。
- 对 `input_schema`/`inputSchema` 做 JSON Schema 校验；strict 只在支持子集内启用；schema 不含密钥、PHI 或业务秘密。
- 并发前检查依赖、共享状态和副作用；独立读取可并行，写操作默认串行；设置超时、取消和并发上限。
- Pin MCP 规范/SDK 版本；测试 capability negotiation、`tools/list`/`resources/list`/`prompts/list`、分页、缓存和 list-changed。
- 把 MCP tools/resources/prompts 分开建模；不要自动把 resources 当工具、把 prompts 当 system policy。
- 所有外部内容标记不可信；工具 server allowlist、OAuth 最小 scope、workspace trust、审批、sandbox 和 prompt-injection red-team 必须在 harness。
- 上下文管理采用最小高信号结果、分页/延迟加载、progressive disclosure、compaction 和记忆摘要；避免把完整数据库/日志一次性塞进模型。
- 评测覆盖正负触发、工具错误、权限拒绝、并行竞态和注入样本；记录 outcome、trace、tool calls、token、延迟、成本和错误率。
- 本项目现有会话/聊天/下行路径仍应显式保留 `player_name` / `sender`，长文本下行继续经过 `BrokerResponseBridge` 或 SDK delivery。
