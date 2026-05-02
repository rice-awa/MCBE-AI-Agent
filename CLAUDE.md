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
Minecraft Client ←→ WebSocket Server ←→ Message Broker ←→ Agent Worker (PydanticAI)
                              ↑                    ↑
                         Connection          Request/Response
                         Manager              Queues
```

核心设计：
- **非阻塞**: WebSocket 处理与 LLM 请求完全分离
- **消息队列**: asyncio.Queue 实现生产者-消费者模式
- **类型安全**: Pydantic 进行数据验证和配置管理
- **多人会话隔离**: MCBE 单个 `/wsserver` 连接可承载多个玩家，历史、锁、上下文、模型、模板和变量均按 `(connection_id, player_name)` 分桶
- **统一流控**: 所有下行长文本统一经过 FlowControlMiddleware 分片，避免 MCBE 拒绝超长 commandRequest

## Flow Control Middleware

`services/websocket/flow_control.py` 是出站消息的统一流控中间件，负责把长文本拆分为 MCBE 可安全接受的命令负载。

- `chunk_tellraw()`：拆分游戏内可见的 `tellraw` 文本。
- `chunk_scriptevent()`：拆分 Addon/脚本事件 `scriptevent` 载荷。
- `chunk_ai_response()`：生成 `mcbeai:ai_resp` 分片事件，客户端按 `id/i/n/p/r/c` 元数据重组。
- `chunk_raw_command()`：只包装原始命令，不做语义不安全的截断；超长时抛出 `ValueError`。
- `chunk_delay_for()`：统一提供 tellraw、scriptevent、AI 响应等场景的分片发送间隔。

流控同时受 MCBE `commandLine` 461 字节实测安全上限、`MAX_CHUNK_CONTENT_LENGTH` 字符上限和 `CHUNK_SENTENCE_MODE` 语义分句策略约束。新增下行发送路径时应复用该中间件，不要在调用点重复实现分片逻辑。

## Multiplayer Session Isolation

MCBE 的 `/wsserver` 在一个世界内通常只有一条 WebSocket 连接，多名玩家会共享同一个 `connection_id`。因此所有玩家相关状态必须显式携带并使用当前消息的 `sender` / `player_name`，不能把 `ConnectionState.player_name` 当作会话身份来源。

- `MessageBroker` 的对话历史和处理锁按 `(connection_id, player_name)` 隔离；同玩家请求串行，不同玩家可并行。
- `ConnectionState.get_player_session(player_name)` 管理每名玩家独立的 `context_enabled`、`current_provider`、`current_template` 和 `custom_variables`。
- `PromptManager` 的模板和变量按玩家分桶；旧的 connection 级 API 仅作为匿名玩家桶兼容层。
- 注销连接时需要清理该连接下的所有玩家会话，优先使用 `clear_connection_sessions()`。
- 新增聊天、UI、上下文、模板、变量或切换模型路径时，必须从本次事件传递 `player_name`，避免多人串上下文或响应推送到错误玩家。

参考修复说明：`claude_md/fix/MULTIPLAYER_SESSION_FIX.md`。

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
| `cli.py` | 应用入口，CLI 命令组 |
| `config/settings.py` | Pydantic Settings 配置管理 |
| `core/queue.py` | MessageBroker 消息队列；对话历史和会话锁按 `(connection_id, player_name)` 隔离 |
| `core/conversation.py` | 对话历史压缩、保存和恢复，按玩家会话桶读写 |
| `services/agent/core.py` | PydanticAI Agent 核心 |
| `services/agent/providers.py` | LLM Provider 注册表 |
| `services/agent/prompt.py` | 系统提示词、模板和变量管理；模板/变量按玩家会话隔离 |
| `services/agent/worker.py` | Agent Worker，消费队列请求；按玩家锁串行化同一玩家请求 |
| `services/websocket/server.py` | WebSocket 服务器；每条玩家消息必须直传当前 `sender` |
| `services/websocket/connection.py` | 连接管理；`PlayerSession` 保存玩家级上下文、模型、模板和变量 |
| `services/websocket/flow_control.py` | 统一流控中间件，负责出站长文本分片与字节安全校验 |
| `services/agent/tools.py` | Agent Tools (MC命令执行、MCWiki搜索) |

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
| `切换模型 <提供商>` | 切换 LLM 提供商 |
| `运行命令 <MC命令>` | 执行 Minecraft 命令 |
| `帮助` | 显示帮助信息 |

## Configuration

环境变量通过 `.env` 文件管理，参考 `.env.example`：
- `SECRET_KEY` - JWT 密钥
- `WEBSOCKET_PASSWORD` - 连接密码
- `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` - LLM API Keys
- `DEFAULT_PROVIDER` - 默认 LLM 提供商
- `LLM_WORKER_COUNT` - Agent Worker 数量
- `MAX_CHUNK_CONTENT_LENGTH` - 单个下行分片的内容字符上限，默认 `400`
- `CHUNK_SENTENCE_MODE` - 是否优先按句子边界进行语义分片，默认 `true`
- `LOG_LEVEL` - 日志级别

## Minecraft Connection

```
/wsserver <服务器IP>:8080
#登录 123456
AGENT 聊天 你好
```
