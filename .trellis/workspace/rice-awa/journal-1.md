# Journal - rice-awa (Part 1)

> AI development session journal
> Started: 2026-08-06

---

## 2026-08-06 — 方案二：玩家身份始终来自当前事件（安全门）

激活子任务 `08-06-player-identity-per-event`，完成 Phase A 安全门测试 + Phase B 重构。

- **Phase A（安全门测试）**：新增 3 个测试到 `tests/test_gateway_hook_auth_chat.py`：
  - `test_two_players_interleave_same_connection_no_identity_leak`：同一连接下 Alice/Bob 请求与 `ai_response_sync` 界面同步不串扰（确定性 `_dispatch`）。
  - `test_approval_ownership_interleaved_same_connection`：同连接两玩家待审批归属不串扰。
  - `test_fallback_with_player_name_none_does_not_leak_to_other_player`：`player_name=None` 回退不得读取连接级 `state._player_name`（重构前该测试失败，证实风险窗口真实）。
- **Phase B（重构）**：`services/gateway/command_handlers.py` 移除 4 处 `state._player_name` 回退读取（`_reply_target`→`"@a"`、`_build_chat_request` sender、`_handle_chat_command` ai_response_sync、审批 owner）与 2 处死写入（`handle_command`/`handle_ui_chat`）。`grep state._player_name services/` 归零。
- **验证**：网关+审批测试 21 通过；全量 762 通过、唯一失败为独立 CLI bug（`08-06-fix-cli-context-window-display`，另行处理）。`trellis-check` 复核通过（3 项先前发现全部解决）。
- **工作分支**：`test/player-identity-interleave`（待创建并提交）。

---

## 2026-08-07 — 方案三：集中 block_ops 宿主侧报文准备


## Session 1: 方案三：集中 block_ops 宿主侧报文准备

**Date**: 2026-08-07
**Task**: 方案三：集中 block_ops 宿主侧报文准备
**Branch**: `dev`

### Summary

block_ops 包内部重构：新增 limits.py/message.py/preflight.py 三个模块；消除 target.py 对 tools_impl 的反向导入；tools_impl.py 瘦身 62KB→31KB；__init__.py 出口收窄；全量方块测试 152/152 通过。

### Git Commits

| Hash | Message |
|------|---------|
| `ef81860` | (see git log) |
| `b86d628` | (see git log) |

### Status

[OK] **Completed**

---

## 2026-08-07 — 方案四：把单次 MCBE Chat Agent 执行从 AgentWorker 生命周期中独立

**Commit**: `29ca4a0`
**Branch**: `dev`（直接提交到 dev）

### Summary

从 `AgentWorker` 提取 `_execute_single_request` 深模块，收敛单次 MCBE Chat Agent 执行的全部终态路径：

- 新增 `ExecutionResult` dataclass 统一描述 success/approval_pending/partial/timeout/cancelled/exception/disconnected
- `_execute_single_request` 承载完整流式执行管道：模型获取、流事件循环、tool_call/tool_result/approval_required/error 处理，成功时自动提交历史/触发标题生成
- `_process_request_locked` 简化为上下文准备 + 委托执行 + 基于结果收尾
- 提取 `_make_context_info_fn` 和 `_maybe_compress_before_run` 辅助方法
- 测试：22/22 agent_worker、32/32 queue_context、27/27 stream_mode、27/27 provider_lifecycle 全部通过

### Status

[OK] **Completed** — 代码已合入 dev，待归档

---

## 2026-08-07 — 方案五：让工具目录成为真实契约中心

**Commit**: `84b14c3`
**Branch**: `dev`（直接提交到 dev）

### Summary

消除工具使用指南的手工维护副本，使 `_TOOL_CATALOG` 成为唯一真实来源：

- `catalog.py` 新增 `project_tool_usage_guide()`，从 `_TOOL_CATALOG` 统一投影提示
- `core.py` 删除重复的 `TOOL_USAGE_GUIDE` 副本
- `prompt.py` 改为从 catalog 投影，消除两份副本
- 测试新增 9 个契约完整性测试：唯一性、MCP 独立、约束引用存在、预览字段匹配、风险一致性等
- 规格文档 `directory-structure.md` 更新

### Status

[OK] **Completed** — 代码已合入 dev，待归档


## Session 2: 方案四/五归档 + 任务全景 review

**Date**: 2026-08-07
**Task**: 方案四/五归档 + 任务全景 review
**Branch**: `dev`

### Summary

review近期6项任务完成状态，确认方案四和方案五已完成；合入 dev、归档 task、更新 spec(runtime-architecture.md 补充 ExecutionResult 完整契约)、清理远程分支、记录 journal。父任务进度更新为[5/6 done]。剩余待办：方案六(planning)和cli上下文窗口bug修复(planning)。

### Git Commits

| Hash | Message |
|------|---------|
| `b6d83a2` | (see git log) |
| `b37904b` | (see git log) |
| `c7590f3` | (see git log) |

### Status

[OK] **Completed**


## Session 3: 方案六实现 + CLI bug修复 + 全六项方案完成

**Date**: 2026-08-07
**Task**: 方案六实现 + CLI bug修复 + 全六项方案完成
**Branch**: `dev`

### Summary

完成方案六(agent-runtime-ownership)：折叠4个浅层全局转发函数(get_agent_manager/get_mcp_manager/get_prompt_manager/get_conversation_manager)，移除ProviderRegistry.set_runtime_adapters()，298个相关测试全部通过。完成CLI bug修复(fix/cli-context-window-display)：添加deepseek-v4-flash上下文窗口(128000)到MODEL_CONTEXT_WINDOWS静态表。架构优化父任务6/6全部完成并推送dev。工作树清理完毕。

### Git Commits

| Hash | Message |
|------|---------|
| `22aa9bd` | (see git log) |

### Status

[OK] **Completed**


## Session 4: finish-work: 架构优化六项方案全部完成

**Date**: 2026-08-07
**Task**: finish-work: 架构优化六项方案全部完成
**Branch**: `dev`

### Summary

收尾操作：归档父任务(architecture-review-optimization，6/6完成)。全六项候选方案均已在 dev 合入并推送到远程。只剩下整合后的 dev 分支，无需进一步工作。

### Git Commits

| Hash | Message |
|------|---------|
| `4025b4a` | (see git log) |
| `22aa9bd` | (see git log) |
| `64b783f` | (see git log) |
| `f13978d` | (see git log) |

### Status

[OK] **Completed**

## 2026-08-07 — 移除硬编码 MODEL_CONTEXT_WINDOWS，完全依赖 models.dev 在线数据

### Summary

删除 config/settings.py 中约 18 个模型的硬编码 MODEL_CONTEXT_WINDOWS 字典，get_context_window() 改为完全依赖 ModelMetadataCache 在线数据，不可用时返回 128_000 合理默认值。同步更新 DEFAULT_FALLBACK_CONTEXT_WINDOW（8192→128000）、默认模型名和对应测试。

### Git Commits

| Hash | Message |
|------|---------|
| dcdc0a0 | feat(config): 移除硬编码 MODEL_CONTEXT_WINDOWS，完全依赖 models.dev 在线数据 |

### Status

[OK] **已完成，已提交 dev 并推送**

---

## 2026-08-07 — 架构优化后续改进三项并行（ExecutionResult模式/运行时加深/校验拆分）

### Summary

架构优化 review 的三条改进建议并行推进并全部完成：

1. **spec-execution-result-pattern**: code-reuse-thinking-guide.md 追加 ExecutionResult 模式条目（已验证契约边界），含模式说明、正确/错误示例。
2. **runtime-deepening**: 移除 ProviderRegistry 薄转发层（providers.py），调用方直接 get_agent_runtime().runtime_adapters；消除 worker.py 两个废弃懒导入导致的 UnboundLocalError。
3. **block-ops-validation-split**: 新建 validation.py，从 tools_impl 迁移 11 个校验/归一化函数（~320行）；修复 execute_block_plan 中 state_unknown_result 引用（下划线未同步更新，潜藏 NameError）。

### Git Commits

| Hash | Message |
|------|---------|
| 4d47e1c | docs(spec): 补充 ExecutionResult 已验证契约边界模式 |
| c09b7b7 | refactor(block_ops): 拆分校验函数到独立 validation.py 模块 |
| 427b667 | refactor(runtime): 移除 ProviderRegistry 薄转发层 |

### Status

[OK] **全部完成，已提交 dev 并归档**



## Session 5: 接入 Anthropic 接口并支持自定义 base_url（官方/第三方兼容端点）

**Date**: 2026-08-08
**Task**: 接入 Anthropic 接口并支持自定义 base_url（官方/第三方兼容端点）
**Branch**: `dev`

### Summary

让 anthropic provider 支持自定义 base_url：settings 新增 anthropic_base_url 字段，get_provider_config 透传到 LLMProviderConfig，_create_anthropic_model 传给 AnthropicProvider。未配置时退回官方 api.anthropic.com。用 DeepSeek Anthropic 兼容端点 https://api.deepseek.com/anthropic + DEEPSEEK_API_KEY 真实调用验证通过。补充 base_url 透传/默认值测试与 CLAUDE.md 配置说明。另归档 08-07-fix-model-metadata-online（代码已提交 dcdc0a0）。

### Git Commits

| Hash | Message |
|------|---------|
| `b9bbabd` | (see git log) |

### Status

[OK] **Completed**

---

## 08-08 · DDUI UI 迁移（feature/ddui-ui-migration）

**Branch**: `feature/ddui-ui-migration`

### Summary

将 beta 分支的 DDUI UI 前端移植到 dev（@minecraft/server-ui v2.1.0 stable）：
- formAdapter 适配 v2.1.0 构造函数 API（`new CustomForm(player, title)`、`new ObservableString/Number/Boolean(value, {clientWritable: true})`、`DataDrivenScreenClosedReason.ClientClosed/ServerClosed`）
- 面板层迁移 beta 版（agentConsole/morePanel/settingsPanel/statsPanel/routes/entry/state），morePanel 取代 dev 的 chatInput/historyPanel
- responseSync 合并 beta 功能（refreshConversation 实时刷新 + isDuplicateUiUserEcho 去重），保留 dev 的 mcbews:text_resp 线协议
- 依赖升级：@minecraft/server-ui 2.1.0 + @minecraft/server 2.8.0；补齐 just-scripts/prettier 工具链（dev 基线缺失，pnpm v11 allowBuilds 配置 esbuild）
- 测试：4 个 DDUI 面板测试 + response-sync 测试移植，mock 适配 v2.1.0（closeButton、程序化关闭返回 ServerClose、failOnObservableCreate）

### Git Commits

| Hash | Message |
|------|---------|
| `6f38bca` | feat(ui): 移植 beta DDUI 面板到 @minecraft/server-ui v2.1.0 |

### 遗留（人工决策）

- `pnpm lint` 的 prettier 阶段仍失败：20 个 dev 基线文件（bootstrap/capabilities/router/toolPlayer/chunking/history/storage 等）不满足 prettier，dev 基线即存在问题（package.json 原本无 prettier 依赖）。已按 prd「bridge 层不修改」撤销格式化，建议单独 chore 处理。
- 手动 MC 客户端验证（prd 验收标准第 8 项，可选）：DDUI 主面板实时刷新、发送消息后面板保持打开、历史去重正确。

### Status

[OK] **Implemented & verified**（117 tests green，eslint/build 通过）


## Session 6: DDUI 流式打字机 + 会话管理协议化 + 面板重构

**Date**: 2026-08-08
**Task**: DDUI 流式打字机 + 会话管理协议化 + 面板重构
**Branch**: `feature/ddui-streaming-sessions`

### Summary

完成 DDUI 全面重构：流式打字机（分片排序/增量渲染/token统计），会话管理协议化（sessionClient + command_handlers + hook + broker_bridge），UI v2 数据结构（per-conversation桶/三级token统计/）

### Git Commits

| Hash | Message |
|------|---------|
| `930d145` | (see git log) |

### Status

[OK] **Completed**
