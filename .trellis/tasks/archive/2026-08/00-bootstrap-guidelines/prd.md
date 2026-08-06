# Trellis 项目规范初始化

## 目标

根据 MCBE-AI-Agent 当前源码、测试和既有协作文档，建立可供后续 Trellis 任务加载的项目化 `.trellis/spec/` 规范。规范描述现状和已验证的本地模式，不引入与本项目无关的 ORM、前端框架或 Trellis 自身模板规则。

## 范围

- Python 服务端：`config/`、`core/`、`models/`、`services/`、`tests/`。
- Minecraft TypeScript Addon：`MCBE-AI-Agent-addon/scripts/`、`tests/`、构建和打包配置。
- 跨层契约：玩家身份、消息/队列、mcbews v1、分片、错误、配置、Trace 和工具审计。
- 不修改产品源代码，不提交 `.env`、`config.json`、日志、数据和构建产物。

## 已完成事项

- [x] 将原始 backend 占位模板改写为基于源码的中文指南。
- [x] 删除不适用的数据库/ORM spec。
- [x] 新增 Python 运行时架构、会话身份、配置、错误、日志与质量指南。
- [x] 新增 Addon 目录、bridge 协议、状态/测试/构建指南。
- [x] 重写跨层与复用指南，移除 Trellis 模板项目的无关内容。
- [x] 为每个层级索引补充开发前检查和完成前检查。
- [x] 检查 spec 没有占位符，索引与最终文件集合一致。

## 关键事实来源

- [`CONTEXT.md`](../../../CONTEXT.md)：项目术语边界。
- [`CLAUDE.md`](../../../CLAUDE.md)、[`AGENTS.md`](../../../AGENTS.md)：SDK、玩家隔离、流控和协作约束。
- `core/queue.py`、`core/session.py`、`services/agent/worker.py`、`services/gateway/`：Python 运行时边界。
- `MCBE-AI-Agent-addon/scripts/bridge/`、`scripts/ui/`：Addon 协议和状态实现。
- `tests/` 与 `MCBE-AI-Agent-addon/tests/`：异步、配置、脱敏、协议和 UI 状态测试模式。

## 验收标准

- `.trellis/spec/backend/index.md` 与 `.trellis/spec/addon/index.md` 均能导航到实际存在的文件。
- 每条重要规则都有源码、测试或项目文档路径作为依据。
- `.trellis/spec/` 不包含 `To be filled`、模板占位符或与本仓库无关的 Trellis 内部示例。
- 后续任务能够据此识别：玩家必须显式传递 `player_name`、长文本必须走 SDK delivery、运行时协议只使用 mcbews v1。
