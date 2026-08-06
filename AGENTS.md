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

仓库只维护两个长期主线：

- `master`：稳定发布线，只接收已经在 `dev` 验证过的内容，并在发布时打 tag。
- `dev`：日常集成线，也是功能 / 修复 PR 的默认目标。

整体流向：

```
feature/* / fix/* / refactor/*  →  dev  →  master
                                      ↘
                                      beta（仅用于独立实验产物）
```

### 变更路径

1. **小而低风险的改动**可以直接提交到 `dev`，例如错别字、少量文档整理、简单配置调整或不影响行为的测试补充。提交前仍需运行相关检查，并确认 `dev` 工作树已同步。
2. **新功能、非简单 bug 修复、协议 / 架构 / 多模块改动**从最新 `dev` 创建主题分支：
   `git fetch origin && git switch -c <type>/<name> origin/dev`。
3. 主题分支完成自测后合入 `dev`。同一主题的实现、测试和文档应放在同一个分支 / PR 中，不要为配套文档另开分支。
4. 普通文档改动不再默认创建 `docs/*` 分支：小改动直接进 `dev`，与功能或修复相关的文档跟随对应主题分支；只有需要单独评审的大型文档工程才例外开分支。
5. 只有经过 `dev` 验证的内容才能从 `dev` 合入 `master`；功能 / 修复分支不得直接合入 `master`。
6. `master` 和 `dev` 不提交无关的临时调试内容；紧急生产修复如必须从 `master` 拉出，合入后要立即把同一修复同步回 `dev`。

### 分支命名

格式统一为 `<type>/<short-kebab-desc>`，使用英文小写和 kebab-case：

| type | 用途 | 示例 |
|------|------|------|
| `feature/` | 新功能 | `feature/ddui-chat-panel` |
| `fix/` | bug 修复 | `fix/request-timeout` |
| `refactor/` | 重构（行为不变） | `refactor/agent-conversation` |
| `test/` | 仅测试 | `test/agent-worker-isolation` |
| `chore/` | 构建、依赖、杂项 | `chore/update-vitest` |
| `worktree/` | 临时 worktree 隔离（不作为长期协作分支） | `worktree/agent-trace-audit` |

规则：

- 新功能只使用 `feature/`，不再使用 `feat/`；`feat` 仅作为 Conventional Commits 的提交类型。
- 普通配套文档不使用 `docs/` 分支，随对应 `feature/` / `fix/` 分支提交。
- 一个分支只处理一个主题；分支名要能说明意图，避免 `tmp`、`wip`、`fix-1`、日期堆砌或纯中文路径名。
- 主题分支合入后及时删除本地和远端分支；不要从已合并的旧分支继续开发。

### 提交信息

遵循 Conventional Commits；提交类型按提交内容选择，不要求与分支前缀完全一致。配套文档可以使用 `docs(...)`，但应留在对应的功能 / 修复分支中，例如：

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
- 小改动直达 `dev` 时，先执行 `git switch dev && git pull --ff-only origin dev`；较大功能或修复优先使用已被忽略的 `.worktrees/` 隔离开发。
- 使用 worktree 时，分支仍按上文命名并从最新 `dev` 拉出；提交、合并或移除 worktree 前先检查 `git status --short` 和 `git worktree list`。
- 推送 / 开 PR 前确认目标分支：主题分支指向 `dev`，发布 PR 指向 `master`。
- 合并完成后检查对应远端分支是否仍存在，及时清理已合并分支。
<!-- TRELLIS:START -->
# Trellis Instructions

These instructions are for AI assistants working in this project.

This project is managed by Trellis. The working knowledge you need lives under `.trellis/`:

- `.trellis/workflow.md` — development phases, when to create tasks, skill routing
- `.trellis/spec/` — package- and layer-scoped coding guidelines (read before writing code in a given layer)
- `.trellis/workspace/` — per-developer journals and session traces
- `.trellis/tasks/` — active and archived tasks (PRDs, research, jsonl context)

If a Trellis command is available on your platform (e.g. `/trellis:finish-work`, `/trellis:continue`), prefer it over manual steps. Not every platform exposes every command.

If you're using Codex or another agent-capable tool, additional project-scoped helpers may live in:
- `.agents/skills/` — reusable Trellis skills
- `.codex/agents/` — optional custom subagents

Managed by Trellis. Edits outside this block are preserved; edits inside may be overwritten by a future `trellis update`.

<!-- TRELLIS:END -->
