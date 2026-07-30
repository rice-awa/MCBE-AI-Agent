# 仓库协作指南

本文件面向在本仓库工作的 AI / coding agents。交流、说明和文档默认使用中文。

## 必读入口

- `CONTEXT.md`：项目共享语言与术语边界。涉及需求、设计、架构命名或文档更新前，优先对齐其中术语，避免使用其标注的替代表达。
- `CLAUDE.md`：Claude Code 专用项目说明，包含当前架构要点、关键文件、配置约定和工具使用要求。
- `config.example.json` / `.env.example`：配置字段来源；普通配置放 `config.json`，密钥和密码放 `.env`。

## 工作原则

- 优先做小而完整的改动，避免无关重构和过度抽象。
- 修改玩家会话、聊天、上下文、模板、变量、模型切换或下行消息路径时，必须显式传递当前事件的 `player_name` / `sender`。
- 新增或修改下行长文本发送路径时，必须走 `BrokerResponseBridge` 或 SDK delivery（`McbeOutboundDelivery` / `McbewsV1Delivery`），不要在调用点重复实现分片。
- 添加新功能或修复 bug 前，按 `CLAUDE.md` 要求使用现有工具获取相关库/框架文档。
- 不提交 `.env`、`config.json`、日志、密钥或其他本地敏感文件。

## 开发规范（Git 工作流）

**所有新功能与 bug 修复必须在新分支上完成，禁止直接在 `master` 或 `dev` 上开发。**

### 分支模型

```
feature/* / fix/* / ...  →  dev  →  master
```

1. **功能 / 修复分支**：从最新 `dev` 拉出（`git fetch origin && git checkout -b <type>/<name> origin/dev`）。
2. **合并回 `dev`**：自测通过后，开 PR 合入 `dev`（或在获准后本地 merge 再 push）。同一主题的多个小提交可 squash。
3. **合回 `master`**：仅从已验证的 `dev` 合入 `master`；不要把 feature/fix 分支直接合进 `master`。
4. **禁止**：在 `master` / `dev` 上直接 commit；跳过 `dev` 直合 `master`；用模糊或无类型的分支名。

热修复若必须从 `master` 拉出，修复后仍先合入 `dev`，再让 `dev` 合回 `master`，避免分支漂移。

### 分支命名

格式：`<type>/<short-kebab-desc>`

| type | 用途 | 示例 |
|------|------|------|
| `feature/` 或 `feat/` | 新功能 | `feature/ddui-chat-panel`、`feat/models-dev-context-metadata` |
| `fix/` | bug 修复 | `fix/pydantic-ai-1.0-import` |
| `refactor/` | 重构（行为不变） | `refactor/agent-conversation` |
| `chore/` | 构建、依赖、杂项 | `chore/update-vitest` |
| `docs/` | 仅文档 | `docs/addon-bridge-protocol` |
| `test/` | 仅测试 | `test/agent-worker-isolation` |

规则：

- 英文小写 + kebab-case；描述要能从名字看出意图，避免 `fix-1`、`tmp`、`wip`、纯中文路径名。
- 一个分支一个主题；不要在同一分支混杂无关功能。
- 临时 worktree / agent 分支可用 `worktree-<topic>`，合入前整理为上述正式命名或经 PR 标题说明主题。
- 不强制在分支名中写日期；需要区分并行方案时再加简短后缀（如 `-beta`）。

### 提交信息

遵循 Conventional Commits，与分支 type 对齐，例如：

```
feat(chat): 支持 @AI 触发与连续对话
fix(agent): 提高 tool_calls_limit 默认值
docs(agents): 补充 dev → master 合并规范
```

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
- 需要隔离开发较大功能或修复时，优先使用已被忽略的 `.worktrees/`，分支仍按上文命名并从 `dev` 拉出。
- 提交、合并或移除工作树前先检查 `git status --short`。
- 推送 / 开 PR 前确认目标分支是 `dev`（功能与修复），不是 `master`。
