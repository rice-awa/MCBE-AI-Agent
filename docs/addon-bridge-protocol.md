# Addon Bridge Protocol (mcbews v1)

## 目标

在 Python 宿主与 Minecraft Addon 之间建立稳定的桥接协议。**线协议权威为 `mcbe-ws-sdk` 的 mcbews v1**（破坏性：不再兼容 `mcbeai:*` / `MCBEAI|*`）。

使用 `/scriptevent` 发起请求，并通过聊天分片返回响应；同时支持 Addon UI 向 Python 自动发送聊天消息。

## 权威资产与独立版本轴

Host、SDK 内置 Addon 和产品 Addon 以 SDK `0.2.1` wheel 内的
`MCBEWS_V1_MANIFEST` / wire vectors 为契约来源；产品 Addon 的
`scripts/bridge/protocol.generated.ts` 是由该资产同步的投影。兼容线
`MCBEWS/1` 是独立的 wire 标识；其余四个 schema/persistence 版本轴也不能混用成一个
“协议版本”：

| 轴 | 当前值 | 含义 |
|------|------:|------|
| 兼容线 | `MCBEWS/1` | wire channel 与兼容策略；不启用 `mcbeai:*` |
| capability request schema | `2` | `mcbews:bridge_req` 请求体的 `v` |
| session schema | `1` | `MCBEWS|SESSION` 请求与 `mcbews:session_resp` 响应的 `v` |
| text response framing | `1` | `mcbews:text_resp` 帧字段/分片语义 |
| DDUI persistence | `2` | Addon 玩家 DynamicProperty 的 per-conversation 状态格式；不是 wire 版本 |

`commandLine` 的 `461` 字节是本项目压力测试得到的**实测兼容预算**，不是
Minecraft 官方 API 的稳定上限；SDK manifest 将其标记为 `empirical`，部署可以把预算调低。

## 运行时拓扑

```text
Minecraft /wsserver
    │
    ▼
McbeServerFacade (mcbe-ws-sdk)
    ├─ ConnectionManager + ConnectionState
    ├─ AddonBridgeService (mcbews v1)
    ├─ HostConnectionHook ──► MessageBroker / auth / 业务命令
    └─ HostResponseSink  ◄── SDK response_queue
BrokerResponseBridge  ◄── MessageBroker per-connection response queue
AgentWorker  ◄── 注入的 AddonBridgeService
```

## 线常量（bit-identical with SDK `MCBEWS_V1`）

| 用途 | 值 |
|------|-----|
| Bridge 请求 scriptevent | `mcbews:bridge_req` |
| AI / 文本响应 scriptevent | `mcbews:text_resp` |
| Session 请求前缀 | `MCBEWS\|SESSION` |
| Session 请求 scriptevent | `mcbews:session_req` |
| Session 响应 scriptevent | `mcbews:session_resp` |
| Bridge 响应聊天前缀 | `MCBEWS\|BRIDGE` |
| UI 聊天前缀 | `MCBEWS\|UI_CHAT` |
| 模拟工具玩家名 | `MCBEWS_BRIDGE` |
| capability request schema | `v=2` |

世界存储动态属性键 `mcbeai:ui_state` **故意保留**，以免旧世界丢 UI 状态；它不是线协议的一部分。

## 链路概览

### 链路 A：Python → Addon 能力请求（Bridge）

```text
Python Agent Tool
  -> AddonBridgeService (SDK)
  -> scriptevent mcbews:bridge_req <json>
  -> Addon scriptEventReceive
  -> capability handler
  -> MCBEWS_BRIDGE 模拟玩家聊天分片 (MCBEWS|BRIDGE|id|i/n|payload)
  -> WebSocket PlayerMessage
  -> SDK 分片重组与 future 唤醒
```

### 链路 B：Addon UI → Python 自动聊天（UI Chat）

```text
玩家 UI 面板输入
  -> MCBEWS_BRIDGE 模拟玩家 (MCBEWS|UI_CHAT|...)
  -> WebSocket PlayerMessage
  -> SDK 重组 -> HostConnectionHook.on_ui_chat_reassembled
  -> MessageBroker / AgentWorker
```

### 链路 C：AI 文本同步到 Addon UI

```text
AgentWorker / CommandHandlers
  -> MessageBroker ai_response_sync
  -> BrokerResponseBridge + McbewsV1Delivery
  -> scriptevent mcbews:text_resp {json chunks}
  -> Addon responseSync.ts 重组并刷新 UI
```

## 请求格式（Python → Addon）

- 命令：`scriptevent mcbews:bridge_req <json>`
- JSON 字段（`v=2`）：
  - `request_id`: string
  - `capability`: string（如 `get_player_snapshot`）
  - `payload`: object
  - `v`: 2

## 专用方块能力（`block_ops` v1）

连接建立后，Python 宿主通过 `get_capabilities` 读取 Add-on 能力。当前行为包返回：

```json
{
  "schema_version": "1",
  "capabilities": {
    "block_ops": {
      "version": 1,
      "inspect": true,
      "place": true,
      "batch": true,
      "fill": true,
      "multiblock_placement": "command_fallback"
    }
  }
}
```

- `block_ops.inspect=true` 时，宿主向模型公开 `inspect_block`、`place_block` 与 `fill_block`；缺失或为 `false` 时返回 `UNSUPPORTED_CAPABILITY`。
- 模型只看到 `inspect_block(target)`、`place_block(pos, block, expect?, states?)` 与 `fill_block(from, to, block, expect?, states?)` 契约；`mode`、`edits`、`dimension`、以及 `locked_targets`、`phase` 等恢复字段均不在模型 schema 中。宿主把这些工具映射到既有 wire capability（`capability=edit_blocks` 的 `mode=place` / `mode=fill` 帧、`capability=inspect_block`），线协议字段不变。
- `place_block` / `fill_block` 的失败结果必须含稳定 `code` 和 `fallback_allowed`。仅 `ADDON_UNAVAILABLE`、`UNSUPPORTED_CAPABILITY` 可为 `true`；其余失败为 `false`。宿主只会对 `setblock`、`fill`、`clone` 自动回退执行此结论，且允许的原始命令仍要单独审批。
- `multiblock_placement="command_fallback"` 表示门、床等多格方块由宿主在获得明确能力结果后，
  按允许的原生命令回退路径处理；它不表示 Addon 的能力 handler 已直接完成多格放置。
  回退命令仍须经过既有审批与安全策略，不能把 `command_fallback` 当作无条件成功。

`config.json` 的 `addon.block_tools` 可调整有界工作量：`max_discrete_positions`、`max_fill_volume`、`cells_per_tick`、`max_locked_targets_on_wire`、`inspect_summary_threshold`、`inspect_sample_limit`。默认值见 `config.example.json`；所有值均由宿主硬上限夹紧。

## 响应分片格式（Addon → Python）

聊天消息：

```text
MCBEWS|BRIDGE|<request_id>|<index>/<total>|<payload_fragment>
```

发送者必须为 `MCBEWS_BRIDGE`（SDK 过滤）。

## UI 聊天分片

```text
MCBEWS|UI_CHAT|<msg_id>|<index>/<total>|<payload_fragment>
```

## 链路 D：会话管理协议（session v1）

会话管理协议允许 Addon UI 以结构化方式操作 Python 侧的会话（对话），覆盖 `AGENT 对话` 命令的完整操作集。
会话请求和响应使用 `session_schema=1`；一个 session response 必须是一个完整、可解析的
`mcbews:session_resp` ScriptEvent，不得经过通用长文本分片器。

### 请求格式（Addon → Python）

Addon 通过可信 ToolPlayer `MCBEWS_BRIDGE` 发送一条完整的聊天命令，格式：

```text
MCBEWS|SESSION|<json>
```

JSON 字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `request_id` | string | 是 | 请求唯一 ID，响应中原样带回 |
| `action` | string | 是 | 操作名称（见下方全集） |
| `player_name` | string | 否 | 目标玩家名 |
| `cid` | string | 否 | conversation_id（new/switch 用） |
| `sid` | string | 否 | session_id（restore/delete 用） |
| `v` | number | 是 | session schema，当前为 1 |

### 操作全集

| action | 对应命令 | 所需参数 | 说明 |
|--------|----------|----------|------|
| `list` | `AGENT 对话 list` | 无 | 列出当前连接内所有对话 |
| `new` | `AGENT 对话 new` | `cid`（可选） | 新建并切换到指定/自动 ID 的对话 |
| `switch` | `AGENT 对话 switch` | `cid`（必填） | 切换到指定对话 |
| `status` | `AGENT 对话 status` | 无 | 当前对话状态 |
| `clear` | `AGENT 对话 clear` | 无 | 清除当前对话历史 |
| `save` | `AGENT 对话 save` | 无 | 保存当前对话到持久化存储 |
| `restore` | `AGENT 对话 restore` | `sid`（必填） | 从持久化存储恢复会话 |
| `saved` | `AGENT 对话 saved` | 无 | 列出已保存的会话 |
| `delete` | `AGENT 对话 delete` | `sid`（必填） | 删除已保存的会话 |
| `compress` | `AGENT 对话 compress` | 无 | 手动压缩当前对话 |

### 响应格式（Python → Addon）

Python 向 Addon 回发 `scriptevent mcbews:session_resp <json>`，JSON 格式：

| 字段 | 类型 | 说明 |
|------|------|------|
| `request_id` | string | 原请求 ID |
| `v` | number | 协议版本 1 |
| `ok` | boolean | 操作是否成功 |
| `action` | string | 原操作名称 |
| `data` | object/null | 结构化响应数据（见下方按 action） |
| `error` | object/null | `{code, message}`；`ok=false` 时非空 |

正常结果与超限结果都只发一个 ScriptEvent。若完整 JSON 超过实测命令预算，SDK 返回仍可
单帧解析的结构化错误，而不是发送碎 JSON：

```json
{
  "v": 1,
  "request_id": "sess-1",
  "action": "list",
  "ok": false,
  "error": {
    "code": "SESSION_RESPONSE_TOO_LARGE",
    "message": "session response exceeds the atomic command budget"
  }
}
```

### 按 action 的 data 结构

**list**:
```json
{
  "conversations": [
    {"id": "chat-...", "short_id": 1, "title": "...", "message_count": 5, "is_active": true}
  ]
}
```

**saved**:
```json
{
  "saved": [
    {"session_id": "...", "title": "...", "message_count": 5, "updated_at": "2026-08-08T..."}
  ]
}
```

**status**:
```json
{
  "conversation_id": "chat-...",
  "short_id": 1,
  "title": "...",
  "turns": 5,
  "max_history_turns": 50,
  "context_enabled": true,
  "title_status": "ready"
}
```

**new** / **switch**:
```json
{
  "conversation_id": "chat-...",
  "short_id": 1,
  "title": "...",
  "message_count": 0
}
```

**clear / save / restore / delete / compress**: 返回 `{"message": "..."}` 文本结果文本。

### 宿主接入

| 组件 | 路径 |
|------|------|
| 协议入口 | `services/gateway/hook.py`（`on_player_message` 检测 SESSION 前缀并 fire-and-forget） |
| 会话处理 | `services/gateway/command_handlers.py`（`handle_session_req` 复用 `_handle_conversation`） |
| 响应桥 | `services/gateway/broker_bridge.py`（`_session_resp` 路由 session_resp→scriptevent） |
| SDK 契约 | `mcbe-ws-sdk` 的 `McbewsV1Profile`（`session_request_script_event_id` / `session_response_script_event_id`） |
| Addon 发送 | `MCBE-AI-Agent-addon/scripts/bridge/sessionClient.ts` |


## text_resp 帧扩展（text framing schema 1）

`mcbews:text_resp` 帧在现有 6 字段（`id, i, n, p, r, c`）基础上扩展可选字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `cid` | string | conversation_id，缺省不输出 |
| `t` | string | 对话标题，缺省不输出 |
| `u` | object | usage 信息 `{i: input_tokens, o: output_tokens}`，只允许出现在完成帧，缺省不输出 |

`cid` / `t` 在同一响应的相关帧中保持一致；`u` 只在 `i == n` 的完成帧携带。
缺省字段时帧结构与旧格式逐字节一致，旧 Addon 解析器按未知字段忽略处理（JSON 天然兼容）。
接收端只接受 `user`、`assistant`、`approval` 三种 role；未知 role 在进入 history 前拒绝。

## UI Chat 扩展

`MCBEWS|UI_CHAT` 上行 payload 扩展可选字段 `cid`（conversation_id），Addon 发聊天时携带当前会话 ID。
SDK 重组后将 `cid` 传入 Host 的 `ChatRequest.conversation_id`；缺失时只归一化为 `default`，
不会读取接收时的 active conversation 作为替代。

## DDUI persistence 2 与玩家状态

`responseSync.ts` 通过 `isV2State()` 运行时类型守卫区分旧版扁平 `history` 和 DDUI persistence
`2` 的 `conversations[id].history` 桶。新增代码只使用 `AgentUiStateV2`；旧状态只在加载/迁移
路径归入 `default` 会话。当前产品 UI 仍使用 `ActionFormData` / `ModalFormData` 适配层，
因此 persistence version `2` 不宣称已接入官方 DDUI `CustomForm` / `Observable` API。

## 宿主接入点更新

| 组件 | 路径 |
|------|------|
| 网关入口 | `services/gateway/server.py` (`HostGatewayServer`) |
| 设置映射 | `services/gateway/settings_map.py` |
| Hook / 命令 | `services/gateway/hook.py`, `command_handlers.py` |
| Broker 出站 | `services/gateway/broker_bridge.py` |
| 会话协议入口 | `services/gateway/hook.py`（SESSION 前缀）, `command_handlers.py`（handle_session_req） |
| WS command 关联 | `services/gateway/ws_command_runner.py` |
| 宿主 Addon 常量 | `MCBE-AI-Agent-addon/scripts/bridge/constants.ts` |
| 推荐 SDK Addon 参考 | `mcbe-ws-sdk/addon/scripts/bridge/` |

## 安装、构建与本地部署

Addon 工程位于 `MCBE-AI-Agent-addon/`，本地调试前至少执行一次依赖安装、测试、构建和本地部署：

```bash
cd MCBE-AI-Agent-addon
npm install        # 安装 @minecraft/server、@minecraft/server-ui 与构建依赖
npm test           # 运行桥接协议、路由与 UI 状态容器相关测试
npm run build      # 构建行为包脚本
npm run local-deploy  # 部署到 Minecraft 本地开发目录
```

打包好的 Addon 也可在 [GitHub Releases](https://github.com/rice-awa/MCBE-AI-Agent/releases) 获取。

## 调试步骤

1. 启动 Python 服务：`python cli.py serve --dev`（开发模式跳过 `#登录`；可先 `python cli.py info` 验证配置）。
2. 在 `MCBE-AI-Agent-addon/` 下执行 `npm run local-deploy`，确保最新脚本已部署。
3. 进入启用了对应开发包的世界，等待 Addon 初始化。
4. 在游戏内确认模拟玩家 `MCBEWS_BRIDGE` 已生成。
5. 使用 `/wsserver <服务器IP>:8080` 连接 Python 服务。
6. 执行一次正常聊天命令，例如 `AGENT 聊天 读取一下我当前附近的实体`，观察 Python 日志与游戏内行为。
7. 手持原版命令方块 `minecraft:command_block` 并使用，确认游戏内聊天面板可以打开。
8. 在面板中发送一条消息，确认本地历史、统计信息和设置保存行为正常；如果 Python 未收到 UI 消息，请按面板提示在聊天框手动发送等价的 `AGENT 聊天 <消息>`。

## 验证桥接链路

当前桥接方向是 `Python -> scriptevent -> Addon -> 模拟玩家聊天分片 -> Python`。按下面的方式确认链路完整：

1. 先确认 `MCBEWS_BRIDGE` 存在。
2. 触发一个会调用 Addon 能力的 Agent 请求，例如：

```text
AGENT 聊天 请读取我的玩家状态并告诉我当前位置
```

3. Python 侧应向游戏发送 `scriptevent mcbews:bridge_req <json>`。
4. Addon 侧处理后，会驱动 `MCBEWS_BRIDGE` 以聊天分片形式回传 `MCBEWS|BRIDGE|...`。
5. Python 侧会在 WebSocket `PlayerMessage` 事件流中拦截这些分片并完成重组，最终把工具结果继续交给 Agent。

如果第 3 步已发出但最终超时，通常表示：

- Addon 未正确部署或世界未启用最新行为包。
- `MCBEWS_BRIDGE` 未生成或被移除。
- 聊天分片没有成功回到 Python 所连接的 WebSocket 事件流。

## 当前桥接能力

- `get_player_snapshot`：获取目标玩家基础快照，包括位置、维度、朝向和基础状态。
- `get_look_block`：获取目标玩家视线射线命中的方块（`getBlockFromViewDirection`），默认当前对话玩家。
- `get_inventory_snapshot`：获取目标玩家背包槽位与物品快照。
- `find_entities`：按类型、名称、标签、距离等条件查找实体。
- `run_world_command`：由 Addon 在世界侧执行命令并返回结果。

## 聊天命令与 UI 共存说明

当前 UI 实现为第一阶段游戏内聊天面板，不替代现有聊天命令入口：

- 现有 `AGENT 聊天`、`AGENT 上下文`、`切换模型`、`运行命令` 等聊天命令仍然是主入口。
- 面板入口绑定为使用原版命令方块物品 `minecraft:command_block`，避免抢占聊天监听。
- 面板支持发送消息、本地聊天记录、设置保存和统计信息；发送消息会记录本地历史，并提示等价的 `AGENT 聊天 <消息>`。
- 当前本地 `@minecraft/server-ui` 类型只暴露 `ActionFormData` / `ModalFormData`，暂不能直接使用官方 DDUI `CustomForm` / `Observable`；
  这不影响 DDUI persistence `2` 的 per-player/per-conversation DynamicProperty contract。
- 后续如果类型和运行时支持真正 DDUI，可在 Addon 的表单适配层中替换实现，而不重写业务状态。

## 当前限制

- Addon -> Python 的响应回传依赖模拟玩家 `MCBEWS_BRIDGE` 发送聊天分片，不是独立的回传通道。
- Python 侧通过 WebSocket `PlayerMessage` 事件流拦截桥接分片，因此桥接能力依赖聊天事件正常上送。
- `run_world_command` 在当前本地依赖版本下基于同步 `runCommand` 实现，不是异步命令管线。

## 破坏性说明

旧世界若仍装 **mcbeai** 行为包，桥会超时。必须：

1. 更新行为包到 mcbews 常量版本；
2. 开启 **实验 → Beta APIs**；
3. 重新 `/wsserver` 连接。

配置里遗留的 `addon.protocol.*` 值是 deprecated/ignored 的文档镜像：读取可产生迁移诊断，
但不能改变任何运行时 wire 值；运行时始终使用 SDK `McbewsV1Profile` 的 MCBEWS/1 manifest。

## 发布顺序与真实 MCBE smoke checklist

本任务不宣称 SDK 已发布，也不自动创建 tag、上传 PyPI 或发布 Addon。发布者必须按以下顺序操作：

1. 在 SDK 仓库合并包含本契约的变更，构建并发布 SDK `v0.2.1`，确认 PyPI 上的 wheel artifact
   可下载、metadata 为 `0.2.1`，且 wheel-installed public/codec contract 通过。
2. 在 Host 仓库以干净 venv 安装该 PyPI wheel，运行 `tests/test_sdk_dependency.py` 与 Host gate；
   再合并/发布 Host 和产品 Addon。
3. Addon 发布后再执行真实世界 smoke；不要以本地 editable SDK 或仅单元测试替代发布验证。

真实 MCBE smoke 至少覆盖：

- 记录 `PlayerMessage` 的真实 `sender`，并记录 ScriptEvent 的 `sourceType` / source object；
  验证可信 ToolPlayer 为 `MCBEWS_BRIDGE`，业务 `player_name` 仍来自已验证 payload/pending owner。
- 两名玩家、两个 conversation 交错发送 UI Chat；验证 CJK 与 surrogate-pair emoji 无损，最终
  `tell @s` commandLine 不超过实测 `461` 字节。
- 发送长 session `list` / `saved` 结果，确认正常响应是单帧，超限响应是可解析的
  `SESSION_RESPONSE_TOO_LARGE`，而不是等待超时或碎 JSON。
- 触发单个和批量 approval，验证玩家+CID 归属、伪造 owner 被拒绝、断线后 pending/task 清理。
