# Addon Bridge Protocol

## 目标

在 Python 服务与 Minecraft Addon 之间建立稳定的桥接协议，使用 `/scriptevent` 发起请求，并通过聊天分片返回响应。

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

- 前缀：`MCBEAI|RESP`
- 单片格式：`MCBEAI|RESP|<request_id>|<index>/<total>|<content>`
- `<index>` 从 1 开始
- `<content>` 是 JSON 响应字符串的片段

示例：

```text
MCBEAI|RESP|req-1|1/2|{"ok":true,
MCBEAI|RESP|req-1|2/2|"players":["Steve"]}
```

## 能力清单（任务 1 基线）

- `get_player_snapshot`：获取玩家快照（位置、维度、朝向、基础状态）
- `get_inventory_snapshot`：获取背包快照（槽位、物品、数量、附加数据）
- `find_entities`：按条件筛选实体（类型、名称、标签、距离）
- `run_world_command`：受控执行世界命令并返回结果

## 错误码（协议级）

- `BRIDGE_INVALID_NAMESPACE`：分片命名空间不是 `MCBEAI`
- `BRIDGE_INVALID_PREFIX`：分片类型前缀不是 `RESP`
- `BRIDGE_INVALID_CHUNK_FORMAT`：分片字段数量错误
- `BRIDGE_INVALID_CHUNK_METADATA`：分片元数据非法（索引/总数/请求 ID）
- `BRIDGE_EMPTY_CHUNKS`：重组时分片列表为空
- `BRIDGE_CHUNK_SEQUENCE_ERROR`：分片序号缺失、重复或顺序不一致
- `BRIDGE_CHUNK_REQUEST_MISMATCH`：同一批分片出现不同 `request_id`
- `BRIDGE_CHUNK_TOTAL_MISMATCH`：同一批分片出现不同 `total_chunks`
- `BRIDGE_INVALID_JSON_PAYLOAD`：重组后的 JSON 反序列化失败

## 约束与设计依据

- `/scriptevent <messageId> <message>` 中 `message` 最大 2048 字符，超长消息必须分片。
- 脚本侧可通过 `ScriptEventCommandMessageAfterEvent` 读取 `id` 与 `message`，因此保留显式命名空间路由 `mcbeai:bridge_request`。

## 当前基线实现

- Python:
  - `encode_bridge_request`：编码请求命令
  - `decode_bridge_chat_chunk`：解析响应分片
  - `reassemble_bridge_chunks`：重组并解析 JSON payload
- Addon:
  - `formatResponseChunk`：格式化单个分片
  - `chunkBridgePayload`：按最大片段长度分割并格式化全部分片
