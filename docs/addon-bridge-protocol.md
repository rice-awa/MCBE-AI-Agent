# Addon Bridge Protocol

## 目标

在 Python 服务与 Minecraft Addon 之间建立稳定的桥接协议，使用 `/scriptevent` 发起请求，并通过聊天分片返回响应。同时支持从 Addon UI 自动向 Python 发送聊天消息。

## 当前实现概览

当前落地链路如下：

```text
Python Agent Tool
  -> AddonBridgeService
  -> scriptevent mcbeai:bridge_request <json>
  -> Addon scriptEventReceive
  -> capability handler
  -> MCBEAI_TOOL 模拟玩家聊天分片
  -> WebSocket PlayerMessage
  -> Python 分片重组与 future 唤醒
```

当前已实现两条独立链路：

### 链路 A：Python -> Addon 能力请求（Bridge）

```
Python Agent Tool
  -> AddonBridgeService
  -> scriptevent mcbeai:bridge_request <json>
  -> Addon scriptEventReceive
  -> capability handler
  -> MCBEAI_TOOL 模拟玩家聊天分片 (MCBEAI|RESP)
  -> WebSocket PlayerMessage
  -> Python 分片重组与 future 唤醒
```

### 链路 B：Addon UI -> Python 自动聊天（UI Chat）

```
玩家打开 UI 面板输入消息
  -> chatInput.ts: sendUiChatMessage(playerName, message)
  -> MCBEAI_TOOL 模拟玩家聊天分片 (MCBEAI|UI_CHAT)
  -> WebSocket PlayerMessage
  -> Python 分片重组
  -> callback -> handle_chat -> Agent Worker -> tellraw 响应
```

其中：
- Python 请求入口位于 `services/addon/service.py`。
- Addon 请求监听基于 `scriptEventReceive`，命名空间固定为 `mcbeai:bridge_request`。
- Addon 响应不是直接回发到 WebSocket，而是由模拟玩家 `MCBEAI_TOOL` 发送聊天分片。
- Python 侧会在 WebSocket `PlayerMessage` 流中识别并拦截这些桥接分片，不再把它们当作普通聊天消息继续处理。
- **UI 聊天**由 Addon UI 面板发起，通过模拟玩家 `MCBEAI_TOOL` 发送 `MCBEAI|UI_CHAT` 格式分片，Python 重组后自动作为聊天请求处理，无需玩家在聊天框手动输入命令。

## 请求格式（Python -> Addon）

- 命令格式：`scriptevent mcbeai:bridge_request <json>`
- `message_id` 固定为 `mcbeai:bridge_request`
- `json` 结构：
  - `request_id`: string
  - `capability`: string
  - `payload`: object

示例：

```text
scriptevent mcbeai:bridge_request {"request_id":"req-1","capability":"get_player_snapshot","payload":{"target":"@a"}}
```

## 响应分片格式（Addon -> Python）

### Bridge 响应（Python -> Addon 请求的回复）

- 前缀：`MCBEAI|RESP`
- 单片格式：`MCBEAI|RESP|<request_id>|<index>/<total>|<content>`
- `<index>` 从 1 开始
- `<content>` 是 JSON 响应字符串的片段
- 当前实现中，分片由模拟玩家 `MCBEAI_TOOL` 通过聊天消息发送

示例：

```text
MCBEAI|RESP|req-1|1/2|{"ok":true,
MCBEAI|RESP|req-1|2/2|"players":["Steve"]}
```

### UI Chat 消息（Addon UI -> Python 自动聊天）

- 前缀：`MCBEAI|UI_CHAT`
- 单片格式：`MCBEAI|UI_CHAT|<msg_id>|<index>/<total>|<content>`
- `<index>` 从 1 开始
- `<content>` 是 JSON 字符串的片段，完整 JSON 结构为 `{"player": "<玩家名>", "message": "<聊天内容>"}`
- 分片同样由模拟玩家 `MCBEAI_TOOL` 通过 `tell @s` 发送，真实玩家不会看到回显

示例（单分片）：

```text
MCBEAI|UI_CHAT|ui-1744876800000-1|1/1|{"player":"Steve","message":"你好世界"}
```

示例（多分片）：

```text
MCBEAI|UI_CHAT|ui-1744876800000-1|1/2|{"player":"Steve","mes
MCBEAI|UI_CHAT|ui-1744876800000-1|2/2|sage":"你好世界"}
```

## 能力清单（任务 1 基线）

- `get_player_snapshot`：获取玩家快照（位置、维度、朝向、基础状态）
- `get_inventory_snapshot`：获取背包快照（槽位、物品、数量、附加数据）
- `find_entities`：按条件筛选实体（类型、名称、标签、距离）
- `run_world_command`：受控执行世界命令并返回结果

## 请求关联与生命周期

- 每次 Python 发起桥接调用时，都会生成唯一 `request_id`。
- `request_id` 会同时出现在 `/scriptevent` 请求体和 Addon 聊天分片头部，用于关联同一轮调用。
- Python 侧按连接维度维护 pending request，并按 `request_id` 缓存分片。
- 当同一 `request_id` 的全部分片收齐后，Python 会重组 JSON payload，唤醒对应等待中的请求 future。
- 如果收到未知 `request_id` 的分片，当前实现会忽略，不会为其创建新请求。

## 超时行为

- Python 侧桥接服务当前默认超时时间为 5 秒。
- 如果 Python 已经发送 `/scriptevent`，但在超时窗口内没有收齐指定 `request_id` 的全部分片，请求会以“Addon 桥接响应超时”失败。
- 如果命令发送阶段本身失败，例如 `/scriptevent` 执行返回错误，则不会进入等待分片阶段，而是直接失败。
- 超时或失败后，Python 会清理该 `request_id` 对应的 pending request 与分片缓存。

## 错误码（协议级）

### Bridge 响应错误

- `BRIDGE_INVALID_NAMESPACE`：分片命名空间不是 `MCBEAI`
- `BRIDGE_INVALID_PREFIX`：分片类型前缀不是 `RESP`
- `BRIDGE_INVALID_CHUNK_FORMAT`：分片字段数量错误
- `BRIDGE_INVALID_CHUNK_METADATA`：分片元数据非法（索引/总数/请求 ID）
- `BRIDGE_EMPTY_CHUNKS`：重组时分片列表为空
- `BRIDGE_CHUNK_SEQUENCE_ERROR`：分片序号缺失、重复或顺序不一致
- `BRIDGE_CHUNK_REQUEST_MISMATCH`：同一批分片出现不同 `request_id`
- `BRIDGE_CHUNK_TOTAL_MISMATCH`：同一批分片出现不同 `total_chunks`
- `BRIDGE_INVALID_JSON_PAYLOAD`：重组后的 JSON 反序列化失败

### UI Chat 错误

- `UI_CHAT_INVALID_NAMESPACE`：分片命名空间不是 `MCBEAI`
- `UI_CHAT_INVALID_PREFIX`：分片类型前缀不是 `UI_CHAT`
- `UI_CHAT_INVALID_CHUNK_FORMAT`：分片字段数量错误
- `UI_CHAT_INVALID_CHUNK_METADATA`：分片元数据非法
- `UI_CHAT_EMPTY_CHUNKS`：重组时分片列表为空
- `UI_CHAT_CHUNK_SEQUENCE_ERROR`：分片序号缺失、重复或顺序不一致
- `UI_CHAT_INVALID_JSON_PAYLOAD`：重组后的 JSON 反序列化失败
- `UI_CHAT_MISSING_MESSAGE`：JSON 中缺少 `message` 字段

## 约束与设计依据

- `/scriptevent <messageId> <message>` 中 `message` 最大 2048 字符，超长消息必须分片。
- 脚本侧可通过 `ScriptEventCommandMessageAfterEvent` 读取 `id` 与 `message`，因此保留显式命名空间路由 `mcbeai:bridge_request`。
- 当前 Addon -> Python 回传依赖聊天通道，而不是独立二进制或自定义网络通道，因此需要考虑聊天消息长度与分片顺序问题。
- Python 侧只会在 WebSocket `PlayerMessage` 事件中拦截桥接分片，所以聊天事件订阅链路必须正常。
- 由于本地 `@minecraft/server` 依赖版本限制，`run_world_command` 当前基于同步 `runCommand` 实现。
- 当前 UI 仅提供最小状态容器与 `ActionFormData` / `ModalFormData` 骨架，不代表完整 DDUI 已完成；该限制不影响桥接协议本身，但会影响 UI 层能力范围。

## 当前基线实现

### Python 侧

- `encode_bridge_request`：编码请求命令
- `decode_bridge_chat_chunk`：解析 Bridge 响应分片
- `reassemble_bridge_chunks`：重组并解析 JSON payload
- `decode_ui_chat_chunk`：解析 UI Chat 消息分片
- `reassemble_ui_chat_chunks`：重组 UI Chat 分片并提取玩家名与消息
- `AddonBridgeService`：发送 `/scriptevent`、等待 future、处理超时、UI Chat 回调分发
- WebSocket 侧在 `PlayerMessage` 事件流中拦截 `MCBEAI_TOOL` 的桥接分片与 UI Chat 消息

### Addon 侧

- `formatChunk`：通用分片格式化（支持自定义前缀）
- `formatResponseChunk`：格式化 Bridge 响应分片
- `chunkPayload`：通用分片分割（支持自定义前缀）
- `chunkBridgePayload`：按最大片段长度分割 Bridge 响应
- `chunkUiChatPayload`：按最大片段长度分割 UI Chat 消息
- `sendBridgeResponseChunks`：驱动 `MCBEAI_TOOL` 发送 Bridge 响应分片
- `sendUiChatMessage`：驱动 `MCBEAI_TOOL` 发送 UI Chat 消息
- `registerBridgeRouter`：订阅 `scriptEventReceive` 并分派 capability handler
