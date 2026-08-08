# Research: Python 侧协议改动点（B 部分）

- **Query**: 探索 Python 仓库的 broker_bridge、command_handlers、worker、mcbe-ws-sdk mcbews v1 delivery、addon-bridge-protocol.md、core/session 与 core/conversation 的现有协议点，为「流式打字机 + 会话管理协议化 + token 统计」提供改动点清单
- **Scope**: internal
- **Date**: 2026-08-08

## 1. broker_bridge.py — _ai_sync 全文与扩展性

文件：`services/gateway/broker_bridge.py`

### _ai_sync（broker_bridge.py:325-339）全文

```python
async def _ai_sync(self, state: ConnectionState, response: dict[str, Any]) -> None:
    player_name = response.get("player_name", DEFAULT_PLAYER_DISPLAY_NAME)
    role = response.get("role", "assistant")
    text = response.get("text", "")
    if not text:
        return
    delivery = self._delivery(state)
    if delivery is None:
        return
    v1 = McbewsV1Delivery(delivery, profile=self._profile)
    await v1.send_response(
        player_name=player_name,
        role=role,
        text=text,
    )
```

- **text_resp 构造**：仅读 `player_name` / `role` / `text` 三个字段；`text` 为空直接 return（不发帧）。**`send_response` 已支持可选 `response_id` 参数**（SDK delivery.py:31），但 host 侧 `_ai_sync` 未传入——目前无 id 关联能力。
- **能带额外字段**：`response` 是普通 dict，新增字段（如 `conversation_id`、`usage`）只需在此读取；但**块编码只包含 id/i/n/p/r/c 六个字段**（见 SDK codec），要传给 Addon 需扩展 SDK 编码（见第 4 节边界）。
- 分发入口 `_handle`（broker_bridge.py:126-150）：`dict` 按 `type` 字段路由：`run_command` / `ai_response_sync` / `game_message`，其余打 warning `broker_bridge_unknown_dict`。**新增 `type` 值（如 `session_resp`）可在此加分支。**

### 会话相关 handler 现状

- **无**：`broker_bridge.py` 中不存在 session 相关 handler；会话命令全部走命令文本通道（command_handlers.handle_conversation → tellraw 回复），没有结构化协议。

## 2. command_handlers.py — handle_conversation 10 个 action 与 handle_ui_chat

文件：`services/gateway/command_handlers.py`

### handle_conversation 入口（command_handlers.py:573-587）

```python
async def handle_conversation(self, state, option, player_name=None) -> None:
    parts = option.strip().split(None, 1) if option.strip() else []
    action = parts[0].lower() if parts else "状态"
    if action in ("状态", "status", "", "list", "列表", "已保存", "saved"):
        msg = await self._handle_conversation(state, option, player_name)
    else:
        lock = self.broker.get_session_lock(state.id, player_name)
        async with lock:
            msg = await self._handle_conversation(state, option, player_name)
    await self._send_player_reply(state, msg, source="conversation", player_name=player_name)
```

- 只读 action（status/list/saved）不拿锁；写 action 拿 `(connection, player)` 级锁。

### _handle_conversation 的 10 个 action 完整签名与返回（command_handlers.py:589-764）

返回类型均为 `TellrawMessage`（`self.protocol.create_*_message(text)`）。action 判定的同义词：

| action | 同义词 | 返回文本 |
|---|---|---|
| `new` | 新建/创建 | 成功：`已新建并切换到对话: #{short_id} {new_id}`；已存在：`对话 {id} 已存在，请使用 AGENT 对话 switch {id} 切换`（error） |
| `switch` | 切换 | `已切换到对话: #{short_id} {id}（{turns}轮）`；新会话 `已创建并切换到新会话: {display_id}`；无参 error：`请指定要切换的对话 ID\n用法: AGENT 对话 switch <ID>` |
| `clear` | 清除 | `对话 {conversation_id} 的历史已清除` |
| `status` | 状态/空 | `当前对话: {id}\n对话轮数: {turns}/{max_history_turns}\n压缩触发: {…}\n上下文携带: {启用/关闭}` |
| `list` | 列表 | 多行：`当前连接内对话:` + 每行 `• #{short_id} {conv_id}{" *" if 当前} - {title or "未命名"} - {n} 条消息` |
| `compress` | 压缩 | success/info：`{result}`（如 `压缩完成(LLM摘要): N轮 -> M轮`） |
| `save` | 保存 | 成功：`对话已保存: {session_id}`；失败 error：`{result}`（如 `对话历史为空，无法保存`） |
| `restore` | 恢复 | 成功：`{result}`（`已恢复会话 {session_id} 到对话 {id}，共 N 条消息`）；无参 error：`请指定要恢复的会话 ID\n用法: AGENT 对话 restore <保存ID>` |
| `saved` | 已保存 | `format_conversation_list` 输出（见 core/conversation.py:963-991），首行 `已保存的对话:` |
| `delete` | 删除 | 成功：`已删除会话: {session_id}`；无参 error：`请指定要删除的保存会话 ID\n用法: AGENT 对话 delete <保存ID>` |

- 无效 action：`无效选项，请使用: new [ID]/switch <ID>/clear/status/list/compress/save/restore <保存ID>/saved/delete <保存ID>`（command_handlers.py:761-764）
- 关键实现细节：
  - `new` 带参数时校验唯一（`_generate_unique_conversation_id`，command_handlers.py:766-782）；自动生成格式 `chat-YYYYMMDD-HHMMSS-ffffff-<uuid6>`（command_handlers.py:784-791）
  - `switch` 支持 `#2` 与 `2` 短 ID（`resolve_conversation_short_id`）
  - `restore`/`delete` 的 session_id 直接透传给 `conv_manager`（owner 校验在 core/conversation.py 内）
  - `actor = player_name or session.player_name`（command_handlers.py:597）

### handle_ui_chat 流程（command_handlers.py:429-466）

```python
async def handle_ui_chat(self, state, player_name, message) -> None:
    # 1. 日志 ui_chat_received
    # 2. 未登录：error "请先登录" + request.rejected trace，return
    # 3. 回写 user 回显：broker.send_response(state.id, {
    #      "type": "ai_response_sync", "player_name": ..., "role": "user", "text": message })
    # 4. await self.handle_chat(state, message, delivery="tellraw", player_name=player_name)
```

- 注意：`handle_chat` 内部会再次经 `_handle_chat_command` 外的正常路径（`_handle_chat_command` 也回写 user 回显 ai_response_sync，command_handlers.py:293-301 —— 两条路径都回写 user 回显）。

### _reply_target（command_handlers.py:81-83）

```python
def _reply_target(self, player_name: str | None) -> str:
    # 身份只来自当前事件；缺失时广播到全体（非业务性默认），不读连接级状态。
    return player_name or "@a"
```

## 3. worker.py — ai_response_sync 构造处、usage_dict、title 生成

文件：`services/agent/worker.py`

### ai_response_sync 构造（assistant 完成路径，worker.py:1113-1119）

```python
if response_text:
    await self.broker.send_response(connection_id, {
        "type": "ai_response_sync",
        "player_name": request.player_name or DEFAULT_PLAYER_DISPLAY_NAME,
        "role": "assistant",
        "text": response_text,
    })
```

- **usage_dict 位置**：同作用域 worker.py:1009-1010（`usage = event.metadata.get("usage"); usage_dict = usage if isinstance(usage, dict) else None`），在构造 ai_response_sync **之前**已可用；当前未随 ai_response_sync 发送，仅用于日志（worker.py:1108）与 `ExecutionResult.usage`。
- **conversation_id 来源**：`request.conversation_id`（ChatRequest 字段），同作用域多处使用（worker.py:1027-1028 等）。ai_response_sync dict 中**当前未带 conversation_id**。
- user 回显的 ai_response_sync 由 command_handlers 构造（见第 2 节），worker 不产生 user 回显。

### title 生成机制（worker.py:1042-1057, 1493-1578）

- 触发条件（worker.py:1042-1049）：历史写成功且 `_count_user_prompts(trimmed_history) == 1` 且 `mark_conversation_title_generating` 返回 True → `_schedule_title_generation(connection_id, player_name, conversation_id, request.content, model)`
- `_generate_title_for_conversation`（worker.py:1493-1535）：`title = await generate_conversation_title(first_user_message, model)` → `self.broker.set_conversation_title_if_connected(...)`；失败 `mark_conversation_title_failed`
- `set_conversation_title_if_connected`（core/queue.py:330-345）：`if not self.has_connection(connection_id): return None`；否则 `sessions.set_conversation_title(...)`（core/session.py:151-166：`metadata.title = title; metadata.title_status = "ready"`）
- **title 状态流转**：`pending` → `generating`（mark_conversation_title_generating）→ `ready` / `failed`（core/session.py:168-198）

## 4. mcbe-ws-sdk mcbews v1 delivery — 编码格式与扩展边界

### 编码函数（`mcbe-ws-sdk/src/mcbe_ws_sdk/profiles/mcbews_v1/codec.py:178-208`）

```python
def encode_text_response_commands(*, player_name, role, text, flow, response_id=None, profile=MCBEWS_V1) -> list[str]:
    message_id = response_id or f"resp-{uuid4().hex}"

    def encode_frame(content: str, index: int, total: int) -> str:
        return json.dumps(
            {"id": message_id, "i": index, "n": total, "p": player_name, "r": role, "c": content},
            ensure_ascii=False, separators=(",", ":"),
        )

    return flow.chunk_framed_scriptevent(
        text, message_id=profile.response_message_id, encode_frame=encode_frame, emit_empty=True,
    )
```

- **text_resp 块格式固定为 6 字段**：`id / i / n / p / r / c`（紧凑 JSON，无空格）。**增加字段（如 conversation_id、usage、delta/typing 标记）需要改 SDK 编码**（frame 结构 + Addon 侧 AiRespChunk 类型同步）。
- `flow.chunk_framed_scriptevent`（flow_control.py:131-156）：按 max_length 迭代切分，最多 10 次迭代收敛，不收敛抛 `ProtocolError`；`emit_empty=True` 保证空文本也发一帧。
- 注意 codec.py:25-27：**分片结构是 `scriptevent <message_id> <json>`** —— 每帧是独立 scriptevent，message_id 为 `mcbews:text_resp`，块内容为紧凑 JSON。

### McbewsV1Delivery.send_response（delivery.py:25-48）

- 签名：`send_response(*, player_name: str, role: str, text: str, response_id: str | None = None) -> int`（返回分片数）
- 前置 `await self._sleep(profile.response_prelude_delay)`（默认 0.5s），经 `outbound.send_chunked(payloads, "text_resp", "mcbews_v1_text_resp", delay=profile.response_chunk_delay)`（默认 0.15s）

### McbewsV1Profile 常量（profile.py）

```python
bridge_request_message_id = "mcbews:bridge_req"
bridge_response_prefix = "MCBEWS|BRIDGE"
ui_chat_prefix = "MCBEWS|UI_CHAT"
bridge_sender = "MCBEWS_BRIDGE"
response_message_id = "mcbews:text_resp"
request_version = 2
response_chunk_delay = 0.15
response_prelude_delay = 0.5
```

- 新增线协议 message id（如 `mcbews:session_req` / `session_resp`）应在此 dataclass 增加字段（冻结 dataclass，默认值向后兼容）。

### SDK 边界确认

- `requirements.txt:3`: `mcbe-ws-sdk>=0.1.0` —— **pip 依赖，非 vendored 子模块**（`mcbe-ws-sdk/` 目录是本地源码副本用于开发/测试，但发布边界是 pip 包）。
- CLAUDE.md 明确：「mcbe-ws-sdk 为 pip 依赖（>=0.1.0）；线协议与分片由 SDK 拥有，**勿改 SDK 源码除非另开 SDK PR**」。
- 结论：**协议字段扩展（text_resp 带 extra 字段、新增 session_req 通道）默认属于 SDK 改动，需要另开 SDK PR 或与 SDK 版本同步；宿主仓库内改 SDK 源码会被视为越界**。host 侧 `broker_bridge._ai_sync` 本身可以自由扩展（读 extra 字段 + 新 dict type 分支），但最终编码在 SDK。

## 5. docs/addon-bridge-protocol.md 结构

文件：`docs/addon-bridge-protocol.md`（215 行），现有章节：

1. 目标（线协议权威为 SDK mcbews v1）
2. 运行时拓扑
3. 线常量表（`mcbews:bridge_req` / `mcbews:text_resp` / `MCBEWS|BRIDGE` / `MCBEWS|UI_CHAT` / `MCBEWS_BRIDGE` / `v=2`；`mcbeai:ui_state` 动态属性键故意保留）
4. 链路概览：链路 A（Bridge 能力请求）/ 链路 B（UI Chat 上行）/ 链路 C（AI 文本同步 text_resp）
5. 请求格式（Python → Addon）：`scriptevent mcbews:bridge_req <json>`，字段 `request_id/capability/payload/v=2`
6. 专用方块能力（block_ops v1）
7. 响应分片格式（`MCBEWS|BRIDGE|<request_id>|<i>/<n>|<fragment>`）
8. UI 聊天分片（`MCBEWS|UI_CHAT|<msg_id>|<i>/<n>|<fragment>`）
9. 宿主接入点表（server.py / settings_map.py / hook.py / command_handlers.py / broker_bridge.py / ws_command_runner.py / addon constants.ts / SDK addon 参考）
10. 安装构建部署 / 调试步骤 / 验证桥接链路 / 当前桥接能力 / 聊天命令与 UI 共存说明（含"DDUI 类型暂不可用"旧说明，第 197 行——**已过时**，现在 formAdapter 已用 v2.1.0 DDUI）/ 当前限制 / 破坏性说明

- **新增 session_req/resp 建议位置**：新增一节「会话管理协议（session v1）」放在「链路概览」之后、或作为新链路 D；同时更新线常量表（新 message id 与前缀）、宿主接入点表。会话响应建议复用现有 text_resp 下行通道（player_name 分片）或在响应块中加 `kind` 字段区分。

## 6. core/session.py — ConversationMetadata 字段与短 ID

文件：`core/session.py`

### ConversationMetadata（core/session.py:17-23）

```python
@dataclass
class ConversationMetadata:
    conversation_id: str
    short_id: int
    title: str | None = None
    title_status: str = "pending"
```

### 关键方法

- `normalize_conversation_id`（session.py:26-29）：空 → `"default"`（`DEFAULT_CONVERSATION_ID`）
- `make_session_key(connection_id, player_name, conversation_id)`（session.py:32-39）：`(connection_id, player, normalized_cid)`；player 空 → `"__anonymous__"`（`DEFAULT_PLAYER_KEY`）
- `ensure_conversation_metadata`（session.py:97-130）：按 `(conn, player)` 分配递增短 ID（`_next_conversation_short_id` 从 1 起）；同 conversation_id 复用已有短 ID
- `resolve_conversation_short_id(conn, player, "#2"|"2")`（session.py:132-149）：剥 `#` 前缀 + isdigit 校验 + 查表
- title 状态机方法：`set_conversation_title`（→ready）/ `mark_conversation_title_generating`（pending 才可标记）/ `mark_conversation_title_failed`（仅 generating → failed）（session.py:151-198）
- `list_player_conversation_metadata(conn, player)`（session.py:213-234）：从 histories/metadata 键收集 conversation_id，按 `short_id` 排序
- `get_active_conversation_id` / `set_active_conversation_id`（session.py:75-95）
- `list_player_conversations(conn, player)`（session.py:374-387）：返回 `[(conversation_id, message_count)]` 排序列表

### core/conversation.py — session_id 格式与校验

文件：`core/conversation.py`

- **session_id 生成**（conversation.py:663-665）：`session_id = f"{connection_id}_{timestamp}_{uuid4().hex[:8]}"`，其中 timestamp 为 `%Y%m%d_%H%M%S_%f`。文件存储为 `{conversations_dir}/{session_id}.json`
- **路径校验**（conversation.py:716-727）：`normalized.name != session_id or normalized.suffix` → 抛 `ValueError(f"非法会话 ID: {session_id}")`；再 resolve 后确认在 storage_root 内。**session_id 含 `_` 与 `.` 字符会被 suffix 检查拒绝**（"会话不存在"）。
- **owner 校验**（conversation.py:729-739）：`data.get("player_name") != player_name` → `无权访问该会话：所有者不匹配`；精确字符串匹配
- **save_conversation**（conversation.py:629-714）：`(bool, str)` 返回；title 取 `metadata.title or "未命名"`；保存 JSON 含 `metadata: {title, template, conversation_id, custom_variables}`
- **restore_conversation**（conversation.py:818-861）：owner 校验 → bump invalidation epoch → `set_conversation_history(conn, player, messages, conversation_id)`；返回 `已恢复会话 {session_id} 到对话 {cid or 'default'}，共 N 条消息`
- **list_conversations**（conversation.py:863-910）：仅返回 `data.get("player_name") == player_name` 的文件；字段 `session_id/player_name/conversation_id/title/provider/model/created_at/updated_at/message_count`；按 updated_at 倒序
- **delete_conversation**（conversation.py:912-961）：owner 校验后 `file_path.unlink()`
- **format_conversation_list**（conversation.py:963-991）：`已保存的对话:` + `{i+1}. [{session_id}] {title} | {player} | {provider}/{model} | 对话:{conversation_id} | {n}条消息 | {updated[:16]}`；空 → `暂无保存的对话`
- **ConversationMetadata pydantic 模型**（conversation.py:48-61）：`connection_id, player_name, conversation_id, provider, model, created_at, updated_at, message_count, title, template, custom_variables`（与运行时 dataclass 是两套）

## 关键改动点清单（供实现阶段参考）

1. **text_resp 带额外字段**（conversation_id / usage / 流式标记）：SDK codec.encode_text_response_commands 的 frame 加字段 + Addon AiRespChunk 同步 —— 需 SDK PR
2. **session_req 新通道**：SDK profile 加 message id + Addon router 加监听 + host 新 handler 分支 —— 需 SDK PR（或复用 bridge_req capability 机制：`capability="session_*"` 走现有通道，router.ts 加 handler 即可，零 SDK 改动）
3. **ai_response_sync 增强**：worker.py:1113-1119 可加 `conversation_id`、`usage`（worker.py:1009 已有 usage_dict）；broker_bridge._ai_sync 透传；`send_response` 的 `response_id` 参数现成可用
4. **usage 数据源**：`event.metadata.get("usage")`（dict 形态）——唯一来源
5. **流式打字机**：text_resp 目前是"整块完成才发"（worker 只在 response_text 齐全后 send_response），流式需要 worker 在 stream 事件中增量发 ai_response_sync 或新增 type —— 注意 hook 非阻塞约束（asyncio.create_task）与 SDK 流控（chunk_delays.text_resp 0.15s）
6. **会话协议化**：现有 10 个 action 全部返回文本（create_*_message）；结构化返回需要新增序列化路径（如 `{ok, action, data}` JSON 经 text_resp 或新通道），或让 Addon 侧解析 tellraw 文本
