# MCBE AI Agent — 硬编码内容提取评审报告

**生成时间**：2026-06-19  
**评审范围**：`cli.py`、`config/`、`core/`、`models/`、`services/`（含 websocket、agent、auth、addon）  
**评审目的**：识别可提取到 `config.json` / 模块常量的硬编码内容，提升 Locality、Leverage 与可测试性。

---

## 1. 评审结论

| 指标 | 数量 |
|---|---|
| 主要提取候选 | 7 个 |
| 重复字面量位置 | 12+ 处 |
| 与现有 ADR 冲突 | 未发现 |

整体判断：仓库已经通过 `config.example.json` / `config/settings.py` 将大量配置外部化，但仍有**玩家可见文本**、**重复默认值**、**运行时调参数值**散落在业务代码中，具备较好的提取价值。

---

## 2. 首要推荐

建议优先处理以下两个候选：

1. **候选 1：玩家可见中文消息文本** — 影响面集中，收益最直接（文案可热更新、支持本地化）。
2. **候选 2：重复默认字面量** — 侵入性最低，能立即消除跨模块不一致风险。

这两个候选为后续更复杂的配置化（流控数值、Harness 规则）建立统一模式：

- 普通配置进入 `config.json` / `config.example.json`；
- 纯内部语义常量保留为模块常量；
- 敏感信息只放在 `.env`。

---

## 3. 候选详情

### 候选 1：玩家可见中文消息文本

| 项目 | 内容 |
|---|---|
| **推荐强度** | **Strong** |
| **涉及文件** | `services/websocket/server.py`、`services/agent/worker.py`、`core/conversation.py`、`core/exceptions.py` |
| **问题** | 玩家可见文本直接嵌入 Handler 与业务逻辑，形成浅层模块。任何文案调整都需要修改多处源码，无法在不发布新版本的情况下调整语气、修复错别字或做本地化。 |
| **方案** | 在 `config.example.json` 中新增 `minecraft.messages` 与 `agent.messages` 配置段；代码中通过 `settings.minecraft.messages.login_success` 等读取。保留合理的模块级 fallback 常量。 |
| **收益** | - **Locality**：文案变更集中在一处配置。<br>- **Leverage**：新增命令或修改提示无需触碰业务逻辑。<br>- 测试可从接口层面验证配置键渲染，而非逐行断言硬编码字符串。 |

**Before / After 示意**：

```text
Before:
  server.py  → "登录成功！"
  server.py  → "上下文已启用"
  worker.py  → "AI正在为{player}思考"
  conversation.py → "压缩完成..."

After:
  config.json → minecraft.messages.login_success
  server.py   → settings.minecraft.messages.login_success
  worker.py   → settings.agent.messages.thinking_broadcast
```

---

### 候选 2：重复默认字面量

| 项目 | 内容 |
|---|---|
| **推荐强度** | **Strong** |
| **涉及文件** | `core/session.py`、`services/agent/prompt.py`、`services/websocket/connection.py`、`models/messages.py`、`services/websocket/minecraft.py`、`services/agent/worker.py`、`services/websocket/server.py`、`services/agent/core.py`、`services/agent/title.py` |
| **问题** | 相同字面量在多个模块中手写，违反 DRY。修改默认值时容易漏改，且不同模块的 fallback 可能不一致，导致多人会话隔离或默认模型行为出现意外差异。 |
| **方案** | 在 `core/session.py` 已存在的常量基础上，统一所有调用方 import；默认玩家名提升为模块常量；默认模型从 `settings.default_provider` 与对应 provider 的 `model` 推导。 |
| **收益** | - **Locality**：默认值只在一个地方声明。<br>- 删除测试更容易验证：修改单一常量即可覆盖全链路。<br>- 避免不同模块对同一概念给出不同 fallback。 |

**重复的硬编码值**：

| 字面量 | 出现位置 | 建议处理 |
|---|---|---|
| `"__anonymous__"` | `core/session.py`、`services/agent/prompt.py`、`services/websocket/connection.py` | 统一从 `core/session.py` import |
| `"default"` 对话 ID | `core/session.py`、`models/messages.py`、`services/websocket/minecraft.py` | 统一从 `core/session.py` import |
| `"Player"` | `services/agent/worker.py`、`services/websocket/server.py`、`services/websocket/connection.py` | 新增 `DEFAULT_PLAYER_DISPLAY_NAME` 常量 |
| `"deepseek:deepseek-chat"` | `services/agent/core.py`、`services/agent/title.py` | 从 settings 推导默认 provider/model |

---

### 候选 3：流控与 Agent 数值调参

| 项目 | 内容 |
|---|---|
| **推荐强度** | **Strong** |
| **涉及文件** | `services/websocket/flow_control.py`、`services/agent/core.py`、`services/agent/worker.py`、`core/conversation.py` |
| **问题** | 这些数值与运行时环境（MCBE 版本、网络延迟、模型速度）强相关，但当前深埋在实现内部。每次调优都需要改代码并重新测试整个模块。 |
| **方案** | 已有 `flow_control.max_chunk_content_length` 与 `flow_control.chunk_sentence_mode`，继续扩展为完整调参表；worker 超时进入 `agent.worker_*`；摘要模型进入 `agent.compression.*`。 |
| **收益** | - **Leverage**：一次配置调整即可影响所有调用点。<br>- **Locality**：性能调优集中在配置文件，无需改动核心逻辑。<br>- 方便为不同部署环境维护不同配置。 |

**可提取的数值示例**：

| 硬编码 | 文件 | 建议配置键 |
|---|---|---|
| `_COMMAND_LINE_BYTE_BUDGET = 461` | `services/websocket/flow_control.py` | `flow_control.command_line_byte_budget` |
| `_CHUNK_DELAYS` 映射 | `services/websocket/flow_control.py` | `flow_control.chunk_delays.*` |
| `NON_STREAM_BATCH_MAX_CHARS = 150` | `services/agent/core.py` | `flow_control.non_stream_batch_max_chars` |
| `NON_STREAM_SEND_DELAY = 0.1` | `services/agent/core.py` | `flow_control.non_stream_send_delay` |
| `httpx.AsyncClient(timeout=60)` | `services/agent/worker.py` | `agent.worker_http_timeout` |
| `wait_for(..., timeout=10.0)` 命令响应 | `services/agent/worker.py` | `agent.run_command_timeout` |
| `retries=2` | `services/agent/core.py` | `agent.retries` |
| `openai:gpt-4o-mini` 摘要模型 | `core/conversation.py` | `agent.compression.summary_model` |

---

### 候选 4：Addon 协议标识符

| 项目 | 内容 |
|---|---|
| **推荐强度** | Worth exploring |
| **涉及文件** | `services/addon/protocol.py` |
| **问题** | `BRIDGE_MESSAGE_ID`、`BRIDGE_PREFIX`、`UI_CHAT_PREFIX`、`BRIDGE_TOOL_PLAYER_NAME`、`AI_RESP_MESSAGE_ID` 是 addon 端与服务端的契约。当前作为模块常量，跨仓库/跨版本同步时缺乏显式配置 seam。 |
| **方案** | 新增 `addon.protocol` 配置段，服务启动时读取并注入协议模块；addon 端通过对应配置或构建变量消费。保留当前常量作为 fallback。 |
| **收益** | - **Seam**：协议标识成为可替换的适配点。<br>- 降低跨端联调时因硬编码字符串不一致导致的排查成本。 |

---

### 候选 5：文件路径与日志配置

| 项目 | 内容 |
|---|---|
| **推荐强度** | Worth exploring |
| **涉及文件** | `config/logging.py`、`core/conversation.py`、`services/auth/jwt_handler.py` |
| **问题** | 路径和日志策略写死在模块中，部署到容器或希望按环境切分日志目录时，必须修改源码或挂载到固定路径。 |
| **方案** | 新增 `logging.log_dir`、`logging.files.*`、`logging.rotation.*` 以及 `storage.conversations_dir` / `storage.tokens_file`。 |
| **收益** | - **Leverage**：部署配置即可改变持久化位置。<br>- 测试可使用临时目录替代真实文件系统。 |

**可提取路径示例**：

| 硬编码 | 文件 | 建议配置键 |
|---|---|---|
| `logs/`、`app.log`、`websocket.log`、`llm.log` | `config/logging.py` | `logging.log_dir`、`logging.files.*` |
| `data/conversations` | `core/conversation.py` | `storage.conversations_dir` |
| `data/tokens.json` | `services/auth/jwt_handler.py` | `storage.tokens_file` |

---

### 候选 6：CLI 输出文本

| 项目 | 内容 |
|---|---|
| **推荐强度** | Speculative |
| **涉及文件** | `cli.py` |
| **问题** | `init`、`info`、`test-provider`、`runtime-harness analyze`、`mcp` 等命令的输出文本完全硬编码。虽不影响运行时 Harness，但维护成本随命令数量增加而上升，且与游戏内消息配置不统一。 |
| **方案** | 新增 `cli.messages` 配置段；或退一步，先集中到 `cli/_messages.py` 常量模块，再根据需求决定是否进入 config。 |
| **收益** | 运维侧文案与游戏侧文案统一分层；便于后续支持多语言 CLI。 |

---

### 候选 7：Runtime Harness 工具目录与提示文本

| 项目 | 内容 |
|---|---|
| **推荐强度** | Worth exploring |
| **涉及文件** | `services/agent/harness/catalog.py`、`services/agent/harness/prompting.py`、`services/agent/harness/analyze.py` |
| **问题** | 工具目录和审计规则与代码紧耦合。当需要调整工具说明或审计敏感度时，必须修改 Python 源码，增加了反馈闭环的摩擦。 |
| **方案** | 已有 `agent.runtime_harness` 配置段，继续扩展为 `agent.runtime_harness.tool_catalog`、`.prompt_guidance`、`.audit.*`。初期可保持 JSON/YAML 结构化数据文件，由代码加载。 |
| **收益** | - **Leverage**：提示词迭代和规则调整不再重新部署。<br>- **Locality**：工具智能化相关配置集中在同一配置域。 |

---

## 4. 与现有配置的冲突说明

以下配置项已经在 `config.example.json` 中定义，代码里不应再重复硬编码默认值，而应优先从 `settings` 读取：

- `minecraft.commands`
- `minecraft.welcome_message_template`
- `minecraft.*_prefix` / `*_color`
- `flow_control.*`
- `auth.jwt_expiration`
- `providers.*`
- `agent.compression_*`
- `model_metadata.*`

`.env` 只应存放密钥、密码等敏感项，不要把普通配置（超时、消息文本、路径）放进 `.env`。

---

## 5. 建议执行顺序

若决定提取，建议按以下顺序执行，以控制改动范围并尽早获得收益：

1. **统一重复默认字面量**（候选 2）— 最低侵入，消除不一致。
2. **迁移玩家可见中文消息**（候选 1）— 文案可配置化，收益最大。
3. **提取流控与 Agent 数值调参**（候选 3）— 性能调优可热更新。
4. 视需要继续处理 Addon 协议标识符、日志路径、CLI 文本、Harness 目录。

---

## 6. 备注

- 本报告未修改任何仓库源代码。
- 报告遵循项目 `CONTEXT.md` 术语（运行时 Harness、MCBE Chat Agent、工具目录、反馈闭环等）与 `improve-codebase-architecture` 技能中的架构词汇（Module、Seam、Locality、Leverage）。
