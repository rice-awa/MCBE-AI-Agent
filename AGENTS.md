# 仓库指南

本文件为 AI Agent 在本仓库内协作开发时的统一工作说明。交流与文档统一使用中文。

## 项目概览

MCBE AI Agent 是一个 Minecraft Bedrock Edition AI 聊天机器人服务器，基于 PydanticAI 框架构建，采用现代化异步架构。

- **Python**：3.11+
- **框架**：PydanticAI >= 1.0.0，Pydantic >= 2.0
- **主要依赖**：websockets、httpx、PyJWT、structlog、click、pydantic-settings

## 项目结构与模块组织

- `config/`：配置与日志（`settings.py`、`logging.py`）。
- `models/`：数据模型（WebSocket 消息、Minecraft 协议、Agent 依赖）。
- `core/`：消息队列与事件系统。
- `services/`：核心服务（`agent/`、`websocket/`、`auth/`）。
- `storage/`：存储层（当前为占位）。
- `tests/`：测试用例目录，文件名建议使用 `test_*.py`。
- 入口：`main.py`（服务启动）、`cli.py`（命令行）。

## 架构

```text
Minecraft Client <-> WebSocket Server <-> Message Broker <-> Agent Worker (PydanticAI)
                              ^                    ^
                         Connection          Request/Response
                         Manager              Queues
```

核心设计：

- **非阻塞**：WebSocket 处理与 LLM 请求完全分离。
- **消息队列**：使用 `asyncio.Queue` 实现生产者-消费者模式。
- **类型安全**：使用 Pydantic 进行数据验证和配置管理。

## 关键文件

| 文件 | 用途 |
| --- | --- |
| `cli.py` | 应用入口，CLI 命令组 |
| `config/settings.py` | Pydantic Settings 配置管理 |
| `core/queue.py` | `MessageBroker` 消息队列 |
| `services/agent/core.py` | PydanticAI Agent 核心 |
| `services/agent/providers.py` | LLM Provider 注册表 |
| `services/agent/worker.py` | Agent Worker，消费队列请求 |
| `services/websocket/server.py` | WebSocket 服务器 |
| `services/websocket/connection.py` | 连接管理 |
| `services/agent/tools.py` | Agent Tools（MC 命令执行、MCWiki 搜索） |

## 构建、测试与开发命令

- 安装依赖：`pip install -r requirements.txt`
- 初始化配置：`python cli.py init`（生成 `.env` 并填写 API Key）。
- 查看配置：`python cli.py info`
- 测试 LLM：`python cli.py test-provider deepseek`
- 启动服务：`python cli.py serve` 或 `python main.py`
- 运行测试：`pytest`

## 代码风格与命名规范

- Python 3.11，4 空格缩进。
- 命名：模块/变量使用 `snake_case`，类使用 `PascalCase`，常量使用 `UPPER_CASE`。
- 格式与静态检查：`ruff`（行宽 100）、`mypy`（严格模式）。

## 测试规范

- 预期使用 `pytest` + `pytest-asyncio`（见 `pyproject.toml`）。
- 测试目录约定为 `tests/`，文件名建议使用 `test_*.py`。
- 新增功能或修复 bug 时，请补充对应测试。
- 如果测试无法运行，需要在交付说明中明确原因和风险。

## 支持的 LLM Provider

- DeepSeek（默认）
- OpenAI
- Anthropic
- Ollama（本地模型）

## 支持的游戏内命令

| 命令 | 说明 |
| --- | --- |
| `#登录 <密码>` | 用户认证 |
| `AGENT 聊天 <消息>` | 与 AI 对话 |
| `AGENT 脚本 <消息>` | 使用 ScriptEvent 发送 |
| `AGENT 上下文 <开启/关闭/状态>` | 管理对话上下文 |
| `切换模型 <提供商>` | 切换 LLM 提供商 |
| `运行命令 <MC命令>` | 执行 Minecraft 命令 |
| `帮助` | 显示帮助信息 |

## 配置

环境变量通过 `.env` 文件管理，参考 `.env.example`：

- `SECRET_KEY`：JWT 密钥。
- `WEBSOCKET_PASSWORD`：连接密码。
- `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`：LLM API Key。
- `DEFAULT_PROVIDER`：默认 LLM 提供商。
- `LLM_WORKER_COUNT`：Agent Worker 数量。
- `LOG_LEVEL`：日志级别。

## Minecraft 连接示例

```text
/wsserver <服务器IP>:8080
#登录 123456
AGENT 聊天 你好
```

## Git 工作树开发说明

需要隔离开发新功能、修复 bug 或执行较大实现计划时，优先使用 Git 工作树，避免污染当前工作区。

### 工作树目录选择

按以下优先级选择工作树目录：

1. 如果仓库中存在 `.worktrees/`，优先使用 `.worktrees/`。
2. 如果不存在 `.worktrees/` 但存在 `worktrees/`，使用 `worktrees/`。
3. 如果两者都不存在，检查本文件是否指定了工作树目录偏好。
4. 如果仍无约定，先询问用户再创建。

项目内工作树目录必须被 Git 忽略。创建前使用以下命令验证：

```bash
git check-ignore -q .worktrees 2>/dev/null || git check-ignore -q worktrees 2>/dev/null
```

如果目标目录未被忽略，应先将对应目录加入 `.gitignore`，再创建工作树。

### 创建工作树

建议分支命名使用 `feature/<主题>`、`fix/<主题>` 或 `refactor/<主题>`。

```bash
git worktree add .worktrees/<branch-name> -b <branch-name>
cd .worktrees/<branch-name>
```

如果使用 `worktrees/` 作为目录，则替换为：

```bash
git worktree add worktrees/<branch-name> -b <branch-name>
cd worktrees/<branch-name>
```

### 初始化与基线验证

进入工作树后，根据项目文件自动执行必要初始化：

```bash
pip install -r requirements.txt
pytest
```

如果基线测试失败，不要直接继续实现；需要先记录失败命令、关键错误与影响范围，并确认这是既有问题还是需要先修复的问题。

### 开发与收尾

- 不要在主工作区和工作树中同时修改同一文件，避免合并冲突。
- 不要回退或覆盖用户在其他工作区中的改动。
- 完成后在工作树内运行相关测试与静态检查，并记录结果。
- 合并或提交前确认工作树状态：`git status --short`。
- 工作树不再需要时，先确认变更已合并或保留，再执行：`git worktree remove <path>`。

## Commit 与 Pull Request 规范

- 提交信息遵循 Conventional Commits，例如：`refactor(minecraft): 统一聊天命令前缀为 AGENT`。
- PR 建议包含：变更摘要、关键配置影响（如 `.env` 字段）、本地测试结果与日志截图（如有 UI 或日志变化）。

## 安全与配置提示

- `.env` 存放敏感配置（API Key、JWT 密钥等），不要提交到版本库。
- 日志默认写入 `logs/`（如 `logs/mcbe_ai_agent.log`），排查问题时附上关键片段。

## 研究与文档准则

如果用户要求添加新功能或修复 bug，必须先使用现有工具获取相关文档，了解最佳实践后再进行实现。

优先使用 MCP 工具获取最新文档：

- `context7`：获取库/框架的官方文档和代码示例。
- `firecrawl`：获取网页内容、搜索信息。
- `WebSearch`：搜索最新技术和解决方案。

避免盲目实现，确保代码符合框架/库的最佳实践。
