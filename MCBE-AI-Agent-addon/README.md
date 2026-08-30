# MCBE AI Agent Addon

MCBE AI Agent 的 Minecraft Bedrock 侧组件：提供脚本桥接（Script Bridge）与游戏内聊天 UI，通过 WebSocket 与 Python 服务端（`/wsserver`）通信。

完整的系统说明、安装与使用文档见仓库根目录 [README](../README.md) 与 [docs/addon-bridge-protocol.md](../docs/addon-bridge-protocol.md)。

## 功能

- **脚本桥接**：游戏内 `AGENT` 命令与 Python 服务端之间的双向消息桥（线协议 `mcbews v1`：`mcbews:bridge_req` / `mcbews:text_resp` / `MCBEWS|*`）
- **游戏内 UI**：聊天面板、DDUi 消息展示与命令输入

## MCBEWS/1 契约

协议资产由 SDK `0.2.1` wheel 提供；本 Addon 的 `scripts/bridge/protocol.generated.ts`
是同步后的投影。兼容线为 `MCBEWS/1`，另有四个独立的 schema/persistence 轴：capability
request schema `2`、session schema `1`、text response framing `1`、DDUI persistence `2`。其中 persistence `2` 是玩家
DynamicProperty 的 per-conversation 状态格式，不宣称当前运行时已提供官方 DDUI API。

UI Chat 始终携带真实 `player_name` 与当前 `cid`；text response 的 `cid`/标题在同一响应中保持
一致，token usage `{i,o}` 只在完成帧出现。Session response 是单帧原子 JSON，超出实测
`461` 字节命令预算时返回结构化 `SESSION_RESPONSE_TOO_LARGE`，不会等待碎 JSON。

能力广告中的 `multiblock_placement=command_fallback` 只表示宿主可以在安全策略与审批允许时
走原生命令回退；它不是 Addon 直接成功完成多格方块的声明。配置里的 `addon.protocol.*`
是 deprecated/ignored 镜像，运行时 wire 值由 SDK manifest 决定。

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

发布顺序：先在 SDK 仓库合并并发布 `v0.2.1`，验证 PyPI wheel artifact 与 wheel-installed
contract，再合并/发布 Host 和产品 Addon。本仓库不在此处宣称 SDK 已发布。

发布前真实 MCBE smoke 需覆盖：`sender`/ScriptEvent source、可信 `MCBEWS_BRIDGE` 与业务
owner 分离、CJK+emoji 与 `461` 字节预算、长 session 的单帧/超限错误、approval owner/批次
以及断线 task cleanup。详见 [根协议文档](../docs/addon-bridge-protocol.md)。

## 版本

Addon 版本与 Python 服务端统一，以仓库根 `_version.py` 为唯一真源，由 `scripts/sync-version.sh` 同步。
