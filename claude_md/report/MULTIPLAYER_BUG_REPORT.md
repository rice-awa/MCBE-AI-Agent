# 多人会话串扰 BUG 分析报告

> 日期: 2026-05-02
> 测试日志: 神奇的日志.log / 神奇的日志2.log / 神奇的日志3.log
> 涉及玩家: WANWEIyee (UI 面板), fantong7038 (聊天框), MCBEAI_TOOL (工具人)
> 修复结果: 见 [`claude_md/fix/MULTIPLAYER_SESSION_FIX.md`](../fix/MULTIPLAYER_SESSION_FIX.md)

---

## 一、问题描述

多人同时使用 AI Agent 时存在三个核心问题：

1. **玩家识别错误** — AI 被某个玩家问"你是谁"时，回答了另一个玩家的名字
2. **上下文/对话历史互串** — A 玩家的对话历史被 B 玩家的请求读到
3. **UI (Addon) 侧多用户标识一致** — 所有 UI 用户的消息经同一 connection_id 送达，导致后端无法区分

---

## 二、根因分析

### 2.1 核心架构缺陷：1 个 WebSocket 连接 = 1 个 connection_id = 所有玩家

MCBE WebSocket 服务器的工作模型是：

```
Minecraft 世界 ←—— 单个 /wsserver 连接 ——→ Python 后端
```

游戏只建立**一个 WebSocket 连接**（`/wsserver` 只能指向一个地址）。所有玩家的消息（不论是聊天框命令还是 Addon UI）都通过同一个连接传输。因此：

- `ConnectionState` 只有一个 `id` 和一个 `player_name`
- `MessageBroker` 的 `_conversation_histories` 按 `connection_id` 分桶
- 所有玩家共享同一份对话历史

**这是整个问题的根源。**

### 2.2 player_name 只记录了第一个说话的人

`server.py:212-214`:
```python
# 更新玩家名称
if player_event.sender and not state.player_name:
    state.player_name = player_event.sender
```

`_handle_ui_chat_message` 也是：
```python
if player_name and not state.player_name:
    state.player_name = player_name
```

`state.player_name` 一旦被设置就**不再更新**。所以：
- fantong7038 先发了消息，`state.player_name = "fantong7038"`
- WANWEIyee 随后通过 UI 发消息时，`state.player_name` 依然是 `"fantong7038"`
- ChatRequest 里的 `player_name` 因此一直是 `"fantong7038"`

### 2.3 对话历史按 connection_id 分桶导致串上下文

`MessageBroker._conversation_histories` 的键是 `connection_id`（UUID），不是 player_name。

由于只有一个连接：
- fantong7038 和 WANWEIyee 的消息全部写入同一个 `history[connection_id]`
- Worker 处理 WANWEIyee 的请求时，加载的历史包含 fantong7038 的对话内容
- LLM 拿到混合历史，自然会"串"

日志证据 — `神奇的日志2.log:12`:
```
response=君乃问者，自号"fantong7038"。
```
WANWEIyee 问 "你是谁"，AI 回答了 "fantong7038"，因为 `player_name` 和历史都来自 fantong7038。

### 2.4 AI 响应同步到 UI 也是基于错误的 player_name

`worker.py:348`:
```python
await self.broker.send_response(connection_id, {
    "type": "ai_response_sync",
    "player_name": request.player_name or "Player",
    ...
})
```

`request.player_name` 来自 `ChatRequest`，而 `ChatRequest` 又来自 `state.player_name`（即第一个说话者）。Addon 侧 `responseSync.ts` 依赖 `player_name` 查找目标玩家（`players.find(p => p.name === playerName)`），结果响应永远写到第一个玩家的 UI 面板。

### 2.5 ConnectionState 上的可变状态被共享

以下字段都是"连接级"而非"玩家级"的：
- `context_enabled` — 一个人关了上下文，所有人都关了
- `current_provider` — 一个人切模型，所有人都变了
- `current_template` / `custom_variables` — 同理

---

## 三、日志中的具体证据

| 时间 | 事件 | 说明 |
|------|------|------|
| 15:52:02 | `ui_chat_received player=WANWEIyee` | WANWEIyee 通过 UI 发消息 |
| 15:53:28 | `command_received player=fantong7038` | fantong7038 通过聊天框发消息 |
| 15:53:28 | `processing_chat_request worker=0` | 使用同一个 connection_id |
| 15:53:32 | `ui_chat_received player=WANWEIyee` | WANWEIyee 再发消息 |
| 15:53:40 | worker=0 完成 fantong7038 的请求 | |
| 15:53:40 | `processing_chat_request worker=1` | worker=1 处理 WANWEIyee 但此时历史已包含 fantong7038 的对话 |
| 15:58:50 | WANWEIyee 问"你是谁" | response 回答 "fantong7038" — **串了** |
| 16:01:55 | WANWEIyee 问"你是谁" | worker=0 调用 get_player_snapshot(target="fantong7038") — **player_name 错误** |

---

## 四、影响范围

| 组件 | 受影响 | 说明 |
|------|--------|------|
| `ConnectionState.player_name` | 是 | 只记录第一个发消息的人 |
| `MessageBroker._conversation_histories` | 是 | 按 connection_id 分桶，多人共享 |
| `MessageBroker._connection_locks` | 是 | 同一连接的请求串行，导致 B 等 A |
| `ChatRequest.player_name` | 是 | 继承自错误的 state.player_name |
| `AgentDependencies.player_name` | 是 | Agent 的工具(get_player_snapshot等)用的名字也错了 |
| `PromptManager` 模板/变量 | 是 | 按 connection_id 管理，所有人共享模板 |
| `ai_response_sync` | 是 | player_name 错误导致 UI 响应推给错人 |
| `AddonBridgeSession` | 轻微 | 按 connection_id 隔离，但所有人共享同一会话 |
| `context_enabled` / `current_provider` | 是 | 连接级，一人操作影响所有人 |

---

## 五、修复方案

详见 [`claude_md/fix/MULTIPLAYER_SESSION_FIX.md`](../fix/MULTIPLAYER_SESSION_FIX.md)。

核心思路：把所有按 `connection_id` 分桶的状态改为按 `(connection_id, player_name)` 复合键索引；server.py 每条消息都用本次事件的 `sender` 而不是缓存值；引入 `PlayerSession` 让 ConnectionState 上的可变设置按玩家隔离。
