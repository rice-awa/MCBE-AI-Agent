# PR #25 代码审查报告

- 审查日期：2026-06-19
- PR：#25 `Fix PR 24 review follow-ups`
- 对比：`fix/pr24-review-followups` → `master`
- PR 状态：Open，mergeable
- 变更规模：27 个文件，约 `+1900/-212`
- 审查方式：读取 `gh pr view 25` 与 `gh pr diff 25`，静态审查关键实现文件，并使用独立只读审查代理复核 WebSocket 投递、流控、会话隔离、MCP reload 与 AI 广播路径
- 验证说明：本次审查未重新运行测试；PR 描述记录完整测试为 `343 passed, 2 skipped`

## 总体结论

PR #25 基本完成了 PR #24 审查报告中列出的核心修复：玩家触发命令响应改为私聊目标，`tellraw` / `scriptevent` / AI response 分片按最终 `commandLine` 字节预算校验，raw command 超长不再被语义不安全地截断，对话历史 revision 与 invalidation epoch 已拆分，MCP reload 后会刷新 Agent 工具集，连接注销时也清理 PromptManager 状态。同时新增的 AI 聊天广播控制保持了“可见流式输出目标”和“Addon UI 历史归属玩家”的分离，方向正确。

没有发现需要阻断合并的高风险正确性或安全问题。建议合并前重点确认一个中低风险退化：`flow_control.chunk_sentence_mode` 目前看起来只影响未被生产路径调用的 `_split_text()`，新增的最终字节预算试探分片路径会逐字符填充，可能让“优先按句子分片”的配置在实际出站发送中失效。

## 发现摘要

| 严重级别 | 问题 | 影响 | 建议优先级 |
| --- | --- | --- | --- |
| 中/低 | `chunk_sentence_mode` 配置在生产分片路径中疑似失效 | 长文本会按最大字节容量硬切，语义分句配置不再影响实际 tellraw/scriptevent/AI response 输出 | P2 |

## 详细问题与建议

### 1. 中/低：`chunk_sentence_mode` 只覆盖未使用的旧分片函数

**位置：**

- `cli.py:50-55`
- `config/settings.py:669-680`
- `services/websocket/flow_control.py:70-129`
- `services/websocket/flow_control.py:152-207`
- `services/websocket/flow_control.py:291-318`
- `services/websocket/flow_control.py:321-392`

**问题：**

应用启动仍会把 `settings.chunk_sentence_mode` 注入 `FlowControlMiddleware.configure()`，配置描述也是“分片时是否优先按句子分割”。但 PR #25 中生产使用的三个主要路径：

- `chunk_tellraw()` → `_split_tellraw_text()`
- `chunk_scriptevent()` → `_split_by_command_fit()`
- `chunk_ai_response()` → `_split_ai_response_text()` → `_split_by_command_fit()`

都只按字符数和最终 `commandLine` 字节预算逐字符填充，不再调用 `_split_text()`。当前 `_split_text()` 只被单元测试直接调用，因此 `DEFAULT_SENTENCE_MODE` / `chunk_sentence_mode` 对实际出站长文本是否按句子边界分片没有可见效果。

**影响：**

这不是字节安全问题，PR 新实现对 MCBE 461B 上限的防护更可靠。但它可能改变用户可见体验：长 AI 回复、长 tellraw 或长 scriptevent 会在接近预算处硬切，而不是优先在 `。！？.!?\n` 等句子边界分片。由于配置项仍存在，用户可能以为关闭或开启语义分片会影响实际发送，但实际上不会。

**建议：**

- 如果语义分片仍是产品契约：在 `_split_by_command_fit()` 前先按句子候选合并，再用最终 `commandLine` 试探预算兜底；或者让 `_split_by_command_fit()` 在 `DEFAULT_SENTENCE_MODE=True` 时优先消费句子片段。
- 如果 PR 有意牺牲语义分片换取最终字节安全：更新 `config/settings.py` 的配置描述、CLAUDE.md 中 Flow Control Middleware 说明，以及相关测试名称，明确该配置已不影响最终出站路径。
- 增加一条生产路径测试，例如 `FlowControlMiddleware.configure(sentence_mode=True)` 后对包含多个短句的长 `chunk_tellraw()` 断言分片尽量落在句子边界；或相反，删除/降级 `_split_text()` 的语义测试，避免测试覆盖一个未使用行为。

## 正向确认

- `services/websocket/server.py:332-342` 的 `_send_player_reply()` 统一将玩家命令响应改写为触发玩家目标，覆盖 auth/login/chat/context/conversation/template/setting/mcp/switch/help/run_command/ai_broadcast 等路径。
- `models/minecraft.py:95-124` 使用 `json.dumps()` 构造 tellraw rawtext，并校验 scriptevent message id，修复了手拼 JSON 与命令注入风险。
- `services/websocket/flow_control.py:70-207` 按最终 `commandLine` UTF-8 字节数分片，并处理 AI response metadata、总片数位数变化和 wrapper 无内容空间的边界。
- `services/agent/worker.py:154-157`、`services/agent/worker.py:341-347` 使用请求捕获的 invalidation epoch 阻止 clear/switch/restore 后的旧队列请求写回历史，同时不再用 history generation 阻塞连续聊天自然推进。
- `services/agent/core.py:184-194` 与 `services/agent/runtime.py:52-57` 提供 MCP reload 后刷新 primary Agent 的路径，避免长期停留在 fallback agent。
- `services/websocket/connection.py:125-144` 在注销时清理 pending command future、队列内 run_command future、PromptManager 和 broker 会话状态，生命周期边界更完整。
- 新增测试覆盖了关键回归面：`tests/test_ai_broadcast.py`、`tests/test_flow_control.py`、`tests/test_websocket_delivery.py`、`tests/test_queue_context.py`、`tests/test_agent_tools.py`。

## 测试建议

合并前建议至少重跑 PR 描述中的核心验证：

```bash
pytest tests/test_ai_broadcast.py tests/test_websocket_conversation_commands.py
pytest tests/test_flow_control.py tests/test_websocket_delivery.py
pytest tests/test_queue_context.py
pytest tests/test_agent_tools.py tests/test_mcp.py tests/test_prompt_template.py
pytest
```

如果决定保留语义分片契约，建议额外补一条覆盖 `chunk_tellraw()` / `chunk_ai_response()` 真实生产路径的句子边界测试。
