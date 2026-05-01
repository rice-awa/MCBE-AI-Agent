# 流控中间件 P2/P3 收尾优化

> 关联报告：`claude_md/report/FLOW_CONTROL_MIDDLEWARE_REPORT.md`
> 完成日期：2026-05-01
> 涉及优先级：4.4（字节兜底校验）、5.1–5.5（代码质量）、P3.1–P3.3（建议项）

---

## 1. 任务清单与状态

| 报告条目 | 内容 | 状态 |
|---------|------|------|
| 4.4 | 字符 vs 字节语义错配；加字节兜底校验 | ✅ 本次确认已实现 + 补测 |
| 5.1 | `AI_RESP_MAX_CHUNK_LENGTH = 400` 重复魔法数 | ✅ 本次重构 |
| 5.2 | `chunk_ai_response` 手工拼接命令行 | ✅ 已用 `create_scriptevent` |
| 5.3 | 空文本契约模糊 | ✅ tellraw/scriptevent 空文本返回 `[]` |
| 5.4 | `chunk_raw_command` 逻辑分叉冗余 | ✅ 长度超限改为抛 `ValueError` |
| 5.5 | `_get_max_length` 应支持配置注入 | ✅ `configure()` 类方法已实现 |
| P3.1 | 集中"分片间延迟"策略 | ✅ 本次新增 `chunk_delay_for(kind)` |
| P3.2 | ws 层指标日志 | ✅ 本次补 `connection.py` 的 `command_line_bytes` |
| P3.3 | e2e 风格测试 | ✅ 本次新增 mock websocket 端到端测试 |

---

## 2. 本次具体变更

### 2.1 P3.1 集中分片间延迟（`flow_control.py`）

新增类常量与查询方法，把原先散落在 `connection.py` 三处 `asyncio.sleep` 的硬编码值
（0.05 / 0.15 / 0.5）收敛到一处：

```python
class FlowControlMiddleware:
    _CHUNK_DELAYS: dict[str, float] = {
        "tellraw": 0.05,
        "scriptevent": 0.05,
        "ai_resp": 0.15,
        "ai_resp_prelude": 0.5,
    }

    @classmethod
    def chunk_delay_for(cls, kind: str) -> float:
        return cls._CHUNK_DELAYS.get(kind, 0.0)
```

未知 kind 返回 0.0 不抛错，避免阻塞调用方；策略集中后未来调优只改一处。

### 2.2 P3.2 ws 指标日志统一（`connection.py`）

新增 `ConnectionManager._log_ws_send` 辅助方法，与 `server.py` 的同名方法语义对齐，
把 `command_line_bytes` 字段加到所有 `websocket_response_sent` 日志事件上：

```python
def _log_ws_send(self, state, payload, source):
    command_line_bytes = 0
    try:
        data = json.loads(payload)
        command_line_bytes = len(
            data.get("body", {}).get("commandLine", "").encode("utf-8")
        )
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    ws_raw_logger.info(
        "websocket_response_sent",
        connection_id=str(state.id),
        source=source,
        payload=payload,
        command_line_bytes=command_line_bytes,
    )
```

替换了原本 `_send_game_message_with_color` / `_run_command` / `_sync_response_to_addon`
/ `_send_script_event` 四处直接调 `ws_raw_logger.info(...)` 的代码。
`server.py:_send_ws_payload` 同步收益：多片时也按 `chunk_delay_for("tellraw")` 节奏发送，
避免一次性洪泛。

### 2.3 5.1 消除魔法数重复（`services/addon/protocol.py`）

把 `AI_RESP_MAX_CHUNK_LENGTH = 400` 改为以函数形式从 `FlowControlMiddleware` 动态读取，
保证 `.env` 的 `MAX_CHUNK_CONTENT_LENGTH` 经 `configure()` 注入后真正生效。
模块级常量保留为兼容快照（仅 import 期固化），新代码请直接读 FlowControlMiddleware。

`encode_ai_response_chunks(max_chunk_length=None)` 默认值改为 `None`，由中间件自行决策，
避免双层默认值不一致。

### 2.4 P3.3 e2e 风格测试（`tests/test_flow_control.py`）

新增三个测试类共 7 个用例：

- `TestChunkDelayFor`：验证已知/未知 kind 的延迟值；
- `TestByteSafetyAssertion`：纯中文 / emoji / ai_response 三种场景的 commandLine 字节
  数均 ≤ 461 B（4.4 字节兜底校验的回归保护）；
- `TestEndToEndConnectionFlow`：mock `state.websocket`，调用 `_send_game_message_with_color`
  与 `_run_command`，断言 `websocket.send` 被调次数与每次 payload 的合法性。

---

## 3. 测试结果

```
tests/test_flow_control.py        40 passed
tests/test_addon_bridge_protocol.py + test_connection_manager.py  18 passed
全仓非 live 测试                   146 passed, 13 failed (失败全部与 PydanticAI mock /
                                   stream_mode / MCP / queue_context 相关，
                                   与本次流控变更无关)
```

`TypeError: type 'MockAgent' is not subscriptable` 这类失败是 PydanticAI 版本导致的
既有问题，本次未触碰相关代码路径。

---

## 4. 收益

1. **真正的"统一流控"**：分片逻辑、字节预算、分片间延迟、指标日志四个维度全部收敛
   到 `FlowControlMiddleware` + 配套统一日志方法，不再有"散落在四处的复制粘贴"。
2. **配置可调**：`MAX_CHUNK_CONTENT_LENGTH` 在 `protocol.py` 路径同样生效，不再被
   模块级常量隔离。
3. **可观测性**：所有 `websocket_response_sent` 日志附带 `command_line_bytes`，未来
   可直接用日志统计实测命中率，决定是否调整 461 B 上限或 400 字符默认。
4. **回归防护**：字节兜底测试 + e2e mock websocket 测试覆盖了原报告里"理论上会触底"
   但缺少回归用例的场景。

---

*报告完*
