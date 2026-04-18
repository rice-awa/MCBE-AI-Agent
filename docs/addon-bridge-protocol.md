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
