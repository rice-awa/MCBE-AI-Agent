# 版本体系与 README 结构优化方案

> 基于 2026-08-04 仓库全面审查结果，所有发现详见下文。

---

## 一、当前问题总览

### 1.1 版本号碎片化

| 位置 | 版本 | 角色 |
|------|------|------|
| `_version.py` | `2.4.0` | Python 服务端 `__version__`，注入欢迎消息和 User-Agent |
| `MCBE-AI-Agent-addon/package.json` | `0.2.0` | Addon npm 包版本，release workflow 以此为发布版本号 |
| Git tags | `v0.1.0`, `v0.2.0`, `v0.2.0-preview` | 仅覆盖 addon 发布历史，与 Python 侧 v2.x 脱节 |
| `README.md` 标题 | `v2.0` | 自 v2.0.0 发布后未更新 |
| `README.md` 页脚 | `2.4.0` | 与 `_version.py` 一致 |

**根因**: 仓库是 Python 服务端 + MC Addon (TypeScript) 的 monorepo，但两个组件版本各自独立演进。Git tags 和 release workflow 仅服务于 addon 发布，忽略了 Python 服务端。

### 1.2 README 臃肿

- **1082 行、27 个 h2 章节**，信息密度过低
- Termux 相关内容出现 3 次（部署指南、FAQ、优化建议），可合并
- 内联更新日志占据 ~87 行（v2.0.0 ~ v2.4.0），更适合放在 GitHub Releases 或独立 CHANGELOG
- "开发指南"部分（添加 Provider / Tool / 命令）代码示例已过时
- "未来计划"所列项目（对话持久化、Docker 等）早已实现或已放弃，信息失真

### 1.3 文档 vs 代码差异（严重）

| 文件 | 问题简述 |
|------|---------|
| README:387-435 | Addon Bridge 部分仍用旧协议名 `MCBEAI_TOOL` / `MCBEAI\|RESP\|`，实际已改为 `MCBEWS_BRIDGE` / `MCBEWS\|BRIDGE` / `mcbews:bridge_req` |
| README:42-72 | 项目结构树列出不存在的 `services/websocket/`，缺少实际存在的 `storage/` |
| README:541-559 | 游戏内命令列表缺 8 个命令 (`AGENT 脚本`/`保存`/`模板`/`设置`/`MCP`/`同意`/`拒绝`/`连续模式`) |
| README:657 | `enable_ws_raw_log` / `enable_llm_raw_log` 默认值写成 `true`，实际代码和 `config.example.json` 均为 `false` |
| README:573 | 引用不存在的 `claude_md/report/MULTIPLAYER_BUG_REPORT.md` |
| README:852,893 | 开发指南的代码示例使用旧架构 API，路径不完整 |
| CLAUDE.md:10 | PydanticAI 版本写成 `>= 1.0.0`，实际 `requirements.txt` 锁 `~=1.94.0` |
| CLAUDE.md:81 | 同样引用不存在的 `claude_md/` 文件 |
| CLAUDE.md:147 | 声称 `mcbe-ws-sdk/` 是本地可编辑 path 依赖，实际仅有 pip 依赖 |
| docs/ddui-*.md | 多处仍用旧协议 `mcbeai:bridge_request` / `mcbeai:ai_resp`，实际已改为 `mcbews:bridge_req` / `mcbews:text_resp` |
| config/settings.py:784 | `system_prompt` 默认值与 `config.example.json:34` 措辞不一致 |

---

## 二、版本体系优化方案

### 2.1 统一版本策略

**决策：全仓库单一版本号，`_version.py` 为唯一真源 (single source of truth)。**

理由：
- Python 服务端与 Addon 作为同一系统的两个组件，统一版本便于用户理解"我用的 v2.4.0 服务端配哪个 addon？"——答案永远是同版本号
- Addon 不独立发布，其 `.mcaddon` 文件随服务端版本一起发布
- Release workflow 目前只打包 addon，但发布的是"MCBE AI Agent 系统"整体

### 2.2 具体执行

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | `_version.py` → `2.5.0` | 新版本号，覆盖此次文档 / README 重构 |
| 2 | `addon/package.json` → `2.5.0` | 同步到统一版本 |
| 3 | Release workflow 改为从 `_version.py` 读取版本 | `python -c "from _version import __version__; print(__version__)"` |
| 4 | 添加 `scripts/sync-version.sh` | 一键同步脚本：从 `_version.py` 写到 `package.json`、所有 manifest.json 的 `header.version` 等 |
| 5 | 后续发布打 tag `v2.5.0` | 不再使用 `v0.x.x` |

### 2.3 版本号规则

- **格式**: `MAJOR.MINOR.PATCH` (SemVer)
  - MAJOR: 重大架构变更、破坏性协议更新
  - MINOR: 新功能（新增命令、工具、provider、addon 能力）
  - PATCH: bug 修复、文档更新、小优化
- **预发布**: 使用 `-beta.N` / `-preview` 后缀，tag 格式 `v2.5.0-beta.1`
- **CI 构建**: 使用 `-dev` 后缀（自动），不生成 tag

### 2.4 对外展示

- 欢迎消息 `MCBE AI Agent v{__version__}`（已有，无需改）
- Release 名称 `MCBE AI Agent v2.5.0`（不再写 "Addon"）
- `.mcaddon` 文件名保持 `MCBE-AI-Agent-v2.5.0.mcaddon`

---

## 三、README 结构优化方案

### 3.1 当前结构诊断

```
行数    章节                         问题
1       (标题 v2.0)                  版本过时
3-6     概述                         可用
7-37    核心特性                      模型名写错
39-73   项目结构                      目录树过时
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

### 3.2 目标结构（精简版 ~400 行）

```
# MCBE AI Agent v{version}

## 概述                                (~10 行)
## 快速开始                            (~100 行)
## 游戏内使用                          (~80 行)
## 配置                                (~30 行，指向 config.example.json)
## CLI 工具                            (~50 行，trace/audit 等)
## Addon Bridge                        (~60 行，精简调试步骤)
## 部署                                (~60 行，合并所有 Termux 内容)
## 架构                                (~30 行，合并"架构设计"+"架构亮点")
## 故障排查                            (~30 行)
## 安全                                (~10 行，保留要点)
## 技术栈与致谢                        (~10 行)
```

总目标：从 1082 行压缩到 ~400-500 行，信息不丢失（内移 docs/，外移到 GitHub Releases）。

### 3.3 内容迁移计划

| 迁移内容 | 从哪里 | 到哪里 | 原因 |
|----------|--------|--------|------|
| 更新日志 v2.0.0~v2.4.0 | README:996-1041 | `CHANGELOG.md` (新建) | 内联 changelog 不是有效的 README 内容 |
| "添加新 Provider/Tool/命令" | README:848-903 | `docs/development.md` (新建) | 开发指南应独立，且需要更新示例 |
| Addon 调试步骤(详细) | README:382-391 | `docs/addon-bridge-protocol.md` | 协议文档已有大部分内容，补全即可 |
| Termux 部署+FAQ+优化 | README:438-517, 575-638, 793-846 | `docs/termux.md` (新建) | 合并三项为一份完整指南 |
| "与旧版对比" | README:780-791 | 删除 | 历史文档，新版用户不需要 |
| "未来计划" | README:1043-1052 | 删除 | 已过时；活文档(PR/Milestones)替代 |
| "扩展性" | README:959-974 | 删除 | 内容空洞 |
| Runtime Harness 审计 | README:247-301 | 精简为 CLI 工具章节的一个小节 | 审计是 CLI 子功能 |
| Agent Trace 审计 | README:303-346 | 精简为 CLI 工具章节的一个小节 | 同上 |

### 3.4 修复的具体错误

所有需要修复的文档错误：

| 位置 | 当前值 | 修复为 | 依据 |
|------|--------|--------|------|
| README 标题 | `v2.0` | `v2.5.0` | `_version.py` |
| README:26 | `deepseek-reasoner` | `deepseek-chat` | `config.example.json:17` |
| README:27 | `GPT-5` | `gpt-4o` | `config.example.json:22` |
| README:28 | `Claude Sonnet 4.5` | `claude-sonnet-4-20250514` | `config.example.json:26` |
| README:42-72 | 项目结构树 | 更新为实际目录结构 | 当前文件系统 |
| README:387-435 | `MCBEAI_TOOL` / `MCBEAI\|RESP\|` | `MCBEWS_BRIDGE` / `MCBEWS\|BRIDGE` / `mcbews:bridge_req` | `config.example.json:200-202` + bridge 协议文档 |
| README:541-559 | 命令列表缺 8 个命令 | 补全 | `config.example.json:95-170` |
| README:657 | `true` | `false` | `config/settings.py:921-929` |
| README:573 | 引用 `claude_md/` | 删除或替换为有效引用 | 目录不存在 |
| README:852 | `providers.py` | `services/agent/providers.py` | 实际路径 |
| README:893 | `COMMANDS = {...}` Python 代码 | 改为 JSON 配置示例 | 当前架构使用 config.json |
| README 页脚 | `2.4.0` / `2026-06-19` | `2.5.0` / `2026-08-04` | 当前版本 |

---

## 四、CLAUDE.md 修复项

| 行 | 当前值 | 修复为 | 依据 |
|----|--------|--------|------|
| 10 | `PydanticAI >= 1.0.0` | `PydanticAI ~= 1.94.0` | `requirements.txt` |
| 81 | `claude_md/fix/MULTIPLAYER_SESSION_FIX.md` | 删除此行 | 文件不存在，多人会话隔离已在 CLAUDE.md 中充分说明 |
| 147 | 声称 `mcbe-ws-sdk/` 为本地可编辑依赖 | 改为 pip 依赖描述 | `requirements.txt:3` |
| 157-169 | 命令表缺 7 个命令 | 补全 | `config.example.json:95-170` |

---

## 五、docs/ddui-*.md 修复项

批量替换旧的 `mcbeai:` 协议标识符：

| 旧标识符 | 新标识符 |
|----------|----------|
| `mcbeai:bridge_request` | `mcbews:bridge_req` |
| `mcbeai:ai_resp` | `mcbews:text_resp` |
| `mcbeai:ui_event` | 删除（此 scriptevent ID 已不存在） |

同时更新 `ddui-development-guide.md:650` "ScriptEvent ID 不变"这一错误声明。

---

## 六、config/settings.py:784 system_prompt 统一

**当前状态**:
- `settings.py:784`: `"请始终保持积极和专业的态度。回答尽量保持一段话不要太长..."`
- `config.example.json:34`: `"你是一个MCBE助手，请始终保持积极友好的态度。回答尽量保持一段话不要太长..."`

**方案**: 以 `config.example.json` 的措辞为准（更友好、更具体），将 `settings.py` 的默认值同步为一致文本。用户通过 `config.json` 覆盖不受影响。

---

## 七、执行步骤

| 阶段 | 步骤 | 影响文件 |
|------|------|---------|
| **A: 版本统一** | 1. `_version.py` → `2.5.0` | `_version.py` |
| | 2. `package.json` → `2.5.0` | `MCBE-AI-Agent-addon/package.json` |
| | 3. Release workflow 改为读 `_version.py` | `.github/workflows/release.yml` |
| | 4. 添加 `scripts/sync-version.sh` | `scripts/sync-version.sh` (新建) |
| **B: README 重构** | 5. 按 3.2 目标结构重写 README | `README.md` |
| | 6. 外移 changelog → `CHANGELOG.md` | `CHANGELOG.md` (新建) |
| | 7. 外移开发指南 → `docs/development.md` | `docs/development.md` (新建) |
| | 8. 外移 Termux 合集 → `docs/termux.md` | `docs/termux.md` (新建) |
| | 9. 删除"与旧版对比""未来计划""扩展性" | `README.md` |
| **C: 修复 CLAUDE.md** | 10. 修复 4 处错误 | `CLAUDE.md` |
| **D: 修复 docs/ddui-** | 11. 批量替换旧协议名 | `docs/ddui-development-guide.md`, `docs/ddui-migration-assessment.md` |
| **E: 修复 system_prompt** | 12. 统一默认值 | `config/settings.py` |

---

## 八、风险与约束

- **向后兼容**: 版本号变更不影响任何运行时行为（`__version__` 仅用于展示和 User-Agent）
- **addon 用户**: 新 addon 版本号 `2.5.0` 不再沿用小版本号 `0.2.0`，建议在 release notes 中注明"版本体系统一，从 v0.2.0 跳至 v2.5.0"
- **已有 git tags**: 保留 `v0.1.0` / `v0.2.0` / `v0.2.0-preview` 不删除，它们是历史记录
- **README 精简**: 确保所有外移的内容在新文件中完整保留，README 中保留链接指向
