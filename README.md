# MCBE AI Agent v2.5.0

基于 **PydanticAI** 的 Minecraft Bedrock Edition AI 聊天机器人服务器。这是 [MCBE WebSocket GPT](https://github.com/rice-awa/MCBE_WebSocket_gpt) 项目的完全重构版本：现代化异步架构，WebSocket 连接与 LLM 请求完全解耦，支持多 LLM 提供商、多人会话隔离、Addon 桥接与完整的游戏内命令系统。

## 核心特性

### 🚀 现代化架构
- **异步非阻塞**: WebSocket 通信与 LLM 请求完全分离，LLM 延迟不影响 MC 连接
- **消息队列**: `asyncio.Queue` 生产者-消费者模式，`MessageBroker` 解耦 WS 与 Agent
- **类型安全**: 全面使用 Pydantic 数据验证
- **结构化日志**: 基于 structlog 的现代日志系统，支持原始报文与 LLM 日志开关

### 🤖 AI Agent 能力
- **PydanticAI 框架**: 类型安全的 AI Agent 实现
- **流式响应**: 实时流式输出，按完整句子发送
- **Agent Tools**: 内置 Minecraft 命令执行、MCWiki 搜索、Addon 能力（玩家快照 / 背包 / 实体 / 方块操作）等工具
- **MCP 扩展**: 支持通过 MCP (Model Context Protocol) 接入外部工具服务器，运行时热重载
- **Runtime Harness**: 高风险工具审批/拒绝、命令回退策略、隐私友好审计
- **模型预热**: 启动时自动预热 LLM 模型，提高首次响应速度
- **对话管理**: 多对话新建/切换/保存/恢复，上下文开关，历史自动压缩
- **动态系统提示词**: 根据玩家信息与模板动态调整

### 🔌 多 LLM 支持（openai兼容格式）
- **DeepSeek**: `deepseek-v4-flash`（默认）
- **OpenAI**: `gpt-5.6` 等模型
- **Anthropic**: `claude-sonnet-5`
- **Ollama**: 本地模型（如 `llama3`）

### 🎮 用户友好
- **多人会话隔离**: 同一 `/wsserver` 连接下按玩家隔离历史、上下文、模型、模板和变量，避免串扰
- **实时切换模型**: 游戏内 `切换模型` 命令动态切换 LLM
- **AI 聊天广播**: 私聊回复可切换为全服广播或指定玩家广播
- **JWT 认证**: 安全的令牌认证机制
- **ScriptEvent 支持**: 支持 scriptevent 发送方式
- **Addon UI 面板**: 游戏内聊天面板（命令方块触发）

## 项目结构

```
MCBE-AI-Agent/
├── cli.py                  # 应用入口与 CLI 工具（serve/init/info/trace/runtime-harness 等）
├── config/                 # Pydantic Settings、日志、脱敏
├── core/                   # MessageBroker 队列、会话、对话压缩/存储
├── models/                 # 常量与 Pydantic 模型
├── services/
│   ├── agent/             # PydanticAI Agent、Worker、Tools、Provider 注册表、Runtime Harness、Trace
│   ├── gateway/           # SDK 适配层：HostGatewayServer、Hook、命令、出站桥
│   ├── auth/              # 认证服务
│   └── addon/             # Addon 相关
├── web/trace/              # Agent Trace 静态审计工作台（由 trace serve 托管）
├── MCBE-AI-Agent-addon/    # Minecraft 行为包 + 资源包工程
├── tests/                  # 测试用例
├── docs/                   # 协议、部署、开发文档
├── data/                   # 对话历史、token 统计等运行数据
├── _version.py             # 版本唯一真源
└── config.example.json     # 配置模板
```

## 快速开始

### 1. 准备环境（推荐虚拟环境）

**Windows:**
```powershell
python -m venv venv
.\venv\Scripts\activate
```

**Linux/macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
```

**Termux (Android):** 见 [docs/termux.md](docs/termux.md)。

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

宿主依赖固定为 `mcbe-ws-sdk>=0.2.1,<0.3.0`。该约束对应包含 MCBEWS/1 契约的 SDK wheel；
发布/合入顺序必须是先在 SDK 仓库发布并验证 `v0.2.1` PyPI artifact，再安装或合并 Host 与
产品 Addon。开发时若 SDK 尚未出现在 PyPI，请在 SDK 仓库本地构建 wheel 后安装到隔离 venv，
不要把 nested checkout 以 editable 方式当作发布验证。

### 3. 初始化配置

```bash
python cli.py init
```

这会创建两个本地配置文件（均不提交到 Git）：

- `.env`：只保存密钥、密码等敏感内容。
- `config.json`：普通应用配置，模板来自 `config.example.json`。

先编辑 `.env` 填入密钥：

```env
SECRET_KEY=your-secret-key
WEBSOCKET_PASSWORD=your-password
DEEPSEEK_API_KEY=your-api-key-here
```

再按需编辑 `config.json`。JSON 字符串可以使用 `${VAR}` 引用 `.env` 或进程环境变量，例如：

```json
{
  "providers": {
    "deepseek": {
      "api_key": "${DEEPSEEK_API_KEY}",
      "base_url": "https://api.deepseek.com",
      "model": "deepseek-v4-flash"
    }
  }
}
```

如果 `${VAR}` 指向的变量缺失或为空，服务启动会失败并显示对应 JSON 路径和变量名。

### 4. 查看配置信息

```bash
python cli.py info
```

### 5. 测试 LLM 连接

```bash
python cli.py test-provider deepseek
```

### 6. 启动服务器

```bash
python cli.py serve
```

### 开发模式

开发模式适用于本地开发和调试，启用后跳过身份验证。

```bash
python cli.py serve --dev
```

或在 `config.json` 中设置 `"dev_mode": true`。

**⚠️ 安全警告**: 开发模式**仅用于本地开发和调试**，**切勿在生产环境启用**——启用时任何人连接服务器都会自动通过认证。

## 游戏内使用

### 1. 连接服务器

> ⚠️ 连接前请在游戏设置 → 通用中**启用 WebSocket并关闭需要加密的 Websocket选项**。本项目服务端默认使用未加密的 `ws://` 连接，开启加密后 MCBE 会拒绝连接。

在 Minecraft 聊天框输入：

```
/wsserver <服务器IP>:8080
```

### 2. 登录认证

```
#登录 123456
```

### 3. 开始聊天

```
AGENT 聊天 你好，请介绍一下自己
```

### 4. 命令大全

所有命令均可通过游戏内 `帮助` 查看。命令前缀与别名定义见 `config.example.json` 的 `minecraft.commands`：

| 命令 | 说明 | 用法 |
|------|------|------|
| `#登录 <密码>` | 用户认证 | `<密码>` |
| `AGENT 聊天 <内容>` | 与 AI 对话 | `<内容>` |
| `AGENT 脚本 <内容>` | 使用 ScriptEvent 发送 | `<内容>` |
| `AGENT 保存` | 保存当前对话历史 | - |
| `AGENT 对话 <子命令>` | 管理对话 | `<new/switch/clear/status/list/save/restore>` |
| `AGENT 连续模式 <开启/关闭/状态>` | 无需前缀自动触发 AI 聊天 | `<开启\|关闭\|状态>` |
| `AGENT 上下文 <启用/关闭/状态>` | 管理上下文开关 | `<启用\|关闭\|状态>` |
| `AGENT 模板 <模板名/list>` | 切换提示词模板 | `<模板名/list>` |
| `AGENT 设置 <子命令>` | 变量 / 别名管理 | `<变量/别名> <子命令>` |
| `AGENT MCP <list/status/reload>` | MCP 服务器管理 | `<list/status/reload>` |
| `AGENT 广播 <子命令>` | AI 聊天广播策略管理 | `<状态\|关闭\|全服 开启\|关闭\|玩家 <名> 开启\|关闭>` |
| `AGENT 同意 [id\|对话\|永远]` | 同意待审批高风险工具 | `[approval_id\|对话\|永远]` |
| `AGENT 拒绝 [id\|对话\|永远]` | 拒绝待审批高风险工具 | `[approval_id\|对话\|永远]` |
| `运行命令 <MC命令>` | 执行 Minecraft 命令 | `<命令>` |
| `切换模型 <provider>` | 切换 LLM 提供商 | `<provider>` |
| `帮助` | 显示帮助信息 | - |

命令示例：

```
AGENT 对话 new 建筑规划     # 新建并切换到一个对话
AGENT 对话 switch default  # 切换到指定对话
AGENT 对话 clear           # 清除当前对话历史
AGENT 对话 list            # 查看当前连接内的对话
AGENT 上下文 启用          # 启用携带当前对话历史
AGENT 广播 全服 开启       # 开启 AI 全服广播
AGENT 广播 玩家 <名> 开启  # 指定玩家开启广播
切换模型 openai            # 切换到 OpenAI
运行命令 time set day      # 执行游戏命令
```

### 多人会话说明

MCBE 世界通常只通过 `/wsserver` 建立一条 WebSocket 连接，所有玩家的聊天框命令和 Addon UI 消息复用这条连接。后端以 `(connection_id, player_name)` 区分真实玩家会话，并在玩家内用 `conversation_id` 区分不同对话：

- 玩家 A 和玩家 B 的对话历史互不读取；同一玩家的不同对话也互不读取。
- `AGENT 对话` 负责新建、切换、清除、保存和恢复对话；`AGENT 上下文` 只控制是否携带历史。
- `切换模型`、模板和变量设置只影响发起命令的玩家。
- Agent Worker 对同一玩家串行处理，不同玩家请求可并发执行。
- UI 响应同步使用当前消息的真实 `player_name`，避免写入其他玩家面板。

## 配置

### `config.json` 普通配置

普通应用配置写入 `config.json`（模板 `config.example.json`）。常用配置路径：

| 配置路径 | 说明 | 默认值 |
|--------|------|--------|
| `server.host` / `server.port` | 服务器地址 / 端口 | `0.0.0.0` / `8080` |
| `auth.*` | JWT 密钥、过期时间、默认密码 | - |
| `providers.default` | 默认 LLM | `deepseek` |
| `providers.<name>.model` / `.base_url` / `.api_key` | Provider 配置；api_key 通常写 `${DEEPSEEK_API_KEY}` 等 | 取决于 provider |
| `agent.system_prompt` | 系统提示词 | 见 `config.example.json` |
| `agent.runtime_harness.*` | 高风险工具审批/拒绝与审计 | 默认启用 |
| `agent.agent_trace_*` | Agent Trace journal 与只读 API | 默认关闭 |
| `queue.llm_worker_count` / `queue.max_size` | Worker 数 / 队列大小 | `2` / `100` |
| `minecraft.commands` | 游戏内命令定义（前缀/类型/别名/用法） | 见 `config.example.json` |
| `minecraft.ai_broadcast_default` | 新连接默认 AI 全服广播 | `true` |
| `mcp.enabled` / `mcp.servers` | MCP 功能开关与服务器配置 | `false` / `{}` |
| `flow_control.*` | 出站长文本分片流控（tellraw/scriptevent/text_resp）；`command_line_byte_budget=461` 是实测预算 | 见 `config.example.json` |
| `addon.protocol.*` | deprecated/ignored 桥协议文档镜像；运行时值始终来自 SDK MCBEWS/1 manifest | - |
| `logging.*` | 日志级别、文件、原始日志开关 | `INFO`；raw log 默认 `false` |
| `storage.*` | 对话历史与 token 统计路径 | `data/` 下 |
| `dev_mode` | 开发模式（跳过身份验证） | `false` |

### `.env` 敏感配置

仅保留敏感变量：`SECRET_KEY`、`WEBSOCKET_PASSWORD`、`DEEPSEEK_API_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`。不要提交 `.env` 到版本控制。

### 代码中访问配置

```python
from config import get_settings

settings = get_settings()
print(settings.default_provider)
print(settings.list_available_providers())
```

## CLI 工具

```bash
python cli.py init                 # 初始化配置
python cli.py info                 # 查看配置信息
python cli.py test-provider <name> # 测试 LLM 连接
python cli.py serve                # 启动服务器（--dev 开发模式）
```

### Runtime Harness 审计

Runtime Harness 在 Agent 工具调用时写入隐私友好的 JSONL 摘要（默认 `logs/runtime_harness_tools.jsonl`），用于定位重复失败、高风险工具调用和高耗时工具：

```bash
tail -n 20 logs/runtime_harness_tools.jsonl   # 查看审计文件
python cli.py runtime-harness analyze         # 输出文本报告
python cli.py runtime-harness analyze --recent 200
python cli.py runtime-harness analyze --json
python cli.py runtime-harness analyze --no-llm
```

- 审计不记录玩家原始消息与完整工具返回；参数按工具目录白名单预览，敏感字段脱敏。
- 默认模式使用 `providers.default` 生成 2-4 条中文改进建议；Provider 不可用时保留规则建议并回退。

### Agent Trace

完整 Agent 运行追踪写入独立的 append-only JSONL journal（默认 `logs/agent_traces.jsonl`），与 Runtime Harness 审计分离。在 `config.json` 的 `agent.agent_trace_*` 下启用：

```json
{
  "agent": {
    "agent_trace_enabled": true,
    "agent_trace_include_content": false,
    "agent_trace_path": "logs/agent_traces.jsonl",
    "agent_trace_max_records": 10000,
    "agent_trace_api_host": "127.0.0.1",
    "agent_trace_api_port": 8787
  }
}
```

```bash
python cli.py trace serve        # 启动本地只读 API + 静态工作台，open http://127.0.0.1:8787
python cli.py trace list --recent 20
python cli.py trace list --status failed --player alex
python cli.py trace show <trace_id> [--json]
python cli.py trace health
```

- API 为**本地只读**（GET），不修改 journal；完整正文仅在 `agent_trace_include_content=true` 时持久化（opt-in）。
- 静态审计工作台位于 `web/trace/`，无构建步骤。
- journal 轮转会保留最近 N 条记录，面向本地/开发体量。

## Addon Bridge

仓库内置一条 Python ↔ Addon ↔ 游戏桥接链路，让 Agent 通过 Addon 获取稳定的游戏内上下文（玩家快照、背包、实体、方块操作等）。线协议为 **mcbews v1**（`mcbews:bridge_req` / `mcbews:text_resp` / `MCBEWS|*`），由 `mcbe-ws-sdk` 拥有；完整协议、构建与调试步骤见 [docs/addon-bridge-protocol.md](docs/addon-bridge-protocol.md)。

快速上手：

```bash
python cli.py serve --dev        # 1. 启动 Python 服务（开发模式跳过登录）
cd MCBE-AI-Agent-addon
npm install && npm test && npm run build && npm run local-deploy  # 2. 构建并部署 Addon
```

进入世界后确认模拟玩家 `MCBEWS_BRIDGE` 已生成，`/wsserver <IP>:8080` 连接后即可对话触发 Addon 能力。

> **⚠️ 使用 Addon 前置条件**：启用本 Addon 必须在世界设置中开启 **实验性 API**（创建世界时"实验性玩法"或世界设置的"实验性内容"里勾选 **实验性 API / Beta API**）。Addon 依赖实验性游戏测试框架（`@minecraft/server-gametest` 等），未开启时行为包不会加载，`MCBEWS_BRIDGE` 模拟玩家也不会生成。

### MCBEWS/1 契约要点

SDK manifest 将兼容线 `MCBEWS/1` 与四个独立的 schema/persistence 轴分别命名，不能把它们
混称为一个 v1/v2：capability request schema `2`、session schema `1`、text response framing
`1`，以及 Addon DynamicProperty 的 DDUI persistence `2`。`MCBEWS|UI_CHAT` 的 `cid` 会随真实
`player_name` 传入对应 `ChatRequest`；`mcbews:text_resp` 的 `cid`/`t` 在相关帧保持一致，
`u={i,o}` 只出现在完成帧。长 session 响应采用单帧原子策略，超出实测预算时返回
`SESSION_RESPONSE_TOO_LARGE` 结构化错误，不发送碎 JSON。

能力广告中的 `multiblock_placement=command_fallback` 表示宿主可在能力结果允许时走受审批的
原生命令回退路径，不表示 Addon handler 已无条件完成多格放置。`addon.protocol.*` 仅是
deprecated/ignored 配置镜像，任何旧 `mcbeai` 值都不能改变运行时 MCBEWS/1 wire。

### 发布顺序与真实世界 smoke

发布者应先合并并发布 SDK `v0.2.1`、确认 PyPI wheel artifact 与 wheel-installed contract
通过，再合并/发布 Host 和产品 Addon。本仓库当前文档只准备 release gate，不宣称该版本已经
发布，也不自动创建 tag 或上传 PyPI。

发布前在真实 MCBE 世界至少检查：`sender` 与 ScriptEvent `sourceType` 的实际值；可信
`MCBEWS_BRIDGE` ToolPlayer 与业务 owner 分离；两玩家×两 conversation 的 CJK/emoji 往返及
`tell @s` 的实测 `461` 字节预算；长 session list/saved 的单帧或结构化超限错误；approval
归属、伪造 owner、断线后的 pending/task 清理。完整步骤见
[Addon Bridge Protocol](docs/addon-bridge-protocol.md)。

## 部署

### Termux（Android）

完整的 Termux 部署指南（依赖安装、特定配置、后台运行、常见问题与优化建议）见 [docs/termux.md](docs/termux.md)。

### 其他平台

项目为纯 Python 服务 + Minecraft Addon，支持 Windows / Linux / macOS。部署三步：

1. 初始化配置：`python cli.py init` 并填写 `.env` / `config.json`
2. 安装依赖：`pip install -r requirements.txt`
3. 启动：`python cli.py serve`

生产环境建议使用反向代理 + HTTPS 接入 WebSocket（见下节安全建议）。

## 故障排查

### 1. 连接失败

```bash
# 检查端口监听
netstat -tulpn | grep 8080

# 测试本地连接
curl http://localhost:8080/health
```

### 2. LLM 请求失败

```bash
python cli.py test-provider deepseek   # 测试提供商连接
tail -f logs/MCBE-AI-Agent.log        # 查看日志
```

### 3. 内存不足
```json
{
  "queue": {
    "llm_worker_count": 1,
    "max_size": 50
  }
}
```

### 4. Python 依赖问题

```bash
pip install --prefer-binary -r requirements.txt   # 使用预编译包
pip uninstall -r requirements.txt -y && pip install -r requirements.txt  # 重装
```

更多 Termux 专项排查见 [docs/termux.md](docs/termux.md)。

## 安全建议

1. **生产环境**: 更改 `SECRET_KEY` 为强随机值；设置复杂的 `WEBSOCKET_PASSWORD`；通过反向代理启用 HTTPS。
2. **API 密钥管理**: 不要提交 `.env` 到版本控制；密钥只写入 `.env`，`config.json` 用 `${VAR}` 引用。
3. **Runtime Harness**: 高风险命令默认要求玩家审批，`hard_deny_*` 列表内置 `op`/`stop` 等敏感命令根；新增命令工具时接入审批策略，不要绕过。
4. **开发模式**: 仅本地调试使用，生产环境禁用。

## 技术栈与致谢

- **Python 3.11+** / **PydanticAI** / **Pydantic** / **WebSockets** / **httpx** / **PyJWT** / **structlog** / **Click**
- **mcbe-ws-sdk**: Minecraft Bedrock WebSocket SDK（mcbews v1 线协议、出站流控与分片；Host 约束 `>=0.2.1,<0.3.0`）
- 原项目: [rice-awa/MCBE_WebSocket_gpt](https://github.com/rice-awa/MCBE_WebSocket_gpt)
- PydanticAI: [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai)
- Termux: [termux/termux-app](https://github.com/termux/termux-app)

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

[MIT](./LICENSE)

---

**版本**: 2.5.0
**最后更新**: 2026-08-04
**架构**: 现代化异步 + PydanticAI
**平台支持**: Windows, Linux, macOS, Termux (Android)
