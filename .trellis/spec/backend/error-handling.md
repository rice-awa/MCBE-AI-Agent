# 错误处理

## 异常层级

服务端的领域异常定义在 [`core/exceptions.py`](../../../core/exceptions.py)，以 `MCBEAgentError` 为基类，按边界区分 `AuthenticationError`、`LLMProviderError`、`ConnectionError`、`MessageQueueError`、`CommandError` 和 `ConfigurationError` 等类型。需要向调用方提供结构化信息时，沿用异常的 `message` 和 `details`，不要在每个调用点重新拼接错误字符串。

工具和 block operations 还使用结构化的 `ToolResult` / `BridgeResult`，其失败 code、message 和 bounded payload 是模型、用户响应和工具审计共同消费的契约。修改错误 code 时要同时检查工具提示、审计和相邻测试。

## 按边界处理

- 配置加载在 CLI 启动边界失败：`cli.py` 将 `FileNotFoundError` / `ValueError` 转成带有 `config.json` 或环境变量提示的 CLI 输出；不要捕获后静默使用不完整默认值。
- Gateway 命令把内部失败转换成 `CommandResponse` 或 `ErrorMessage`，并通过现有 response bridge 发送。用户消息应说明可行动的失败原因，不能把 Python traceback、密钥或内部 HTTP 响应原文直接发到游戏内。
- Agent provider、MCP、Wiki 和 Addon 外部调用要保留 provider/工具上下文，记录经过脱敏的错误摘要，并让 Worker 进入既有失败/完成路径；不要让一个工具的异常杀掉 Worker 主循环。
- `asyncio.CancelledError` 表示生命周期取消，应保持取消语义并执行清理；只有清理阶段的 best-effort 操作才可使用 `contextlib.suppress` 或受限的 `except`。
- `asyncio.QueueFull` 是明确的背压结果，应记录队列状态并向调用方返回“稍后重试”类结果，而不是无限重试或阻塞 WebSocket 事件处理。

参考实现：[`cli.py`](../../../cli.py)、[`services/agent/worker.py`](../../../services/agent/worker.py)、[`services/agent/tool_results.py`](../../../services/agent/tool_results.py)、[`services/agent/harness/execution.py`](../../../services/agent/harness/execution.py)。

## 外部状态未知

Minecraft 命令、Addon 写入和某些网络调用可能已经触发外部副作用后才超时。此类工具结果必须区分“明确失败”和“执行结果未知”，不要自动重复可能有副作用的操作。运行时 Harness 的结果、审计字段和提示应保留这个语义；block 写入则通过 preflight、locked target 和 recheck 防止把过期计划当成成功。

## 不要这样处理

- 不要在底层模块 `except Exception: return None`，这会丢失失败原因并让上层误判为成功。
- 不要在日志中记录完整异常对象的敏感参数、Authorization header、API key 或完整 prompt。
- 不要为每个命令新增一套错误格式；优先使用 `ErrorMessage`、`CommandResponse`、`ToolResult` 或既有 bridge contract。

验证参考：[`tests/test_cli_config_validation.py`](../../../tests/test_cli_config_validation.py)、[`tests/test_runtime_harness_execution.py`](../../../tests/test_runtime_harness_execution.py)、[`tests/test_agent_worker.py`](../../../tests/test_agent_worker.py)。
