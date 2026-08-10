# Root-cause evidence

## Exact SDK repro

输入用户日志中的等价帧：

```text
MCBEWS|SESSION|{"request_id":"sess-1786357953879-1881","v":1,"action":"switch","player_name":"fantong7038","cid":"default"}
```

`AddonBridgeService.handle_player_message()` 稳定返回：

```text
ProtocolError: 1 validation error for SessionRequest
Value error, switch requires cid
```

对照矩阵：

| 输入 | 当前结果 |
|---|---|
| `switch`, `cid="default"` | reject |
| `switch`, `cid="chat-a"` | accept |
| `switch`, omitted `cid` | reject |
| `list`, `cid="default"` | accept |

## Host repro

先把玩家 active conversation 设置为 `chat-a`，再执行：

```python
ConversationOperations.execute(
    connection_id,
    player_name="fantong7038",
    action="switch",
    conversation_id="default",
)
```

当前结果为 `INVALID_ARGUMENT: 请指定要切换的对话 ID`。

## Pydantic field-presence probe

对 `SessionRequest`：

| 构造输入 | `model_fields_set` |
|---|---|
| omit `cid` | 不含 `conversation_id` |
| alias `cid="default"` | 含 `conversation_id` |
| field name `conversation_id="default"` | 含 `conversation_id` |

Context7 的 Pydantic 结果确认 alias 属于 validation contract，但未返回
`model_fields_set` 的直接文档片段；因此实现以公开属性加三类自动化测试为门禁。
