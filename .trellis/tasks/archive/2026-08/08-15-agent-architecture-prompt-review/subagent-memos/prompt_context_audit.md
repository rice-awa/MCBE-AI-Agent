# `/root/prompt_context_audit` 原始回传备忘录

来源说明：以下正文逐字保留该只读子代理的 `FINAL_ANSWER` 原始回传；仅新增本标题与本说明，未对原始内容作修正或删减。

### 审查结论

主链路身份隔离总体正确：`gateway -> ChatRequest(player/provider/cid) -> Worker -> AgentDependencies -> PydanticAI Agent`，历史按 `(connection, player, conversation)` 分桶；工具回调和下行消息也显式携带玩家。[services/gateway/command_handlers.py:118](services/gateway/command_handlers.py:118)、[services/agent/worker.py:559](services/agent/worker.py:559)、[core/queue.py:451](core/queue.py:451)

提示词实际顺序是：选择 `(connection, player)` 模板 → 选择 runtime Harness 工具指南或 legacy guide → 注入 `player/provider/model/context/system_prompt` → 顺序字符串替换自定义变量 → 追加未使用变量 → 追加信任约束。[services/agent/prompt.py:335](services/agent/prompt.py:335)、[services/agent/prompt.py:346](services/agent/prompt.py:346)、[services/agent/prompt.py:375](services/agent/prompt.py:375)、[services/agent/prompt.py:393](services/agent/prompt.py:393)
Worker 先加载/清洗/压缩历史，再调用 `Agent.iter/run(prompt, deps, model, message_history, usage_limits)`；每次模型请求由 `ContextBuilder` 裁剪历史，Harness 动态暴露工具和 schema。[services/agent/core.py:322](services/agent/core.py:322)、[services/agent/core.py:1044](services/agent/core.py:1044)、[services/agent/context.py:518](services/agent/context.py:518)

### P1：确认缺陷

- `concise`、`detailed` 内置模板没有 `{system_prompt}`，任意自定义模板也可省略它；切换模板会静默丢失 `Settings.system_prompt`，且 detailed 的 Markdown 指令与默认“不要 Markdown”不一致。[services/agent/prompt.py:64](services/agent/prompt.py:64)、[config/settings.py:785](config/settings.py:785)
  测试缺口：用 sentinel system prompt 遍历所有模板。

- 上下文预算明显低估。当前 runtime Harness 文本约 4,816 字/2,408 estimated tokens，而 `ContextBuilder` 默认只预留 system 800、工具 schema 1,600；同时目录有 21 个内置工具，MCP 工具数量未计入。[services/agent/context.py:38](services/agent/context.py:38)、[services/agent/context.py:346](services/agent/context.py:346)、[services/agent/harness/prompting.py:55](services/agent/harness/prompting.py:55)
  模型元数据缺失时统一 fallback 128k；真实小窗口模型可能超窗。[config/settings.py:973](config/settings.py:973)、[services/agent/context.py:384](services/agent/context.py:384)

- 信任边界不完整：只有超长工具结果才包装为不可信资料，短 `tool-return` 保持原文；摘要检测只要正文任意位置包含 marker 就原样放行，可绕过 `source/trust` 封装。[services/agent/context.py:251](services/agent/context.py:251)、[services/agent/context.py:541](services/agent/context.py:541)、[core/conversation.py:240](core/conversation.py:240)
  测试缺口：短恶意工具结果、正文中伪造 `factual_hints_only...` 的摘要。

### P2：风险

- 插值完全是 raw `str.replace`；玩家名、自定义变量可含换行、占位符或系统指令，且自定义变量无长度上限并可能追加到 system prompt。[services/agent/prompt.py:354](services/agent/prompt.py:354)、[services/agent/prompt.py:385](services/agent/prompt.py:385)
  连接级兼容 API 还会回退到匿名桶，可能让同连接其他玩家继承模板/变量；当前 Gateway 主路径使用 per-player，故属兼容路径风险。[services/agent/prompt.py:145](services/agent/prompt.py:145)、[services/agent/prompt.py:294](services/agent/prompt.py:294)

- Harness 卡片列出全部内置工具，但实际工具由 capability 动态过滤；MCP 工具通过 allowlist 暴露，却不进入工具目录/提示卡片，模型看不到 MCP 的用途和审批规则。[services/agent/harness/catalog.py:353](services/agent/harness/catalog.py:353)、[services/agent/harness/execution.py:500](services/agent/harness/execution.py:500)、[services/agent/harness/execution.py:437](services/agent/harness/execution.py:437)
  这是当前 prompt/schema 重复和动态工具信息缺口。

- `request_timeout` 只包流式读取；非流式仅受 `run_timeout` 约束；HTTP、MCP、主 run、压缩、标题生成各有独立 timeout，压缩/标题调用不计入主 UsageLimits。[services/agent/core.py:176](services/agent/core.py:176)、[services/agent/core.py:1391](services/agent/core.py:1391)、[core/conversation.py:200](core/conversation.py:200)、[services/agent/title.py:50](services/agent/title.py:50)

- Provider 差异：DeepSeek/OpenAI/Ollama 均关闭 `count_tokens_before_request`，仅 Anthropic 开启；DeepSeek 自定义 endpoint 实际走 OpenAI adapter。[services/agent/core.py:88](services/agent/core.py:88)、[services/agent/providers.py:79](services/agent/providers.py:79)
  压缩器仍硬编码 `openai:gpt-4o-mini`，虽传入运行时 model，存在模型无关假设。[core/conversation.py:197](core/conversation.py:197)

### P3：缺口与保留项

- 流结束无 `is_complete` 但已有文本时标记 success，却没有 `all/new_messages`，历史可能不落盘。[services/agent/worker.py:1397](services/agent/worker.py:1397)
- `runtime_harness_enabled=False` 只关闭提示/schema，Harness policy 仍挂载；需明确开关语义。[services/agent/core.py:334](services/agent/core.py:334)
- 优点：工具目录单一来源、历史 tool pair 保持、摘要重复包装测试、玩家/会话显式传播均较完整。[.trellis/spec/backend/directory-structure.md:45](.trellis/spec/backend/directory-structure.md:45)

### XML 评估

当前 XML 收益低：已有 `[历史摘要]`、`[不可信历史资料]` 和独立 tool message；XML 不会改变 provider 的权限执行，也不能修复模板丢失、动态 MCP、预算问题。Raw 插值未转义时 XML 反而可被闭合标签伪造。若实验，只应包装不可信区段并做跨 DeepSeek/OpenAI/Anthropic/Ollama 注入基准。

本次仅静态审查，未运行 pytest；最大测试缺口是“真实 Agent + 动态 prompt + history processor + MCP schema”的端到端组合测试。
