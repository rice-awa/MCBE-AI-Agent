# 跨层数据流与契约

## 先画出实际链路

涉及 Python 服务、SDK、Addon 或 Minecraft 世界的任务，先把完整数据流写出来：

```text
玩家事件 / Addon UI
  → SDK 事件与 sender
  → Gateway Hook / CommandHandlers
  → Pydantic 消息模型
  → MessageBroker
  → AgentWorker / Tool / AddonBridge
  → BrokerResponseBridge / SDK delivery
  → mcbews:text_resp 或游戏命令
  → Addon 玩家 UI / 游戏世界
```

每个箭头都要回答：输入结构是什么、谁验证它、错误如何返回、是否带 `player_name`、是否可能重复或超时，以及是否产生外部副作用。

## 本项目的三个关键契约

### 1. 玩家身份契约

同一 `/wsserver` 连接可以承载多个玩家。`sender` / `player_name` 从事件入口开始必须贯穿 `ChatRequest`、Worker、工具上下文、StreamChunk、Trace 和最终 Addon 响应。任何只按 `connection_id` 或 SDK `ConnectionState.player_name` 分支的实现都可能串玩家。

### 2. 线协议与分片契约

线协议权威是 mcbews v1：`mcbews:bridge_req`、`mcbews:text_resp` 和 `MCBEWS|*`。Python SDK 拥有宿主出站分片；Addon 的 `chunking.ts` 负责 bridge response 的模拟玩家聊天分片，`responseSync.ts` 负责 `text_resp` 分片重组。两边都必须验证 id/index/total，不能把旧 `mcbeai:*` 当成兼容分支重新引入。

详细协议见 [`docs/addon-bridge-protocol.md`](../../../docs/addon-bridge-protocol.md)、[`backend/runtime-architecture.md`](../backend/runtime-architecture.md) 和 [`../addon/bridge-protocol.md`](../addon/bridge-protocol.md)。

### 3. 配置与持久化契约

普通配置来自 `config.json`，敏感值通过 `.env` 中的 `${VAR}` 引用；Pydantic Settings 在入口验证。对话、Trace、工具审计和 UI 状态分别有自己的 JSON/JSONL 或 DynamicProperty contract，不要让 UI 直接依赖 Python 内存结构，也不要让日志文件成为业务状态源。

## 跨层修改清单

开始实现前：

- [ ] 列出事件、消息模型、队列、服务、协议和 UI/世界的每个边界。
- [ ] 明确 `player_name`、`conversation_id`、`trace_id` 和 request id 的来源与去向。
- [ ] 明确验证位置：Pydantic、Addon boundary、block preflight 或 response reassembly。
- [ ] 若默认值本身也是合法业务 ID，先区分字段“显式提供”和“省略”，再做默认值归一化；具体矩阵见
      [`../addon/bridge-protocol.md`](../addon/bridge-protocol.md#scenario-显式切换到-default-会话)。
- [ ] 明确超时/取消/重复/外部状态未知的语义。

完成后：

- [ ] 两名玩家共享一条连接的隔离测试仍通过。
- [ ] 成功、空内容、无效 payload、乱序/缺片和未知能力都有测试或明确的忽略/失败行为。
- [ ] 长文本仍走现有 delivery，未产生第二份分片算法。
- [ ] 文档、配置模板、Python 测试和 Addon Vitest 对同一字段/协议值保持一致。
