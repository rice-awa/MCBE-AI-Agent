# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MCBE AI Agent 是一个 Minecraft Bedrock Edition AI 聊天机器人服务器，基于 PydanticAI 框架构建，采用现代化异步架构。

- **Python**: 3.11+
- **框架**: PydanticAI >= 1.0.0, Pydantic >= 2.0
- **主要依赖**: websockets, httpx, PyJWT, structlog, click, pydantic-settings

## Common Commands

```bash
# 初始化配置
python cli.py init

# 测试 LLM 连接
python cli.py test-provider deepseek

# 启动服务器
python cli.py serve

# 查看配置信息
python cli.py info
```

## Architecture

```
Minecraft Client
      │ /wsserver
      ▼
McbeServerFacade (mcbe-ws-sdk)
  ├─ HostConnectionHook → MessageBroker / 鉴权 / 命令
  ├─ HostResponseSink + BrokerResponseBridge → 出站 tellraw / scriptevent / mcbews:text_resp
  └─ AddonBridgeService (mcbews v1)
      ▼
Message Broker ←→ Agent Worker (PydanticAI)
```

核心设计：
- **SDK 传输**: 运行时 WebSocket / 分片 / 桥协议由 `mcbe-ws-sdk` 拥有；宿主只实现 `services/gateway/` 适配层
- **非阻塞**: 钩子内禁止 await LLM / 桥 RTT；一律 `asyncio.create_task`
- **消息队列**: asyncio.Queue 生产者-消费者；LLM 与连接完全分离
- **类型安全**: Pydantic 进行数据验证和配置管理
- **多人会话隔离**: 身份只用 `event.sender` / 显式 `player_name`；历史、锁、上下文按 `(connection_id, player_name)` 分桶（`HostSessionStore`）
- **统一流控**: 出站长文本走 SDK `FlowControlSettings` / `McbeOutboundDelivery` / `McbewsV1Delivery`
- **线协议**: 仅 **mcbews v1**（`mcbews:bridge_req` / `mcbews:text_resp` / `MCBEWS|*`）；禁止运行时 `mcbeai:*`

## Flow Control (via mcbe-ws-sdk)

出站长文本由 SDK `FlowControlSettings` + `McbeOutboundDelivery` 分片，避免 MCBE 拒绝超长 `commandRequest`。

- tellraw / scriptevent 分片：`McbeOutboundDelivery`
- AI / UI 文本同步：`McbewsV1Delivery` → `mcbews:text_resp`
- 宿主侧不要再手写 `commandLine` 分片

流控参数（`config.json` → 映射到 SDK）：

- `flow_control.command_line_byte_budget`（默认 461）
- `flow_control.max_chunk_content_length`（默认 400）
- `flow_control.chunk_sentence_mode`（默认 true）
- `flow_control.chunk_delays.tellraw / scriptevent / text_resp / text_resp_prelude`
  - 旧键 `ai_resp` / `ai_resp_prelude` 仍可从 JSON 读入并映射到 `text_resp*`
- `flow_control.non_stream_batch_max_chars` / `non_stream_send_delay`（宿主非流式批处理）

新增下行发送路径应走 `BrokerResponseBridge` 或 SDK delivery，不要复制分片逻辑。

## Multiplayer Session Isolation

MCBE 的 `/wsserver` 在一个世界内通常只有一条 WebSocket 连接，多名玩家会共享同一个 `connection_id`。因此所有玩家相关状态必须显式携带并使用当前消息的 `sender` / `player_name`。

- **禁止**用 SDK `ConnectionState.player_name` 做业务分支（该属性在 SDK 侧为只读，且不代表多人身份）。
- `MessageBroker` 的对话历史和处理锁按 `(connection_id, player_name)` 隔离。
- `HostSessionStore` / `HostConnectionSession.get_player_session(player_name)` 管理每名玩家的 `context_enabled`、`current_provider`、`current_template`、`custom_variables` 与 AI 广播开关。
- `PromptManager` 的模板和变量按玩家分桶。
- 断开连接时清理会话、bridge 循环、pending WS commands 与 addon client。

参考：`claude_md/fix/MULTIPLAYER_SESSION_FIX.md`、`docs/addon-bridge-protocol.md`。

## Working Guidelines

**重要**：如果用户要求添加新功能或修复 bug，**必须先使用现有工具获取相关文档**，了解最佳实践后再进行实现。

优先使用 MCP 工具获取最新文档：
- `context7` - 获取库/框架的官方文档和代码示例
- `firecrawl` - 获取网页内容、搜索信息
- `WebSearch` - 搜索最新技术和解决方案

避免盲目实现，确保代码符合框架/库的最佳实践。

## Key Files

| File | Purpose |
|------|---------|
| `cli.py` | 应用入口；组装 `HostGatewayServer` + AgentWorkers |
| `config/settings.py` | Pydantic Settings；协议默认 mcbews；`text_resp*` 流控键 |
| `core/queue.py` | MessageBroker；历史/锁按 `(connection_id, player_name)` |
| `models/constants.py` | 默认玩家键 / 对话 ID / 显示名 |
| `core/conversation.py` | 对话压缩、保存和恢复 |
| `services/gateway/server.py` | `HostGatewayServer`：SDK Facade 生命周期 |
| `services/gateway/hook.py` | `HostConnectionHook`：连接与命令路由（非阻塞） |
| `services/gateway/command_handlers.py` | 游戏内命令业务 |
| `services/gateway/broker_bridge.py` | Broker 响应 → SDK 出站 |
| `services/gateway/ws_command_runner.py` | WS `commandRequest` 关联 |
| `services/gateway/session_store.py` | 宿主会话 / 登录态 / 玩家桶 |
| `services/gateway/settings_map.py` | Settings → SDK GatewaySettings |
| `services/agent/core.py` | PydanticAI Agent 核心 |
| `services/agent/worker.py` | 消费队列；注入 SDK `AddonBridgeService` |
| `services/agent/tools.py` | Agent Tools |
| `mcbe-ws-sdk/` | 可编辑 path 依赖（gitignore；勿改源码除非另开 SDK PR） |
| `docs/addon-bridge-protocol.md` | mcbews 桥协议说明 |

## Supported LLM Providers

- DeepSeek (默认)
- OpenAI
- Anthropic
- Ollama (本地模型)

## Supported Commands (In-Game)

| Command | Description |
|---------|-------------|
| `#登录 <密码>` | 用户认证 |
| `AGENT 聊天 <消息>` | 与 AI 对话 |
| `AGENT 脚本 <消息>` | 使用 ScriptEvent 发送 |
| `AGENT 上下文 <开启/关闭/状态>` | 管理对话上下文 |
| `AGENT 同意 [id|对话|永远]` | 同意待审批工具；省略 id 智能选当前批；`对话`/`永远` 开启自动同意 |
| `AGENT 拒绝 [id|对话|永远]` | 拒绝待审批工具；`对话`/`永远` 关闭对应自动同意 |
| `切换模型 <提供商>` | 切换 LLM 提供商 |
| `运行命令 <MC命令>` | 执行 Minecraft 命令 |
| `帮助` | 显示帮助信息 |

## Configuration

配置分为普通配置和敏感配置：

- `config.json`：普通应用配置，运行 `python cli.py init` 后由 `config.example.json` 生成；不提交到 Git。
- `.env`：只保存密钥、密码等敏感内容，运行 `python cli.py init` 后由 `.env.example` 生成；不提交到 Git。

`.env` 保留的敏感项：
- `SECRET_KEY` - JWT 密钥
- `WEBSOCKET_PASSWORD` - 连接密码
- `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` - LLM API Keys

普通配置在 `config.json` 中维护，包括：
- `server.host` / `server.port`
- `auth.jwt_secret` / `auth.jwt_expiration` / `auth.jwt_algorithm` / `auth.default_password`；`jwt_secret` 和 `default_password` 通过 `${...}` 引用 `.env`
- `providers.default` 与各 provider 的 `model`、`base_url`、`api_key`；`api_key` 字段通常写 `${...}` 引用 `.env` 中的密钥，不要直接写明文密钥。
- `agent.agent_retries` / `agent.worker_http_timeout` / `agent.worker_poll_timeout` / `agent.run_command_timeout` / `agent.system_prompt` / `agent.max_history_turns` / `agent.compression_*` / `agent.stream_sentence_mode` / `agent.llm_warmup_enabled`
- `queue.llm_worker_count` / `queue.max_size`
- `flow_control.command_line_byte_budget` / `flow_control.chunk_delays.*` / `flow_control.non_stream_*` / `flow_control.max_chunk_content_length` / `flow_control.chunk_sentence_mode`
- `addon.protocol.*`（文档镜像；运行时强制 mcbews v1，旧 mcbeai 值会被忽略）
- `storage.conversations_dir` / `storage.tokens_file`
- `logging.level` / `logging.enable_file_logging` / `logging.log_dir` / `logging.files.*` / `logging.rotation_*`
- `mcp.enabled` / `mcp.servers`
- `minecraft.commands`
- `websocket.*`
- `model_metadata.*`

JSON 字符串可以通过 `${VAR}` 引用 `.env` 或进程环境变量。缺失或空值会导致启动失败并显示 JSON 路径和变量名。新增普通配置应进入 `config.json`，不要添加新的普通 `.env` 字段。

## Minecraft Connection

```
/wsserver <服务器IP>:8080
#登录 123456
AGENT 聊天 你好
```
