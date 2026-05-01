# 统一流控中间件审查问题修复

> 关联报告: `claude_md/report/FLOW_CONTROL_MIDDLEWARE_REPORT.md`
> 修复范围: P0、P1 全部 + P2 全部（P3 留作后续迭代）
> 测试: `tests/test_flow_control.py` 33/33 通过；其余测试失败均为预先存在、与本次修复无关

---

## 1. 修复一览

| 报告条目 | 严重程度 | 状态 | 修复方式 |
|---|---|---|---|
| 3.1 `chunk_raw_command` 拆出非法命令 | P0 | 已修 | 改为长度超限抛 `ValueError`，由调用方决策 |
| 4.1 `MAX_CHUNK_CONTENT_LENGTH` / `CHUNK_SENTENCE_MODE` 未接线 | P1 | 已修 | 新增 `FlowControlMiddleware.configure(...)`，在 `cli.py` 应用启动时注入 |
| 4.2 `_send_ws_payload` 反向解析 + 二次转义 | P1 | 已修 | `protocol_handler.create_*_message` 改返回 `TellrawMessage(text, color)`，`_send_ws_payload` 据此统一调 `chunk_tellraw` |
| 4.3 `_split_text` 边界覆盖不足 | P1 | 已修 | 新增 6 个边界测试 |
| 4.4 字符 vs 字节语义错配 | P1 | 已修 | `_assert_byte_safe` 兜底校验 commandLine ≤ 1800 字节 |
| 5.1 256 vs 400 注释易误解 | P2 | 已修 | 重写 `constants.ts` 注释明确上下行差异 |
| 5.2 `chunk_ai_response` 手工拼接 | P2 | 已修 | 改用 `MinecraftCommand.create_scriptevent` 工厂 |
| 5.3 空文本契约 | P2 | 已修 | `chunk_tellraw` / `chunk_scriptevent` 空文本返回 `[]`，`chunk_ai_response` 仍发 1 条 |
| 5.4 `chunk_raw_command` 分支冗余 | P2 | 已修 | 已并入 P0 改造（不再分片） |
| 5.5 `_get_max_length` 配置注入 | P2 | 已修 | 通过 `configure()` 类方法实现 |
| 6.1 集中分片间延迟策略 | P3 | 未做 | 留待后续迭代 |
| 6.2 `commandLine` 长度指标日志 | P3 | 已附带 | `_log_ws_send` 顺手记录 `command_line_bytes` |
| 6.3 e2e 风格测试 | P3 | 未做 | 留待后续迭代 |

---

## 2. 关键代码改动

### 2.1 `services/websocket/flow_control.py`

- 新增类常量 `DEFAULT_SENTENCE_MODE` 与 `_COMMAND_LINE_BYTE_BUDGET = 1800`。
- 新增 `configure(max_content_length, sentence_mode)` 类方法，应用启动时注入运行时默认值。
- `_split_text` 在 `DEFAULT_SENTENCE_MODE=False` 时跳过语义合并，纯按字符等长截断。
- `chunk_tellraw` / `chunk_scriptevent` 空文本短路返回 `[]`；分片后调 `_assert_byte_safe` 字节级兜底。
- `chunk_raw_command` 长度超限直接抛 `ValueError`，禁止静默生成非法分片。
- `chunk_ai_response` 改用 `MinecraftCommand.create_scriptevent` 工厂，避免手工拼接 commandLine。

### 2.2 `services/websocket/minecraft.py`

- 新增 `@dataclass(frozen=True) TellrawMessage(text, color)`。
- `create_error_message` / `create_info_message` / `create_success_message` 改为返回 `TellrawMessage`，不再预先序列化 JSON。

### 2.3 `services/websocket/server.py`

- `_send_ws_payload` 接受 `str | TellrawMessage`：
  - `TellrawMessage` 走 `FlowControlMiddleware.chunk_tellraw` 统一分片；
  - `str` 仅用于订阅消息、连接初始化等已序列化负载，原样发送。
- 删除原来对已序列化 JSON 反向解析提取 text/color 再 `chunk_tellraw` 的逻辑（同时消除二次转义风险）。
- 抽出 `_log_ws_send` 工具方法，统一记录 `command_line_bytes` 便于观察分片命中率。

### 2.4 `cli.py`

- 应用 `start()` 阶段调用 `FlowControlMiddleware.configure(...)`，注入 `settings.max_chunk_content_length` 与 `settings.chunk_sentence_mode`。

### 2.5 `MCBE-AI-Agent-addon/scripts/bridge/constants.ts`

- 注释澄清上行 256 / 下行 400 的双阈值意图，移除"对齐"误导措辞。

---

## 3. 测试

`tests/test_flow_control.py` 共 33 用例，全部通过：

- 原有 17 个用例；
- 新增 6 个 `_split_text` 边界用例：连续分隔符、句首分隔符、超长句紧跟超短句、单字符无分隔符、含 `\r\n`、纯分隔符串；
- 新增 3 个空文本契约用例；
- 新增 3 个 `configure()` 注入用例（含 `sentence_mode=False` 等长截断）；
- `chunk_raw_command` 测试更新为预期 `ValueError`。

> 跑完整 `tests/` 共 152 用例，13 个 fail；逐一确认与本次修改无关（`Unknown model: fake`、`MockAgent not subscriptable`、live LLM 测试等先前已存在）。

---

## 4. 行为变化对调用方的影响

1. **`MinecraftProtocolHandler.create_*_message` 返回类型变更**：从 `str` 改为 `TellrawMessage`。
   仓库内所有调用都立即把返回值传给 `_send_ws_payload`，无其他用法，已兼容。
2. **`chunk_raw_command` 长命令抛 `ValueError`**：当前生产路径未调用此方法，无回归风险；未来若有调用方需要处理超长命令，必须显式拆分。
3. **空文本时 `chunk_tellraw` / `chunk_scriptevent` 返回 `[]`**：上游调用方迭代发送时天然变成 no-op，无负面影响。
4. **配置项真正生效**：`.env` 中 `MAX_CHUNK_CONTENT_LENGTH=300` / `CHUNK_SENTENCE_MODE=false` 现在会改变运行时分片行为。

---

## 5. 留作后续迭代

- P3-6.1：把分片间延迟（0.05/0.15/0.5）集中到中间件策略接口，目前仍由 `connection.py` 的 `_send_*` 方法各自维护。
- P3-6.3：补充 e2e 风格测试（mock websocket、调用 `_send_game_message_with_color`，断言 send 次数 + payload）。

---

*修复完毕*
