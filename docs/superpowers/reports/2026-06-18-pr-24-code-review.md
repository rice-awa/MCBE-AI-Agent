# PR #24 代码审查报告

- 审查日期：2026-06-18
- PR：#24 `refactor: 重构 Agent 运行时与 MCBE 出站通道`
- 对比：`tech-debt-scan-plan` → `master`
- PR 状态：已合并
- 审查方式：读取 PR 元数据与 diff，结合当前分支代码做静态审查，并按运行时/会话、WebSocket 投递/流控、测试/文档三个方向并行抽查
- 验证说明：本次审查未重新运行测试套件；PR 描述中记录的验证为 `308 passed, 2 skipped, 12 warnings`

## 总体结论

PR #24 的重构方向正确：它把 Agent runtime、多人会话状态、MCBE 出站投递和流控逻辑从调用点中抽离出来，整体上降低了隐式耦合，并补充了大量回归测试。尤其是按 `(connection_id, player_name)` 隔离历史与锁、集中处理 raw command、保留 `TellrawMessage(target='@a')` 广播语义、以及 command prefix 边界解析，都是有价值的改进。

但当前实现仍有几类合并后风险：

1. 部分玩家触发的响应仍默认发往 `@a`，可能泄漏单个玩家的上下文、模板、变量、对话列表或认证状态。
2. `tellraw` / AI response 分片仍未完全按最终序列化 commandLine 计算，遇到反斜杠、引号、控制字符或长玩家名时可能生成非法命令或超出 461B 预算。
3. generation stale guard 在生产路径中没有使用请求入队时捕获的 generation，可能让清理/切换后的旧排队聊天重新写回历史。
4. MCP reload、PromptManager 清理和 agent 工具广播语义仍有生命周期或路由残留问题。

建议在后续修复中优先处理高风险项，再整理测试与历史文档，避免后续维护者或 AI agent 被过期计划误导。

## 发现摘要

| 严重级别 | 问题 | 影响 | 建议优先级 |
| --- | --- | --- | --- |
| 高 | 玩家命令响应默认广播到 `@a` | 多人环境下泄漏私有状态 | P0 |
| 高 | `tellraw` 文本转义不完整 | 反斜杠等内容可生成非法 rawtext JSON | P0 |
| 高 | AI response 分片未按最终 JSON 序列化预算 | Addon UI 同步可能丢失或超 MCBE 限制 | P0 |
| 高 | worker 忽略请求捕获的 generation | clear/switch 后旧队列请求可能重写历史 | P0 |
| 中 | MCP reload 不恢复 fallback 状态 | reload 成功但后续仍用无 MCP agent | P1 |
| 中 | PromptManager 连接级状态未在断开时清理 | 长运行进程内存泄漏、状态残留 | P1 |
| 中 | agent 工具仍硬编码 `@a` 或未校验 scriptevent ID | 工具输出误广播或事件路由异常 | P1 |
| 低/中 | 测试与文档存在维护性问题 | 未来修改易误判或被过期计划误导 | P2 |

## 详细问题与建议

### 1. 高：玩家触发的响应仍可能广播给 `@a`

**位置：**

- `services/websocket/minecraft.py:170-203`
- `services/websocket/server.py:348-350`
- `services/websocket/server.py:434-468`
- `services/websocket/server.py:480-530`
- `services/websocket/server.py:545`
- `services/websocket/server.py:737-747`
- `services/websocket/server.py:777-811`
- `services/websocket/server.py:824-877`
- `services/websocket/server.py:924-1004`
- `services/websocket/server.py:1059-1065`

**问题：**

`create_error_message()`、`create_info_message()`、`create_success_message()` 默认 `target='@a'`。PR 中 `_send_ws_payload()` 已改为尊重 `TellrawMessage.target`，不再用 `state.player_name` 隐式改写目标，因此所有未显式传入 `target=player_name` 的玩家命令响应都会广播。

受影响路径包括：未登录提示、登录成功/失败、空聊天、队列繁忙、上下文状态、对话列表/切换/清理结果、模板状态、变量设置、MCP 状态、切换模型结果等。

**影响：**

多人同服时，玩家 A 的对话 ID、对话标题、模板名、自定义变量值、MCP 状态、认证失败等信息可能被玩家 B 看到。该问题与 PR 的“多人会话隔离”目标直接冲突。

**建议：**

- 所有由 `PlayerMessage` 触发且只应回给触发者的路径，都显式传入 `target=player_name or state.player_name`。
- 增加统一 helper，例如 `_reply_to_player(state, player_name, msg)` 或在 protocol factory 层提供 `create_player_*_message()`，减少遗漏。
- 只在欢迎消息、明确的系统广播或 agent 明确要求广播时保留 `@a`。
- 补充多玩家回归测试：对 context/conversation/template/setting/auth/mcp/switch_model 等路径断言 commandLine 以 `tellraw Alice ` 开头，而不是 `tellraw @a `。

### 2. 高：`tellraw` 文本转义不完整，反斜杠可生成非法 JSON

**位置：**

- `models/minecraft.py:24-26`
- `models/minecraft.py:77-86`

**问题：**

`sanitize_tellraw_text()` 只处理了 `"`、冒号和 `%`，没有处理反斜杠。`create_tellraw()` 又手工拼接 rawtext JSON：

```python
command_line = f'tellraw {safe_target} {{"rawtext":[{{"text":"{color}{safe_message}"}}]}}'
```

当消息包含结尾反斜杠、Windows 路径或转义序列时，rawtext JSON 字符串可能被破坏。例如 `endslash\` 会让 JSON 字符串结尾的引号被转义，导致 MCBE 无法解析命令。

**影响：**

AI 回复、工具输出、错误消息、玩家变量值等只要包含反斜杠，就可能造成消息发送失败。该问题也会影响分片预算，因为实际命令中的转义长度与原文长度不一致。

**建议：**

- 不要手工拼接 rawtext JSON；用 `json.dumps()` 构造 `{"rawtext":[{"text": ...}]}`。
- 如果短期保持现有结构，至少先转义反斜杠再转义引号：`message.replace('\\', '\\\\').replace('"', '\\"')`。
- 添加回归测试：尾随 `\`、连续 `\\`、包含 `"`、换行、emoji、中文混合，并验证 rawtext JSON 片段可解析且 commandLine 字节数不超过 461。

### 3. 高：AI response 分片未按最终序列化 commandLine 计算字节预算

**位置：**

- `services/websocket/flow_control.py:155-193`
- `services/websocket/flow_control.py:234-336`
- `services/websocket/connection.py:478-515`

**问题：**

`chunk_ai_response()` 先用固定 wrapper overhead 得到 `byte_budget`，再按原始 `text.encode('utf-8')` 切分。随后每个分片被放入 `inner`，再通过 `json.dumps(inner, ensure_ascii=False)` 写入 `scriptevent mcbeai:ai_resp ...`。

这意味着以下内容会在切分后继续膨胀：

- `"`、`\`、换行、回车等 JSON 转义字符；
- `player_name`、`role`、`id`、`i/n` 等 metadata；
- 长玩家名或包含需转义字符的玩家名。

最终 `_assert_byte_safe()` 可能抛出 `ValueError`。在 `_sync_response_to_addon()` 中，该错误只会被记录，Addon UI 历史同步会丢失。

**影响：**

游戏内 tellraw 可能显示正常，但 Addon UI 历史不同步；或者包含大量引号/反斜杠/控制字符的回复在同步阶段失败。这个问题在普通 ASCII 长文本测试中不一定暴露。

**建议：**

- AI response 分片也应像 tellraw 一样，用最终 `MinecraftCommand.create_scriptevent(json.dumps(inner), 'mcbeai:ai_resp').body.commandLine` 的真实 UTF-8 字节数判定是否可放入当前 chunk。
- 将 metadata 纳入每个候选分片的预算计算，而不是使用固定 overhead。
- 补充测试：大量引号、反斜杠、换行、emoji、中文、长玩家名、带引号/反斜杠的玩家名，并断言每条 commandLine `<= 461` 字节。

### 4. 高：生产路径中 generation stale guard 没有使用请求入队时的 generation

**位置：**

- `models/messages.py:27-28`
- `services/websocket/server.py:448-453`
- `services/agent/worker.py:147-159`
- `services/agent/worker.py:330-336`
- `tests/test_queue_context.py:341-358`

**问题：**

`handle_chat()` 在入队前捕获 `chat_req.conversation_generation`，但生产调用路径 `AgentWorker._process_request()` 没有把该字段传给 `_process_request_locked()`。`_process_request_locked()` 在 `conversation_generation is None` 时直接读取当前 generation：

```python
if conversation_generation is None:
    conversation_generation = self.broker.get_conversation_generation(...)
```

结果是：旧聊天请求即使是在 `AGENT 对话 clear` 或切换/清理之前入队，只要它稍后才开始处理，就会读取清理后的新 generation，并以该 generation 写回历史。

**影响：**

场景示例：

1. 玩家发送聊天，请求进入队列；
2. 玩家立即执行 `AGENT 对话 clear`；
3. worker 稍后处理旧聊天；
4. worker 读取 clear 后的 current generation；
5. 旧回复被当作新历史写入，清理操作被“复活”。

当前测试 `tests/test_queue_context.py:341-358` 手动把 `request.conversation_generation` 传给私有方法，因此不能覆盖生产路径。

**建议：**

- 不要用“当前 generation”替代请求入队时的 generation 来判断是否 stale。
- 同时也要避免连续两条聊天共享初始 generation 导致第二条无法写入。建议把 generation 拆成两类：
  - history revision：普通聊天写入时递增，用于排序/观察；
  - invalidation epoch：clear/switch/delete/model switch 等管理操作递增，用于判定排队请求是否过期。
- `ChatRequest` 捕获 invalidation epoch；worker 写回前比较请求 epoch 与当前 epoch，只有管理操作发生过才拒绝写回。
- 补充生产路径测试：通过 `_process_request()` 或 worker queue 模拟“聊天入队 → clear → worker 处理”，断言历史不被旧请求重建。

### 5. 中：MCP reload 成功后无法恢复 `ChatAgentManager` 的 fallback 状态

**位置：**

- `services/agent/core.py:184-226`
- `services/websocket/server.py:968-989`

**问题：**

MCP 超时后，`mark_mcp_failed()` 会把 `_mcp_available` 置为 `False`，后续 `get_active_agent()` 直接返回无 MCP fallback agent。`AGENT MCP reload <name>` 只调用 `manager.reload_toolset(arg)`，不会：

- 将 `_mcp_available` 恢复为 `True`；
- 清理 `_fallback_agent`；
- 用新的 MCP toolsets 重建 primary agent；
- 重新 initialize `ChatAgentManager`。

**影响：**

用户看到 reload 成功，但后续聊天仍然使用无 MCP fallback agent，MCP 工具不会恢复。

**建议：**

- 在 `AgentRuntime` 上提供 `refresh_mcp_tools(settings)` 或类似方法，reload 成功后重建 agent manager 的 MCP toolsets。
- 或在 `ChatAgentManager` 中提供 `restore_mcp(toolsets)`，显式恢复 `_mcp_available=True`、清理 fallback 并重建 primary agent。
- 增加测试：模拟 `mark_mcp_failed()` 后 reload 成功，断言后续 `get_active_agent()` 使用带 MCP 的 agent。

### 6. 中：PromptManager 的连接级状态未在断开时清理

**位置：**

- `services/websocket/connection.py:110-134`
- `services/agent/prompt.py:262-282`

**问题：**

连接注销时会调用 `broker.unregister_connection(connection_id)`，从而清理 conversation/session store。但 `PromptManager` 也维护了：

- `_session_templates[(connection_id, player_name)]`
- `_session_variables[(connection_id, player_name)]`

虽然 `PromptManager.clear_connection(connection_id)` 已存在，但连接注销路径没有调用它。

**影响：**

由于 connection_id 是 UUID，这更像是长运行进程的内存泄漏，而不是直接串号。但大量玩家重连后，模板和变量状态会持续残留。

**建议：**

- 在 `ConnectionManager.unregister()` 中调用 `get_prompt_manager().clear_connection(str(connection_id))`。
- 添加测试：设置两个玩家的模板/变量，注销连接后断言 PromptManager 对该 connection 下所有玩家状态都被清理。

### 7. 中：agent 工具仍存在广播和 scriptevent ID 校验缺口

**位置：**

- `services/agent/tools.py:218-331`
- `services/agent/tools.py:652-671`
- `models/minecraft.py:95-100`

**问题：**

部分 agent 工具仍构造硬编码 `@a` 的命令：

- `build_title_commands()` 使用 `title @a ...`；
- `build_actionbar_command()` 使用 `title @a actionbar ...`；
- `build_tellraw_command()` 默认 `MinecraftCommand.create_tellraw(... target='@a')`。

此外，`send_script_event()` 接受任意 `message_id`，`MinecraftCommand.create_scriptevent()` 直接拼接：

```python
commandLine=f"scriptevent {message_id} {content}"
```

未限制空白字符、控制字符或非法 namespace。

**影响：**

- 单个玩家请求触发的工具调用可能广播给全服。
- 恶意或异常 `message_id` 可能导致事件路由失败、payload 边界错位或 Addon 侧收到非预期事件。

**建议：**

- 将 `ctx.deps.player_name` 传入消息类工具，默认定向触发玩家。
- 如果需要广播，应提供显式 broadcast 参数或独立工具，并在描述中说明风险。
- 对 `message_id` 使用严格白名单，例如 `^[a-z0-9_.-]+:[a-z0-9_./-]+$`，并拒绝空白/控制字符。
- scriptevent 分片预算应包含实际 `message_id` 长度，而不是固定估算。

### 8. 低/中：raw command 多字节超预算错误语义不够清晰

**位置：**

- `services/websocket/flow_control.py:135-152`
- `services/websocket/server.py:889-907`
- `tests/test_websocket_delivery.py:120-132`

**问题：**

`chunk_raw_command()` 先按字符数检查 `len(command) > max_len`，再用 `_assert_byte_safe()` 检查 commandLine 字节数。多字节命令可能字符数没超，但字节数超过 461，此时错误信息是：

```text
chunked commandLine exceeds byte budget ... this indicates a bug in _split_text or wrapper overhead estimate
```

这对 raw command 使用者来说不是“内部 bug”，而是“命令无法安全发送”。

**影响：**

玩家能收到错误，但错误描述会误导排查；server-level 测试也只覆盖 ASCII 超长路径，未覆盖多字节超预算定向回传。

**建议：**

- raw command 直接按 `MinecraftCommand.create_raw(command).body.commandLine.encode('utf-8')` 检查 461B 预算。
- 抛出面向用户的 `ValueError`，例如 `raw command too long in bytes (... > 461); cannot be safely chunked`。
- 补充 server-level 测试：`运行命令 say 中中...` 超预算时错误只发给触发玩家，不广播到 `@a`。

## 测试与文档建议

### A. 历史审查报告未明确标注 superseded

**位置：**

- `docs/superpowers/reports/2026-06-14-tech-debt-scan-plan-review.md:19-31`
- `docs/superpowers/reports/2026-06-14-tech-debt-scan-plan-review.md:35-80`

该报告仍显示“测试无法运行”和若干旧问题，但 PR 后续已经修复其中一部分。建议在文件顶部添加状态说明：该报告是修复前输入，已被后续 PR 验证/本报告取代；或者移入 archive/historical 路径。

### B. 大型执行计划文档仍以未完成任务形式存在

**位置：**

- `docs/superpowers/plans/2026-06-14-tech-debt-scan-plan-review-fixes.md:1-79`

该文件包含 1300 行左右的 AI worker 执行计划和未勾选步骤，但 PR 已实施大部分内容。建议归档并标注 historical，或压缩成最终设计记录，避免未来 agent 误以为这些任务仍待执行。

### C. stream mode 测试 monkeypatch 全局 `builtins.isinstance`

**位置：**

- `tests/test_stream_mode.py:324-348`

测试通过全局替换 `isinstance` 让 `SimpleNamespace` 伪装成 PydanticAI event class。这可能影响被测代码内部其他类型判断，也容易掩盖真实 API shape 变化。

建议改成：

- monkeypatch `core.PartStartEvent` / `core.PartDeltaEvent` 等符号为轻量 fake class；或
- 抽出 event adapter 函数，单独测试 adapter 输入输出。

### D. flow-control 测试过多断言精确 chunk 数

**位置：**

- `tests/test_flow_control.py:116-123`
- `tests/test_flow_control.py:196-223`
- `tests/test_flow_control.py:263-280`
- `tests/test_flow_control.py:297-320`

精确 chunk 数属于实现细节，未来调整 wrapper overhead 或安全 margin 时可能导致无意义失败。建议更多断言不变量：重组内容等于原文、每条 commandLine `<= 461` 字节、chunk index/total 一致、需要分片时 `len(payloads) >= 2`。

## 建议修复顺序

1. **P0：统一玩家私聊目标。** 先消除默认 `@a` 泄漏，覆盖所有玩家触发命令响应。
2. **P0：修正 tellraw/AI response 的最终 commandLine 预算。** 用真实序列化结果驱动分片，并补齐转义测试。
3. **P0：重新设计 generation stale guard。** 区分普通聊天写入 revision 和管理操作 invalidation epoch。
4. **P1：完善 MCP reload recovery 与 PromptManager 断连清理。** 避免 runtime 状态残留。
5. **P1：收紧 agent 工具目标与 scriptevent ID 校验。** 默认定向触发玩家，广播显式化。
6. **P2：整理历史 docs 与脆弱测试。** 降低后续维护成本。

## 可接受合并标准建议

如果该 PR 尚未合并，建议至少满足以下条件后再合入主干；当前 PR 已合并，则建议作为后续修复 issue/PR 的验收标准：

- 多玩家场景下，所有玩家触发的管理命令默认只回给触发者。
- tellraw、scriptevent、AI response 对中文、emoji、引号、反斜杠、换行、长玩家名均满足 461B commandLine 限制。
- `AGENT 对话 clear/switch/delete` 后，旧排队聊天不会重建已清理或已切换的历史。
- MCP fallback 后 reload 能恢复 MCP 工具集，或明确提示需要重启。
- 断连后 broker、PromptManager、pending command futures 均无连接级状态残留。
- 历史计划/报告明确标注为 historical/superseded，不再呈现为当前待办。
