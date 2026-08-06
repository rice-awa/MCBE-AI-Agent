# Addon / TypeScript 开发规范

## 适用范围

本目录覆盖 [`MCBE-AI-Agent-addon/`](../../../MCBE-AI-Agent-addon/)：Minecraft Bedrock Script API 的启动、Python bridge、能力实现、游戏内 UI、DynamicProperty 持久化和 Vitest 测试。Python Gateway 与它共享的身份、协议和下行约束见 [`../guides/cross-layer-thinking-guide.md`](../guides/cross-layer-thinking-guide.md)。

## 开发前检查

- 阅读 [`docs/addon-bridge-protocol.md`](../../../docs/addon-bridge-protocol.md)，确认 mcbews v1 的消息 ID、分片格式和回传方向。
- 阅读 [`MCBE-AI-Agent-addon/README.md`](../../../MCBE-AI-Agent-addon/README.md)，确认 Node.js 20+、pnpm 9 和构建/打包命令。
- 判断改动属于 bridge、能力、UI 状态还是启动生命周期，并只加载对应指南。
- 涉及 Python 响应或玩家 UI 时，明确 `player_name`、request id 和 chunk id 的来源与目标。

## 指南索引

| 指南 | 内容 |
|---|---|
| [目录与模块边界](./directory-structure.md) | 脚本、能力、UI、测试和生成目录 |
| [Bridge 与协议](./bridge-protocol.md) | mcbews v1、请求路由、响应分片与重组 |
| [状态、测试与构建](./state-testing-and-build.md) | 玩家 UI 状态、DynamicProperty、Vitest 和构建检查 |

## 完成前检查

```bash
cd MCBE-AI-Agent-addon
pnpm test
```

涉及类型、打包或 lint 时再运行 `pnpm build`、`pnpm lint`；协议或玩家路由改动要同时运行 Python 相邻测试。
