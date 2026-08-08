# 待审批 UI 显示与主面板审批按钮 — 技术设计

## 方案总览

复用现有 `mcbews:text_resp` 分片通道传输审批信息，addon 侧按 `r==="approval"` 区分，
不新增 SDK 消息 ID、不新建审批状态机。决策命令复用 `MCBEWS|SESSION` 同款 `tell @s` 通道。

```
worker._handle_approval_required
  └─ StreamChunk(chunk_type="approval_required", content=prompt_text, approval_meta=...)
       │
broker_bridge._stream_chunk  (chunk_type == "approval_required")
  ├─ (保留) tellraw 发送人类可读 prompt   ← 现有行为，不改
  └─ (新增) McbewsV1Delivery.send_response(role="approval", text=approval_json)
       │   frame: {id,i,n,p,r:"approval",c:approval_json,cid,t}
       ▼  scriptevent mcbews:text_resp
addon responseSync.handleChunk
  └─ r==="approval" → JSON.parse(c) → ApprovalInfo → uiState.pendingApprovals.set(id, info)
       └─ refreshConversation()  → 主面板显示「待审批 N 项」

玩家点击「同意」按钮
  └─ toolPlayer.runCommand("tell @s MCBEWS|TOOL_APPROVE|{approval_id}")
       ▼  PlayerMessage (sender=MCBEWS_BRIDGE)
hook.on_player_message  (前缀 MCBEWS|TOOL_APPROVE|)
  └─ handle_tool_approval(state, approval_id, approved=True)
       └─ store.record_decision → _resume_from_completed_batch  ← 现有路径
```

## 数据契约

### 审批帧（后端 → addon）

复用 `AiRespChunk`，`r="approval"`，`c` 为审批 JSON（非对话文本）：

```json
{
  "id": "appr-<uuid>",
  "i": 1, "n": 1,
  "p": "<player_name>",
  "r": "approval",
  "c": "{\"approval_id\":\"ap-xxx\",\"tool_name\":\"run_world_command\",\"args_summary\":\"/time set day\",\"reason\":\"需要确认\",\"batch_id\":\"b-1\",\"batch_size\":2,\"batch_index\":1}",
  "cid": "<conversation_id>",
  "t": "<conversation_title>"
}
```

`c` 内层 `ApprovalInfo` 字段：
- `approval_id` — 决策时回传的 id
- `tool_name` — 工具名
- `args_summary` — 人类可读参数摘要（复用 `summarize_args_for_player`）
- `reason` — 审批原因
- `batch_id` / `batch_size` / `batch_index` — 批次语义

### 决策命令（addon → 后端）

```
tell @s MCBEWS|TOOL_APPROVE|<approval_id>
tell @s MCBEWS|TOOL_DENY|<approval_id>
```
前缀 `MCBEWS|TOOL_APPROVE|` / `MCBEWS|TOOL_DENY|`，载荷为纯 approval_id（非 JSON）。
后端剥离前缀后作为 `content` 传给 `handle_tool_approval(state, content, approved=...)`，
走现有 `resolve_target_approval_id` → `_apply_approval_decision` 路径（显式 id 命中）。

## 改动点

### 后端 Python

1. `services/gateway/broker_bridge.py` `_stream_chunk`：
   - `chunk_type == "approval_required"` 分支：保留 tellraw，新增调用
     `McbewsV1Delivery.send_response(role="approval", text=approval_json, conversation_id=...)`。
   - approval_json 从 StreamChunk 现有字段构造。需确认 StreamChunk 是否携带 approval_meta；
     若无，则在 `worker._handle_approval_required` 把 `approval_id/tool_name/args_summary/
     reason/batch_id/batch_size/batch_index` 放入 StreamChunk 的扩展字段（StreamChunk 是
     dataclass，可加可选字段）。
2. `services/gateway/hook.py` `on_player_message`：
   - 仿照 `_SESSION_REQ_PREFIX`，新增 `_TOOL_APPROVE_PREFIX`/`_TOOL_DENY_PREFIX`，
     匹配后调用 `self.handlers.handle_tool_approval(state, approval_id, approved=True/False,
     player_name=player_event.sender)`。
3. `services/gateway/command_handlers.py`：`handle_tool_approval` 签名已兼容
   `content=str`，无需改动（显式 id 路径已存在）。

### Addon TypeScript

1. `scripts/ui/state.ts` `AgentUiStateV2`：
   - 新增 `pendingApprovals: Map<string, ApprovalInfo>` + `ApprovalInfo` 类型。
   - `createAgentUiStateV2` 初始化空 Map。
2. `scripts/bridge/responseSync.ts` `handleChunk`：
   - `r === "approval"` 时：`JSON.parse(c)` → ApprovalInfo →
     `activeState.pendingApprovals.set(info.approval_id, info)`，调 `refreshConversation()`。
   - 不进 history、不设 isStreaming、不更新 lastResponsePreview。
3. `scripts/ui/panels/agentConsole.ts`：
   - `buildStatusLine`/`buildTitleLine`：`pendingApprovals.size > 0` 时显示
     「⏳ 待审批 N 项（batch: tool_name）」。
   - 新增「同意」「拒绝」按钮（仅 pendingApprovals 非空时显示/启用）：
     点击 → `toolPlayer.runCommand("tell @s MCBEWS|TOOL_APPROVE|<id>")` →
     `pendingApprovals.delete(id)` → refresh。
   - 决策后后端 resume；若仍有同批其他项，后端会再发审批帧更新计数。
4. `scripts/bridge/constants.ts`：新增 `APPROVAL_PREFIX`、`APPROVAL_REQ_PREFIX`、
   `TOOL_APPROVE_PREFIX`、`TOOL_DENY_PREFIX` 常量。

## 边界与风险

- **审批帧不进 history**：`handleChunk` 的 `r==="approval"` 分支在 history 写入前 return，
  避免污染对话。`onMessageComplete` 也需跳过 `role==="approval"`。
- **批次多项**：后端为批内每个 tool 各发一帧 `approval`，addon 累积到 Map；
  全部决策后后端 resume，不再发新审批帧，Map 自然清空（或 resume 后发一帧
  `r="approval_clear"` 清空——见下「待定」）。
- **审批帧分片**：approval_json 通常 < 200 字符，单分片；极端长 args_summary 时
  `send_response` 会分片，addon 重组逻辑已支持（按 i/n 拼接 c）。
- **player_name 一致性**：审批帧 `p` = 当前 player，与决策命令 sender 一致。

## 待定

- 审批完成后的「清空」信号：是否需要后端显式发 `r="approval_clear"` 帧？
  倾向不需要——addon 决策后本地 delete，后端 resume 后若批内仍有未决项会再发帧，
  若全部决完则不再发，计数自然归零。仅边缘场景：后端 TTL 过期清理时 addon 不知情，
  需刷新时发现 Map 项已 stale——可接受（玩家可手动关闭面板重开）。
