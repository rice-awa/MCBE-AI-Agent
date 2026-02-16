# Repository Guidelines

## Project Structure & Module Organization
- `config/`：配置与日志（`settings.py`, `logging.py`）。
- `models/`：数据模型（WebSocket 消息、Minecraft 协议、Agent 依赖）。
- `core/`：消息队列与事件系统。
- `services/`：核心服务（`agent/`, `websocket/`, `auth/`）。
- `storage/`：存储层（当前为占位）。
- 入口：`main.py`（服务启动），`cli.py`（命令行）。

## Build, Test, and Development Commands
- 安装依赖：`pip install -r requirements.txt`
- 初始化配置：`python cli.py init`（生成 `.env` 并填写 API Key）。
- 查看配置：`python cli.py info`
- 测试 LLM：`python cli.py test-provider deepseek`
- 启动服务：`python cli.py serve` 或 `python main.py`

## Coding Style & Naming Conventions
- Python 3.11，4 空格缩进。
- 命名：模块/变量 `snake_case`，类 `PascalCase`，常量 `UPPER_CASE`。
- 格式/静态检查：`ruff`（行宽 100）、`mypy`（严格模式）。

## Testing Guidelines
- 预期使用 `pytest` + `pytest-asyncio`（见 `pyproject.toml`）。
- 测试目录约定为 `tests/`，文件名建议 `test_*.py`。
- 目前仓库未提供测试用例；新增功能请补充对应测试。

## Commit & Pull Request Guidelines
- 提交信息遵循 Conventional Commits，例如：`refactor(minecraft): 统一聊天命令前缀为 AGENT`。
- PR 建议包含：变更摘要、关键配置影响（如 `.env` 字段）、本地测试结果与日志截图（如有 UI/日志变化）。

## Security & Configuration Tips
- `.env` 存放敏感配置（API Key、JWT 密钥等），不要提交到版本库。
- 日志默认写入 `logs/`（如 `logs/mcbe_ai_agent.log`），排查问题时附上关键片段。

## Agent-Specific Instructions
- 交流与文档统一使用中文。

## Research & Documentation Guidelines
- 如果用户要求添加新功能或修复 bug，**必须先使用现有工具获取相关文档**，了解最佳实践后再进行实现。
- 优先使用 MCP 工具获取最新文档：
  - `context7` - 获取库/框架的官方文档和代码示例
  - `firecrawl` - 获取网页内容、搜索信息
  - `WebSearch` - 搜索最新技术和解决方案
- 避免盲目实现，确保代码符合框架/库的最佳实践。
