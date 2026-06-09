# MCBE WebSocket Recorder 实现总结

## 完成内容

- 新增 `tools_mcbe_ws_recorder.py` 独立工具脚本。
- 实现 `record` 子命令，用本地 WebSocket 代理透明转发 MCBE 客户端与项目服务端之间的双向数据包。
- 实现 `replay-server` 子命令，从录制文件筛选 `client_to_server` 文本包并按原始顺序回放到目标服务端。
- 实现 `PacketRecord`，记录原始帧、方向、路径、字节数、JSON 解析状态、header/body、requestId、messagePurpose 和事件名；`json_valid` 表示 JSON 语法有效，不再要求符合 `MinecraftMessage` 发送侧模型。
- 实现 `SessionRecorder`，为每次录制或回放生成 `packets.jsonl`、`packets.json` 和 `SUMMARY.md`。
- 新增 `tests/test_mcbe_ws_recorder.py`，覆盖 JSON 帧记录、非法 JSON 保留、回放筛选、速度延迟计算、会话文件输出、结构化日志和代理启动调用。
- 增强控制台日志，输出 ISO 毫秒时间戳、级别、组件、事件名和关键字段，便于定位目标服务端不可达、包转发和会话关闭原因。

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

结果：`7 passed`。

测试输出中仍存在项目现有 `services.websocket` 模块使用 `websockets.server` 旧 API 的弃用警告；`tools_mcbe_ws_recorder.py` 自身已改为通过 `websockets.serve` 启动监听。

## 断连排查结果

用户现场生成的录制摘要显示两次会话均为 `总包数: 0`，错误为 `[WinError 1225] 远程计算机拒绝网络连接。`，说明代理无法连接 `--target ws://127.0.0.1:8080`。MCBE 看到连接关闭后会自动重连，因此表现为“断了又重连”。

修复后控制台会明确输出：

```text
[2026-06-09T09:16:17.000] [ERROR] [record] target_connect_failed session=... target=ws://127.0.0.1:8080 error=...
```
