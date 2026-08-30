# 待审批 UI 显示与主面板审批按钮

## Goal

让 DDUI 主面板感知待审批工具请求，显示审批状态与工具/参数摘要，并提供「同意」「拒绝」
按钮直接在 UI 内完成审批，复用服务端已有的 `handle_tool_approval` 决策与 resume 路径。

## Requirements

- 后端在生成 `approval_required` StreamChunk 时，除现有 tellraw 提示外，额外通过
  `mcbews:text_resp`（scriptevent）把结构化审批信息送达 addon。
  - 复用 `AiRespChunk` 帧格式，新增字段 `ap`（approval payload）：
    `{approval_id, tool_name, args_summary, reason, batch_id, batch_size}`。
  - `r` 角色用 `"approval"`，与普通 `assistant` 区分；不进入对话 history。
- addon 侧 `responseSync.ts` 识别 `r === "approval"` 帧，更新 `uiState.pendingApprovals`
  （Map<approval_id, ApprovalInfo>），并触发面板刷新。
- 主面板 `agentConsole.ts`：
  - `pendingApprovals` 非空时，状态行/标题显示「⏳ 待审批 N 项」。
  - 新增「同意」「拒绝」按钮：点击后发送 `MCBEWS|TOOL_APPROVE|{approval_id}` /
    `MCBEWS|TOOL_DENY|{approval_id}` 到后端，并从 `pendingApprovals` 移除该项。
  - 无待审批时隐藏审批按钮（或禁用），避免误触。
- 后端 `hook.on_player_message` 识别 `MCBEWS|TOOL_APPROVE|{id}` /
  `MCBEWS|TOOL_DENY|{id}` 前缀，路由到 `handle_tool_approval(state, id, approved=...)`。
- 审批决策完成后，后端通过 `mcbews:text_resp`（或现有 tellraw）反馈结果；
  addon 收到后清空对应 `pendingApprovals` 项。

## Constraints

- 不新建审批状态机；复用 `PendingApprovalStore` + `handle_tool_approval`。
- `r === "approval"` 帧不得污染 `conversations[*].history`，仅供 UI 实时展示。
- 审批帧也应受 flow control 分片保护；单帧 payload 小（< 256 字符），通常单分片。
- 批次语义保留：多 tool 同批时全部决策后才 resume；UI 需展示批次剩余项数。

## Acceptance Criteria

- [ ] 触发需审批工具时，主面板显示「待审批 N 项」与工具名/参数摘要。
- [ ] 点击「同意」→ 对应工具执行，agent run 恢复；点击「拒绝」→ 跳过该工具。
- [ ] 审批完成后面板状态恢复正常，待审批计数归零。
- [ ] 多 tool 批次场景：逐项决策，全部完成后才 resume（与聊天命令行为一致）。
- [ ] addon `tsc --noEmit` 通过；审批相关 pytest 通过。

## Notes

- 决策命令复用 `MCBEWS|SESSION` 同款 `tell @s` 通道（tool player 发送，sender=MCBEWS_BRIDGE），
  Python `hook` 按前缀路由，不经过 SDK bridge/UI_CHAT 分支。
- approval_id 省略时的智能解析仍由 `handle_tool_approval` 内 `resolve_target_approval_id` 处理；
  UI 按钮始终携带显式 id，简化路径。
