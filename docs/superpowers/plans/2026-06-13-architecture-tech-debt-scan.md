# 架构技术债扫描实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 对当前 MCBE Chat Agent 与运行时 Harness 代码做一次只读架构技术债扫描，产出可执行的技术债候选清单和优先级建议。

**架构：** 使用多个只读子代理分别扫描会话状态、WebSocket 流控、Agent 工具与运行时 Harness、Provider 生命周期和测试性。主会话只负责整合，不让子代理直接修改仓库代码；最终报告使用 Module、Interface、Implementation、Depth、Seam、Adapter、Leverage、Locality 术语描述候选项。

**技术栈：** Python 3.11、PydanticAI、asyncio、websockets、pytest、Claude Code skills、Explore 子代理、现有 `CONTEXT.md` 领域语言。

---

## 范围边界

- 本计划只做技术债扫描和报告整理，不实施重构。
- 不创建 git worktree；在当前仓库分支中创建和维护计划文档。
- 子代理只能读取代码、测试、文档和 git 状态，不写文件、不提交代码。
- 报告必须使用 `CONTEXT.md` 中的领域词：运行时 Harness、开发维护 Harness、MCBE Chat Agent、工具智能化、工具审计、反馈闭环、工具目录、工具意图、提示约束、工具提示、反馈建议。
- 架构建议必须使用固定术语：Module、Interface、Implementation、Depth、Seam、Adapter、Leverage、Locality。
- 忽略 `.worktrees/`、`node_modules/`、本地生成文件和未追踪临时文件。

---

## 文件结构

- 读取：`CONTEXT.md`
  - 获取运行时 Harness 与 MCBE Chat Agent 的领域语言。
- 读取：`CLAUDE.md`
  - 确认多人会话隔离、Flow Control Middleware 和配置约束。
- 读取：`core/queue.py`
  - 扫描 MessageBroker 中对话历史、会话锁、队列和多人会话隔离的 Module 深度。
- 读取：`core/conversation.py`
  - 扫描对话压缩、保存、恢复、标题和 turn counting 的 Locality。
- 读取：`services/agent/worker.py`
  - 扫描 Agent Worker 对会话状态、历史写入、流式事件和 Addon 同步的协调职责。
- 读取：`services/agent/core.py`
  - 扫描 streaming / non-streaming run Interface、MCP fallback 和 PydanticAI 兼容逻辑。
- 读取：`services/agent/tools.py`
  - 扫描工具注册、工具目录增强、工具审计包装和具体工具 Implementation 的 Seam。
- 读取：`services/agent/harness/catalog.py`
  - 扫描工具目录作为运行时 Harness 真相源的 Depth。
- 读取：`services/agent/harness/prompting.py`
  - 扫描工具提示渲染和 schema description 增强的 Interface。
- 读取：`services/agent/harness/audit.py`
  - 扫描工具审计的记录粒度、隐私边界和反馈闭环输入。
- 读取：`services/agent/harness/analyze.py`
  - 扫描反馈建议生成是否和工具审计、工具目录保持 Locality。
- 读取：`services/websocket/server.py`
  - 扫描 WebSocket transport、命令处理、玩家身份传递和 conversation command 的职责集中度。
- 读取：`services/websocket/connection.py`
  - 扫描 ConnectionManager、PlayerSession 和出站消息发送的 Interface。
- 读取：`services/websocket/flow_control.py`
  - 扫描 Flow Control Middleware 的 Depth 与调用点泄漏。
- 读取：`services/websocket/command.py`
  - 扫描命令解析和命令语义之间的 Seam 是否浅。
- 读取：`services/websocket/minecraft.py`
  - 扫描 MCBE 协议消息解析、格式化和 WebSocketServer 的耦合。
- 读取：`services/addon/protocol.py`
  - 扫描 Addon bridge 协议常量和 AI response chunking 的复用方式。
- 读取：`services/addon/service.py`
  - 扫描 AddonBridgeService 生命周期和全局 Adapter 使用。
- 读取：`services/addon/session.py`
  - 扫描 Addon 会话状态与 MCBE 玩家会话的交叉点。
- 读取：`services/agent/providers.py`
  - 扫描 ProviderRegistry、模型缓存、HTTP client 生命周期和测试替换 Seam。
- 读取：`services/agent/prompt.py`
  - 扫描 PromptManager 的模板、变量和玩家分桶状态。
- 读取：`config/settings.py`
  - 扫描配置 Interface、JSON flatten、必填路径和运行时 Harness 配置 Locality。
- 读取：`models/agent.py`
  - 扫描 ChatRequest、ChatResponse、StreamEvent 等模型是否表达足够的运行语义。
- 读取：`models/messages.py`
  - 扫描 MCBE message 模型和 WebSocket message 处理的契约。
- 读取：`models/minecraft.py`
  - 扫描 Minecraft command/tellraw 相关模型是否被出站发送路径充分复用。
- 读取：`models/addon_bridge.py`
  - 扫描 Addon bridge 模型与协议处理的 Interface。
- 读取：`tests/test_*.py`
  - 按扫描主题抽样检查测试覆盖、测试 seam 和回归保护缺口。

---

## 报告输出格式

最终报告不直接修改代码，建议由执行代理输出到会话，必要时另写临时 HTML 到 `$TMPDIR/architecture-review-<timestamp>.html`。

每个候选项使用以下结构：

```markdown
## 候选 N：[简短标题]

**Recommendation strength：** Strong | Worth exploring | Speculative

**Files：**
- `exact/path.py`

**Problem：**
用 Module、Interface、Implementation、Depth、Seam、Adapter、Leverage、Locality 描述当前摩擦。

**Deletion test：**
说明如果删除当前 Module，复杂度是消失、集中，还是散落到更多调用点。

**Solution：**
描述要加深哪个 Module，但不提出最终接口设计。

**Benefits：**
说明 Locality、Leverage 和测试性如何改善。

**Evidence：**
列出具体文件和函数/类名，必要时带行号。
```

报告末尾必须包含：

```markdown
## Top recommendation

优先处理的候选项：候选 N
原因：说明它为何最能提升 Locality、Leverage 或降低多人会话 / MCBE 出站路径风险。

## Suggested next skill

如果要继续设计：使用 superpowers:grill-with-docs 或 superpowers:brainstorming。
如果要进入实现计划：使用 superpowers:writing-plans。
如果要拆成 issue：使用 superpowers:to-issues。
```

---

## 任务 1：确认扫描基线

**文件：**
- 读取：`CONTEXT.md`
- 读取：`CLAUDE.md`
- 读取：`docs/superpowers/plans/2026-06-13-architecture-tech-debt-scan.md`

- [ ] **步骤 1：确认当前 git 状态**

运行：

```bash
git status --short --branch
```

预期：输出当前扫描分支，并且除计划文档外没有意外改动。例如：

```text
## tech-debt-scan-plan
?? docs/superpowers/plans/2026-06-13-architecture-tech-debt-scan.md
```

- [ ] **步骤 2：读取领域语言**

读取 `CONTEXT.md`，记录必须使用的词：

```text
运行时 Harness
开发维护 Harness
MCBE Chat Agent
工具智能化
工具审计
反馈闭环
工具目录
工具意图
提示约束
工具提示
反馈建议
```

- [ ] **步骤 3：读取项目约束**

读取 `CLAUDE.md`，记录扫描时必须特别关注的约束：

```text
多人会话隔离必须按 (connection_id, player_name) 分桶。
新增聊天、UI、上下文、模板、变量或切换模型路径时必须传递 player_name。
所有下行长文本必须统一经过 FlowControlMiddleware 分片。
普通配置进入 config.json；敏感配置进入 .env。
```

- [ ] **步骤 4：确认忽略路径**

扫描时明确排除：

```text
.worktrees/
node_modules/
__pycache__/
.pytest_cache/
config.json
.env
```

- [ ] **步骤 5：Commit 计划文档**

运行：

```bash
git add docs/superpowers/plans/2026-06-13-architecture-tech-debt-scan.md
git commit -m "docs: add architecture tech debt scan plan"
```

预期：生成一个只包含计划文档的新 commit。

---

## 任务 2：会话状态与对话 Locality 子代理扫描

**文件：**
- 读取：`core/queue.py`
- 读取：`core/conversation.py`
- 读取：`services/agent/worker.py`
- 读取：`services/agent/prompt.py`
- 读取：`services/websocket/server.py`
- 读取：`services/websocket/connection.py`
- 读取：`tests/test_conversation_manager.py`
- 读取：`tests/test_queue_context.py`
- 读取：`tests/test_prompt_template.py`
- 读取：`tests/test_websocket_conversation_commands.py`

- [ ] **步骤 1：启动 Explore 子代理**

使用一个 Explore 子代理，prompt 精确如下：

```text
Explore /root/mcbe_ai_agent for runtime conversation state technical debt. Ignore .worktrees, node_modules, __pycache__, .pytest_cache, config.json, and .env. Read CONTEXT.md and CLAUDE.md first. Focus on core/queue.py, core/conversation.py, services/agent/worker.py, services/agent/prompt.py, services/websocket/server.py, services/websocket/connection.py, and related tests. Identify where MCBE Chat Agent conversation state, player_name propagation, history, locks, templates, variables, compression, title generation, and active conversation metadata are split across Modules. Use Module, Interface, Implementation, Depth, Seam, Adapter, Leverage, Locality. Apply the deletion test to suspected shallow Modules. Return 2-4 candidates with files, concrete evidence, deletion test result, and testability impact. Do not modify files.
```

- [ ] **步骤 2：人工复核子代理结论**

主会话读取子代理提到的文件片段，确认每条候选都满足：

```text
至少涉及一个真实 Locality 问题。
至少有两个文件或一个过大的 Module 作为证据。
不是单纯代码风格问题。
不建议立即新增抽象接口。
```

- [ ] **步骤 3：整理候选项**

把有效候选写成报告草稿，标题格式：

```markdown
## 候选：运行时 conversation session Module 加深
```

候选描述必须包含：

```text
MessageBroker 是否暴露过多 Implementation。
ConversationManager 是否重复 turn counting 或 history splitting。
AgentWorker 是否承担过多 completion 后状态写入。
WebSocketServer 是否直接操作 conversation 语义。
PromptManager 的玩家分桶是否和 conversation state 分离造成认知负担。
```

- [ ] **步骤 4：记录测试缺口**

检查相关 tests 后，记录以下问题是否存在：

```text
是否能不经过 WebSocketServer 测 conversation command。
是否能不构造真实 AgentWorker 测 history trimming。
是否有多人 player_name 隔离的回归测试。
是否有模型切换清理玩家会话状态的回归测试。
```

---

## 任务 3：WebSocket 命令与 Flow Control 子代理扫描

**文件：**
- 读取：`services/websocket/server.py`
- 读取：`services/websocket/connection.py`
- 读取：`services/websocket/flow_control.py`
- 读取：`services/websocket/command.py`
- 读取：`services/websocket/minecraft.py`
- 读取：`services/addon/protocol.py`
- 读取：`models/minecraft.py`
- 读取：`models/messages.py`
- 读取：`tests/test_flow_control.py`
- 读取：`tests/test_connection_manager.py`
- 读取：`tests/test_mcbe_simulator.py`

- [ ] **步骤 1：启动 Explore 子代理**

使用一个 Explore 子代理，prompt 精确如下：

```text
Explore /root/mcbe_ai_agent for WebSocket command handling and outbound Flow Control Middleware technical debt. Ignore .worktrees, node_modules, __pycache__, .pytest_cache, config.json, and .env. Read CONTEXT.md and CLAUDE.md first. Focus on services/websocket/server.py, services/websocket/connection.py, services/websocket/flow_control.py, services/websocket/command.py, services/websocket/minecraft.py, services/addon/protocol.py, models/minecraft.py, models/messages.py, and related tests. Identify shallow Modules, duplicated outbound delivery Implementation, stringly command Interfaces, leaky seams between transport and command semantics, and any caller knowledge of MCBE command byte limits that should be localized. Use Module, Interface, Implementation, Depth, Seam, Adapter, Leverage, Locality. Apply the deletion test. Return 2-4 candidates with files, concrete evidence, deletion test result, and testability impact. Do not modify files.
```

- [ ] **步骤 2：人工复核 Flow Control 调用点**

主会话检查以下问题：

```text
ConnectionManager 是否重复 chunking、delay 和 logging。
WebSocketServer 是否也实现 tellraw chunking 或 send logging。
Addon protocol 是否持有兼容常量但把核心分片交给 FlowControlMiddleware。
raw command 超长行为是否只在 FlowControlMiddleware 内判断。
```

- [ ] **步骤 3：人工复核命令处理 Seam**

主会话检查以下问题：

```text
CommandRegistry 是否只是解析字符串，而命令语义仍在 WebSocketServer。
认证、聊天、上下文、模型切换、模板变量、MCP 状态是否混在同一 Module。
每个命令是否都显式传递 player_name。
命令处理测试是否必须构造 WebSocketServer 大对象。
```

- [ ] **步骤 4：整理候选项**

至少输出一个 WebSocket 命令候选和一个出站 delivery 候选。标题格式：

```markdown
## 候选：WebSocketServer command Module 加深
## 候选：MCBE outbound delivery Adapter 集中
```

---

## 任务 4：Agent 工具与运行时 Harness 子代理扫描

**文件：**
- 读取：`services/agent/tools.py`
- 读取：`services/agent/harness/catalog.py`
- 读取：`services/agent/harness/prompting.py`
- 读取：`services/agent/harness/audit.py`
- 读取：`services/agent/harness/analyze.py`
- 读取：`services/agent/mcwiki.py`
- 读取：`tests/test_agent_tools.py`
- 读取：`tests/test_agent_addon_tools.py`
- 读取：`tests/test_runtime_harness_catalog.py`
- 读取：`tests/test_runtime_harness_prompt.py`
- 读取：`tests/test_runtime_harness_audit.py`
- 读取：`tests/test_runtime_harness_analyze.py`

- [ ] **步骤 1：启动 Explore 子代理**

使用一个 Explore 子代理，prompt 精确如下：

```text
Explore /root/mcbe_ai_agent for Agent tools and runtime Harness technical debt. Ignore .worktrees, node_modules, __pycache__, .pytest_cache, config.json, and .env. Read CONTEXT.md and CLAUDE.md first. Focus on services/agent/tools.py, services/agent/harness/catalog.py, services/agent/harness/prompting.py, services/agent/harness/audit.py, services/agent/harness/analyze.py, services/agent/mcwiki.py, and related tests. Identify whether 工具目录, 工具提示, 工具审计, 反馈闭环, and concrete tool Implementations have a deep Module Interface or whether tools.py is a shallow mega-Module. Look for private PydanticAI seams, duplicated tool metadata, failure string drift, audit wrapping leakage, and testability gaps. Use Module, Interface, Implementation, Depth, Seam, Adapter, Leverage, Locality. Apply the deletion test. Return 2-4 candidates with files, concrete evidence, deletion test result, and testability impact. Do not modify files.
```

- [ ] **步骤 2：人工复核工具注册 Interface**

主会话检查以下问题：

```text
register_agent_tools() 是否同时承担工具定义、注册、审计包装和 description 增强。
工具目录条目是否和实际工具函数名、参数、失败输出存在双写。
工具审计是否依赖 PydanticAI 私有结构或 wrapper 细节。
工具意图是否真正提升调用者 Leverage，还是只作为文档存在。
```

- [ ] **步骤 3：人工复核运行时 Harness 测试性**

主会话检查以下问题：

```text
是否能单测工具目录一致性而不构造真实 Agent。
是否能单测审计摘要而不调用真实 Minecraft WebSocket。
是否能单测反馈建议输入格式而不调用真实 provider。
是否存在对完整工具结果或敏感玩家信息的误记录风险。
```

- [ ] **步骤 4：整理候选项**

至少输出一个工具注册候选。标题格式：

```markdown
## 候选：运行时 Harness tool Module 加深
```

---

## 任务 5：Provider 生命周期、全局 Adapter 和配置子代理扫描

**文件：**
- 读取：`services/agent/providers.py`
- 读取：`services/agent/core.py`
- 读取：`services/agent/title.py`
- 读取：`services/agent/prompt.py`
- 读取：`core/conversation.py`
- 读取：`services/addon/service.py`
- 读取：`config/settings.py`
- 读取：`tests/test_conversation_title.py`
- 读取：`tests/test_cli_config_validation.py`
- 读取：`tests/test_json_settings.py`
- 读取：`tests/test_mcp.py`

- [ ] **步骤 1：启动 Explore 子代理**

使用一个 Explore 子代理，prompt 精确如下：

```text
Explore /root/mcbe_ai_agent for Provider lifecycle, global Adapter, and configuration technical debt. Ignore .worktrees, node_modules, __pycache__, .pytest_cache, config.json, and .env. Read CONTEXT.md and CLAUDE.md first. Focus on services/agent/providers.py, services/agent/core.py, services/agent/title.py, services/agent/prompt.py, core/conversation.py, services/addon/service.py, config/settings.py, and related tests. Identify process-global singletons, class-level caches, lifecycle ownership confusion, test replacement seams that only have one production Adapter, duplicated HTTP client ownership, and configuration Interface leaks. Use Module, Interface, Implementation, Depth, Seam, Adapter, Leverage, Locality. Apply the deletion test. Return 2-4 candidates with files, concrete evidence, deletion test result, and testability impact. Do not modify files.
```

- [ ] **步骤 2：人工复核全局状态**

主会话检查以下问题：

```text
ProviderRegistry 是否拥有 _model_cache 或 _http_client_cache。
ChatAgentManager、PromptManager、ConversationManager、AddonBridgeService 是否通过 get_global_* 访问。
测试是否依赖 reset、monkeypatch 或执行顺序清理全局状态。
AgentWorker 和 ProviderRegistry 是否分别拥有 httpx.AsyncClient 生命周期。
```

- [ ] **步骤 3：人工复核配置 Interface**

主会话检查以下问题：

```text
普通配置是否都在 config.json/config.example.json。
敏感配置是否只在 .env 或环境变量。
JSON flatten 是否让 Settings Interface 暴露过多 Implementation。
缺失配置报错是否能定位 JSON 路径和变量名。
```

- [ ] **步骤 4：整理候选项**

至少输出一个生命周期或测试 Seam 候选。标题格式：

```markdown
## 候选：运行时 Adapter lifecycle Module 显式化
```

---

## 任务 6：交叉验证测试覆盖和近期变更风险

**文件：**
- 读取：`tests/test_*.py`
- 读取：`services/**/*.py`
- 读取：`core/*.py`
- 读取：`models/*.py`

- [ ] **步骤 1：列出测试文件**

运行：

```bash
python - <<'PY'
from pathlib import Path
for path in sorted(Path('tests').glob('test_*.py')):
    print(path)
PY
```

预期：输出所有测试文件路径，不包含 `.worktrees/` 下的测试。

- [ ] **步骤 2：按主题映射测试覆盖**

主会话建立以下映射：

```text
会话状态：test_conversation_manager.py, test_queue_context.py, test_prompt_template.py, test_websocket_conversation_commands.py
WebSocket/流控：test_flow_control.py, test_connection_manager.py, test_mcbe_simulator.py, test_stream_mode.py
Agent 工具/运行时 Harness：test_agent_tools.py, test_agent_addon_tools.py, test_runtime_harness_*.py
Provider/配置：test_conversation_title.py, test_cli_config_validation.py, test_json_settings.py, test_mcp.py
Addon bridge：test_addon_bridge_protocol.py, test_addon_bridge_service.py
```

- [ ] **步骤 3：运行快速测试基线**

运行：

```bash
pytest tests/test_flow_control.py tests/test_runtime_harness_catalog.py tests/test_queue_context.py -q
```

预期：全部 PASS。若失败，报告中标记“扫描基线异常”，并记录失败测试名，不在本计划中修复。

- [ ] **步骤 4：检查最近提交主题**

运行：

```bash
git log --oneline -10
```

预期：看到近期运行时 Harness、配置、conversation 或 flow control 相关提交。将近期高变更区域和技术债候选交叉标注为更高风险。

---

## 任务 7：汇总候选并排序

**文件：**
- 读取：所有子代理结果
- 读取：人工复核笔记
- 输出：会话中的最终技术债扫描报告
- 可选输出：`$TMPDIR/architecture-review-<timestamp>.html`

- [ ] **步骤 1：去重候选项**

合并子代理结果，按以下规则去重：

```text
同一组文件、同一 Locality 问题，只保留证据最强的一项。
单纯命名、格式、注释问题不进入架构技术债清单。
没有明确 Seam 或 Module 深度问题的项降级为 Speculative 或删除。
```

- [ ] **步骤 2：排序候选项**

按以下优先级排序：

```text
Strong：影响多人会话隔离、MCBE 出站安全、工具审计正确性，且证据跨多个文件。
Worth exploring：能提升 Locality 或测试性，但当前风险可控。
Speculative：可能改善 Depth，但需要更多产品或维护约束确认。
```

- [ ] **步骤 3：写 Top recommendation**

Top recommendation 必须从 Strong 项中选择。若没有 Strong 项，选择 Worth exploring 中测试性收益最高的一项。

推荐理由必须覆盖：

```text
为什么这个 Module 当前最浅或最泄漏。
为什么它最能提升 Locality。
为什么它能让后续测试或重构更安全。
为什么现在不直接提出最终 Interface。
```

- [ ] **步骤 4：输出后续选项**

最终报告末尾提供三个选项：

```text
1. 选择一个 Strong 项，使用 superpowers:grill-with-docs 继续追问设计。
2. 将候选项拆成 issue，使用 superpowers:to-issues。
3. 暂不实现，只保留报告作为技术债基线。
```

---

## 任务 8：验证计划文档质量

**文件：**
- 读取：`docs/superpowers/plans/2026-06-13-architecture-tech-debt-scan.md`

- [ ] **步骤 1：检查计划文件存在**

运行：

```bash
test -f docs/superpowers/plans/2026-06-13-architecture-tech-debt-scan.md
```

预期：命令退出码为 0。

- [ ] **步骤 2：检查禁止占位符**

运行：

```bash
python3 - <<'PY'
from pathlib import Path
path = Path('docs/superpowers/plans/2026-06-13-architecture-tech-debt-scan.md')
text = path.read_text(encoding='utf-8')
for token in [
    '待' + '定',
    'TO' + 'DO',
    '后续' + '实现',
    '补充' + '细节',
    '添加适当的' + '错误处理',
    '添加' + '验证',
    '处理' + '边界情况',
    '类似' + '任务',
]:
    if token in text:
        raise SystemExit(f'forbidden placeholder found: {token}')
print('placeholder scan passed')
PY
```

预期：输出：

```text
placeholder scan passed
```

- [ ] **步骤 3：检查必须提到的子技能**

运行：

```bash
python - <<'PY'
from pathlib import Path
text = Path('docs/superpowers/plans/2026-06-13-architecture-tech-debt-scan.md').read_text(encoding='utf-8')
required = [
    'superpowers:subagent-driven-development',
    'superpowers:executing-plans',
    'superpowers:grill-with-docs',
    'superpowers:to-issues',
]
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit(f'missing skills: {missing}')
print('skill mention scan passed')
PY
```

预期：输出：

```text
skill mention scan passed
```

- [ ] **步骤 4：检查架构术语**

运行：

```bash
python - <<'PY'
from pathlib import Path
text = Path('docs/superpowers/plans/2026-06-13-architecture-tech-debt-scan.md').read_text(encoding='utf-8')
required = ['Module', 'Interface', 'Implementation', 'Depth', 'Seam', 'Adapter', 'Leverage', 'Locality']
missing = [item for item in required if item not in text]
if missing:
    raise SystemExit(f'missing architecture terms: {missing}')
print('architecture term scan passed')
PY
```

预期：输出：

```text
architecture term scan passed
```

- [ ] **步骤 5：Commit 验证后的计划**

如果任务 1 已经提交计划文档，但本任务修改了计划内容，运行：

```bash
git add docs/superpowers/plans/2026-06-13-architecture-tech-debt-scan.md
git commit -m "docs: refine architecture tech debt scan plan"
```

预期：若无修改则不需要提交；若有修改则生成一个只包含计划文档的新 commit。

---

## 自检结果

- 规格覆盖度：已覆盖新建分支、指定目录新建计划文档、使用 skills、不创建工作树、建议子代理扫描、只读技术债扫描和后续执行选项。
- 占位符扫描：计划不包含 writing-plans 禁止清单中的占位表达。
- 类型一致性：所有任务统一使用 Module、Interface、Implementation、Depth、Seam、Adapter、Leverage、Locality；领域词统一使用 `CONTEXT.md` 中的运行时 Harness 与 MCBE Chat Agent 词汇。

---