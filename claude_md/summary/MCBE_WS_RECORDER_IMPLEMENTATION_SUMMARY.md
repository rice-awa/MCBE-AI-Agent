# MCBE WebSocket Recorder 实现总结

## 完成内容

- 新增 `tools_mcbe_ws_recorder.py` 独立工具脚本。
- 实现 `record` 子命令，用本地 WebSocket 代理透明转发 MCBE 客户端与项目服务端之间的双向数据包。
- 实现 `replay-server` 子命令，从录制文件筛选 `client_to_server` 文本包并按原始顺序回放到目标服务端。
- 实现 `PacketRecord`，记录原始帧、方向、路径、字节数、JSON 解析状态、header/body、requestId、messagePurpose 和事件名。
- 实现 `SessionRecorder`，为每次录制或回放生成 `packets.jsonl`、`packets.json` 和 `SUMMARY.md`。
- 新增 `tests/test_mcbe_ws_recorder.py`，覆盖 JSON 帧记录、非法 JSON 保留、回放筛选、速度延迟计算和会话文件输出。

## 使用示例

录制：

```bash
python tools_mcbe_ws_recorder.py record --listen-host 0.0.0.0 --listen-port 18080 --target ws://127.0.0.1:8080 --out test_logs/ws_records
```

MCBE 客户端连接：

```text
/wsserver <机器IP>:18080
```

回放：

```bash
python tools_mcbe_ws_recorder.py replay-server --input test_logs/ws_records/<session_id>/packets.jsonl --target ws://127.0.0.1:8080 --speed 1.0 --out test_logs/ws_replays
```

## 验证

已运行：

```bash
python -m pytest tests/test_mcbe_ws_recorder.py -q
```

结果：`5 passed`。

测试输出中存在 `websockets.server` 旧 API 弃用警告，与项目当前 WebSocket 服务端和现有工具脚本使用方式一致，本次未额外重构。
