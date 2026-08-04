# 版本体系与 README 结构优化方案（修订版 v2）

> 修订版说明：本版基于 2026-08-04 仓库全面审查 + 方案 v1 审查结果。v1 方案存在 4 处事实错误与若干遗漏，本版已逐项修正，修正点见 §1.3。

---

## 一、现状与问题总览

### 1.1 版本号碎片化

| 位置 | 当前值 | 角色 |
|------|--------|------|
| `_version.py` | `2.4.0` | Python 服务端 `__version__`，注入欢迎消息、User-Agent、`cli.py --version` |
| `MCBE-AI-Agent-addon/package.json` | `0.2.0` | Addon npm 包版本，release workflow 目前以此为发布版本号 |
| `MCBE-AI-Agent-addon/behavior_packs/.../manifest.json` | `header.version: [1,0,0]` | 游戏内实际识别版本，**与 package.json 的 0.2.0 本来就不一致** |
| `MCBE-AI-Agent-addon/resource_packs/.../manifest.json` | `header.version: [1,0,0]` | 同上 |
| Git tags | `v0.1.0`, `v0.2.0`, `v0.2.0-preview` | 仅覆盖 addon 发布历史，与 Python 侧 v2.x 脱节 |
| `README.md` 标题 | `v2.0` | 自 v2.0.0 发布后未更新 |
| `README.md` 页脚 | `2.4.0` / `2026-06-19` | 与 `_version.py` 一致，但日期过时 |

**根因**: 仓库是 Python 服务端 + MC Addon (TypeScript) 的 monorepo，两个组件版本各自独立演进；Git tags 和 release workflow 仅服务于 addon 发布，且 addon 的 package.json 版本与 manifest 版本长期脱节。

### 1.2 文档 vs 代码差异（严重）

| 位置 | 问题简述 | 实测确认 |
|------|---------|---------|
| README Addon Bridge 章节 | 仍用旧协议 `MCBEAI_TOOL` / `MCBEAI\|RESP\|` | ✅ 实际为 `MCBEWS_BRIDGE` / `MCBEWS\|BRIDGE` / `mcbews:bridge_req`（`config.example.json` addon 段 + addon `scripts/bridge/constants.ts`） |
| README 项目结构树 | 列出不存在的 `services/websocket/`；**`storage/` 实际已在树中**（误标为"存储层 (TODO)"） | ✅ 实际为 `services/gateway/`、`services/auth/`；树还缺 `web/trace`、`agent/block_ops` 等 |
| README 游戏内命令 | 命令列表不全（缺 8 个） | ✅ 缺：`AGENT 脚本`/`保存`/`模板`/`设置`/`MCP`/`同意`/`拒绝`/`连续模式` |
| README 配置表 | `enable_ws_raw_log` / `enable_llm_raw_log` 默认值写成 `true` | ✅ 实际 `settings.py:921-929` 与 `config.example.json:217-218` 均为 `false` |
| README / CLAUDE.md / ddui 文档 | 引用不存在的 `claude_md/` 目录 | ✅ 三处：README:573、CLAUDE.md:81、`docs/ddui-development-guide.md:846` |
| README 开发指南 | 代码示例过时 | ✅ provider 为代码注册（`RuntimeAdapterRegistry`），**不是 JSON 配置** |
| CLAUDE.md:10 | `PydanticAI >= 1.0.0` | ✅ 实际 `requirements.txt` 锁 `pydantic-ai~=1.94.0` |
| CLAUDE.md:147 | 声称 `mcbe-ws-sdk/` 是本地可编辑 path 依赖 | ✅ 实际仅有 pip 依赖 `mcbe-ws-sdk>=0.1.0` |
| docs/ddui-*.md | 多处仍用旧协议 `mcbeai:*` | ✅ 见 §五 |
| config/settings.py:784 | `system_prompt` 默认值与 `config.example.json:34` 措辞不一致 | ✅ |
| config.example.json | **缺少 `AGENT 连续模式` 命令**（仅存在于 `settings.py:59` 的 `DEFAULT_COMMANDS`） | ✅ 新增发现：用户 `cli.py init` 复制 example 后可能丢失该命令 |
| `MCBE-AI-Agent-addon/README.md` | 是 Microsoft Hello World 示例模板（2022-04-01 遗留） | ✅ 不是本项目文档 |

### 1.3 方案 v1 的修正点（本版相对 v1 的变更）

| # | v1 错误/遗漏 | 本版处理 |
|---|-------------|---------|
| 1 | 声称结构树"缺少 storage/" | 修正：storage 已在树中，真正问题是 `services/websocket/` 不存在 |
| 2 | 以 `config.example.json` 为命令权威 | 修正：命令唯一真源为 `settings.py` 的 `DEFAULT_COMMANDS`（example 缺 `连续模式`） |
| 3 | README 开发指南"改为 JSON 配置示例" | 修正：provider 是代码注册，保留注册式示例并更新为当前 API |
| 4 | 审计漏掉 manifest `[1,0,0]` 与 package.json 不一致 | 补入 1.1 表格与同步清单 |
| 5 | 文档重构却升 MINOR，违反自定 PATCH 规则 | 明确版本号选择与理由（见 §2.2） |
| 6 | 未写版本耦合副作用（文档级发布连带 addon 打版） | 新增 `--addon-only` 模式与风险说明（见 §2.5、§九） |
| 7 | release.yml 工作目录陷阱（`_version.py` 在仓库根） | 新增显式 `cd $GITHUB_WORKSPACE` 步骤 |
| 8 | CHANGELOG 双源冲突 | 明确 CHANGELOG.md 与 release notes 分工，release notes 改为统计全仓库 |
| 9 | system_prompt 统一方向反了 | 修正：`settings.py` 为代码默认值真源，example 同步（§六） |
| 10 | 漏修 `ddui-development-guide.md:846` 的 claude_md 引用 | 补入 |
| 11 | 漏掉 addon README 模板遗留 | 补入（§七） |
| 12 | sync 脚本范围含糊 | 给出明确同步对象清单与禁止项（§2.5） |
| 13 | 执行表漏 release 名称改动 | 补入 §2.6 |
| 14 | 根目录无 `scripts/` 目录 | 明确新建，与 addon 下 scripts 区分 |

---

## 二、版本统一方案

### 2.1 统一版本策略

**决策：全仓库单一版本号，`_version.py` 为唯一真源 (single source of truth)。**

理由：
- Python 服务端与 Addon 是同一系统的两个组件，统一版本便于用户理解"我用的 v2.4.0 服务端配哪个 addon？"——答案永远是同版本号
- Addon 不独立发布，其 `.mcaddon` 文件随服务端版本一起发布
- Release workflow 目前只打包 addon，但发布的是"MCBE AI Agent 系统"整体（本版补充：release 需附带 Python 源码归档，见 §2.6）

### 2.2 版本号选择：`2.5.0`

- v1 方案计划 `2.5.0`（MINOR），但按自定规则（§2.3）纯文档重构应为 PATCH（2.4.1）。
- **本版决策：`2.5.0`**，理由：本版本的核心能力不止文档——① 版本体系统一（新交付能力）；② addon 首次以 2.x 打版（从 0.2.0 跳变）；③ 修复 `config.example.json` 缺失命令（用户可见功能修复）。三者合计构成 MINOR 语义。
- **后续约定**：纯文档/修复级发布按 PATCH 语义升 `2.5.1`；此时是否连带 addon 打版由 sync 脚本 `--addon-only` 决定（§2.5）。

### 2.3 版本号规则

- **格式**: `MAJOR.MINOR.PATCH` (SemVer)
  - MAJOR: 重大架构变更、破坏性协议更新
  - MINOR: 新功能（新增命令、工具、provider、addon 能力、版本体系变更）
  - PATCH: bug 修复、文档更新、小优化
- **预发布**: 使用 `-beta.N` / `-preview` 后缀，tag 格式 `v2.5.0-beta.1`
- **CI 构建**: 使用 `-dev` 后缀（自动），不生成 tag
- 注：`__version__` 仅用于展示和 User-Agent，不参与包发布，不强制 PEP 440 规范

### 2.4 具体执行步骤

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | `_version.py` → `2.5.0` | 新版本号，覆盖此次版本统一 + 文档 / README 重构 |
| 2 | 新增 `scripts/sync-version.sh` | 一键同步脚本（规范见 §2.5） |
| 3 | 运行 sync 脚本，同步 package.json + 所有 manifest | 同步范围见 §2.5 |
| 4 | 修改 release workflow | 改动清单见 §2.6 |
| 5 | 新增 `CHANGELOG.md` | 迁移 README 内联更新日志（§3.3） |
| 6 | 后续发布打 tag `v2.5.0` | 不再使用 `v0.x.x`；旧 tag 保留不删 |

### 2.5 `scripts/sync-version.sh` 规范

- 从仓库根 `_version.py` 读取版本。
- **同步对象**：
  - `MCBE-AI-Agent-addon/package.json` 的 `version`（字符串，如 `2.5.0`）
  - `MCBE-AI-Agent-addon/behavior_packs/MCBE-AI-Agent/manifest.json`：`header.version`、`modules[*].version`（数组格式 `[2, 5, 0]`）
  - `MCBE-AI-Agent-addon/resource_packs/MCBE-AI-Agent/manifest.json`：`header.version`、`modules[*].version`
  - behavior pack `dependencies` 中 resource pack uuid（`450332ea-...`）引用的 `version` 数组——**必须同步**，否则依赖版本与资源包版本不一致
- **禁止改动**：
  - `min_engine_version`（`[1, 21, 80]`）
  - dependencies 中 `module_name` 型依赖版本（`@minecraft/server 2.6.0` 等）
  - 命令清单（真源是 `settings.py`，不在此脚本职责内）
- **模式**：
  - 默认：全量同步到统一版本
  - `--addon-only <ver>`：仅 addon 侧打版（addon 单独热修、Python 侧不动时用）
  - `--check`：只校验各文件版本是否一致，不改写
- 脚本执行后人工复核 `git diff`，应仅出现版本号相关字段变更。

### 2.6 release workflow 改动清单（`.github/workflows/release.yml`）

1. **版本读取**：`Determine version` 步骤改为显式 `cd $GITHUB_WORKSPACE` 后执行 `python -c "from _version import __version__; print(__version__)"`（当前 workflow `defaults.run.working-directory` 是 `./MCBE-AI-Agent-addon`，不切目录会 ImportError）；`version` 手动输入保留为可选覆盖。
2. **Release 名称**：`MCBE AI Agent Addon v...` → `MCBE AI Agent v2.5.0`。
3. **changelog 生成**：去掉 `git log ... -- MCBE-AI-Agent-addon/` 的路径过滤，统计全仓库提交；与 CHANGELOG.md 分工：CHANGELOG.md 手工维护完整历史，release notes 用自动生成 + `generate_release_notes: true`。
4. **附加 Python 源码归档**：release assets 增加仓库源码 zip（排除 `.git`、`node_modules`、`data/`、`logs/`、`.worktrees/`），使发布物真正对应"系统整体"。
5. 其余（构建、打包、命名 `.mcaddon`、prerelease/draft 开关）保持不变。

### 2.7 对外展示

- 欢迎消息 `MCBE AI Agent v{__version__}`（已有，无需改，来自 `services/gateway/hook.py`）
- `cli.py --version` / User-Agent 自动跟随（已有，无需改）
- Release 名称 `MCBE AI Agent v2.5.0`
- `.mcaddon` 文件名 `MCBE-AI-Agent-v2.5.0.mcaddon`（沿用现有重命名逻辑）

---

## 三、README 重构

### 3.1 当前结构诊断

```
行数    章节                         问题
1       (标题 v2.0)                  版本过时
3-6     概述                         可用
7-37    核心特性                      模型名写错
39-73   项目结构                      树过时（services/websocket 不存在；storage 已列但标 TODO）
75-131  架构设计                      可用
133-245 快速开始                      可用
247-301 Runtime Harness 审计工具     可用，可精简
303-346 Agent Trace 审计             可用，可精简
348-436 Addon Bridge 桥接            多处旧协议名，长篇调试步骤可外移
438-517 Termux 部署指南              与下面两项重复
519-573 游戏内使用                    命令列表不完整，引用不存在文件
575-638 Termux 常见问题              与 Termux 部署指南重叠
640-676 配置说明                      默认值有误，settings 表格可外移
678-749 架构亮点                      与"架构设计"重复
751-778 性能优化                      可用，可精简
780-791 与旧版对比                    历史遗留，可删除
793-846 Termux 优化建议               与前面两项 Termux 重叠
848-903 开发指南                      代码示例过时
905-957 故障排查                      Termux + 通用混排，应分离
959-974 扩展性                        内容空洞，可删除
976-994 安全建议                      可用
996-1041 更新日志                     应外移到 CHANGELOG.md
1043-1052 未来计划                    已过时/已实现，应删除或更新
1055-1082 技术栈/许可证/来源         可用
```

### 3.2 目标结构（精简版 ~400-500 行）

```
# MCBE AI Agent v{version}

## 概述 + 核心特性                      (~20 行，模型名修正；核心特性不删，压缩保留)
## 快速开始                            (~100 行)
## 游戏内使用                          (~80 行，16 个命令全量)
## 配置                                (~30 行，指向 config.example.json)
## CLI 工具                            (~50 行，trace/audit 等)
## Addon Bridge                        (~60 行，精简调试步骤)
## 部署                                (~60 行，合并所有 Termux 内容)
## 架构                                (~30 行，合并"架构设计"+"架构亮点")
## 故障排查                            (~30 行)
## 安全                                (~10 行，保留要点)
## 技术栈与致谢                        (~10 行)
```

总目标：从 1082 行压缩到 ~400-500 行，信息不丢失（内移 docs/，外移到 GitHub Releases / CHANGELOG）。

### 3.3 内容迁移计划

| 迁移内容 | 从哪里 | 到哪里 | 原因 |
|----------|--------|--------|------|
| 更新日志 v2.0.0~v2.4.0 | README 更新日志章节 | `CHANGELOG.md` (新建，并补 v2.5.0 条目) | 内联 changelog 不是有效的 README 内容 |
| "添加新 Provider/Tool/命令" | README 开发指南 | `docs/development.md` (新建) | 开发指南应独立，且需要更新示例 |
| Addon 调试步骤(详细) | README Addon Bridge 章节 | `docs/addon-bridge-protocol.md` | 协议文档已有大部分内容，补全即可 |
| Termux 部署+FAQ+优化 | README 三处 Termux 章节 | `docs/termux.md` (新建) | 合并三项为一份完整指南 |
| "与旧版对比" | README | 删除 | 历史文档，新版用户不需要 |
| "未来计划" | README | 删除（未实现项另行维护） | 已过时；`web/trace` 已存在，"Web 管理界面"部分已实现，其余以 GitHub Milestones 维护 |
| "扩展性" | README | 删除 | 内容空洞 |
| Runtime Harness 审计 | README | 精简为 CLI 工具章节的一个小节 | 审计是 CLI 子功能 |
| Agent Trace 审计 | README | 精简为 CLI 工具章节的一个小节 | 同上 |
| 配置项表格 | README 配置章节 | 精简为指向 config.example.json | 避免与 example 双份维护 |

### 3.4 修复的具体错误

| 位置 | 当前值 | 修复为 | 依据 |
|------|--------|--------|------|
| README 标题 | `v2.0` | `v2.5.0` | `_version.py` |
| README 核心特性 | `deepseek-reasoner` / `GPT-5` / `Claude Sonnet 4.5` | `deepseek-chat` / `gpt-4o` / `claude-sonnet-4-20250514` | `config.example.json` providers 段 |
| README 项目结构树 | `services/websocket/`（不存在）等 | 更新为实际目录（`services/gateway/`、`services/auth/`、`services/agent/`、`web/trace`、`storage/`） | 当前文件系统 |
| README Addon Bridge | `MCBEAI_TOOL` / `MCBEAI\|RESP\|` | `MCBEWS_BRIDGE` / `MCBEWS\|BRIDGE` / `mcbews:bridge_req` | `config.example.json` addon 段 + addon `scripts/bridge/constants.ts` |
| README 游戏内命令 | 缺 8 个命令 | 补全 **16 个命令**（`#登录`/`聊天`/`脚本`/`保存`/`对话`/`连续模式`/`上下文`/`模板`/`设置`/`MCP`/`广播`/`同意`/`拒绝`/`运行命令`/`切换模型`/`帮助`） | `config/settings.py` `DEFAULT_COMMANDS`（唯一真源） |
| README 配置表 | raw log 默认 `true` | `false` | `config/settings.py:921-929` |
| README:573 | 引用 `claude_md/report/...` | 删除或替换为 `docs/addon-bridge-protocol.md` | 目录不存在 |
| README 开发指南 provider 示例 | `providers.py` + 旧 API | `services/agent/providers.py` 注册式示例（`RuntimeAdapterRegistry._create_*`），**保持代码示例，不改为 JSON** | `services/agent/providers.py:21` |
| README 开发指南命令示例 | `COMMANDS = {...}` Python 代码 | 指向 `config/settings.py` `DEFAULT_COMMANDS` + `config.json` 覆盖方式 | 当前架构 |
| README 页脚 | `2.4.0` / `2026-06-19` | `2.5.0` / `2026-08-04` | 当前版本 |

---

## 四、CLAUDE.md 修复项

| 行 | 当前值 | 修复为 | 依据 |
|----|--------|--------|------|
| 10 | `PydanticAI >= 1.0.0` | `PydanticAI ~= 1.94.0` | `requirements.txt` |
| 81 | `claude_md/fix/MULTIPLAYER_SESSION_FIX.md` | 删除此行引用，保留 `docs/addon-bridge-protocol.md` | 文件不存在 |
| 147 | 声称 `mcbe-ws-sdk/` 为本地可编辑依赖 | 改为 pip 依赖描述（`mcbe-ws-sdk>=0.1.0`） | `requirements.txt:3` |
| 命令表 | 缺 7 个命令 | 补全 16 个命令（同 §3.4） | `config/settings.py` `DEFAULT_COMMANDS` |

---

## 五、docs/ddui-*.md 修复项

批量替换旧的 `mcbeai:` 协议标识符：

| 旧标识符 | 新标识符 |
|----------|----------|
| `mcbeai:bridge_request` | `mcbews:bridge_req` |
| `mcbeai:ai_resp` | `mcbews:text_resp` |
| `mcbeai:ui_event` | 删除（此 scriptevent ID 已不存在） |

涉及文件：`docs/ddui-development-guide.md`、`docs/ddui-migration-assessment.md`。

同时修复：
- `ddui-development-guide.md:650` "ScriptEvent ID 不变"这一错误声明（协议 ID 已变）
- `ddui-development-guide.md:846` 的 `claude_md/fix/MULTIPLAYER_SESSION_FIX.md` 引用（v1 遗漏项）

---

## 六、config 修复项

### 6.1 system_prompt 统一

**现状**:
- `settings.py:784`: `"请始终保持积极和专业的态度。回答尽量保持一段话不要太长..."`
- `config.example.json:34`: `"你是一个MCBE助手，请始终保持积极友好的态度。回答尽量保持一段话不要太长..."`

**方案**：`settings.py` 是代码默认值（运行时真源），`config.example.json` 是 `cli.py init` 的模板——两处必须一致。以更友好、更具体的措辞（当前 example 的文本）为准，更新 `settings.py:784` 默认值，`config.example.json` 保持该文本不变。用户通过 `config.json` 覆盖不受影响。验收：`tests/test_runtime_harness_prompt.py:87`（断言旧措辞不在 prompt 中）应继续通过。

### 6.2 config.example.json 缺失命令

**现状**：`AGENT 连续模式` 仅存在于 `settings.py:59` 的 `DEFAULT_COMMANDS`，`config.example.json` 缺失。

**方案**：向 `config.example.json` 补入 `AGENT 连续模式`（type: `continuous_mode`），避免 `cli.py init` 复制后丢失该命令。

---

## 七、addon 侧遗留问题

- `MCBE-AI-Agent-addon/README.md` 是 Microsoft "Hello World" 示例模板（2022-04-01），替换为实际 addon 的简短说明（功能、构建、local-deploy、指向主 README），随版本统一 PR 一并处理。

---

## 八、执行阶段与验收标准

> 按 AGENTS.md 分支规范：所有改动在 `fix/*` / `docs/*` 分支上完成，从 `dev` 拉出，合入 `dev` 后经 `dev` 合回 `master`。

### 阶段 A：版本统一（PR: `fix/version-unify-2.5.0`）

| 步骤 | 操作 | 影响文件 |
|------|------|---------|
| A1 | `_version.py` → `2.5.0` | `_version.py` |
| A2 | 新建 `scripts/sync-version.sh` | `scripts/sync-version.sh` (新建) |
| A3 | 运行 sync 脚本并核对 diff | `MCBE-AI-Agent-addon/package.json`、两个 `manifest.json` |
| A4 | 修改 release workflow（§2.6 全部 5 项） | `.github/workflows/release.yml` |
| A5 | 迁移 changelog → `CHANGELOG.md`（含 v2.5.0 条目） | `CHANGELOG.md` (新建)、`README.md` |
| A6 | 替换 addon README 模板 | `MCBE-AI-Agent-addon/README.md` |

**验收**：
- `python -c "from _version import __version__; print(__version__)"` 输出 `2.5.0`
- `bash scripts/sync-version.sh --check` 通过；`--check` 失败后跑一次全量 sync，`git diff` 仅含版本号字段
- workflow 语法可用 `actionlint` 或 GitHub Actions 页面校验
- `cd MCBE-AI-Agent-addon && pnpm install --frozen-lockfile` 仍通过（package.json 版本变更不触发 lockfile 冲突，已实测 lockfile 无版本号引用）

### 阶段 B：README / 文档重构（PR: `docs/readme-restructure`）

| 步骤 | 操作 | 影响文件 |
|------|------|---------|
| B1 | 按 §3.2 目标结构重写 README | `README.md` |
| B2 | 外移开发指南 → `docs/development.md` | `docs/development.md` (新建) |
| B3 | 外移 Termux 合集 → `docs/termux.md` | `docs/termux.md` (新建) |
| B4 | 补全 Addon 调试步骤 → `docs/addon-bridge-protocol.md` | `docs/addon-bridge-protocol.md` |
| B5 | 修复 CLAUDE.md（§四 4 项） | `CLAUDE.md` |
| B6 | 批量替换 ddui 协议名 + 2 处附加修复（§五） | `docs/ddui-development-guide.md`, `docs/ddui-migration-assessment.md` |
| B7 | system_prompt 统一 + 补 `连续模式` 命令（§六） | `config/settings.py`, `config.example.json` |

**验收**：
- README 中所有代码/文件/配置引用均存在（逐条核对 §3.4 表格）
- `python -c "from config.settings import Settings"` 通过
- `pytest`（`-m 'not live'`）全绿，重点 `tests/test_runtime_harness_prompt.py`
- 命令清单与 `settings.py` `DEFAULT_COMMANDS` 逐条一致
- README 行数落在 ~400-500 区间

---

## 九、风险与约束

- **向后兼容**: 版本号变更不影响任何运行时行为（`__version__` 仅用于展示和 User-Agent）。
- **addon 用户**: 新 addon 版本号 `2.5.0` 不再沿用小版本号 `0.2.0`（manifest 从 `[1,0,0]` 跳至 `[2,5,0]`），建议在 release notes 注明"版本体系统一，addon 从 v0.2.0 跳至 v2.5.0"。
- **版本耦合副作用**: 统一版本后，Python 侧文档级 PATCH 发布会连带 addon 版本号变化，用户需重新导入 `.mcaddon`。对策：addon 无实际改动时使用 `sync-version.sh --addon-only` 控制打版节奏，或接受"每次发布重新导入"（Minecraft 以 manifest 版本递增识别更新）。
- **已有 git tags**: 保留 `v0.1.0` / `v0.2.0` / `v0.2.0-preview` 不删除，它们是历史记录。
- **README 精简**: 确保所有外移的内容在新文件中完整保留，README 中保留链接指向。
- **双 changelog 纪律**: CHANGELOG.md（手工完整历史）与 release notes（自动生成）分工明确，避免重复维护产生分歧。
