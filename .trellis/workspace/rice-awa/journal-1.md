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
