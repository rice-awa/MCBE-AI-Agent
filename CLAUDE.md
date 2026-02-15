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

## Key Files

| File | Purpose |
|------|---------|
| `cli.py` | 应用入口，CLI 命令组 |
| `config/settings.py` | Pydantic Settings 配置管理 |
| `core/queue.py` | MessageBroker 消息队列 |
| `services/agent/core.py` | PydanticAI Agent 核心 |
| `services/agent/providers.py` | LLM Provider 注册表 |
| `services/agent/worker.py` | Agent Worker，消费队列请求 |
| `services/websocket/server.py` | WebSocket 服务器 |
| `services/websocket/connection.py` | 连接管理 |
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
- `LOG_LEVEL` - 日志级别

## Minecraft Connection

```
/wsserver <服务器IP>:8080
#登录 123456
AGENT 聊天 你好
```
