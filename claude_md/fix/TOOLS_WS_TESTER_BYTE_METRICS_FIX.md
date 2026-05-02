# tools_ws_tester 发包字节统计修复

## 修改内容

- 在 `tools_ws_tester.py` 的 `SessionStats` 中新增实际发送字节统计：
  - 总 WS payload 字节数
  - 总 commandLine 字节数
  - 最大已发送 WS payload / commandLine 字节数
  - 最大成功 WS payload / commandLine 字节数
  - 最近失败 WS payload / commandLine 字节数
- 在 `_send_and_wait_ack()` 发送前解析实际 JSON，按 UTF-8 统计：
  - WebSocket 实际发送 payload 字节长度
  - `body.commandLine` 字节长度
- 在 commandResponse 失败、超时、burst/sweep/auto 失败输出中展示对应字节长度。
- 在 `stats` 命令和 Markdown 报告中输出最大成功与最近失败字节指标。
- 在 auto 详细数据表中追加每个测试级别的 WS 包字节和 commandLine 字节。

## 用法建议

用于测试 `services/websocket/flow_control.py` 中 `_COMMAND_LINE_BYTE_BUDGET` 的真实值时，重点观察：

- `最大成功commandLine`
- `首次失败 ... commandLine`
- 报告中的 `最大成功 commandLine` 与 `最近失败 commandLine`

这两个值之间的边界区间就是当前 MCBE 环境下 commandLine 可接受字节上限的实测范围。

## 验证

已运行：

```bash
python -m py_compile tools_ws_tester.py
```

结果：通过，无语法错误。
