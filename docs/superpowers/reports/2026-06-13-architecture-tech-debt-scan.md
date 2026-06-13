# 架构技术债扫描报告

日期：2026-06-13

目标：对当前 MCBE Chat Agent 与运行时 Harness 代码做只读架构技术债扫描，产出可执行的技术债候选清单和优先级建议。

范围：本报告只做技术债扫描和排序，不实施重构。

## 扫描基线

- 分支：`tech-debt-scan-plan`
- Git 状态：除 `docs/superpowers/plans/2026-06-13-architecture-tech-debt-scan.md` 未追踪外，无其他意外改动。
- 近期提交风险区域：近期提交集中在运行时 Harness、配置、conversation 和 flow control 相关区域。
- 忽略路径：`.worktrees/`、`node_modules/`、`__pycache__/`、`.pytest_cache/`、`config.json`、`.env`。
- 静态计划验证：文件存在、placeholder scan passed、skill mention scan passed、architecture term scan passed。
- 快速测试基线：未能运行；`pytest` 命令不存在，`python3 -m pytest` 报 `No module named pytest`。

## 候选 1：运行时 conversation session Module 加深

**Recommendation strength：** Strong

**Files：**
- `core/queue.py:96`
- `services/websocket/connection.py:22`
- `services/websocket/server.py:326`
- `services/agent/worker.py:320`
- `services/agent/prompt.py:82`

**Problem：**
MCBE Chat Agent 的玩家对话状态分散在 `MessageBroker`、`PlayerSession`、`PromptManager`、`AgentWorker` 和 `WebSocketServer`。`MessageBroker` 同时是队列 Module 和 conversation state Implementation，`PlayerSession` 持有 active conversation，`WebSocketServer` 直接处理 conversation command 语义，Locality 偏弱。

运行时 Harness 对多人会话隔离的要求是所有玩家相关状态必须按 `(connection_id, player_name)` 分桶，但现在调用者需要同时理解 history、generation、metadata、short id、session lock、active conversation、template 和 variables 的多个存储位置。

**Deletion test：**
删除 `MessageBroker` 的 conversation 方法，复杂度不会消失，只会散到 `WebSocketServer`、`AgentWorker`、`ConversationManager` 和测试 mock。删除 `PlayerSession.active_conversation_id` 则必须重建 active conversation 来源，说明当前 Module 边界不完整。

**Solution：**
加深“运行时玩家对话状态” Module，先集中 current conversation、history、metadata、short id、title status 和 session lock 的 Interface，但暂不设计最终 API。

**Benefits：**
提升多人会话隔离的 Locality，让 `(connection_id, player_name)` 成为一个高 Leverage 边界。后续测试可围绕状态转移而不是跨 `ConnectionState`、`MessageBroker`、`WebSocketServer`、`PromptManager` 和 `AgentWorker` 断言。

**Evidence：**
`MessageBroker` 存储 histories、generations、metadata、short ids 和 session locks：`core/queue.py:96`。`PlayerSession` 存储 `active_conversation_id`、模板和变量：`services/websocket/connection.py:22`。`handle_command()` 直接分派 conversation/template/context/model 语义：`services/websocket/server.py:326`。Worker completion path 写历史、调 title、调压缩：`services/agent/worker.py:320`。`PromptManager` 另存模板变量：`services/agent/prompt.py:82`。

## 候选 2：MCBE outbound delivery Adapter 集中

**Recommendation strength：** Strong

**Files：**
- `services/websocket/server.py:1038`
- `services/websocket/connection.py:400`
- `services/websocket/connection.py:431`
- `services/websocket/connection.py:487`
- `services/websocket/connection.py:534`

**Problem：**
Flow Control Middleware 的 chunking 算法有 Depth，但发送分片、片间 delay、raw logging、source 命名、WebSocket send 的 Implementation 仍分散。调用点需要知道 tellraw、scriptevent、AI response、raw command 的字节与节流细节，Adapter Seam 泄漏。

这会削弱运行时 Harness 对 MCBE 出站路径的约束：新增下行路径时，维护者容易绕过 Flow Control Middleware 或重复实现 byte budget、delay 和日志逻辑。

**Deletion test：**
删除 `server.py:_send_ws_payload()` 或 `connection.py` 中多个 send helper，相关出口会断；但内部循环高度重复。删除 `chunk_raw_command()` 主路径几乎不受影响，说明 raw command safety 规则没有真正成为统一 Interface。

**Solution：**
加深一个 MCBE outbound delivery Adapter，让调用者提交语义对象，例如 tellraw、scriptevent、AI response sync、raw command，由 Adapter 统一执行 Flow Control Middleware、delay、logging 和超长 raw command 拒绝。

**Benefits：**
统一所有下行长文本路径，降低 MCBE 出站安全风险。新增下行路径时只需测试 Adapter，Locality 和回归保护更强。

**Evidence：**
`WebSocketServer._send_ws_payload()` 自行 chunk tellraw 和 sleep：`services/websocket/server.py:1038`。`ConnectionManager._send_game_message_with_color()` 重复 tellraw 分片发送：`services/websocket/connection.py:400`。`_run_command()` 直接 `MinecraftCommand.create_raw()` 并 send：`services/websocket/connection.py:431`。`_sync_response_to_addon()` 和 `_send_script_event()` 各自重复 chunk/send/delay：`services/websocket/connection.py:487`、`services/websocket/connection.py:534`。

## 候选 3：运行时 Harness tool Module 加深

**Recommendation strength：** Strong

**Files：**
- `services/agent/tools.py:40`
- `services/agent/tools.py:46`
- `services/agent/harness/audit.py:24`
- `services/agent/harness/audit.py:102`
- `services/agent/harness/catalog.py:39`

**Problem：**
运行时 Harness 的工具智能化主线已经有工具目录、工具意图、工具提示、提示约束、工具审计、反馈闭环和反馈建议，但 concrete tool Implementation 仍集中在 `tools.py` 内嵌注册函数中。工具审计还通过失败字符串前缀猜测结果，Interface 不是结构化 ToolResult，Seam 易漂移。

工具目录目前更像旁路元数据，而不是工具契约本身。工具名称、参数、失败输出、风险级别、工具提示和审计分类存在多处维护点。

**Deletion test：**
删除 `register_agent_tools()` 后复杂度会散到 Agent 初始化、工具注册、审计包装、schema description 增强和测试。删除 `_FAILURE_PREFIXES` 后失败语义不会消失，只会回到每个工具的自然语言返回值中。

**Solution：**
加深工具 Module：让 concrete tool Implementation 先返回结构化 ToolResult，再由 PydanticAI Adapter 转成模型可见字符串。工具目录逐步从旁路元数据变成工具契约来源。

**Benefits：**
工具审计和反馈闭环获得稳定 Seam。工具提示与工具目录更有 Leverage。测试可绕开 PydanticAI 私有 `_function_toolset`，只保留少量 Adapter 集成测试。

**Evidence：**
`register_agent_tools()` 注册所有工具：`services/agent/tools.py:40`。`run_minecraft_command()` 等具体工具内嵌：`services/agent/tools.py:46`。失败前缀表在审计 Module：`services/agent/harness/audit.py:24`。`summarize_result()` 用 `str(result)` 推断失败：`services/agent/harness/audit.py:102`。工具目录另有 `_TOOL_CATALOG`：`services/agent/harness/catalog.py:39`。

## 候选 4：WebSocketServer command Module 加深

**Recommendation strength：** Worth exploring

**Files：**
- `services/websocket/server.py:326`
- `services/websocket/command.py:55`
- `models/messages.py:76`

**Problem：**
CommandRegistry 的 Interface 主要是 string prefix 解析，真正命令语义仍在 `WebSocketServer.handle_command()`。transport、鉴权、聊天、上下文、conversation、template、setting、MCP、model switch 和 help 分支集中在一个 Module，Seam 偏浅。

命令 Interface 是 stringly 的，`CommandRequest` 模型和实际命令集合也存在漂移风险。新增聊天、UI、上下文、模板、变量或切换模型路径时必须传递 `player_name`，但现在这个约束靠每个分支手动维护。

**Deletion test：**
删除 `CommandRegistry` 后可以较容易在 protocol handler 中重建 prefix 解析；删除 `handle_command()` 则命令语义整体消失。说明 command semantic 的 Locality 放在 transport Module 内。

**Solution：**
加深命令 Module，先引入 typed parsed command / dispatcher 边界，但不急于确定最终 Interface。

**Benefits：**
命令测试可不构造完整 WebSocketServer。新增命令更容易强制传递 `player_name`，降低多人会话串状态风险。

**Evidence：**
`handle_command()` 使用大段 `if/elif` 分派命令：`services/websocket/server.py:326`。CommandRegistry `resolve()` 返回字符串类型和内容：`services/websocket/command.py:55`。`CommandRequest` 类型覆盖与实际命令集合存在漂移风险：`models/messages.py:76`。

## 候选 5：运行时 Adapter lifecycle Module 显式化

**Recommendation strength：** Worth exploring

**Files：**
- `services/agent/providers.py:20`
- `services/agent/providers.py:23`
- `services/agent/providers.py:28`
- `services/agent/providers.py:196`
- `services/agent/core.py:48`

**Problem：**
`ProviderRegistry` 同时是 provider factory、model cache、HTTP client owner 和 process-global Adapter。`ChatAgentManager` 也持有全局 agent、MCP 可用状态与 fallback policy。测试 Seam 主要依赖 monkeypatch 全局类方法或 singleton。

这让 provider 生命周期、HTTP client ownership、MCP fallback policy 和测试替换 seam 混在一起。ProviderRegistry 是事实上的 Adapter Interface，但以 class-level global Implementation 表达。

**Deletion test：**
删除 `_model_cache` 生产仍可运行但会重建模型；删除 `_http_client_cache` 会削弱 shutdown 价值；删除 `ProviderRegistry` 整体则多处无替代 Interface。说明它是事实上的全局 Adapter，但 lifecycle ownership 没显式化。

**Solution：**
显式化 Provider/Agent runtime lifecycle Module，把 model factory、HTTP client owner、MCP fallback policy 和 test fake seam 分层。

**Benefits：**
减少全局状态污染，提升 provider 替换、shutdown、raw logging 与测试替身的 Locality。

**Evidence：**
`ProviderRegistry` 是类级全局：`services/agent/providers.py:20`。模型和 HTTP client cache 是 class vars：`services/agent/providers.py:23`。`get_model()` 负责创建和缓存：`services/agent/providers.py:28`。`shutdown()` 关闭 cache：`services/agent/providers.py:196`。`ChatAgentManager` 持有 agent、MCP toolsets、settings 和 MCP 可用状态：`services/agent/core.py:48`。

## 候选 6：Settings Interface 收窄

**Recommendation strength：** Speculative

**Files：**
- `config/settings.py:520`
- `config/settings.py:732`
- `models/agent.py:121`
- `services/agent/prompt.py:299`
- `services/agent/worker.py:207`

**Problem：**
`Settings` 同时是外部配置模型和运行时依赖对象。Prompt、Worker、Conversation、Tools 都能看到完整配置，Interface 泄漏 Implementation。开发维护 Harness 的配置约束也更难和运行时 Harness 配置演进分离。

**Deletion test：**
删除 `get_settings()` cache 后需要显式传配置；删除 `AgentDependencies.settings` 后会暴露工具真正依赖字段。复杂度不消失，但会显示窄配置对象的收益。

**Solution：**
渐进拆出 narrow config Interface，例如 PromptConfig、CompressionConfig、ProviderSelectionConfig、RuntimeHarnessConfig。

**Benefits：**
减少测试构造完整 Settings 的成本，降低 `config.json` / env interpolation 对单元测试的影响。

**Evidence：**
`Settings` 是大对象：`config/settings.py:520`。`get_settings()` 是全局 cache：`config/settings.py:732`。`AgentDependencies` 传完整 Settings：`models/agent.py:121`。PromptManager 可自行构造 Settings：`services/agent/prompt.py:299`。Worker 把 Settings 注入 deps：`services/agent/worker.py:207`。

## Top recommendation

优先处理的候选项：候选 1

原因：它最直接影响 MCBE Chat Agent 的多人会话隔离，并且能吸收 template/variable 双写、title metadata lifecycle、conversation command 状态切换等后续问题。当前 Module 最浅或最泄漏的点是：conversation state 的 Implementation 同时散在 queue、connection、worker、prompt 和 websocket transport。

先提升这个 Module 的 Locality 和 Depth，会让后续重构与测试更安全。现在不直接提出最终 Interface，是因为需要先用 `superpowers:grill-with-docs` 对领域语言和状态边界做设计追问。

## Suggested next skill

如果要继续设计：使用 `superpowers:grill-with-docs` 或 `superpowers:brainstorming`。

如果要进入实现计划：使用 `superpowers:writing-plans`。

如果要拆成 issue：使用 `superpowers:to-issues`。

## 后续选项

1. 选择一个 Strong 项，使用 `superpowers:grill-with-docs` 继续追问设计。
2. 将候选项拆成 issue，使用 `superpowers:to-issues`。
3. 暂不实现，只保留报告作为技术债基线。
