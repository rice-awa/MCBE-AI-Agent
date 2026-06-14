# tech-debt-scan-plan 分支重构代码审查报告

- 审查日期：2026-06-14
- 审查分支：`tech-debt-scan-plan`
- 对比基线：`master...HEAD`
- 工作区状态：审查开始时干净
- 审查方式：4 个只读子代理并行审查 + 主会话抽查关键代码路径

## 审查范围

本分支相对 `master` 共修改 34 个文件，约 `2956 insertions(+), 659 deletions(-)`，主要覆盖：

- 运行时/会话/消息队列：`core/queue.py`, `core/session.py`, `services/agent/worker.py`
- Agent/provider 生命周期：`services/agent/core.py`, `services/agent/runtime.py`, `services/agent/providers.py`, `services/agent/tools.py`
- WebSocket 命令、投递与流控：`services/websocket/*.py`, `models/minecraft.py`
- 新增和更新测试：`tests/test_agent_provider_lifecycle.py`, `tests/test_websocket_delivery.py`, `tests/test_queue_context.py` 等
- 文档/任务产物：`PROMPT.md`, `docs/superpowers/plans/*`, `docs/superpowers/reports/*`

## 验证状态

测试命令尝试结果：

```bash
python -m pytest
# /bin/bash: line 1: python: command not found

python3 -m pytest
# /usr/bin/python3: No module named pytest
```

当前环境缺少 `python` 命令和 `pytest` 模块，因此未能运行测试套件。以下结论来自静态审查与代码路径抽查，不能替代测试通过证明。

## 关键发现

### 1. 高：同玩家快速连续聊天可能丢失第二次历史写入

- 位置：`services/websocket/server.py:451`, `services/agent/worker.py:154`, `services/agent/worker.py:331`
- 问题：`handle_chat()` 在入队前记录 `conversation_generation`。同一玩家快速发送两条消息时，两条请求可能携带相同 generation。worker 虽然按玩家锁串行处理，但第一条完成后会递增 generation，第二条完成时仍用旧 generation 写历史，于是 `set_conversation_history(... expected_generation=conversation_generation)` 可能拒绝写入。
- 影响：用户能收到第二条回复，但运行时历史缺失该轮对话，后续上下文会不一致。
- 建议：在 `_process_request_locked()` 获得玩家会话锁后、读取历史前，重新获取当前 generation，并用该锁内 generation 作为写回期望值。保留 generation 防护的目标应是防止同玩家管理命令在请求处理期间清空/切换对话，而不是防止同队列内前序聊天自然推进历史。
- 建议测试：模拟同一玩家在处理前连续 `submit_request()` 两条聊天请求，验证两轮消息都保存在同一 conversation history 中。

### 2. 高：MCP 失败后的降级 Agent 可被显式 primary agent 绕过

- 位置：`services/agent/worker.py:251`, `services/agent/worker.py:258`, `services/agent/core.py:208`
- 问题：worker 每次先 `agent_manager.get_agent()` 获取 primary agent，再把该 agent 显式传给 `stream_chat(..., agent=agent)`。但 `ChatAgentManager.get_active_agent()` 在 `agent is not None` 时直接返回该 agent，优先级高于 `_mcp_available` 检查。
- 影响：`mark_mcp_failed()` 之后，设计上后续请求应使用无 MCP fallback agent；实际 worker 路径可能继续使用 stale MCP-enabled primary agent，导致重复 MCP 超时或卡顿。
- 建议：删除 worker 对 primary agent 的显式传入，让 `stream_chat()` 自行通过 manager 选择 active agent；或调整 `get_active_agent()`，在 `_mcp_available == False` 时优先 fallback，除非显式 agent 已确认是 fallback/test override。
- 建议测试：标记 MCP failed 后调用 `stream_chat(..., agent=primary_agent)`，断言实际运行使用 fallback agent。

### 3. 高：tellraw 分片按原文估算，实际转义后可能超 MCBE 字节上限并丢消息

- 位置：`services/websocket/flow_control.py:96`, `models/minecraft.py:77`
- 问题：`chunk_tellraw()` 用原始 `message` 和原始 `target` 计算 byte budget；随后 `MinecraftCommand.create_tellraw()` 会把 `"`, `:`, `%` 等字符转义/替换，导致实际 `commandLine` 变长。
- 影响：大量冒号、百分号、引号或需转义目标名的消息可能分片阶段通过，但 `_assert_byte_safe()` 在创建 payload 后失败。部分调用路径只记录日志，玩家侧表现为回复丢失。
- 建议：用最终 `commandLine` 的真实字节长度驱动分片；或者在分片前应用与 `create_tellraw()` 完全一致的 message/target sanitization，再计算预算。
- 建议测试：长字符串包含大量 `:`, `%`, `"`，以及带空格/引号的玩家名，断言所有分片 payload 均不超过 461 字节。

### 4. 中：runtime shutdown 后再次 initialize 会复用旧 Agent/MCP 状态

- 位置：`services/agent/runtime.py:52`, `services/agent/core.py:101`
- 问题：`AgentRuntime.shutdown()` 关闭 MCP manager 和 runtime adapters，但不重置 `chat_agent_manager` / `mcp_manager`。如果同进程再次 `initialize()`，`ChatAgentManager.initialize()` 可能因 `_initialized` 为真直接返回，继续持有旧 settings/toolsets/agent。
- 影响：CLI 子命令、测试、嵌入式运行或同进程重启时，可能使用旧 provider/MCP toolsets，忽略新配置。
- 建议：增加 `ChatAgentManager.shutdown()/reset()`，清理 `_agent`, `_fallback_agent`, `_mcp_toolsets`, `_settings`, `_initialized`, `_mcp_available`；`AgentRuntime.shutdown()` 后也应清空或替换 `mcp_manager`。
- 建议测试：shutdown 后用不同 settings/toolsets 再 initialize，断言新 agent 反映新配置。

### 5. 中：会话锁会导致已清理玩家重新出现 phantom default conversation

- 位置：`core/session.py:227`, `core/session.py:353`
- 问题：`list_player_conversation_metadata()` 和 `list_player_conversations()` 根据 `_session_locks` 推断 `default` conversation 存在。清理历史、metadata、short id 和 active conversation 后，锁仍可能保留，从而重新合成空的 default 对话。
- 影响：模型切换、玩家清理或连接清理后，UI/命令列表可能显示一个实际已清理的对话，并重新分配 short id。
- 建议：不要从 `_session_locks` 推断 conversation；只从 history、metadata 或显式 active conversation 派生。需要空对话时应由 `ensure_conversation()` 明确创建。
- 建议测试：`clear_player_conversation_histories()` 后立即调用两个 list 方法，断言不生成 default 对话。

### 6. 中：命令解析使用 `startswith`，缺少命令边界

- 位置：`services/websocket/command.py:75`, `services/websocket/command.py:85`
- 问题：命令前缀和 alias 直接使用 `message.startswith(prefix/alias)` 匹配，没有要求精确匹配或空白边界。
- 影响：普通聊天可能被误判为命令，例如 alias `ask` 匹配 `askew...`，`AGENT 聊天室...` 匹配 `AGENT 聊天`。
- 建议：匹配规则改为 `message == prefix` 或 `message.startswith(prefix + whitespace)`；alias 同理。
- 建议测试：添加前缀/alias 的部分匹配负例。

### 7. 中：`TellrawMessage(target="@a")` 在 server adapter 层被隐式改写为上一个玩家

- 位置：`services/websocket/server.py:1048`
- 问题：`_send_ws_payload()` 对所有 `TellrawMessage` 执行 `target=payload.target if payload.target != "@a" else state.player_name or "@a"`。这把广播语义 `@a` 改成了连接状态中的最后玩家名。
- 影响：系统消息、帮助消息或本应广播的消息，在连接上曾有玩家发言后可能只发给 stale player。该行为也重新依赖 connection-level `player_name`，与多人隔离设计相冲突。
- 建议：不要在 adapter 层隐式重写 `@a`；需要单人响应时，在调用点显式传入本次事件的 `player_name`。
- 建议测试：设置 `state.player_name` 后发送 `TellrawMessage(target="@a")`，断言仍然广播。

### 8. 中：过长 raw command 在玩家路径中静默失败

- 位置：`services/websocket/server.py:889`, `services/websocket/server.py:896`
- 问题：`handle_run_command()` 直接调用 `send_raw_command()`。当原始命令超过长度/字节上限时，`FlowControlMiddleware.chunk_raw_command()` 会抛 `ValueError`，但这里没有捕获并回传用户错误。
- 影响：真实 `handle_message()` 流程可能只在外层 generic handler 记录日志，玩家收不到错误提示。
- 建议：在 `handle_run_command()` 捕获 `ValueError`，通过 `create_error_message(str(exc))` 发回当前玩家。
- 建议测试：通过 `handle_message()` 发送过长 `运行命令`，断言玩家收到错误消息。

### 9. 中：工具结果事件的工具名从 call id 猜测，可能显示错误

- 位置：`services/agent/core.py:677`, `services/agent/core.py:691`
- 问题：`FunctionToolResultEvent` 的 metadata 用 `event.tool_call_id.split("_")[0]` 猜测 `tool_name`。provider/framework 的 tool call id 不保证包含工具名。
- 影响：用户可见工具结果可能显示为 `test`、`call` 等无意义前缀，而不是 `run_minecraft_command` 等真实工具名。
- 建议：在处理 `FunctionToolCallEvent` 时维护 `tool_call_id -> tool_name` 映射，结果事件用映射取真实工具名。
- 建议测试：tool call id 为 `test_call_id` 时，结果 metadata 仍使用真实 tool name。

### 10. 低：标题生成存在 cleanup TOCTOU 窗口

- 位置：`services/agent/worker.py:508`, `services/agent/worker.py:509`, `services/agent/worker.py:511`
- 问题：标题任务先 `has_connection()`，再 `set_conversation_title()`。二者之间如果连接被 unregister，后者仍可能重新创建 metadata。
- 影响：极端时序下，连接清理后会被 late title task 重新留下会话 metadata。
- 建议：增加 broker/session 级原子方法，例如 `set_conversation_title_if_connected()`，或让 title 更新不在连接不存在时创建 metadata。
- 建议测试：monkeypatch broker 在 `has_connection()` 后 unregister，断言不会重建 metadata。

### 11. 低：历史扫描报告和根目录 PROMPT.md 易误导后续维护

- 位置：`docs/superpowers/reports/2026-06-13-architecture-tech-debt-scan.md:7`, `PROMPT.md:25`
- 问题：旧报告仍说这是“scan only / no refactor”，且提到 pytest 无法运行、计划文件未跟踪；但本分支已经包含实现和测试。`PROMPT.md` 是一次性执行提示，包含“commit after each slice”“output <promise>DONE</promise>”等流程指令。
- 影响：合入后可能被未来 agent/developer 误读为当前项目政策或当前验证状态。
- 建议：把旧报告标记为历史输入，补充最终实现/验证状态；删除 `PROMPT.md` 或移动到历史任务产物目录并标注 superseded。

## 建议修复顺序

1. 先修复历史写入 generation 竞态、MCP fallback 绕过、tellraw 字节预算这三项高风险问题。
2. 再处理 runtime reset、phantom conversation、命令边界和 `@a` 目标改写，这些会影响长期稳定性和多人语义。
3. 最后补齐 raw command 错误回传、工具名映射、title TOCTOU 和文档清理。
4. 修复后运行完整测试；至少新增覆盖上述每个风险点的回归测试。

## 总体评价

本分支的重构方向清晰，新增了 session/runtime/delivery 等集中化抽象，也覆盖了不少原有多人隔离和流控路径。但当前仍存在几处重构引入的边界问题：队列串行化与 generation 防护的交互、MCP lifecycle 状态优先级、以及 MCBE command 字节安全预算都需要在合并前修复并测试。由于本环境无法运行 pytest，本报告不对测试通过性作背书。
