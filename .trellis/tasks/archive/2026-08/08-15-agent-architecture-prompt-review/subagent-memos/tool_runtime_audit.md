# tool_runtime_audit 原始回传

来源：只读子代理 `/root/tool_runtime_audit` 的 FINAL_ANSWER 原文（会话记录 ordinal 700）；以下正文仅作原样记录。
审查结论：Harness 主链路完整，但存在一个 P0 级语义丢失缺陷：生产工具返回的结构化 `ToolResult` 被提前转成字符串，导致超时未知、失败、不可重试等状态被当作成功处理。

调用路径：

`PydanticAI ToolCall/schema → HarnessCapability.prepare_tools → HarnessToolset.call_tool（预检/幂等/策略）→ 拒绝或 ApprovalRequired → PendingApprovalStore → Gateway 校验 → Worker resume → 实际 builtin/MCP 工具 → audit/trace → 模型反馈 → BrokerResponseBridge/SDK delivery`。Worker 明确传递 `player_name/conversation_id`，响应也统一走 delivery。

关键问题：

1. **P0 已确认：`ToolResult` 被 stringify，副作用状态错误缓存。**
   `[services/agent/tools.py:91](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/tools.py:91)` 将所有返回值 `str(result)`，并在 `[services/agent/tools.py:1266](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/tools.py:1266)` 启用。`ToolResult` 的 `retryable/external_state_unknown` 位于 `[services/agent/tool_results.py:93](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/tool_results.py:93)`；Harness 对字符串分支无条件写入成功幂等缓存 `[services/agent/harness/execution.py:1536](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/harness/execution.py:1536)`，审计/Trace 也默认 success。命令超时本应返回 unknown `[services/agent/worker.py:2058](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/worker.py:2058)`，却可能被缓存为成功。建议保留 `ToolResult` 至 Harness 收尾，仅在返回模型边界 materialize；补真实 builtin+Harness 超时/失败集成测试。现有测试主要是直接调用函数或自定义工具，未覆盖生产注册路径 `[tests/test_agent_tools.py:58](/home/riceawa/Desktop/code/MCBE-AI-Agent/tests/test_agent_tools.py:58)`。

2. **P1 已确认：命令审批只覆盖窄根集合。**
   目录把 `run_minecraft_command` 标为 HIGH `[services/agent/harness/catalog.py:81](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/harness/catalog.py:81)`，但未命中审批根时自动 ALLOW `[services/agent/harness/execution.py:431](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/harness/execution.py:431)`；现有测试还明确断言 `give/time` 允许 `[tests/test_runtime_harness_execution.py:701](/home/riceawa/Desktop/code/MCBE-AI-Agent/tests/test_runtime_harness_execution.py:701)`。`tp/gamemode/effect/execute/event` 等未知副作用命令可旁路审批。应采用显式安全根 allowlist，未知根默认审批。

3. **P1 已确认：方块状态未知仍可重复执行。**
   Bridge 明确返回 `external_state_unknown=true`、禁止自动重试 `[services/agent/block_ops/bridge.py:218](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/block_ops/bridge.py:218)`，但 `execute_block_plan` 仅成功才标记 executed，失败允许再次恢复 `[services/agent/block_ops/tools_impl.py:188](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/block_ops/tools_impl.py:188)`。超时后可能重复 place/fill。应把 unknown 作为终态缓存，补“实际修改后抛超时，再次 resume 不重复发送”测试。

4. **P1 已确认：审批批次可能丢失。**
   `record_decision` 先弹出完整批次 `[services/agent/harness/approvals.py:385](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/harness/approvals.py:385)`，随后队列提交失败只回复 busy `[services/gateway/command_handlers.py:1366](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/gateway/command_handlers.py:1366)`，没有恢复 pending。应事务化提交或 QueueFull 时回滚；补满队列测试。

5. **P1：审批恢复异常未统一收尾。**
   普通执行捕获异常并写 audit/trace，但恢复路径直接 `await execute_block_plan` `[services/agent/harness/execution.py:1783](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/harness/execution.py:1783)`。Addon 异常/取消会绕过 `tool.execution.failed`。应复用同一异常收尾链。

6. **P1 隐私风险：日志写入完整 prompt、参数、结果。**
   Core/Worker 分别记录完整 prompt/tool args/result `[services/agent/core.py:1062](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/core.py:1062)`、`[services/agent/worker.py:900](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/worker.py:900)`、`[services/agent/worker.py:926](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/worker.py:926)`；structlog processor 没有全局脱敏。应统一截断、脱敏、hash，并加 caplog 测试。

7. **P1 条件性安全风险：Trace API 无认证。**
   GET/DELETE 均无鉴权 `[services/agent/trace_api.py:55](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/trace_api.py:55)`；默认仅 loopback `[config/settings.py:885](/home/riceawa/Desktop/code/MCBE-AI-Agent/config/settings.py:885)`，但 CLI 可改 host。非本机绑定时可读取/删除玩家 Trace。应强制 loopback 或要求 token。

8. **P2：目录与运行时仍是双事实源。**
   `schema_hash` 只是可选字段 `[services/agent/harness/catalog.py:45](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/harness/catalog.py:45)`；目录测试把 catalog 名称与自身 `list_tool_names()` 比较，并未检查实际注册 `[tests/test_runtime_harness_catalog.py:43](/home/riceawa/Desktop/code/MCBE-AI-Agent/tests/test_runtime_harness_catalog.py:43)`。应实例化真实 Agent 比较注册名、schema、hash。当前 docs/schema 也有漂移：批量命令、Wiki limit、视线距离、标题 ticks 均缺少 Field 约束。

9. **P2 MCP 风险：仅按工具名 allowlist，且环境继承过宽。**
   `[services/agent/harness/execution.py:500](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/harness/execution.py:500)` 不绑定 server/schema；`MCPServerInfo.tools` 未填充，状态工具数失真 `[services/agent/mcp.py:99](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/mcp.py:99)`。stdio MCP 继承完整 `os.environ` `[services/agent/mcp.py:259](/home/riceawa/Desktop/code/MCBE-AI-Agent/services/agent/mcp.py:259)`，可能泄漏宿主密钥。应绑定 `(server, tool, schema_hash)`，并采用最小环境白名单。

保留项：身份校验、断线清理、审批 owner/run/tool-call/hash 重校验、主路径取消处理、per-plan 锁、audit/trace 默认关闭正文且异步 fail-soft、BrokerResponseBridge 统一 delivery 均设计良好。
