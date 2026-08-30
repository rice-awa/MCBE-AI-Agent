## 研究备忘录（角度 A：Codex CLI / Responses API / Agents SDK）

研究日期：2026-08-15。方法：只读核验 OpenAI 官方 API 文档、Codex 官方文档及 `openai/codex`、`openai/openai-agents-python` 官方公开源码；源码引用固定到已核验 commit。未推断闭源内部实现。以下保留原始回传的全部结论、官方链接、不确定项和项目检查清单。

## 研究要点（只读，官方资料）

1. **层级**：Responses 支持 `system/developer/user/assistant`；system/developer 优先于 user。`instructions` 属于高优先级上下文；使用 `previous_response_id` 时不会自动继承旧 instructions。
[Responses API，高](https://platform.openai.com/docs/api-reference/responses-streaming/response/content_part)

2. **XML**：官方仅把 Markdown/XML 视为提示词分隔与可读性写作约定，不是权限或安全边界。
[Prompt engineering，高](https://developers.openai.com/api/docs/guides/prompt-engineering)

3. **ToolSpec**：原生 function tool 使用 JSON Schema；`strict:true` 可约束参数，通常要求 `additionalProperties:false` 和全部字段 required（可用 `null` 表示可选）。
[Function calling，高](https://developers.openai.com/api/docs/guides/function-calling)

4. **调用循环**：模型返回 `function_call(name, arguments, call_id)`；应用执行后必须回传 `function_call_output`，带同一 `call_id`，再请求模型。循环可持续多轮。
[Function calling，高](https://developers.openai.com/api/docs/guides/function-calling)

5. **并行**：一次响应可含多个 function call，每个有独立 `call_id`；`parallel_tool_calls:false` 保证最多一个函数调用。副作用工具仍需应用层排序/锁定。
[Function calling，高](https://developers.openai.com/api/docs/guides/function-calling)

6. **PTC**：Programmatic Tool Calling 让模型在隔离 V8 中写 JS，循环、条件和并行调用工具；嵌套调用有父子 caller/call ID。应用仍执行客户端工具。
[PTC，高](https://developers.openai.com/api/docs/guides/tools-programmatic-tool-calling)

7. **Agents SDK**：Agent 封装 instructions、tools、guardrails、handoffs、输出约束；Runner 循环为“模型→执行工具→继续/交接→最终输出”。运行时 context 不会自动暴露给模型。
[定义 Agent，高](https://developers.openai.com/api/docs/guides/agents/define-agents) · [运行 Agent，高](https://developers.openai.com/api/docs/guides/agents/running-agents)

8. **SDK ToolSpec/审批**：Python `FunctionTool` 默认倾向严格 JSON Schema，支持 `needs_approval`、`allowed_callers`、超时及模型可见错误。
[官方源码，高](https://github.com/openai/openai-agents-python/blob/9aba9002935b56d7253d28d99b2a8bca2d2450a7/src/agents/tool.py#L441-L520)

9. **Codex CLI prompt harness**：Codex 基础提示明确其接收 harness 上下文、输出 terminal/apply-patch 函数调用，调用可能升级审批；提示模板采用 Markdown 标题，不依赖 XML。
[官方源码，高](https://github.com/openai/codex/blob/12933b69551394328319dcdd1bcee7907326dc85/codex-rs/core/gpt_5_2_prompt.md#L1-L32)

10. **AGENTS.md**：Codex 从全局、项目根到当前目录发现指令，嵌套目录可覆盖，存在大小上限；这是 harness 注入上下文的机制，不能等同 API system message。
[官方源码，高](https://github.com/openai/codex/blob/12933b69551394328319dcdd1bcee7907326dc85/codex-rs/core/src/agents_md.rs#L50-L90)

11. **Codex loop**：Codex 请求携带工具规格和 `parallel_tool_calls`；收到调用后执行并将结果继续采样，工具路由保留 `call_id`。
[官方源码，高](https://github.com/openai/codex/blob/12933b69551394328319dcdd1bcee7907326dc85/codex-rs/core/src/session/turn.rs#L139-L151)

12. **安全、上下文、评估**：Codex 将审批与 sandbox 分开；技能说明属于 user-level 输入且需视为不可信；Responses compaction 返回规范化上下文窗口；Agent Evals 用 trace 覆盖模型、工具、guardrail、handoff。
[审批安全，高](https://developers.openai.com/codex/agent-approvals-security) · [Skills，高](https://developers.openai.com/api/docs/guides/tools-skills) · [Compaction，高](https://developers.openai.com/api/docs/guides/compaction) · [Evals，高](https://developers.openai.com/api/docs/guides/agent-evals)

### 不确定项

- XML、AGENTS、skills 的实际角色取决于 harness 版本；Codex 文档与当前源码在 AGENTS 角色注入上可能存在版本差异。
- PTC 不是普通客户端 function calling；可用模型、托管环境和工具白名单可能不同。
- 重试、并行副作用排序属于 SDK/CLI 实现策略，不是所有 API 调用的统一保证。

### MCBE Chat Agent 检查清单

明确 system/developer/user 边界；ToolSpec 严格校验；全链路保留 `call_id`；副作用调用串行且幂等；审批先于高影响操作；sandbox/network 最小权限；压缩后保留玩家、会话、`player_name/sender`；skills/外部文本按不可信输入处理；重试区分可重放与不可重放；用 trace/eval 覆盖工具、错误、路由和交付路径。
