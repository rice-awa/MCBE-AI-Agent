# Harness Engineering 资料整合

## 摘要

Harness Engineering 是围绕 AI coding agents 兴起的一组工程实践。它关注的不是继续调模型本身，而是为 agent 构造一套可持续改进的运行约束、上下文、验证环境和反馈回路：在任务开始前提供正确上下文、工具和规则，在执行中约束行为，在完成后用自动化检查、审查和可观测性数据验证结果，并把失败沉淀为新的规则、测试、工具或流程。

如果说模型是“马匹”，强大、快速但不会天然知道目标，那么 harness 就是“马具”：缰绳、鞍座、护栏和反馈系统。人类工程师不再亲自奔跑，而是提供方向、设计约束、观察行为并持续改进控制系统。没有 harness 的 AI agent 就像没有马具的野马，速度惊人，但难以稳定完成工程目标。

当前较权威的主线资料包括 Martin Fowler 网站上 Thoughtworks Distinguished Engineer Birgitta Böckeler 于 2026-04-02 发布的《Harness engineering for coding agent users》、OpenAI 的 Harness Engineering 实践总结、Anthropic 的 context engineering 与多 agent 经验、LangChain 的 agent harness 中间件实验，以及 Mitchell Hashimoto、MiniMax、NxCode 等实践者和社区资料。综合这些资料，Harness Engineering 可以被理解为一种新型软件工程基础设施：它把“如何监督 AI 写代码”工程化。

## 资料来源与可信度

优先参考资料：

- [Harness engineering for coding agent users - Martin Fowler](https://martinfowler.com/articles/harness-engineering.html)：核心权威来源，作者来自 Thoughtworks，文章系统提出 feedforward、feedback、steering loop、regulation categories、harnessability 等概念。
- [Harness engineering - OpenAI](https://openai.com/index/harness-engineering/)：OpenAI 官方实践总结，提出 Harness Engineering 术语和大规模 agent 编码系统中的环境、反馈循环、控制系统经验。
- [Effective context engineering for AI agents - Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)：权威相邻资料，定义 context engineering，并讨论系统提示词、工具设计、动态上下文检索、压缩、记忆和 sub-agent 架构。
- [Context Engineering - LangChain](https://www.langchain.com/blog/context-engineering-for-agents)：相邻工程实践资料，把 agent context engineering 总结为 write、select、compress、isolate 四类策略。
- 用户提供的知乎文章内容：作为中文社区整合视角，补充词源隐喻、三大支柱、成熟度路径、常见陷阱和跨文章贡献总结。本文吸收其结构性观点，但对关键案例尽量使用公开来源或以“资料称”“案例显示”的方式表述，避免把未交叉验证的信息写成无条件事实。

## 核心定义

在 Fowler/Thoughtworks 的语境中，harness 可以理解为“除模型以外，围绕 AI agent 的全部工程约束和运行环境”。对 coding agent 用户来说，它不是单个 prompt，也不是单次测试命令，而是一套让 agent 能被引导、观测和纠偏的系统。

一个实用定义是：

> Harness Engineering 是为 AI coding agent 构建、组合和迭代外部控制系统的工程实践，用 feedforward 提前塑形任务执行，用 feedback 检测和纠正输出，用 steering loop 持续把失败案例转化为更强的上下文、工具、测试和流程约束。

也可以从四个动作理解：

| 动作 | 含义 | 常见实现 |
| --- | --- | --- |
| Constrain | 限制 agent 能做什么，不能做什么 | 架构边界、依赖方向、权限限制、工具白名单 |
| Inform | 告诉 agent 它该做什么和如何判断上下文 | `AGENTS.md`、设计文档、API 合约、任务计划 |
| Verify | 检查 agent 是否正确完成任务 | 测试、lint、类型检查、CI、端到端验证 |
| Correct | 当 agent 出错时纠正当前任务并改进系统 | 反馈循环、失败模式记录、自我修复、规则更新 |

## 与相邻概念的区别

| 概念 | 范围 | 关注点 | 典型产物 |
| --- | --- | --- | --- |
| Prompt Engineering | 单一交互 | 如何把一次指令写清楚 | 提示词、角色设定、输出格式 |
| Context Engineering | 模型上下文窗口 | 模型看到什么信息 | 系统提示词、工具说明、检索、记忆、压缩 |
| Harness Engineering | 整个 agent 工作系统 | 环境、约束、反馈、生命周期 | 规则、工具、测试、hooks、审查、CI、反馈回路 |
| Agent Engineering | agent 内部架构 | agent 如何规划、路由和调用工具 | planner、router、memory、sub-agent |
| Platform Engineering | 基础设施 | 部署、扩展、运维和开发平台 | CI/CD、环境、权限、监控、平台服务 |

Harness Engineering 包含 Context Engineering，并借鉴 Prompt Engineering，但它运行在更高层面。它关心的不是“这次怎么提示模型”，而是“怎样让 agent 在一个真实工程系统里稳定地产出可维护、可验证、可纠正的结果”。

## 为什么重要

### 模型逐渐商品化，Harness 成为差异

随着主流模型能力接近，团队之间的差异越来越不只来自“用了哪个模型”，而来自“模型被放进了怎样的工程环境”。同一个模型，在不同 harness 下可能表现出完全不同的可靠性、速度和可维护性。

LangChain 的 Terminal Bench 2.0 实验给出了典型信号：资料称，使用相同模型时，初始 agent 得分为 52.8%，经过 harness 优化后提升到 66.5%，排名从 Top 30 之外进入 Top 5。关键点不在具体榜单名次，而在于因变量是 harness，而不是模型。

### OpenAI 的大规模实证

OpenAI 的 Harness Engineering 实践显示，agent 编码可以在足够强的环境、反馈和控制系统下承担大规模工程工作。官方资料描述了一个 5 个月、约 100 万行代码、约 1500 个 PR 的项目，核心经验不是“人类完全不重要”，而是工程师职责发生迁移：从直接写每一行代码，转向设计环境、明确意图、提供反馈、定义验证、维护控制系统。

这类案例的启发是：当 agent 生成能力足够强时，瓶颈会从“能不能写代码”转向“能不能稳定写对、能不能被验证、能不能持续维护”。

### Anthropic 的多 Agent 对比

Anthropic 的实验更直观地展示了 harness 的作用。资料中提到，简单单 agent 方案在复杂前端或游戏类任务上容易停留在核心功能不完整、体验不可用的状态；引入规划器、生成器、评估器、真实浏览器验证、上下文重置和评分标准后，产物在功能完整性和可用性上显著提高。

这说明 harness 并不只是“多加几句规则”，而是把任务拆解、执行节奏、评估标准、工具调用和反馈循环组织成一个系统。

### 工程师角色的转变

| 职责 | 传统工程 | Harness Engineering |
| --- | --- | --- |
| 编写代码 | 主要工作之一 | 逐步减少，更多由 agent 执行 |
| 设计架构 | 重要工作 | 核心工作 |
| 编写文档 | 经常滞后于实现 | 关键基础设施 |
| 评审 PR | 评审人类代码 | 评审 agent 输出和 harness 有效性 |
| 调试 | 阅读代码、复现问题 | 分析 agent 行为模式和反馈缺口 |
| 编写测试 | 覆盖业务行为 | 设计 agent 可执行、可解释的验证策略 |
| 维护流程 | 团队协作需要 | agent 可靠运行的前提 |

Harness Engineering 不是让工程师消失，而是把工程师的工作重心推向系统设计、架构约束、验证策略、上下文治理和反馈循环。

## 三大支柱

### 1. 上下文工程

核心原则：从 agent 的角度看，它在上下文中无法访问的东西就是不存在的。

静态上下文包括：

- 架构规范、API 合约、模块职责、风格指南。
- `AGENTS.md`、`CLAUDE.md`、`.cursorrules` 等项目级规则文件。
- 设计文档、迁移记录、ADR、测试策略和交叉链接的文档。
- 目录结构说明、配置说明和常见错误记录。

动态上下文包括：

- agent 启动时的目录结构映射、最近 diff、当前分支状态。
- CI/CD 状态、测试结果、失败日志、运行时指标和追踪信息。
- 从代码检索、文档检索、MCP 工具或内部知识库中按需选择的片段。
- 任务过程中的计划、假设、验证结果和中间结论。

关键实践：

- 代码仓库是唯一真相源。架构决策、约定、计划、验收标准应版本化存入仓库，而不是散落在聊天记录、会议纪要或人脑中。
- 渐进式披露。`AGENTS.md` 应像目录和规则索引，而不是百科全书；细节应链接到更具体的文档。
- 上下文隔离。不同任务、不同玩家会话、不同子 agent 应避免共享不相关状态。
- 上下文压缩。长任务中要定期压缩历史、记录决策和丢弃噪声，避免上下文膨胀造成性能下降。
- 上下文重置。Anthropic 的实践显示，在长任务或多阶段任务中重置上下文、重新注入精简目标和状态，有助于缓解模型在长上下文中的漂移和焦虑。

LangChain 将 agent context engineering 概括为四类：

| 策略 | 含义 |
| --- | --- |
| Write | 把上下文写入外部持久位置，例如 notes、memory、scratchpad |
| Select | 在需要时选择相关上下文进入窗口 |
| Compress | 压缩历史和中间结果，只保留必要信息 |
| Isolate | 把上下文隔离到不同 agent、线程、环境或子任务中 |

### 2. 架构约束

Harness Engineering 与传统提示工程最明显的区别，是它不只告诉 agent“写好代码”，而是用机械化规则强制好代码的形状。

常见约束工具：

| 工具类型 | 描述 | 示例 |
| --- | --- | --- |
| 自定义 linter | 自动标记违反团队规则的代码 | 依赖方向检查、命名约定、禁止 API |
| 结构测试 | 验证架构边界和模块分层 | `models -> config -> repository -> service -> runtime -> UI` |
| 预提交钩子 | 提交前自动检查 | 格式化、lint、基础测试、配置校验 |
| CI 门禁 | 合并前强制验证 | 单元测试、类型检查、安全扫描、构建 |
| LLM 审计器 | 审查难以形式化的语义风险 | 架构适配性、需求一致性、代码审查 |

核心洞察是：约束并不只是限制，也是在赋能。没有边界时，agent 会浪费 token 探索无效方案；边界清晰时，agent 更容易收敛到符合项目风格和架构的解法。

Fowler 文章中的 computational / inferential 区分也适用于约束设计：

- Computational：确定性、快速、可重复，例如测试、lint、类型检查、脚本、正则检查、配置校验。
- Inferential：依赖语义判断，例如 LLM code review、架构适配性判断、需求一致性审查、复杂风险评估。

工程上应优先把明确规则做成 computational 检查，把 inferential 检查留给“重要但难以完全形式化”的部分。

### 3. 熵管理

AI 生成的代码库会快速积累熵：文档与现实脱节、命名约定分歧、死代码堆积、重复实现增多、架构边界漂移、测试只覆盖表面行为。Harness Engineering 不能只关注“生成”，还必须关注“垃圾回收”。

常见熵管理 agent：

| Agent 类型 | 职责 |
| --- | --- |
| 文档一致性 agent | 检查文档是否与当前代码、配置和 API 匹配 |
| 约束违规扫描器 | 查找绕过早期检查的架构或规则违规 |
| 模式执行 agent | 识别并修复与既有模式不一致的实现 |
| 依赖审计器 | 跟踪循环依赖、重复依赖和不必要依赖 |
| 测试缺口 agent | 根据变更和事故记录建议补齐回归测试 |

MiniMax 的自进化实验把熵管理推向更进一步：agent 不只维护业务代码，还能分析失败轨迹、规划工具链变更、修改自身工作流、运行评估、对比结果并决定保留或回滚。公开资料称，其系统在 100+ 轮自动迭代中优化了采样参数、工作流指南和循环检测机制，内部评估性能提升约 30%。

对普通团队来说，不需要一开始就追求自进化系统。更实际的起点是：把重复失败记录下来，并让它们变成测试、规则、脚本或文档。

## 关键概念

### Feedforward：提前塑形

Feedforward 是在 agent 产生输出之前施加的控制。目标是减少 agent 走错方向的概率，而不是等它犯错后再补救。

常见做法包括：

- 在 `CLAUDE.md`、`AGENTS.md`、项目规范中明确架构边界、命名规则、测试要求和禁止事项。
- 把高频工作流固化为 slash command、技能、脚本或模板，减少每次临场解释。
- 让工具说明、MCP 工具、检索入口和上下文片段具备明确职责，避免 agent 在模糊工具之间猜测。
- 在任务启动前给出验收标准、目标文件范围和风险边界。

### Feedback：检测与纠偏

Feedback 是在 agent 行动之后观察结果，并把结果用于纠正当前任务或改进后续 harness。

常见做法包括：

- 自动运行单元测试、类型检查、lint、构建、端到端测试。
- 对 diff 做静态分析、安全检查、代码审查或语义审查。
- 用日志、运行时断言、集成测试和真实环境 smoke test 观察行为。
- 把重复失败沉淀为新规则、新测试、新工具或更明确的工作流。

### Steering Loop：转向回路

Steering loop 是 harness engineering 的核心闭环：

1. 人类给 agent 一个任务和初始 harness。
2. agent 执行任务。
3. harness 用测试、检查、审查和人工反馈暴露问题。
4. 人类判断问题是一次性失误，还是 harness 缺口。
5. 如果是 harness 缺口，就补充规则、工具、测试、上下文或流程。
6. 下一次类似任务复用更强的 harness。

这个循环把“我下次提醒你”变成“系统下次自动约束你”。

### Keep Quality Left：把质量前移

Harness Engineering 不鼓励把质量全部压到最后一次人工 code review。更好的方式是尽早暴露问题：

- 任务开始前：明确边界、验收标准、项目规则。
- 执行过程中：让 agent 使用专门工具读取文档、查找代码、验证局部假设。
- 修改之后：立刻运行相关测试和静态检查。
- 交付之前：执行最终审查和摘要。

质量越早进入 agent 的工作循环，返工成本越低。

### Regulation Categories：调节目标

Fowler 文章把 harness 想要调节的结果分成几类，适合转化成团队内检查维度：

- Maintainability：代码是否易读、简单、低耦合、符合团队习惯。
- Architecture fitness：是否符合系统边界、依赖方向、模块职责和长期架构约束。
- Behavior：功能行为是否符合需求，错误路径和边界条件是否正确。

这三类分别对应“代码质量”“架构适配”和“业务正确性”，需要不同的传感器组合。

### Harnessability：代码库可驾驭性

Harnessability 指一个代码库是否容易被 harness 约束和验证。可测试、模块清晰、依赖明确、命令可重复、文档集中、工具稳定的项目更适合 agent 自动化。

如果一个项目缺少测试、启动复杂、边界混乱、配置靠口口相传，那么 agent 的失败不一定只是模型问题，也可能是项目本身缺少可被自动化 harness 利用的结构。

## Harness 架构模式

### Anthropic：Planner / Generator / Evaluator

Anthropic 的多 agent 实践借鉴了“生成器-评估器”对抗循环：

```text
Planner -> Generator -> Evaluator -> Feedback
   ^                                  |
   +----------------------------------+
```

各角色职责：

| 角色 | 职责 |
| --- | --- |
| Planner | 将用户需求扩展为产品规格、任务计划和验收标准 |
| Generator | 按计划实现功能，必要时分冲刺推进 |
| Evaluator | 使用浏览器、测试、截图、评分标准等方式实际验证产物 |
| Feedback loop | 把评估结果反馈给生成器或规划器进行修正 |

关键机制：

- 上下文重置：在长任务中定期用压缩后的目标、计划和状态重新开始，降低上下文漂移。
- 真实工具验证：例如使用 Playwright 打开页面、检查交互、截图对比，而不是只让 LLM 自我评价。
- 评分标准量化：把设计质量、原创性、工艺和功能性等主观指标拆成可评估维度。
- 随模型演进简化 harness：当模型能力提升后，某些冲刺结构或评估环节可以被移除或降级为可选。

### LangChain：可组合中间件

LangChain 的实验把 harness 构建为可组合中间件层：

```text
Agent request
  -> LocalContextMiddleware
  -> LoopDetectionMiddleware
  -> ReasoningSandwichMiddleware
  -> PreCompletionChecklistMiddleware
  -> Agent response
```

典型中间件职责：

| 中间件 | 作用 |
| --- | --- |
| LocalContextMiddleware | 启动时映射代码库、工具路径和本地环境 |
| LoopDetectionMiddleware | 监控重复编辑、反复失败和无效尝试 |
| ReasoningSandwichMiddleware | 在规划、实现、验证阶段分配不同推理预算 |
| PreCompletionChecklistMiddleware | 在 agent 退出前强制执行验证和需求核对 |

这种模式的优势是模块化：每层只增加一种能力，不修改核心 agent 逻辑，因此更容易测试、组合和替换。

### Mitchell Hashimoto：个人实践型 Harness

Mitchell Hashimoto 的经验更适合个人开发者和小团队：

- 用 `AGENTS.md` 记录 agent 常见错误、项目约定和禁止事项。
- 为 agent 准备自动截图、测试脚本、验证命令，让它能自我纠正。
- 每天结束前启动 agent 处理低风险后台任务，例如调研、issue 分类、简单重构。
- 把高成功率、低复杂度任务交给 agent，把需要深度判断的复杂任务保留给人类。
- 持续维护自己的工程判断力，不把所有复杂问题都外包给 agent。

这个路线强调从轻量 harness 开始，而不是一上来建设复杂平台。

### 自进化 Harness

MiniMax 等实验展示了更激进的方向：agent 不只是被 harness 约束，还能改进 harness 本身。典型循环是：

```text
分析失败轨迹 -> 规划变更 -> 修改工具链或工作流 -> 运行评估 -> 对比结果 -> 保留或回滚
```

这一方向适合评估体系清晰、任务分布稳定、回滚机制完善的组织。否则，自进化 harness 也可能放大错误目标或让系统变得难以解释。

## 设计模式与技巧

### 自验证循环

问题：agent 常在“写完代码”后停止，缺少对任务要求的独立验证。

建议流程：

1. 规划与探索：阅读任务、扫描代码库、确认约束和风险。
2. 构建：实现计划，同时补充正常路径和边缘路径测试。
3. 验证：运行测试，并对照原始任务要求检查结果，而不是只看代码是否能运行。
4. 修复：分析错误原因，修改实现或测试，再重新验证。

PreCompletionChecklistMiddleware 的价值在于：在 agent 结束前强制它执行验证步骤，并明确说明哪些验证已经运行、哪些没有运行。

### 循环检测

问题：agent 可能陷入反复微调同一错误方案的循环。

可行做法：

- 统计单文件编辑次数、同类错误重试次数和测试失败模式。
- 超过阈值后提示 agent 停止局部修补，重新评估方案。
- 要求 agent 写出当前假设、失败证据和替代路径。
- 对重复失败任务升级给人类或更强评估 agent。

### 推理资源分配

问题：全程高推理预算会变慢甚至超时，全程低推理预算又容易影响质量。

LangChain 的“Reasoning Sandwich”思路是按阶段分配推理资源：

| 阶段 | 推理预算 | 原因 |
| --- | --- | --- |
| 规划 | 高 | 需要理解目标、约束和架构 |
| 实现 | 中 | 大量操作可以由代码结构和工具反馈驱动 |
| 验证 | 高 | 需要对照需求、分析失败、判断是否真正完成 |

资料称，在其 Terminal Bench 2.0 实验中，全程 xhigh 容易超时，全程 high 得分为 63.6%，reasoning sandwich 达到 66.5%。这说明推理预算本身也是 harness 的一部分。

### 评估标准量化

许多任务表面上主观，但仍然可以转化为评分维度。例如前端设计质量可拆为：

| 维度 | 描述 | 权重 |
| --- | --- | --- |
| 设计质量 | 整体感、一致性、视觉层级 | 高 |
| 原创性 | 是否有定制化决策，而非模板化产物 | 高 |
| 工艺 | 排版、间距、色彩、响应式细节 | 中 |
| 功能性 | 独立于美学的可用性和交互完整性 | 中 |

评估 agent 需要少量样本校准，使其判断与人类偏好保持一致。对工程任务也类似，可以将“可维护”拆成依赖方向、命名一致性、测试覆盖、错误处理、配置边界等具体维度。

### 时间预算提醒

在任务上下文中注入时间或资源约束，可以帮助 agent 更合理地分配探索、实现和验证时间。例如：

- 剩余 20 分钟时优先完成核心路径和最小验证。
- 修改范围超过预期时停止扩散，回到任务目标。
- 测试失败超过阈值时先定位根因，不继续盲目改动。

## 成熟度路径

### 级别 1：基础 Harness

适合个人开发者，通常 1-2 小时可以起步。

建议配置：

- `AGENTS.md`、`CLAUDE.md` 或 `.cursorrules`，写清楚项目约定。
- 可由 agent 直接运行的测试命令。
- 基础格式化、lint 和预提交钩子。
- 清晰目录结构、命名规则和模块职责。
- 常见错误和禁止事项记录。

影响：防止最常见的 agent 错误，让 agent 不再每次从零猜项目规则。

### 级别 2：团队 Harness

适合 3-10 人小团队，通常 1-2 天可建立初版。

在级别 1 基础上增加：

- 团队统一的 `AGENTS.md` 和任务模板。
- 由 CI 强制执行的架构约束和关键测试。
- 常见任务的共享 prompt、slash command 或脚本。
- 文档即代码：链接、配置、API 示例由 linter 或测试验证。
- 针对 agent 生成 PR 的代码审查清单。

影响：确保团队内部 agent 行为一致，降低“每个人都在训练自己的临时 agent”的混乱。

### 级别 3：生产级 Harness

适合工程组织，通常需要 1-2 周建立初版，并持续迭代。

在级别 2 基础上增加：

- 自定义中间件层，例如循环检测、推理预算分配、完成前检查。
- 可观测性集成，让 agent 可读取日志、指标、追踪和 CI 状态。
- 定时运行的熵管理 agent。
- Harness 版本控制、评估集和 A/B 测试。
- Agent 性能监控仪表板。
- Agent 卡住、越权或高风险改动时的升级策略。

影响：agent 开始像自主贡献者一样工作，而不是只作为一次性代码生成工具。

## 常见错误与陷阱

### 过度设计控制流

模型能力变化很快，过度复杂的 planner、router、workflow 可能在下一次模型升级后变成负担。Harness 应该具备可剥离性：当模型足够智能时，复杂组件应能被移除、降级或替换。

### 把 Harness 当成静态资产

Harness 需要随模型、代码库和团队工作方式演进。每次主要模型更新、重大架构调整或事故复盘后，都应检查现有规则、测试和中间件是否仍然有效。

### 忽视文档层

最具影响力的 harness 改进往往很朴素：更好的文档、更清晰的目录、更明确的验收标准。文档不是附属品，而是 agent 可消费的基础设施。

### 没有反馈循环

没有反馈的 harness 只是牢笼，不是指南。Agent 需要知道何时成功、何时失败、失败后应如何改变策略。测试、日志、审查和人类反馈都应该进入闭环。

### 只保留人类可访问的知识

如果架构决策存在于人脑、会议、Slack 或 agent 无法访问的 Confluence 页面里，对 agent 来说这些知识就不存在。Agent 所需的一切应尽量进入仓库或可检索的工具链。

### 过度依赖 LLM 自我审查

LLM 审查适合处理语义判断，但不应替代确定性检查。能用测试、类型检查、脚本或 linter 表达的规则，应优先自动化。

### 忽略项目自身的 Harnessability

没有测试、启动复杂、边界混乱、配置靠口口相传时，agent 很难稳定产出可信结果。提高 harnessability 本身就是一项工程治理工作。

## 各资料的独特贡献

| 来源 | 核心贡献 |
| --- | --- |
| OpenAI | 提出 Harness Engineering 术语；强调环境、反馈循环和控制系统；提供大规模 agent 编码实践 |
| Martin Fowler / Thoughtworks | 系统化分析 feedforward、feedback、steering loop、regulation categories、harnessability；强调 coding agent 用户侧实践 |
| Anthropic | 提供 context engineering 框架；展示 planner / generator / evaluator、多 agent、上下文重置和主观任务评估方法 |
| LangChain | 提供可组合中间件模式；用 Terminal Bench 2.0 实验量化 harness 改进价值；提出循环检测、推理三明治、自验证循环等技巧 |
| Mitchell Hashimoto | 从个人实践者角度强调 `AGENTS.md`、验证工具、低风险后台任务和人类保留复杂判断 |
| NxCode / 中文社区整合 | 提供成熟度模型、概念对比和跨文章总结，有助于团队形成落地路径 |
| MiniMax | 展示自进化 harness 方向：agent 分析失败、修改工具链、运行评估并保留有效改进 |

## 在本项目中的启发

MCBE AI Agent 已经具备一些 Harness Engineering 雏形：

- `AGENTS.md` 提供了项目级 feedforward，明确多人会话隔离、统一流控、配置边界和测试命令。
- `docs/` 下的设计与迁移文档提供长期上下文，可减少 agent 误判项目目标。
- `pytest`、`ruff`、`mypy` 等约定可以作为 computational feedback。
- 统一的 `FlowControlMiddleware` 和多人会话隔离规则体现了 architecture fitness 约束。
- 配置分离策略（`.env` 存密钥、`config.json` 存普通配置）为 agent 提供了明确安全边界。

后续如果要增强本项目 harness，可以按优先级推进：

1. 补齐关键回归测试
   - 多人会话隔离：确保历史、锁、模板、变量按 `(connection_id, player_name)` 分桶。
   - 统一流控：覆盖 `tellraw`、`scriptevent`、AI 响应事件和超长原始命令错误路径。
   - 配置解析：覆盖 `${VAR}` 环境变量替换、缺失变量报错和敏感配置边界。

2. 强化架构约束
   - 为 WebSocket、MessageBroker、Agent Worker、PromptManager 的调用边界建立结构测试或专项 lint。
   - 禁止在调用点重新实现分片逻辑，新增下行消息必须复用 `FlowControlMiddleware`。
   - 禁止用 `ConnectionState.player_name` 作为真实玩家会话身份来源。

3. 建立任务级验证清单
   - 修改 WebSocket 路径后必须跑相关异步测试。
   - 修改 provider 或配置后必须跑配置加载和 provider 初始化测试。
   - 修改流控后必须跑字节长度、分片数量、延迟策略和 MCBE 命令包装测试。

4. 建立熵管理机制
   - 定期扫描文档中已过期的命令、配置字段和模块职责。
   - 对重复工具函数、绕过统一中间件的实现进行审计。
   - 把 agent 在开发中反复犯的错误记录进 `AGENTS.md` 或对应测试。

5. 为高风险区域建立升级策略
   - 权限、认证、命令执行、文件写入、外部 API 和 MCP 配置变更需要显式审查。
   - 对生产影响路径保留人工确认和 CI 门禁。

## 核心结论

### Harness 是新的竞争力

当模型能力趋于同质化时，harness 的质量决定 AI 辅助开发的上限。同一个模型，放在不同上下文、约束、工具和反馈系统里，结果会明显不同。

### 工程师不会消失，而是进化

Harness Engineering 不替代工程师，而是改变工程师的工作重心：从“亲自写每一行代码”转向“设计 agent 能稳定工作的系统”。这要求更强的架构思维、测试思维、文档能力和系统治理能力。

### 从简单开始，持续迭代

一个精心维护的 `AGENTS.md`、一组可运行测试和基础预提交钩子，往往比复杂中间件更早产生价值。Harness 应从最常见失败开始，随模型和项目演进逐步增强，也要能在不再需要时简化。

### 代码仓库应成为唯一真相源

设计文档、架构图、产品规范、执行计划、测试策略和复盘结论都应以版本化方式进入仓库或 agent 可访问工具链。对 agent 不可见的知识，在执行时基本等同于不存在。

### 未来方向是自进化系统

MiniMax 等实验预示了下一阶段：agent 不只被 harness 驾驭，还能改进自己的 harness。当“设计、执行、评估、改进”循环逐渐自动化，真正的自进化 AI 工程系统就会出现。但在此之前，团队仍需要先建立清晰的验证体系、回滚机制和人类监督边界。

## 一句话总结

Harness Engineering 的重点是把“人类如何监督 coding agent”工程化：用上下文引导它，用架构约束限制它，用测试和审查观察它，用反馈循环纠正它，用熵管理长期维护它。它不是替代软件工程基本功，而是把文档、测试、架构、工具和流程变成 agent 可以实际使用的质量系统。
