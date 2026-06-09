# MCBE AI Agent Harness Context

This context defines the shared language for Harness Engineering in this MCBE AI Agent project. It keeps runtime agent control concepts separate from future coding-agent maintenance practices.

## Language

**运行时 Harness**:
围绕 MCBE Chat Agent 在线服务的一组上下文、约束、工具执行边界、审计和反馈机制，用来让玩家请求在游戏运行时被稳定、安全、可观察地处理。
_Avoid_: Agent Harness, Harness, 控制层

**开发维护 Harness**:
围绕 coding agent 维护本仓库的一组项目规则、验证流程、文档入口和审查机制；它不是当前设计主线，但需要保留接入点。
_Avoid_: 开发 Harness, 项目 Harness, Coding Harness

**MCBE Chat Agent**:
面向 Minecraft Bedrock 玩家消息的在线 AI 助手，由玩家会话、对话上下文、提示词、模型 provider 和可调用工具共同构成。
_Avoid_: AI Agent, Bot, 聊天机器人

**工具智能化**:
运行时 Harness 第一阶段的目标：让 MCBE Chat Agent 更准确地选择工具、组织参数、处理失败结果，并知道何时不该调用工具。
_Avoid_: 工具优化, Tool Enhancement, 聪明调用

**工具审计**:
对 MCBE Chat Agent 的工具选择、参数、执行结果和失败原因进行可追踪记录，用于定位误用和改进后续提示词或工具定义。第一版采用结构化摘要，记录基础摘要、目录声明的参数预览和结果摘要，并保留有限玩家/会话身份，不记录完整结果。
_Avoid_: 日志, 监控, 审计日志

**反馈闭环**:
把工具调用中的重复失败、误用和高价值成功案例转化为更清晰的工具说明、测试、规则或运行时检查。第一版聚合工具审计结果并生成改进建议，但不自动修改工具目录或执行规则。
_Avoid_: 复盘, 自我改进, 反馈系统

**工具目录**:
运行时 Harness 对可调用工具的统一语义说明，包含工具用途、适用场景、禁用场景、参数约束、风险级别和审计分类。第一版风险级别采用低、中、高、危险四级。
_Avoid_: 工具列表, 工具配置, Tool Registry

**工具意图**:
工具目录的主分组方式，按玩家想达成的目标组织工具，而不是按底层通道或风险等级组织工具。第一版包含改变世界、通知展示、查询世界、查询知识和系统信息五类。
_Avoid_: 工具类别, 工具分组, Capability

**提示约束**:
运行时 Harness 第一阶段的工具执行边界：通过工具说明、系统提示和反馈信息引导 MCBE Chat Agent 避免误用工具，但暂不在代码中硬拦截工具执行。
_Avoid_: 权限拦截, 硬策略, 工具权限

**工具提示**:
MCBE Chat Agent 在选择和调用工具前可见的运行时 Harness 指引。第一版由按工具意图组织的简短决策树和工具卡片构成，工具卡片包含适用场景、禁用场景和参数约束。
_Avoid_: Tool Prompt, 工具说明, Prompt 模板

**反馈建议**:
反馈闭环产出的可执行改进建议，用来指出哪些工具提示、工具目录条目、测试或运行时检查需要调整。第一版由 CLI 聚合最近 N 条工具审计结果，并使用默认 provider 生成建议，不自动修改代码或配置。
_Avoid_: 自动优化, 自我修复, 建议报告
