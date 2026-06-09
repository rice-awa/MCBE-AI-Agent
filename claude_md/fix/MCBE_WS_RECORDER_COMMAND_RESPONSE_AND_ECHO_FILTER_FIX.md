# MCBE WebSocket 录制器 commandResponse 与回声过滤修复

## 背景

根据 `WS_RECORD_ANALYSIS_20260609_094020.md`，录制会话中所有 `commandResponse` 都因为 `MinecraftMessage.header.messagePurpose` 枚举缺失而被 Pydantic 校验拒绝，同时 `PlayerMessage` 回声占比过高，尤其是接收者为 `MCBEAI_TOOL` 的重复回声。

## 修复内容

- 在 `models/minecraft.py` 中允许 `messagePurpose="commandResponse"`，保留命令执行结果、状态码和状态消息。
- 在 `tools_mcbe_ws_recorder.py` 的 `SessionRecorder` 中加入脚本层过滤器，默认过滤 `receiver=MCBEAI_TOOL` 的 `PlayerMessage` 回声。
- 过滤只影响录制落盘，不影响透明代理转发到目标端。
- `SUMMARY.md` 新增过滤统计，便于确认过滤效果。
- `record` 子命令新增 `--filter-echo-receiver`，可重复传入接收者名称；传空字符串可关闭默认过滤。
- 修复 `replay-server` 的回放选择逻辑，只回放真实玩家输入的 `PlayerMessage`，不再把服务端 `tellraw` 回声和 `commandResponse` 发回服务端。

## 验证

```bash
pytest tests/test_mcbe_ws_recorder.py -q
```

结果：`11 passed, 4 warnings`。警告来自现有 `websockets.server` 旧导入的弃用提示，本次未改动该路径。

额外验证：对 `test_logs/ws_records/20260609_101315/packets.jsonl` 执行修复后的 `load_replay_packets()`，只选中 4 条玩家输入：`AI chat hi`、`AI chat 给我64颗钻石`、`帮助`、`运行命令 say hi`。
