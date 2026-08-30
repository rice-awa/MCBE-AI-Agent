# DDUI 待审批与响应修复

## Goal

修复 DDUI 面板两个关联缺陷：(1) UI 不显示待审批工具请求，玩家无法在面板内直接审批；
(2) 聊天手动切换会话后，AI 响应因 `mcbews:text_resp` 最后一帧超出字节预算而无法送达面板。

## Background

- 审批系统当前 100% 服务端、纯文本（tellraw）：`worker._handle_approval_required` 生成
  `chunk_type="approval_required"` 的 StreamChunk，`broker_bridge._stream_chunk` 的 `else`
  分支以 tellraw 发送，addon 完全无感知（无 message id、无 handler、无 UI）。
- token/usage 传递：后端把完整 PydanticAI usage dict（含 `cache_*`/`details`/`requests`/
  `tool_calls`，约 286 字节）原样塞进 `mcbews:text_resp` 最后一帧的 `u` 字段，叠加
  `cid`（长会话 ID）+ 实际文本后超过 461 字节预算，抛 `FrameTooLargeError`。
  addon 侧又只解析 `u.i`/`u.o`（compact 格式），与 Python 的 `input_tokens`/`output_tokens`
  不匹配，导致 token 统计全 0。

## Requirements

### 子任务 1：待审批 UI 显示与主面板审批按钮
- 后端通过 `mcbews:text_resp`（或等价 scriptevent）把待审批请求的结构化信息送达 addon。
- addon 主面板显示「待审批 N 项」状态及待审批工具/参数摘要。
- 主面板提供「同意」「拒绝」按钮，点击后把决策发回后端并恢复 agent run。
- 复用现有 `PendingApprovalStore` + `handle_tool_approval` 决策路径，不新建审批状态机。

### 子任务 2：切换会话后响应丢失与 byte budget 修复
- 后端发送给 addon 的 usage 必须是 compact 格式 `{"i": N, "o": N}`，不得传完整 usage dict。
- 修复后切换会话（长 cid）+ 有 token 用量时，`mcbews:text_resp` 不再抛
  `FrameTooLargeError`，响应能正常流式送达面板。
- addon 侧 token 统计正常显示（input/output/合计）。

## Acceptance Criteria

- [ ] 触发需要审批的工具调用时，DDUI 主面板显示待审批状态与工具/参数摘要。
- [ ] 在主面板点击「同意」/「拒绝」后，agent run 正确恢复（同意→执行工具，拒绝→跳过）。
- [ ] 聊天中手动切换会话后发消息，AI 响应能正常流式显示到面板，无 byte budget 报错。
- [ ] 统计面板 token 数值非 0 且与后端 usage 的 input/output 一致。
- [ ] `python -m pytest`（审批/bridge 相关）通过；addon `tsc --noEmit` 通过。

## Notes

- 父任务负责集成验收与跨子任务一致性；实际实现由两个 child task 承担。
- 子任务 2 的 usage 精简修复同时解决「token 全 0」（字段名不匹配）与「byte budget 超限」两个症状。
