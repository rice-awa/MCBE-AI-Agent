# 仓库协作指南

本文件面向在本仓库工作的 AI / coding agents。交流、说明和文档默认使用中文。

## 必读入口

- `CONTEXT.md`：项目共享语言与术语边界。涉及需求、设计、架构命名或文档更新前，优先对齐其中术语，避免使用其标注的替代表达。
- `CLAUDE.md`：Claude Code 专用项目说明，包含当前架构要点、关键文件、配置约定和工具使用要求。
- `config.example.json` / `.env.example`：配置字段来源；普通配置放 `config.json`，密钥和密码放 `.env`。

## 工作原则

- 优先做小而完整的改动，避免无关重构和过度抽象。
- 修改玩家会话、聊天、上下文、模板、变量、模型切换或下行消息路径时，必须显式传递当前事件的 `player_name` / `sender`。
- 新增或修改下行长文本发送路径时，必须复用 `services/websocket/flow_control.py`，不要在调用点重复实现分片。
- 添加新功能或修复 bug 前，按 `CLAUDE.md` 要求使用现有工具获取相关库/框架文档。
- 不提交 `.env`、`config.json`、日志、密钥或其他本地敏感文件。

## 常用命令

```bash
python cli.py init
python cli.py info
python cli.py test-provider deepseek
python cli.py serve
pytest
```

## 代码与测试

- Python 3.11+，4 空格缩进；模块/变量用 `snake_case`，类用 `PascalCase`，常量用 `UPPER_CASE`。
- 预期使用 `pytest` + `pytest-asyncio`；新增功能或修复 bug 时补充相邻测试。
- 如果测试无法运行，交付说明中写明失败命令、关键错误和影响范围。
- 格式与静态检查以仓库配置为准（如 `ruff`、`mypy`），不要新增未配置的工具链。

## Git 与工作树

- 不要提交或覆盖用户未明确要求处理的改动。
- 需要隔离开发较大功能或修复时，优先使用已被忽略的 `.worktrees/`。
- 提交信息遵循 Conventional Commits，例如 `fix(websocket): 避免玩家会话串上下文`。
- 提交、合并或移除工作树前先检查 `git status --short`。
