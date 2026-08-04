# MCBE AI Agent Addon

MCBE AI Agent 的 Minecraft Bedrock 侧组件：提供脚本桥接（Script Bridge）与游戏内聊天 UI，通过 WebSocket 与 Python 服务端（`/wsserver`）通信。

完整的系统说明、安装与使用文档见仓库根目录 [README](../README.md) 与 [docs/addon-bridge-protocol.md](../docs/addon-bridge-protocol.md)。

## 功能

- **脚本桥接**：游戏内 `AGENT` 命令与 Python 服务端之间的双向消息桥（线协议 `mcbews v1`：`mcbews:bridge_req` / `mcbews:text_resp` / `MCBEWS|*`）
- **游戏内 UI**：聊天面板、DDUi 消息展示与命令输入

## 构建与部署

前置：Node.js 20+ 与 pnpm 9。

```bash
pnpm install

# 构建（生产模式会去除 dev: 标签）
pnpm build:production

# 打包为 .mcaddon
pnpm mcaddon:production

# 本地部署到 Minecraft 开发目录（需要 .env 配置）
pnpm local-deploy
```

`local-deploy` 支持 `--watch` 模式监听变更自动编译部署。

## 版本

Addon 版本与 Python 服务端统一，以仓库根 `_version.py` 为唯一真源，由 `scripts/sync-version.sh` 同步。
