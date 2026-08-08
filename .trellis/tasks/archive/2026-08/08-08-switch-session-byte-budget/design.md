# 切换会话后响应丢失与 byte budget 修复 — 技术设计

## 方案

在 `broker_bridge._ai_sync` 出口处把完整 usage dict 精简为 compact `{"i", "o"}` 再传给
`McbewsV1Delivery.send_response`。改动面最小，trace/日志的完整 usage 不受影响。

```python
# broker_bridge._ai_sync（改动后）
usage = response.get("usage")
compact_usage = _compact_usage(usage)  # {"i": N, "o": N} 或 None
await v1.send_response(..., usage=compact_usage)
```

## 数据流（改后）

```
worker.py: usage_dict = {"input_tokens":9768,"output_tokens":83,"cache_read_tokens":9728,...}
  └─ broker.send_response({"type":"ai_response_sync","usage":usage_dict})  ← 完整，供 trace
       └─ broker_bridge._ai_sync
            └─ compact_usage = {"i":9768,"o":83}   ← 新增精简
                 └─ McbewsV1Delivery.send_response(usage=compact_usage)
                      └─ codec: frame["u"] = {"i":9768,"o":83}  ← 17 字节
                           └─ addon responseSync: chunk.u.i=9768, chunk.u.o=83  ✓
```

## 改动点

### `services/gateway/broker_bridge.py`

1. 新增模块级 helper：
   ```python
   def _compact_usage(usage: Any) -> dict[str, int] | None:
       """精简 usage dict 为 addon 所需的 compact 格式 {"i","o"}。"""
       if not isinstance(usage, dict):
           return None
       input_tokens = usage.get("input_tokens") or usage.get("request_tokens")
       output_tokens = usage.get("output_tokens") or usage.get("response_tokens")
       if input_tokens is None and output_tokens is None:
           return None
       return {"i": int(input_tokens or 0), "o": int(output_tokens or 0)}
   ```
2. `_ai_sync` 中 `usage=usage` 改为 `usage=_compact_usage(usage)`。

### 不改动

- `worker.py`：`usage_dict` 保持完整（trace/audit/logger 依赖）。
- SDK `codec.py`：`frame["u"] = usage` 透传逻辑不变。
- addon `responseSync.ts`：已兼容 compact 与 verbose（commit f65ea8a），无需再改。

## 边界

- usage 缺 `input_tokens`/`output_tokens`（部分 provider）：`_compact_usage` 返回 None，
  `send_response(usage=None)` → codec 不写 `u` 字段 → addon 不更新 token，符合预期。
- usage 为 None：同上。
- `request_tokens`/`response_tokens` 别名：PydanticAI `RequestUsage` 字段别名兼容
  （dataclass field validation alias），序列化后可能用任一名，helper 两者都取。

## 验证

- 构造完整 usage dict（含 cache/details）+ 长 cid + 50 字符文本，确认最后一帧
  commandLine < 461 字节（精简后约 223 字节）。
- `python -m pytest tests/ -k broker_bridge`。
