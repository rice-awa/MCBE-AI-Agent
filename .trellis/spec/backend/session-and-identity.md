# 会话与身份隔离

## 身份模型

Minecraft 世界通常只有一条 `/wsserver` WebSocket 连接，因此 `connection_id` 不是玩家身份。业务状态至少按以下维度理解：

- `connection_id`：SDK/WebSocket 连接。
- `player_name` / 入站事件的 `sender`：当前真正发起消息的玩家。
- `conversation_id`：该玩家在当前连接下的对话桶。
- `trace_id` / `run_id` / `attempt_id`：一次玩家意图、一次执行和一次重试/审批恢复的执行身份。

MCBEWS/1 的 transport `sender` 只证明消息来自可信 ToolPlayer（`MCBEWS_BRIDGE`）；它不是业务
玩家。Session/approval 的业务 `player_name`、`conversation_id` 必须来自已验证 payload 或
pending approval owner，不能把 ToolPlayer 名称回填为业务身份。

`models/messages.py` 中的 `ChatRequest`、`StreamChunk` 和 `SystemNotification` 已显式携带这些跨层需要的字段。新增玩家相关消息或内部 request 时，沿用同一字段，而不是在下游重新猜测玩家。

## 分桶与锁

统一使用 [`core/session.py`](../../../core/session.py) 中的构造函数：

- 对话历史键为 `(connection_id, player_name, conversation_id)`，空玩家名回落到 `DEFAULT_PLAYER_KEY`，空对话 ID 回落到 `default`。
- 同一玩家的处理锁为 `(connection_id, player_name)`，所以同一玩家跨对话串行，不同玩家可以并行。
- `ConversationSessionStore` 同时维护活动对话、短 ID、元数据、历史 generation 和管理操作 invalidation epoch；清除/切换/恢复等管理操作必须使用它的 API，不能直接改内部字典。
- `HostSessionStore` 管理连接级的玩家 session，包括上下文开关、当前 provider、模板、变量和 AI 广播策略。通过 `get_player_session(player_name)` 获取，不要把这些值挂在连接本身。

`MessageBroker`、`ConversationSessionStore` 和 `HostSessionStore` 的实现分别见 [`core/queue.py`](../../../core/queue.py)、[`core/session.py`](../../../core/session.py) 和 [`services/gateway/session_store.py`](../../../services/gateway/session_store.py)。

## 必须显式传递的路径

修改以下路径时，从事件入口一直传到最终消费者：

1. 游戏内命令 / Addon UI → `sender` → `CommandRequest` / `ChatRequest.player_name`。
2. ChatRequest → Worker → Agent dependencies、工具上下文、TraceContext 和 StreamChunk。
3. Broker 响应 → `BrokerResponseBridge` / `McbewsV1Delivery` → Addon `mcbews:text_resp` 的玩家字段。
4. 对话、模板、变量、模型切换、广播设置和审批恢复。

UI Chat 的 `cid` 必须沿 SDK reassembly → Host adapter → `ChatRequest.conversation_id` 传递；
text response 的 `cid`/title 在相关帧保持一致，usage 只出现在完成帧。Session response 使用
request id correlation 并且原子单帧发送，超限时返回结构化错误。

禁止使用 SDK `ConnectionState.player_name` 做业务分支；该属性既不是多人世界中的真实玩家身份，也不应替代事件中的 `sender`。禁止使用全局“当前玩家”、单一连接历史或未带玩家参数的缓存。

连接级可变状态（如曾经的 `state._player_name`）不得作为业务身份的回退来源：一名玩家的任务写入后等待、另一名玩家覆盖连接状态，前者若在回退分支读取连接状态，就会得到后者的身份。回退分支在缺少显式 `player_name` 时应使用非业务性默认值（如广播到全体的 `@a` 或 `DEFAULT_PLAYER_DISPLAY_NAME`），而不是连接级状态。相关改动应先补同一连接下双玩家交错的确定性安全门测试（见 [`tests/test_gateway_hook_auth_chat.py`](../../../tests/test_gateway_hook_auth_chat.py) 的方案二测试组）。

## 验证方式

涉及此规则的改动至少覆盖一名连接下两名玩家的隔离测试。现有参考包括 [`tests/test_queue_context.py`](../../../tests/test_queue_context.py)、[`tests/test_gateway_session_store.py`](../../../tests/test_gateway_session_store.py)、[`tests/test_gateway_hook_auth_chat.py`](../../../tests/test_gateway_hook_auth_chat.py) 和 [`tests/test_agent_context.py`](../../../tests/test_agent_context.py)。检查历史、锁、provider、模板、变量、广播和 UI 响应是否都落在正确的玩家桶中。
