# 切换会话后响应丢失与 byte budget 修复 — 执行计划

## 顺序

### 1. 新增 `_compact_usage` helper
- `services/gateway/broker_bridge.py`：在 `_ai_sync` 上方新增 `_compact_usage(usage)`，
  返回 `{"i": int, "o": int}` 或 None。
  - 兼容 `input_tokens`/`request_tokens`、`output_tokens`/`response_tokens` 别名。
  - 非 dict / 全 None → None。

### 2. `_ai_sync` 调用精简 usage
- `usage = response.get("usage")` 后，`send_response(usage=_compact_usage(usage))`。

### 3. 验证
- `python -m pytest tests/ -k "broker_bridge or ai_sync"`（若有）。
- 手动：切换会话后发消息，确认无 `FrameTooLargeError`、面板正常显示响应、
  统计面板 token 非 0。
- 字节验证（可选脚本）：
  ```python
  import json
  frame = {"id":"resp-x","i":7,"n":7,"p":"fantong7038","r":"assistant",
           "c":"A"*50,"cid":"chat-20260808-172115-742928-d7208e",
           "u":{"i":9768,"o":83}}
  cmd = f"scriptevent mcbews:text_resp {json.dumps(frame,separators=(',',':'))}"
  assert len(cmd.encode()) <= 461
  ```

## Review Gates
- 步骤 1-2 完成后跑 pytest + 字节验证脚本。

## Rollback
- 单提交；若 `_compact_usage` 有问题，回退该提交即恢复原状（原状有 byte budget bug，
  但不阻断其他流程）。
