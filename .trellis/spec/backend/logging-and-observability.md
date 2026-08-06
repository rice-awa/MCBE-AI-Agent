# 日志与可观察性

## 应用日志

仓库统一通过 [`config/logging.py`](../../../config/logging.py) 的 `get_logger()` 获取 structlog logger。事件名使用稳定的 `snake_case`，结构化字段作为关键字传入：

```python
logger.info(
    "host_gateway_started",
    host=settings.host,
    port=settings.port,
    dev_mode=settings.dev_mode,
)
```

`debug` 用于队列、分片和调试细节；`info` 用于连接/Worker/Gateway 生命周期和用户可关联的正常事件；`warning` 用于队列满、未知连接、降级或重试；`error` 用于需要调查的失败。不要用 `print` 代替服务端日志（CLI 面向用户的输出除外）。

## 身份字段和脱敏

涉及请求的日志优先带 `connection_id`、`player_name`、`conversation_id`、`trace_id`、`run_id` 或工具名等 bounded 身份字段，以便关联链路；不要直接写入聊天全文。敏感值统一经过 [`config/redaction.py`](../../../config/redaction.py) 的策略处理，包括异常、映射参数和长度限制。

`enable_ws_raw_log`、`enable_llm_raw_log` 和 Trace `include_content` 默认关闭。即使临时打开，也要遵守脱敏和白名单，不把密钥、密码、Authorization header、完整工具参数或无界模型正文写入长期文件。

## Trace 与工具审计

- `services/agent/trace.py` 记录版本化 Trace 事件到 JSONL；`TraceRecorder` 使用队列和后台 writer，写盘失败只增加 dropped/write_failed/gap 计数，不能阻塞 Agent 主路径。
- 运行时 Harness 工具审计记录工具选择、参数预览、结果摘要和失败原因，不等同于普通应用日志。使用 `preview_parameters`、敏感 key 检查和长度上限；不要把完整工具结果直接记录。
- Trace 的完整正文由 `_PAYLOAD_ALLOWED_KEYS` 白名单和 `include_content` 门控控制；新增正文字段必须评估隐私和大小，不要把任意 `kwargs` 原样序列化。

参考测试：[`tests/test_logging.py`](../../../tests/test_logging.py)、[`tests/test_agent_trace.py`](../../../tests/test_agent_trace.py)、[`tests/test_runtime_harness_execution.py`](../../../tests/test_runtime_harness_execution.py)。
