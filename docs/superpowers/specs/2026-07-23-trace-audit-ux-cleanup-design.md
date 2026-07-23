# Trace Audit UX Cleanup Design

Date: 2026-07-23  
Status: draft (pending user review)

## Goal

让 Agent Trace 审计工作台能**一眼看出「玩家说了什么 → 模型回了什么」**，去掉写入与展示里的镜像冗余，并补上真正可用的 **删除当前 trace / 清空 journal**（确认后落盘）以及 **导出当前 trace 为 JSON**。

## Background

现状问题：

1. `model.request.completed` 的 `payload.messages` 已是 PydanticAI 的 request/response pair，但检查器只 dump 整坨 JSON，没有对话轮次结构。
2. 写入存在镜像键：`tool_args`≡`parameters`、`tool_result`≡`result`、`content`≡`final_response`。
3. 前端「清除」只清筛选条件，不碰 journal；用户期望真正删除。
4. 仅支持「复制事件」，无法一键导出整条 trace。

相关代码：

- `services/agent/trace.py` — `TraceRecorder` / `record_model_messages` / `record_tool_*`
- `services/agent/trace_query.py` — 只读聚合
- `services/agent/trace_api.py` — GET-only HTTP API + 静态前端
- `web/trace/` — 审计工作台

## Decisions (locked)

| 项 | 决策 |
|----|------|
| 对话结构 | **前端优先**：写入仍保持一条 `model.request.completed` pair；UI 渲染对话轮次 |
| 去冗余 | **写入 + 前端** 都做；读侧兼容旧 journal |
| 清除 | **删除当前 trace + 清空全部 journal**；独立按钮 + 确认；筛选区「清除」行为不变 |
| 导出 | **纯前端** 下载当前 `GET /api/traces/{id}` 详情 JSON |
| 方案 | 方案 1：前端对话渲染 + 最小写入清理 + 本地 DELETE API |

## Non-Goals

- 不拆 `model.request` / `model.response` 两条事件
- 不升 `schema_version`（仍为 1；字段收敛向后兼容）
- 不改 `runtime_harness_tools.jsonl`
- 不做按玩家/时间范围清除
- 不做远程鉴权（本地 loopback 运维面）
- 不提供「精简对话版」导出——仅完整 detail JSON
- 不压缩 messages 内部 null 字段（如 `id: null`）

## Architecture Overview

```
Worker / TraceRecorder          TraceQuery                Trace API              Frontend
─────────────────────          ──────────                ────────              ────────
emit model pair (unchanged)  → read JSONL as-is
record_* without mirrors     → compat resolve keys    → GET detail
                             → models[].*preview      → DELETE trace / clear
                                                      → static UI
                                                                             dialogue cards
                                                                             timeline summary
                                                                             confirm + delete/clear
                                                                             Blob export
```

## 1. Write Path — Dedup

### 1.1 Keep

- `model.request.completed`：`payload.messages = [request_msg, response_msg?]` pair（worker 配 pair 逻辑不变）
- attributes：`pair_index`, `provider`, `finish_reason`, `usage`（末对）, 身份字段（`message_id` / `connection_id` / `player_name` / `conversation_id`）
- messages 内部 PydanticAI 序列化结构**原样**（不剥离内部 `usage` / null 字段，避免二次加工失真）

### 1.2 Change (`services/agent/trace.py`)

| API | Before | After |
|-----|--------|-------|
| `record_tool_call` | `payload = {tool_args, parameters}` 相同值 | 只写 **`tool_args`** |
| `record_tool_result` | `payload = {tool_result, result}` | 只写 **`tool_result`** |
| `record_final_response` | `payload = {content, final_response}` | 只写 **`final_response`** |
| `record_model_messages` | `model_name` 常缺 | 从 response 抽出写入 `attributes.model_name`（若有） |

`_PAYLOAD_ALLOWED_KEYS` **保留**旧键名（`parameters` / `result` / `content` / `user_message` 等），便于读旧 journal；**新写入不再产生镜像键**。

原则：**消息正文原样；元数据只放 attributes 一份**。不在 `model.request.completed` 的 payload 顶层再塞 `usage` / `provider` / `model_name`（当前 `record_model_messages` 已是这样，保持）。

### 1.3 Read compatibility

统一解析（query 层 + 前端）：

```
tool_args   = payload.tool_args ?? payload.parameters
tool_result = payload.tool_result ?? payload.result
final_text  = payload.final_response ?? payload.content
```

## 2. Query Layer — Derived Model Summaries

`TraceQuery._group_models` 为每条 model 事件增加派生字段（**不改 `events` 原文**）：

| Field | Source |
|-------|--------|
| `user_preview` | 首个 `part_kind == "user-prompt"` 的 content，截断约 80 字 |
| `assistant_preview` | response 首个 `part_kind == "text"` 的 content，截断约 80 字 |
| `tool_names` | response 中所有 `part_kind == "tool-call"` 的 `tool_name` 列表 |
| 既有字段 | `finish_reason` / `usage` / `provider` / `model_name`：attributes 优先，payload 作 fallback |

当 content 未开启（无 payload）时，preview 字段为 `null` 或省略，前端退回现有 provider/model 展示。

`get_trace` 响应形状（增量字段示例）：

```json
{
  "summary": { "...": "..." },
  "events": [{ "event_name": "...", "payload": {} }],
  "attempts": [],
  "models": [
    {
      "event_id": "...",
      "event_name": "model.request.completed",
      "user_preview": "现在呢",
      "assistant_preview": "你是指再查一次背包吗？…",
      "tool_names": ["get_inventory_snapshot"],
      "usage": {},
      "payload": { "messages": [] }
    }
  ],
  "tools": [],
  "approvals": [],
  "delivery": []
}
```

工具聚合 `_group_tools` 读侧：优先 `tool_args` / `tool_result`，fallback 旧键 `parameters` / `result`。

### 2.1 Journal mutation helpers

新增 journal 改写能力（建议挂在 `TraceQuery` 或同包小函数，避免与 recorder 热路径耦合）：

```python
def delete_trace(path: Path, trace_id: str) -> int:
    """Rewrite JSONL without lines matching trace_id.
    Returns removed event count.
    Atomic write (temp + replace), same style as TraceRecorder.
    Malformed lines: keep them (do not drop unknown data).
    """

def clear_journal(path: Path) -> None:
    """Atomically write empty journal (preserve parent dir)."""
```

并发语义：

- 与 recorder writer 可能竞态；删除/清空使用 atomic replace。
- 接受「清空/改写瞬间可能丢 1 条正在写入的行」的本地运维语义，文档说明即可。
- 改写后下一次 GET 重新 load；前端刷新列表并清空选中。

## 3. API — Delete / Clear

当前 `TraceAPIServer` 为 GET-only。扩展为**本地运维写操作**（默认绑定 loopback 配置）。

### 3.1 Endpoints

| Method | Path | Body | Effect |
|--------|------|------|--------|
| `DELETE` | `/api/traces/{trace_id}` | `{"confirm": true}`（必须） | 从 journal 删除该 `trace_id` 的所有行 |
| `DELETE` | `/api/traces` | `{"confirm": "CLEAR_ALL"}`（必须精确匹配） | 清空整个 journal |

约定：

- `Content-Type: application/json`
- 删除当前：body 缺省或 `confirm !== true` → **400** `{ "error": "confirm_required" }`
- 清空全部：`confirm` 必须精确等于字符串 `"CLEAR_ALL"`，否则 **400**
- 成功删除：`200` `{ "ok": true, "removed_events": N, "trace_id": "..." }`
- 成功清空：`200` `{ "ok": true, "cleared": true }`
- 未知 trace：`404` `{ "error": "not_found", "trace_id": "..." }`
- 写盘失败：`500` `{ "error": "write_failed", "message": "..." }`（不伪造成功）
- 其它非 GET 方法（除上述 DELETE）仍 **405**

### 3.2 Config surface

`GET /api/config` 增加：

```json
{
  "read_only": false,
  "mutations_enabled": true,
  "mutations": ["delete_trace", "clear_journal"]
}
```

首期 **mutations 始终开启**（本地审计台）。文档标明：服务绑定非 loopback 时勿暴露公网。后续若需开关再加 `agent_trace_api_mutations` 配置项。

## 4. Frontend

### 4.1 Dialogue rendering（检查器）

当 `event_name === "model.request.completed"` 且 `payload.messages` 为数组：

**默认不**展示整坨「LLM messages」JSON。改为：

1. **轮次卡片**
   - **玩家**：`user-prompt` parts（多段合并）
   - **模型**：`text` parts；无 text 时显示 `(无文本，仅 tool-call)`
   - **Tool calls**：名 + 格式化 args（args 字符串若为 JSON 则 pretty）
   - **Tool returns**：若本 pair 的 request 侧含 `tool-return`（多轮工具后续请求），单独小节
2. **Usage / finish_reason / provider / model_name**：来自 attributes（fallback payload/response）
3. **可折叠**「Raw messages JSON」与「Raw event JSON」

content 关闭时：显示与现有一致的 metadata note，不伪造正文。

### 4.2 Timeline summary

时间线 model 事件副信息优先用 `models` 聚合 preview：

```
model.request.completed
#12 · +1.2s · 「现在呢」→ get_inventory_snapshot
```

无 preview 时退回现有 `provider/model` bits。

### 4.3 Inspector 去冗余展示

- Tool：只显示一组 args / 一组 result（兼容旧键解析）
- Final：只显示 `final_response`（fallback `content`）
- 展示顺序：**结构化内容 → 折叠 raw payload → 折叠 raw event**，避免同一屏重复 dump

### 4.4 清除 UI

| 控件 | 位置 | 行为 |
|------|------|------|
| **清除**（现有） | 筛选区 | **不变**：只清筛选 |
| **删除本条** | 时间线 header（有选中 trace 时显示） | `window.confirm` → `DELETE /api/traces/{id}` body `{"confirm":true}` → toast → 清选中 → 刷新列表 |
| **清空全部** | 顶栏 actions（刷新旁） | `window.confirm`（强调不可恢复 + journal path）→ `DELETE /api/traces` body `{"confirm":"CLEAR_ALL"}` → 刷新 |

确认文案示例：

- 删除：`确定删除 trace {id}？此操作从 journal 移除该 trace 的所有事件，不可恢复。`
- 清空：`确定清空 journal？\n{path}\n此操作不可恢复。`

取消 confirm 时**不发请求**。

### 4.5 导出 UI

- 按钮 **导出 JSON**：时间线 header（与删除并列），仅在有 `state.detail` 时启用
- 实现：`JSON.stringify(state.detail, null, 2)` → `Blob` → `<a download="trace-{id}.json">`
- 成功 toast：`已开始下载`

### 4.6 Fetch extension

现有 `fetchJson` 仅 GET。新增 mutation 辅助：

```js
async function fetchMutation(url, { method, body, controller }) { /* ... */ }
```

错误通过 toast / 区域错误态展示，不静默失败。

## 5. Files to Touch

| File | Change |
|------|--------|
| `services/agent/trace.py` | 去镜像写入；`model_name` 抽出 |
| `services/agent/trace_query.py` | model preview；tool 键兼容；`delete_trace` / `clear_journal` |
| `services/agent/trace_api.py` | DELETE handlers；config mutations 字段 |
| `web/trace/index.html` | 删除本条、清空全部、导出 JSON 按钮 |
| `web/trace/assets/app.js` | 对话渲染、去冗余 inspector、mutation、export |
| `web/trace/assets/styles.css` | 对话卡片、危险按钮样式 |
| `tests/test_agent_trace.py` | 无镜像键断言；`model_name` 抽出 |
| `tests/test_trace_query.py` | preview + delete/clear |
| `tests/test_trace_api.py` | DELETE 契约 |
| `tests/test_trace_integration.py`（可选） | 兼容读旧 fixture |

## 6. Error Handling

| Case | Behavior |
|------|----------|
| DELETE 无 / 错误 confirm | 400，前端提示 |
| trace 不存在 | 404，toast |
| journal 不可写 | 500，toast，UI 状态不变 |
| 并发 append during rewrite | atomic replace；可能丢极短窗口内新行；可接受 |
| content 模式关闭 | 对话卡片显示「无正文」说明 |
| 导出时无 detail | 按钮 hidden/disabled |
| 取消 confirm | 无网络请求、无副作用 |

## 7. Testing

1. **Unit (`trace`)**：新 `record_*` payload 键集合无镜像；旧键仍在 allowlist
2. **Query**：含 user-prompt 的 pair → 正确 `user_preview` / `assistant_preview` / `tool_names`；`delete_trace` 只删目标；`clear_journal` 后 list 空；畸形行在 delete 时保留
3. **API**：DELETE 契约、错误码、GET 路径不受影响
4. **Frontend 手动验收**：
   - 打开含 tool 的 trace → 检查器见玩家 / 模型 / tool 分段
   - 导出文件可被 `json.load`
   - 取消 confirm 不发请求
   - 删除后列表消失且 hash 清空
   - 筛选「清除」仍只清筛选

## 8. Acceptance Criteria

- [ ] `model.request.completed` 检查器默认呈现「玩家 → 模型 [→ tools]」，非整坨 messages
- [ ] 时间线 model 事件在 content 开启时带 user/tool 摘要
- [ ] 新写入无 `parameters` / `result` / `content` 镜像键
- [ ] 旧 journal 工具参数/结果与终态响应仍可显示
- [ ] 删除本条 / 清空全部：确认后落盘，取消无副作用
- [ ] 筛选区「清除」行为不变
- [ ] 导出下载完整 `get_trace` JSON
- [ ] 现有 trace 相关测试通过；新测覆盖上述路径

## 9. Implementation Order

1. `trace.py` 去镜像 + 单测
2. `trace_query.py` preview + delete/clear + 单测
3. `trace_api.py` DELETE + config + 单测
4. 前端 HTML/CSS/JS：对话渲染 + 导出 + 删除/清空
5. 用真实 journal 手动验收一遍

## 10. Out of Scope Follow-ups (optional later)

- 配置开关 `agent_trace_api_mutations`
- 精简对话导出格式
- 自绘 confirm modal（替代 `window.confirm`）
- 写入侧拆分 `model.request` / `model.response` 事件
- 按玩家 / 时间范围批量删除
