# Backend / Python 服务开发规范

## 适用范围

本目录覆盖仓库根目录的 Python 服务端：配置、消息队列、会话与对话、Agent、Gateway、认证、运行时 Harness 和 Trace。Addon 的 TypeScript 代码见 [`../addon/index.md`](../addon/index.md)，Python 与 Addon 之间的共同约束见 [`../guides/cross-layer-thinking-guide.md`](../guides/cross-layer-thinking-guide.md)。

## 开发前检查

开始涉及服务端的任务前，先阅读：

1. [`CONTEXT.md`](../../../CONTEXT.md)，对齐“MCBE Chat Agent”“运行时 Harness”“工具审计”等项目术语。
2. [`CLAUDE.md`](../../../CLAUDE.md)，确认 SDK、多人会话和出站流控边界。
3. 与任务相关的现有协议或开发文档，尤其是 [`docs/addon-bridge-protocol.md`](../../../docs/addon-bridge-protocol.md)。
4. 本索引中与改动路径对应的指南。

涉及玩家消息、会话、上下文、模板、变量、模型切换或下行消息时，先标出当前事件的 `sender` / `player_name`；涉及长文本下行时，先确定复用的 `BrokerResponseBridge` 或 SDK delivery。

## 指南索引

| 指南 | 内容 |
|---|---|
| [目录与模块边界](./directory-structure.md) | Python 模块的职责、**工具目录契约中心**、测试和文档位置 |
| [运行时架构](./runtime-architecture.md) | WebSocket、队列、Worker、Agent 与 Gateway 的异步边界 |
| [会话与身份](./session-and-identity.md) | 玩家隔离、对话键、锁和 trace 身份的传递 |
| [配置与敏感信息](./configuration-and-secrets.md) | `config.json`、`.env`、Pydantic Settings 和新增配置字段 |
| [错误处理](./error-handling.md) | 异常层级、边界转换、取消与外部状态未知 |
| [日志与可观察性](./logging-and-observability.md) | structlog、脱敏、工具审计和 Trace |
| [质量与测试](./quality-guidelines.md) | Python 风格、类型、pytest 和交付检查 |

## 完成前检查

- 运行与改动范围相邻的 pytest；默认测试不应依赖真实 LLM、Minecraft 世界或外部网络。
- 检查所有玩家相关路径仍显式传递 `player_name`，并检查同一连接下两名玩家不会共享会话状态。
- 检查新增的长文本下行仍经过现有 bridge/delivery，而不是复制分片逻辑。
- 检查日志、Trace 和工具审计没有写入密钥、完整凭据或未经允许的正文。
- 若修改配置字段，同时更新 `config.example.json`、Settings 模型和相邻配置测试。
