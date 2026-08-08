# 待审批 UI 显示与主面板审批按钮 — 执行计划

## 顺序

### 1. 后端：StreamChunk 携带审批元数据
- `models/messages.py` `StreamChunk` 新增可选字段：
  `approval_id: str | None`, `args_summary: str | None`, `approval_reason: str | None`,
  `batch_id: str | None`, `batch_size: int | None = None`, `batch_index: int | None = None`。
- `services/agent/worker.py` `_handle_approval_required`：构造 `StreamChunk` 时填充
  上述字段（数据在循环里已有：`approval_id`/`call.tool_name`/`args_summary`/
  `meta.get('reason')`/`batch_id`/`len(sibling_ids)`/循环 index）。
- 验证：`python -m pytest tests/ -k approval` 不破坏现有断言。

### 2. 后端：broker_bridge 发送审批帧到 addon
- `services/gateway/broker_bridge.py` `_stream_chunk`：
  - `chunk_type == "approval_required"` 分支：保留 tellraw（现有 `else` 路径或显式分支）。
  - 新增：构造 `approval_json = json.dumps({approval_id, tool_name, args_summary,
    reason, batch_id, batch_size, batch_index})`，调
    `McbewsV1Delivery(delivery, profile=self._profile).send_response(
      player_name=chunk.player_name, role="approval", text=approval_json,
      conversation_id=chunk.conversation_id)`。
  - 用 try/except 包裹，失败仅 log（不阻断 tellraw）。
- 验证：`broker_bridge` 相关单测；手动触发审批看 scriptevent 是否发出。

### 3. 后端：hook 路由审批决策命令
- `services/gateway/hook.py`：
  - 新增 `_TOOL_APPROVE_PREFIX = "MCBEWS|TOOL_APPROVE|"`、
    `_TOOL_DENY_PREFIX = "MCBEWS|TOOL_DENY|"`。
  - `on_player_message` 在 `_SESSION_REQ_PREFIX` 分支后，增加两个前缀匹配分支：
    ```python
    for prefix, approved in ((_TOOL_APPROVE_PREFIX, True), (_TOOL_DENY_PREFIX, False)):
        if player_event.message.startswith(prefix):
            approval_id = player_event.message[len(prefix):].strip()
            task = asyncio.create_task(
                self.handlers.handle_tool_approval(
                    state, approval_id, approved=approved,
                    player_name=player_event.sender,
                ), name=f"host-tool-approval:{state.id}")
            self._track(task)
            return
    ```
- 验证：`tests/test_tool_approval_command.py` 新增 UI 决策路径用例。

### 4. Addon：state 类型 + 常量
- `scripts/ui/state.ts`：
  - 新增 `ApprovalInfo` 类型（approval_id/tool_name/args_summary/reason/batch_id/
    batch_size/batch_index）。
  - `AgentUiStateV2` 新增 `pendingApprovals: Map<string, ApprovalInfo>`，
    `createAgentUiStateV2` 初始化 `new Map()`。
- `scripts/bridge/constants.ts`：
  - `TOOL_APPROVE_PREFIX = "MCBEWS|TOOL_APPROVE"`
  - `TOOL_DENY_PREFIX = "MCBEWS|TOOL_DENY"`

### 5. Addon：responseSync 解析审批帧
- `scripts/bridge/responseSync.ts` `handleChunk`：
  - 在 `ensureStreamingState` 之前，`if (chunk.r === "approval")` 分支：
    - 等待分片完整（`buffer.size < n` 时 return）。
    - 完整后 `JSON.parse(fullText)` → ApprovalInfo →
      `activeState.pendingApprovals?.set(info.approval_id, info)` →
      `activeState.refreshConversation?.()` → `chunkBuffers.delete(id)` → return。
  - `onMessageComplete`：开头 `if (role === "approval") return;`（审批帧不走 history）。
  - 类型：`AiRespChunk.r` 放宽为 `string`（已是）。

### 6. Addon：主面板审批按钮
- `scripts/ui/panels/agentConsole.ts`：
  - `buildTitleLine`/`buildStatusLine`：`pendingApprovals.size > 0` 时显示
    「⏳ 待审批 N 项」（取首个 info 的 tool_name）。
  - 在按钮区新增「同意」「拒绝」两个 button（放在「发送」「新会话」之后或独立行）：
    ```ts
    .button("同意", () => {
      const first = uiState.pendingApprovals.values().next().value;
      if (!first) return;
      // 复用 toolPlayer 发送决策命令
      const tp = world.getAllPlayers().find(p => p.name === TOOL_PLAYER_NAME);
      tp?.runCommand(`tell @s ${TOOL_APPROVE_PREFIX}|${first.approval_id}`);
      uiState.pendingApprovals.delete(first.approval_id);
      refreshConversation();
    })
    ```
    「拒绝」同理用 `TOOL_DENY_PREFIX`。
  - 无 pendingApprovals 时按钮仍显示但点击提示「无待审批」（或用条件禁用——
    DDUI button 无 disabled，改为点击提示）。
  - 需 `import { world } from "@minecraft/server"` 与 `TOOL_PLAYER_NAME`。

### 7. 验证
- `cd MCBE-AI-Agent-addon && npx tsc --noEmit`
- `python -m pytest tests/ -k "approval or broker_bridge"` 
- 手动：触发需审批工具 → 面板显示待审批 → 点同意/拒绝 → agent 恢复。

## Review Gates
- 步骤 1-3（后端）完成后跑审批相关 pytest。
- 步骤 4-6（addon）完成后跑 tsc。
- 步骤 7 整体集成验证。

## Rollback
- 各步骤独立提交；后端审批帧发送用 try/except 隔离，失败不影响 tellraw。
- addon 审批按钮失败不影响普通对话流（独立按钮 + 独立处理分支）。
